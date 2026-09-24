# Backlog pushes on purpose, reruns flaky CI once, and bounds each run to eight tasks

**Status:** proposed (2026-09-24)
NOT YET IMPLEMENTED. Every number below is from the run store, the scheduler's
`run_history` table and GitHub, measured on 2026-09-24. Nothing in the graphs has changed.

The auto-merge stage is live on all four repositories, and in practice it does not merge.
Of the 34 PRs that scheduler-dispatched `backlog` runs opened between 2026-09-19 and
2026-09-24, the workflow squash-merged **4**. The operator merged 29 by hand and closed
1. After the stall-watchdog fix of 2026-09-22 the rate did not change: 2 of 13. We
make six changes. Four fix why the merge phase stops. Two make runs shorter.

## What stopped the other 30

| Cause | PRs | Status |
|---|---|---|
| Stall watchdog cancelled `review_merge.watch_checks` at 1800s | writers-app#944, #946, #947, #948, #951, #952, #953, lawncare-saas#2610 | Fixed 2026-09-22 (`stall_timeout="60m"`) |
| `ci_fix` gave up on a flake or a CI infrastructure fault | writers-app#966, womens-fantasy-sports#1245, #1246, lawncare-saas#2608 | **D2** |
| `ci_fix` could not fix a failure in files outside the PR diff | lawncare-saas#2606, #2607, womens-fantasy-sports#1237 | **D4** |
| `ci_fix_t1` hit its 20m timeout ("CI-fix report is missing or invalid") | writers-app#965, lawncare-saas#2609, womens-fantasy-sports#1236 | **D3** |
| `merge` hit fabro's 10m command default while it waited for CI on a new head | writers-app#963, womens-fantasy-sports#1247 | **D1** |
| Merge-phase `standards` hit its 15m timeout on a 23-file and a 72-file PR | lawncare-saas#2618, womens-fantasy-sports#1238 | **D6** for #2618; neither for #1238 |
| The 60-minute merge budget ran out before CI settled | writers-app#955, #964 | **D1**, indirectly |
| No check reported within five minutes | writers-app#945, womens-fantasy-sports#1235 | Not examined; both on 2026-09-21 |
| Squash merge rejected at once | jelly-swipe#389, womens-fantasy-sports#1234 | Not examined; both on 2026-09-20 |
| Unfixed findings, merge correctly refused | womens-fantasy-sports#1248 | Working as designed |
| Empty PR, closed | writers-app#949 | Fixed by `open_pr_prep`'s empty-tree floor |

## D1. `backlog` stops pushing its run branch after every stage

**Evidence.** Fabro makes every checkpoint an allow-empty commit
(`lib/components/fabro-workflow/src/sandbox_git.rs:105`, `options.allow_empty = true`, not
configurable). `backlog` runs with `[run.run_branch] push = true`, so each stage pushes a
commit, and once the PR exists each push starts a full CI run. womens-fantasy-sports#1247
carries 80 commits, and its branch has 78 Actions runs, 53 of them cancelled. Commit
`4e5e751`, the `watch_checks` checkpoint, changes 0 files. That flood competes with every
other PR for the self-hosted runners.

It also defeats the merge. The checkpoint after `watch_checks` moves the head, so `merge`
waits in `settle` for CI to start again from zero. `merge` has no `timeout`, so it gets
fabro's 600000ms command default. On #1247 it timed out at "3 pending of 14", and on #963
at "1 pending of 7". Both runs had already passed CI on the reviewed head.

**Change.**

1. `backlog/workflow.toml`: `[run.run_branch] enabled = true, push = false`. This is
   the setting `pr-review` already uses. Checkpoints still commit locally.
2. **`open_pr` must widen the fetch refspec for the run branch.** Without this step, D1
   breaks `deliver`. The clone is single-branch (`remote.origin.fetch` maps only `main`),
   so there is no `refs/remotes/origin/fabro/run/<id>`. A bare `--force-with-lease` then
   has no expected value, and git rejects it as `stale info`. Reproduced on 2026-09-24
   with a real bare remote. Today `deliver`, `ci_fix_gate` and `rebase` succeed only
   because the checkpoint push has already moved the remote to `HEAD`, which makes each
   of their pushes an `Everything up-to-date` no-op. **Every explicit push in `backlog`'s
   merge phase is a no-op today.** The checkpoint push does the real work. After
   `git push -u origin HEAD`, `open_pr` runs
   `git config --add remote.origin.fetch "+refs/heads/$B:refs/remotes/origin/$B" && git fetch origin`,
   the same widening `pr-review`'s `claim` does. Later lease pushes then update the
   tracking ref themselves (verified in the same reproduction).
3. `merge` gets `timeout="50m"`. `settle` is already bounded by `merge_deadline`. This
   value makes the node timeout larger than that bound and keeps it below
   `stall_timeout="60m"`.
4. Correct the comments that D1 makes false. `merge`'s "a node boundary is a checkpoint
   is a new head" stays true locally but is now false on the remote.
   `backlog/workflow.toml`'s "push stays true so in-progress work survives a server
   crash" is removed. Nothing here resumes from a pushed run branch. A restart fails the
   run (ADR 0002), and any retry starts from a fresh clone.

**What changes around it.** CI runs once when `open_pr` pushes and once when `deliver`
pushes. After that it runs only when `ci_fix_gate` or `rebase` pushes a real change. The
`.github/workflows/` invariant loses its quiet half: a rejected push is no longer a
checkpoint warning, and `open_pr` meets the rejection directly. `open_pr_prep`'s
`git log origin/main..HEAD -- .github/workflows/` gate stays. The branch sweeper has less
to reap, because a run that never reaches `open_pr` never publishes a branch. The
`--delete-branch=false` invariant stays, though its original reason (a later checkpoint
publish) no longer applies to `backlog`.

## D2. `watch_checks` reruns the failed jobs once before it calls `ci_fix`

**Evidence.** In four of the seven cases where `ci_fix` "could not fix", the failure had
no connection to the change:

- writers-app#966: a Rust reindexer integration test missed its 5s window, and the PR
  touched only frontend files.
- womens-fantasy-sports#1246: `test-playwright (3, 4)`, a theme-toggle test the repository
  already calls flaky (commit `e502b5b`).
- womens-fantasy-sports#1245: `test-docker-compose`, a cold LAN registry mirror timed out
  after 720s, three times.
- lawncare-saas#2608: `build-backup`, `blob upload invalid` from the registry cache.

The operator merged all four with no further commits, so the same code passed CI when it
ran again. #1246's head shows both the failed `test-playwright (3, 4)` attempt and the
later passing one.

**Change.** When checks settle with a failure and no rerun has happened in this merge
phase, `watch_checks` runs `gh run rerun <id> --failed` for each failing Actions run,
writes `/tmp/fabro/ci_rerun_done`, and goes back to polling in the same node. A failure
after the rerun goes to `ci_fix_t1` as today. The rerun happens only if both of these
are true:

- at least 30 minutes of `merge_deadline` remain, so a real failure still leaves
  `ci_fix` its budget
- the node has run less than 20 minutes, so a slow repository's second wait cannot hit
  the node's 50m timeout. That timeout routes to `mark_needs_human`, which is worse than
  `ci_fix`.

A check that is not an Actions run cannot be rerun, and falls through to `ci_fix`.

**Cost.** A real failure now waits one extra failed-job cycle before `ci_fix` starts.
That is the trade: in this sample, 4 flakes against 3 real failures.

## D3. `ci_fix_t1` runs on `glm-5.3-flash`

**Evidence.** `ci_fix_t1` carries `class="rebase"`, which in `backlog` resolves to the
run's leased coder box. Over 12 visits its p50 was 14.0 minutes, and 3 visits died at the
20m timeout (writers-app#965, lawncare-saas#2609, womens-fantasy-sports#1236). Each
timeout leaves no `ci_fix_result.json`, and the merge is refused as "report missing or
invalid". In `pr-review`, `.rebase` also names the coder pool, so the same stage runs on a
box there too.

**Change.** Add a new class `.ci-fix { model: glm-5.3-flash; }` to **both** importing
stylesheets. That is the review-merge coverage invariant: a class missing from one of
them silently inherits `*`, which in `backlog` is a local box. `ci_fix_t1` moves to it,
and its label loses "(tier 1: coders)". `ci_fix_t2` stays on `.rebase-t2` (`glm-5.3`),
and `rebase` stays on the box. glm-5.3-flash is reasoning-capable and has a 262144
window, so the `glm-4.7` rule does not apply.

**The 20m timeout is now unmeasured.** It was sized on the box, and the invariant on
moving a class says that voids it. It stays at 20m because the 60-minute merge budget
assumes one 20m attempt per tier. Record the flash durations of the first ten visits and
re-time the node from them.

## D4. A repository's `ci.sh` runs what its GitHub CI runs

**Evidence.** The three out-of-diff failures were backend tests or generated files that
`validate` never ran. At that point the coder could still have fixed them without
touching `ci_fix`'s file boundary.

- lawncare-saas#2606: `tests/test_cron_tick.py` and `tests/test_isolated_writes.py` patch
  `backend.services.cron_steps.cross_account_session`, which the PR removed.
- lawncare-saas#2607: `api-staleness` needed a regenerated `frontend/src/api/*.gen.ts`, and
  `test` failed too.
- womens-fantasy-sports#1237: `scripts/measure-schedule-shape.py:143` still calls
  `gameweek_for(session, …)` after the PR removed `session`.

lawncare-saas's `.fabro/ci.sh` runs `ruff`, `typecheck` and `build`, with no pytest.
womens-fantasy-sports' runs `prek`, frontend unit tests and `build`, with no backend
suite. Across the 38 runs, `validate` failed 0 times in 33 lawncare-saas visits. That is
not a sign of clean code. The check that would fail is not there.

**Change, in the target repositories (not this one).**

- lawncare-saas: the `ci.sh` that `docs/perf/03-repo-contract-changes.md` already measured
  (`fabro-pg-ensure`, `CI=1`, the `test.yml` environment,
  `uv run pytest -n auto tests/`: 2430 passed in 135s). Also add the
  `scripts/check-api-stale.sh` that `frontend.yml`'s `api-staleness` job runs, from that
  job's working directory.
- womens-fantasy-sports: an issue in that repository to make the backend suite run in a
  fabro sandbox. `docs/perf/03` records what is left: mailcatcher, `prestart.sh`'s
  `TypeError`, and the 8-worker cap. Until it lands, backend failures there still reach
  `ci_fix`.

`ops/profile-images/build-images.sh` gates each image on its repository's `ci.sh`.
Rebuild `fabro-python-node:local` after the lawncare-saas change merges. Expect
lawncare-saas's `validate` failure rate to rise from 0. Those are failures the coder can
now fix while it still holds the task.

`ci_fix`'s file boundary does **not** change. It is what stops an unreviewed file from
merging unread. D4 moves these failures to a stage where the boundary does not apply.

## D5. An autofix step runs between each coder stage and its review

**Evidence.** `validate` failed 33 times in 246 visits (13%), and **31 of those were
writers-app**. In writers-app, 31 of 154 visits failed, and run
`01M358C6G3BFF0KD1NCDC58AR9` took `coder → validate ✗ → rework_t1` on most of its 21
tasks. Most of the failures were `cargo fmt` diffs (`Diff in …/scene_path_map.rs`) and
clippy lints (`item in documentation is missing backticks`, `using .clone() on a
ref-counted pointer`, an unfulfilled lint expectation). A formatter fixes those with no
model involved. Each one cost a `rework_t1` stage (p50 3.5 min) and a second `validate`
(p50 1.8 min), and it spent a rung of the rework ladder.

**Change.**

- A new optional per-repository contract file, `.fabro/fix.sh`. It may contain only
  formatters and machine-applicable lint fixes: `cargo fmt --all`,
  `cargo clippy --fix --allow-dirty --allow-staged …`, `ruff format` plus
  `ruff check --fix`, `eslint --fix`. It must never contain a step that can change
  behaviour or generate code. It must also not reformat files that CI does not check.
- A new command node, `autofix`, in `backlog`'s task loop. The edges
  `coder → prep_review` and `rework_t1..t4 → prep_review` become `… → autofix`, and
  `autofix → prep_review [condition="outcome=succeeded"]` is added.
- `autofix` runs `./.fabro/fix.sh` when the file exists, prints its output, and always
  exits 0. It fails open because `validate` is the gate, not this node. Its unconditional
  edge goes to `human_rescue`, as the command-node invariant requires.
- The checkpoint after `autofix` commits whatever it changed, before `prep_review`
  computes the diff. The reviewer therefore reviews the formatted code, and nothing
  lands unreviewed.
- The first `fix.sh` goes into writers-app. The other three repositories get one only if
  their `validate` failures show the same kind of mechanical fault.

`autofix` does not help with compile errors or failing tests. Those still take the ladder.

## D6. A run implements at most eight tasks, and files the rest as one remainder issue

**Evidence.** Six of the 38 runs reached 9 or more coder visits (9 to 17). They held **42% of all
lease hours** (79 of 189), averaged 13 hours each against 3.4 hours for the rest,
produced PRs with a median of 30+ changed files, and **none of them auto-merged**. One
of them produced one of the two merge-phase `standards` timeouts (lawncare-saas#2618).
The other timeout, womens-fantasy-sports#1238, was a 72-file PR from a run below the
budget. D6 does not bound file count. Runs with 1–8 tasks auto-merged 4 of
26.

These runs did not start large. `decompose` produced 4, 8, 7, 6, 5 and 4 tasks. `improve`
splits then added 1–3 tasks per run. Most of the growth came from the pre-PR extra review:
its follow-ups added 3–10 tasks per run. Across all 38 runs, follow-ups account for 64 of
188 coder visits (34%). So a cap on `decompose` alone would not have limited any of the
six. The cap must count every task the run starts, from any source.

**Change.**

- **Task budget: 8.** It counts tasks that reach a coder, from any source:
  `decompose`, `improve` splits, and extra-review follow-ups. `improve_gate` increments
  `/tmp/fabro/tasks_coded` each time it routes a task to `coder`. A task `improve` calls
  `redundant` does not count, and neither does the parent of a split, which never reaches
  a coder itself. Counting `next_task` selections instead would count a split task twice,
  because a split rewinds `task_index` and `next_task` selects the same slot again.
- `next_task` enforces the budget, because it is the one node every task passes through.
  When 8 tasks have reached a coder and more remain, `next_task` moves the remaining
  entries to `/tmp/fabro/remainder.json` and reports `tasks_done=true`.
- `extra_prep` starts no new extra-review round once the budget is spent. The round that
  spent it still runs and still reviews. Its follow-ups go to the remainder, not the
  queue.
- A new command node, `file_remainder`, runs between `open_pr` and `pr_handoff`. When
  `remainder.json` is non-empty, it files a **remainder issue**. The issue links the
  parent issue and this PR, lists the remaining tasks, and says the tasks were decomposed
  against an older `main` and must be decomposed again. Its body carries the machine line
  `<!-- fabro:remainder parent=<N> pr=<P> -->`. It is labelled `agent-remainder` and
  `ai-generated`, and **not** `agent`, so the queue cannot see it while `main` lacks this
  PR. The node also posts one comment on the PR naming the remainder issue. `render.jq`
  does not change.
- **The scheduler promotes it** (amended 2026-09-24, replacing "`report_merged` adds
  `agent`"). On each 60-second inventory poll, it reads the open `agent-remainder`
  issues of each repository and checks the parent PR named in the marker:
  - merged, automatically or by hand: it adds `agent` and `priority`, and removes
    `agent-remainder`
  - closed without merging: it adds `agent-stuck`, and removes `agent-remainder`
  - still open: it changes nothing.

  The promotion therefore needs no human on either merge path. A PR that never merges
  leaves the remainder visibly parked, not silently lost.
- **`priority` label.** The scheduler ranks any queued issue that carries `priority`
  after the operator's manual override and ahead of the starvation and repo-priority
  tiers, oldest first. The operator can use it on any issue. It is how a promoted
  remainder becomes the next issue worked.
- The PR still says `Resolves #N`, which the merge gate requires. The parent closes on
  merge, and the remainder issue carries its unfinished scope. We accept that the parent
  closes with scope still open, rather than keep a parent open that the scheduler would
  dispatch again.

**The quality trade.** An extra-review follow-up is sometimes a correction to this PR's
own code, not new scope. Past the budget, that correction lands in the remainder issue
instead of this PR. The merge phase's `standards`, `spec` and `review_fix` still review
the PR and fix findings in the diff before any merge, so a deferred follow-up does not
mean an unreviewed defect.

**8 is a first value, not a measurement of the right one.** It sits at the top of the
bucket that still auto-merged. The sample is small (26 runs of 8 tasks or fewer, 4 merges).
Re-derive it from the next 30 runs.

## Not adopted (operator decision, 2026-09-24)

The same review proposed four other changes. The operator declined them. They are listed
so that nobody proposes them again without new evidence.

- Moving `.improve` back to a hosted model. On the box it is 27% of box-busy time, with a
  p50 of 7.6 min, against about 3 min on `glm-5.3`.
- Releasing the coder lease at `pr_handoff`. The box is busy for only 60% of lease time,
  and the merge phase averages about 50 minutes per run.
- Moving the merge phase's `rebase` to a hosted class.
- A hosted coder pool in the scheduler.

The review also considered loosening `ci_fix`'s file boundary, and we did not do it. D4
is the chosen way to handle out-of-diff failures.

## Testing

`ops/test-task-gates.sh` is the seam, and each change that runs shell belongs there:

- **D1:** a real-git section like the existing `open_pr_prep` one. Make a bare remote,
  make a `--single-branch` clone, run `open_pr`'s push and refspec widening, then run
  `deliver`'s `git push --force-with-lease` with a new local commit. Assert that the
  remote moved. Without the widening, the same test must show `stale info`, which proves
  the test can see the failure.
- **D2:** `watch_checks` with a `gh` stub that answers "failed" and then "passed". Assert
  exactly one `gh run rerun`, and `checks_ok=true`. Also cover the two guards: less than
  30 minutes left, and more than 20 minutes in the node. Each must skip the rerun.
- **D5:** `autofix` with no `fix.sh` (no-op, exit 0), and with a `fix.sh` that exits 1
  (still exit 0, output printed).
- **D6:** `next_task` against a 10-entry `tasks.json`. Assert that the ninth call reports
  `tasks_done=true` and that `remainder.json` holds entries 9 and 10. Also test a queue
  that grows past the budget between calls, the way the extra-review loop grows it.

D3 is stylesheet-only. Check it with `fabro preflight` (which names the resolved model)
and by reading both stylesheets for the `.ci-fix` rule. `fabro validate` checks neither
coverage nor the catalog. D4 is tested where it lands: by `build-images.sh`'s gate and by
one lawncare-saas run.

`fabro validate`'s `Backlog` baseline in `AGENTS.md` changes with D5 and D6 (new nodes and
edges). Update it in the same commit.

## How we will know

Re-run the 2026-09-24 measurement after 30 scheduler-dispatched runs, using the same
sources: `GET /runs/{id}/stages`, the scheduler's `/api/history` and `gh pr view`. The
numbers that should move:

- PRs squash-merged by the workflow: from 4 of 34.
- Actions runs per PR branch: from 78 on #1247.
- `ci_fix_t1` timeouts: from 3 of 12.
- `validate` failures in writers-app: from 31 of 154.
- Runs over 8 tasks: from 6 of 38. By construction this should become 0.
- Lease hours per run: from 5.0.

## Consequences

- The AGENTS.md invariants table needs one new row and three edits. New: "`backlog` does
  not push checkpoints; every push in the merge phase is explicit and needs the widened
  refspec". Edits: the rationale for `--delete-branch=false`, the `.github/workflows/`
  row's description of checkpoint-push warnings, and the list of classes that need rules
  in both stylesheets (add `ci-fix`).
- `.fabro/fix.sh` is a new per-repository contract, beside `setup.sh` and `ci.sh`, and
  `ops/README.md`'s profile-image section should name it.
- `CONTEXT.md` gains **Task budget**, **Remainder issue** and **Priority label**.
- The scheduler changes in two places: a `priority` tier in `rank()`, and a remainder
  promoter in the inventory poll. Both deploy with `docker compose up -d --build
  scheduler`. The implementation plan is `docs/merge-rate/`.
