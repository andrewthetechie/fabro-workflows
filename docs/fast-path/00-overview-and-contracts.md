# Fast path and merge queue: overview and canonical contracts

**Spike 2026-10-11:** `spike-2026-10-11.txt` answers S1-S9; the corrections are marked *(spike)* below.

**Status:** tasks 01-09 implemented and pushed 2026-10-11 (`e0b2203`); `make check` and
`make check-host` pass (counts in `AGENTS.md`). Deployed the same day: `make deploy`, the four
`merge-queue-<repo>` rows (schedules still disabled), and the 05:07 retention cron. Task 10:
step 1 passed; step 2 (quiet sweep) passed, run `01M4PEFW4G6YVTVD3QSG4VM06X`, 4 s to the first
node and 8 s in total, so S6 needs no `light` environment; step 5 (hand-off) passed, run
`01M4PEJCY2C51EQT7BFSWAPP2S` on jelly-swipe#453, closed after. Step 4 (strict repository) passed on jelly-swipe: #454 -> PR #456 (run `01M4PFJP7BFA50A1022D7P8DVW`, 15.2 min, lease 15.3 min) merged by sweep `01M4PH696HFRZDPPKJFSGN20BB`; #455 -> PR #457 (run `01M4PGF0M471YCZV1VS2J311N4`, 12.2 min) went BEHIND, was rebased by `01M4PH897893XQC28P53XW45DK` (REBASE method, author kept, committer the token user) and merged by `01M4PHCWR236ZMRNHPQN1KW95G`. Steps 3a-e also hold on #454; step 3 proper did not complete: wfs#1834 (run `01M4PF5JV0FHXNV5TA0PC8TV3E`, coder 53.6m) hit a merge conflict in `open_pr_prep` after the operator's cluster PR #1918 fixed and closed the same issue mid-run (not a fast-lane fault; abandoned at the gate), and lawncare#2764 is still queued. In its place, quiet sweeps passed on lawncare-saas, womens-fantasy-sports and writers-app (`01M4PK7X49…`, `01M4PK816Z…`, `01M4PK84CP…`, 10-12 s each). **Step 6, live 2026-10-11T23:03:03Z:** the four merge-queue schedules are on, and the first batch is womens-fantasy-sports #1599 #1805 #1812 #1818 #1832 #1842 #1843 #1844 #1846 #1847 (unsafe type assertion, ten distinct files). Steps 7-9 are open,
and the host cap is 8 (restarted 21:44Z). ADR 0023 (the fast lane) and ADR 0024 (the merge queue) are
proposed. The series answers issue #23 with the operator's alternative: one label-selected
lane per issue instead of a batch lane.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. **Before you edit a `.fabro` file, a prompt or a `workflow.toml`, invoke the
`/fabro-workflow` skill** and read `docs/agents/invariants-graph.md`,
`docs/agents/invariants-backlog.md` and `docs/agents/invariants-merge.md`. Before you change
anything under `ops/`, read `docs/agents/invariants-host.md` and `docs/agents/deploying.md`.

## What this is

Two changes. Each one is useful alone, and together they make one path:

1. **The fast lane** (ADR 0023). A human labels a small issue `fast-path` beside `agent`.
   `backlog` then skips `decompose`, `improve`, `excerpts` and the extra review. It keeps the
   coder, the per-task review, the rework ladder, the shared merge phase and the Refuter.
   When `merge_gate` passes, the phase labels the PR `merge-on-green` and the run ends
   without waiting for CI. The coder box is then free for the next issue.
2. **The merge queue** (ADR 0024). A sixth package, `merge-queue`, runs every 10 minutes
   per repository. It has no agent. It merges each `merge-on-green` PR whose approved head
   is intact, whose checks are green and that GitHub says is mergeable. It rebases a
   `BEHIND` PR through GitHub's API and leaves it for a later tick. It reruns red CI once,
   and then gives the PR to a human.

```
issue [agent, fast-path]
  └─ backlog: claim → prep → route_lane ─fast─► next_task → coder → autofix → validate → review ─┐
                                                   ▲ (rework ladder on red validate / rejection)  │
                                                   └──────────────────────────────────────────────┘
       → open_pr_prep → open_pr → pr_handoff (merge_mode=queue) → review_merge[queue mode]
            standards → spec → review_fix → validate → hygiene → refute → deliver → merge_gate
            → report_queued: approval marker + label merge-on-green → run ends, box freed
PR [merge-on-green]
  └─ merge-queue-<repo>, every 10 min: prep (guard) → inventory → decide → act → summarize
       green + CLEAN → squash-merge │ BEHIND → API rebase │ red → rerun once │ else hand off
```

## The state on 2026-10-11

| Item | Value | Source |
|---|---|---|
| `max_concurrent_runs` | 4, read once at startup; a change needs a fabro restart, which fails every in-flight run | `ops/settings.toml.example`, `docs/agents/invariants-host.md` |
| Branch protection | jelly-swipe only: required check `test`, branch must be up to date. The other three are private on a free plan (`GET .../protection` returns 403) | `docs/dependabot/00-overview-and-contracts.md` (2026-10-10), ADR 0022 D26 |
| Repository merge settings | all four: `allow_squash_merge: true`, `allow_auto_merge: false`, `delete_branch_on_merge: true` | same |
| Packages | `backlog`, `pr-review`, `issue-triage`, `arch-review`, `dependabot`; `_shared/review-merge/` is imported by `backlog`, `pr-review` and `dependabot` | `AGENTS.md` |
| Baselines (fabro 0.362.0-nightly.0) | `Backlog (69 nodes, 163 edges)`, `PrReview (34, 73)`, `Dependabot (46, 101)` | `AGENTS.md` |

## Operator decisions (settled; do not change them)

| # | Decision | From |
|---|---|---|
| O1 | Issues for the fast path are chosen ahead of time and labelled. No agent selects the lane. | 2026-10-11 request |
| O2 | A fast-path issue is a one-commit change: no `decompose`, no `improve`. | same |
| O3 | No extra-review scope in the fast path. The PR still gets the merge phase's review and direct fixes. | same |
| O4 | The Refuter stays in the fast path. | same |
| O5 | The per-task `review` and its rework ladder stay in the fast path. | answer of 2026-10-11 |
| O6 | Fast-path PRs merge when green, without the run waiting for CI. A label on the PR marks it ready. | 2026-10-11 request |
| O7 | A separate workflow merges labelled PRs on a cadence of about 10 minutes. It merges when CI is green and the PR is mergeable, and it can rebase on `origin/main` and leave the PR for its next loop. | same |
| O8 | The merge sweeper is a **fabro workflow**. The plan works around fabro's limits, and raising `max_concurrent_runs` is acceptable. | answer of 2026-10-11 |
| O9 | Red CI on a queued PR: rerun the failed jobs once, then hand the PR to a human. | answer of 2026-10-11 |

## Drafted decisions (the operator may veto each one until the task named lands)

| # | Decision | ADR | Lands in |
|---|---|---|---|
| P1 | Lane label `fast-path`, PR label `merge-on-green`. | 0023 D1, 0024 D2 | 02, 03 |
| P2 | Fast-lane diff ceiling: 200 changed lines or 10 files, excluding the hygiene `exclude_paths`. Above it, and on the `[P] Accept partial` path, `merge_mode = watch`. | 0023 D6 | 03 |
| P3 | A fast task that reaches rework round 6 is demoted to the standard lane: `fast-path` off, `agent` back on, no PR. | 0023 D7 | 04 |
| P4 | One `merge-queue-<repo>` automation per repository, staggered `0-59/10`, `2-59/10`, `5-59/10`, `7-59/10`. | 0024 D1 | 09 |
| P5 | The queue merges only the approved head, or the head that its own recorded rebases reached from it. | 0024 D3 | 06 |
| P6 | At most 5 merges per tick per repository. At most one queue rebase waiting for CI per repository. | 0024 D4, D5 | 06, 07 |
| P7 | Hand off a PR whose head checks have been pending for 2 hours. | 0024 D4 | 06 |
| P8 | `max_concurrent_runs` goes from 4 to 8 (operator, 2026-10-11; the draft said 6). | 0024 D8 | 09 |
| P9 | Retention: prune `merge-queue` runs older than 7 days, daily. | 0024 D8 | 08 |
| P10 | The merge-queue switches are the existing ones: host `FABRO_AUTO_MERGE` plus the `auto_merge` token on `pr-review-<repo>`. Pausing the queue alone means disabling its schedules. | 0024 D3 | 05 |

## Verified facts (sources)

Checked on 2026-10-11 against HEAD `35135ee` and `context/fabro` at `5f82fb086`. The host runs
0.362.0-nightly.0, which is older than `context/fabro`, so task 01 re-checks every fabro fact
marked †.

- **`claim` already fetches the labels.** It writes `gh issue view --json
  state,number,title,body,labels` to `/tmp/fabro/issue.json` (`backlog/workflow.fabro`,
  `claim`). `route_lane` reads that file and makes no network call.
- **The coder runs without a dossier.** In `_io/manifest.json`, `coder`'s
  `task-context.md` and `task-code.md` inputs are `required: false`, with `absent` texts.
  `current_task.json` and `issue.json` are required, and `next_task` writes the first from
  `tasks.json`.
- **`next_task`'s unconditional edge goes to `improve`.** The edges out of it are
  `extra_prep` (`tasks_done=true`), `resolve_merge` (`rebase_failed=true`) and `improve`
  (unconditional). `resolve_merge_gate -> improve` has `merge_ok=true, weight=10`. A
  matching condition beats the unconditional edge (skill REFERENCE.md, *Transition cascade*).
- **`improve_gate` is the only place that counts `tasks_coded`.** A fast task never passes
  it, so the 8-task budget never fires in the fast lane.
- **`rework_router`'s round-6 edge** goes to `drop_followup` only for exempt tasks
  (`context.round=6 && context.task_exempt=true`). Every other task falls to `rescue_brief`.
- **The merge phase's tail.** `deliver` pushes the head and goes to `merge_gate`, which goes
  to `watch_checks` (`merge_eligible=true`, weight 10) or `report_blocked`. `merge_gate`
  check 13b refuses a HEAD whose tree differs from the tree the hygiene counters read.
  `done` accepts only `merged | blocked | needs_human` (`review-merge.fabro`).
- **R7** (`ops/check-graph-invariants.py`) requires a `checkpoint_saved` hook for each node in
  `REPORT_NODES = ("report_merged", "report_blocked", "mark_needs_human")` in each importing
  graph, except the pairs in `R7_EXCEPTIONS`.
- **`backlog` and `pr-review` route `review_merge -> exit`.** `dependabot` routes it to
  `after_merge`, which reads `/tmp/fabro/review_merge_outcome`. Neither of them writes
  `merge_mode`, so neither ever sees `queued`.
- **The switches.** `pr_handoff` reads the `pr-review-<repo>` row's `auto_merge=` token
  through `$FABRO_API_URL/automations` (absent means on. Anything else, or an unreachable
  API, means off.). `FABRO_AUTO_MERGE` arrives as `{{ vars.FABRO_AUTO_MERGE }}`.
  `dependabot/workflow.toml` passes `FABRO_API_TOKEN`, `FABRO_API_URL` and
  `FABRO_AUTO_MERGE` in `[run.environment.env]`.
- **`dep_merge` is the model for a deterministic merge**: re-read state and head, wait for
  `mergeable` to leave `UNKNOWN`, `gh pr merge --squash --delete-branch=false
  --match-head-commit`, and retry "Base branch was modified" 3 times.
- **Schedule triggers have no overlap policy (confirmed at `a192bce`, spike S8).** `fire_scheduled_automation_run` creates a
  run for every due trigger, and the run then queues at the cap
  (`lib/apps/fabro-server/src/server/automation_scheduler.rs`).
- **`fabro system prune`** (confirmed on 0.362, spike S5; `--workflow` is a substring match) takes `--workflow`, `--older-than` and `--yes`, and is a dry run
  without `--yes` (`lib/apps/fabro-cli/src/commands/system/prune.rs`, `args.rs`
  `RunsPruneArgs`). The server has `DELETE /runs/{id}`.
- **`GET /runs` paging** uses `page[limit]` (clamped at 100) and `page[offset]`, filters by
  repeated `status=`, and a run row carries `workflow.slug`, `timestamps.created_at` and
  `lifecycle.status.kind` (`ops/scheduler/src/fabro_scheduler/fabro.py`,
  `list_active_runs`, and `ops/fabro-monitor.sh`, C3/C4 notes).
- **`fabro-monitor.sh` reads one page of 20 runs** for C3 (idle) and C4 (stuck). Its comment
  accepts the gap at "~24 runs/day". The merge queue adds 576 a day.
- **`GET /runs` ignores `workflow=`** and a run created from an automation carries `.automation.id` (scheduler-fired `backlog` runs carry null), so the guard filters client-side on `.automation.id` *(spike S4)*.
- **The run id** is the last path segment of the run branch: `file_remainder` takes
  `basename "$(git rev-parse --abbrev-ref HEAD)"`. This needs `[run.run_branch] enabled =
  true`, which `dependabot` sets with `push = false`.
- **`apply_split`** copies the parent's labels to each child, minus a fixed list of
  queue-state and triage labels (`_shared/triage/triage.fabro`, `apply_split`, the `PL=`
  line). `fast-path` is not in that list today.
- **Discord.** `backlog/scripts/discord-notify.sh` builds one message per kind. Changing it is
  a deploy (`make deploy-notify`). `dependabot-summary` is the model for a summary kind.

## Graph shapes

### `backlog`, the fast lane (task 03, task 04)

```
claim → prep → route_lane ─ lane=standard ─► decompose → … (unchanged)
                  │ lane=fast (writes tasks.json: one task)
                  ▼
              next_task ─ tasks_done=false && rebase_failed=false && lane=fast ─► coder
                  │ rebase_failed=true ─► resolve_merge → resolve_merge_gate ─ merge_ok && lane=fast ─► coder
                  │ tasks_done=true && lane=fast ─► open_pr_prep
              coder → autofix → prep_review → validate → review → review_gate → integrate → next_task
              rework_router ─ round=6 && lane=fast ─► demote_lane → exit
              open_pr_prep → open_pr → file_remainder → pr_handoff → review_merge → exit
```

Each new edge is exclusive with the edge it sits beside. The standard edges gain
`&& context.lane!=fast` where both would otherwise match (task 03 lists each one).

### `_shared/review-merge/`, queue mode (task 02)

```
rm_setup (publishes merge_mode; removes merge-on-green from the PR) → … → deliver → merge_gate
merge_gate ─ merge_eligible=true && merge_mode!=queue ─► watch_checks   (unchanged path)
merge_gate ─ merge_eligible=true && merge_mode=queue  ─► report_queued → done → exit
report_queued ─ (unconditional, on failure) ─► mark_needs_human
```

### `merge-queue` (tasks 05 to 08)

```
start → prep ─ sweep=run ─► inventory → decide → act → summarize → finish → exit
          │ sweep=skip (an older sweep is active, or a switch is off) ─────► summarize
every command node's unconditional edge ─► summarize
```

## Contracts

### C1. Labels

| Label | On | Added by | Removed by |
|---|---|---|---|
| `fast-path` | issue | a human, beside `agent` | `demote_lane` (P3). `apply_split` never copies it. |
| `merge-on-green` | PR | `report_queued` only | `rm_setup` (every merge-phase entry), a hand-off, and GitHub on merge (the label stays on a merged PR, and the queue only reads open PRs) |
| `ai-review-needs-human` | PR | the merge phase (today), a hand-off (new) | a human |

`report_queued` creates `merge-on-green` if it is missing:
`gh label create merge-on-green --color 0E8A16 --description 'Approved by the fabro merge phase; the merge queue merges it when CI is green (ADR 0024)' 2>/dev/null || true`.

### C2. The lane (`backlog`)

`route_lane` writes `/tmp/fabro/lane`, which holds `fast` or `standard`, and publishes
`context_updates: {lane: "fast" | "standard"}`. The value is `fast` if and only if
`/tmp/fabro/issue.json`'s `.labels[].name` contains `fast-path` exactly. In the fast lane it
also writes:

```json
[{"id": "fast-path", "title": "<issue title>", "body": "<issue body>", "files": [], "covers": [], "source": "fast-path"}]
```

to `/tmp/fabro/tasks.json`, and `0` to `/tmp/fabro/task_index`. An empty issue body becomes
`"Resolve issue #<N>: <title>"`, because `next_task` and the coder need a non-empty body.

### C3. The merge mode (shared phase entry contract)

`/tmp/fabro/merge_mode` holds `queue` or `watch`. If it is absent or holds anything else,
the mode is `watch`. The importing graph writes it before `review_merge`. `rm_setup` reads it
and publishes `context_updates.merge_mode`. Only `backlog`'s `pr_handoff` writes `queue`, and
only when all of these are true:

- `/tmp/fabro/lane` is `fast`.
- `/tmp/fabro/remainder_kind` is absent. That file means the `[P] Accept partial` path.
- The diff is inside the ceiling (C9).

The exit contract gains one value: `review_merge_outcome = merged | blocked | needs_human |
queued`.

### C4. The approval marker

`report_queued` posts the rendered review comment (render kind `queued`). Its **first line**
is:

```
<!-- fabro:merge-queue approved head=<40-hex sha> run=<run id> -->
```

`head` is the PR's remote `headRefOid` at that moment. `report_queued` asserts that
`git rev-parse <head>^{tree}` equals `git rev-parse HEAD^{tree}` (the reviewed tree, which
check 13b already tied to the hygiene tree). If it is not equal, the node fails. The comment
is posted **before** the label is added. If either call fails, the node writes both reason
files and exits 1, and its unconditional edge goes to `mark_needs_human`.

### C5. The rebase marker

After a successful `updatePullRequestBranch`, the sweeper posts a comment whose first line is:

```
<!-- fabro:merge-queue rebased from=<old head> to=<new head> run=<run id> -->
```

`to` is the PR's `headRefOid` **re-read after the mutation** (the mutation itself returns the OLD head; retry the read 3 times, 2 s apart, until it differs from `from`) *(spike S2)*. The comment is retried 3 times, 5 s
apart. If it still fails, the next tick hands the PR off (head moved without a record). That
is a safe failure.

### C6. The hand-off comment

```
<!-- fabro:merge-queue handoff head=<head> run=<run id> -->
**The merge queue stopped on this PR.** <reason>

<one line on what a human can do: merge by hand once green, or re-run review>
```

The reason starts with `retryable: ` for the mechanical classes (C8). The sweeper then runs
`gh pr edit N --remove-label merge-on-green --add-label ai-review-needs-human`.

### C7. Markers: who counts

A marker counts only when the comment's author login equals the login of the run's token
(`gh api user --jq .login`, read once in `prep`). Markers are read from the PR's comments,
oldest first. The **approval chain** is the newest approval marker, followed by the rebase
markers after it whose `from` equals the previous link's head. The chain's last head is the
**expected head**. Rule 4 of ADR 0024 D3 holds when the PR's `headRefOid` equals the
expected head.

### C8. The decision record (`/tmp/fabro/mq/plan.json`, written by `decide`)

`decide` is a pure `jq` program over `/tmp/fabro/mq/prs.json` (the inventory),
`/tmp/fabro/mq/env.json` and the current time. It makes no network call, so its tests are
fixtures alone.

```json
[{"number": 412, "head": "<sha>", "action": "merge", "reason": "", "retryable": false,
  "approved_at": "2026-10-12T10:00:00Z", "subject": "fix: …", "rerun_ids": []}]
```

- `action` is one of `merge`, `rebase`, `rerun`, `handoff` or `wait`.
- Entries are ordered by `approved_at` (the time of the chain's approval marker), oldest
  first. That is the merge order.
- `env.json` holds `{"login": "…", "base": "main", "now": <epoch>, "merge_cap": 5}`.

| Condition (first match wins) | `action` | `reason` (retryable classes start with `retryable: `) |
|---|---|---|
| No approval marker from the token's login | `handoff` | `the PR carries merge-on-green but no approval from the merge phase` |
| `headRefOid` ≠ the expected head | `handoff` | `the head moved after the merge phase approved it; the new commits were not reviewed` |
| Draft, `architecture`, `ai-review-needs-human`, no `agent-authored`, `CHANGES_REQUESTED`, or an unresolved thread | `handoff` | the rule's own sentence, as in `merge_gate` |
| Title is not a Conventional Commit, or the body block has no `Resolves #` | `handoff` | as in `merge_gate` checks 11 and 12 |
| `mergeStateStatus = DIRTY` | `handoff` | `retryable: the PR conflicts with <base>` |
| `mergeStateStatus = UNKNOWN` | `wait` | |
| `mergeStateStatus = BEHIND` | `rebase`, or `wait` when another rebase is in flight (P6) | |
| Checks red, and every failing check is an Actions run on attempt 1 | `rerun` | |
| Checks red otherwise | `handoff` | `retryable: CI failed after one rerun: <names>`, or `CI failed and the failing check cannot be rerun: <names>` |
| Checks pending, and the chain's last marker is over 2 hours old | `handoff` | `retryable: CI on the head did not settle in 2 hours` |
| Checks pending, or no checks yet | `wait` | |
| Checks green, and `CLEAN` or `HAS_HOOKS` | `merge` (the first `merge_cap`), then `wait` | |
| Checks green, and `BLOCKED` or `UNSTABLE` | `handoff` | `branch protection or a hidden check blocks the merge: <status>` |

Checks come from the head's `statusCheckRollup`. It can list one name twice (a `pull_request` and a `push` run), so `decide` groups by name and judges the newest run of each, and it can be `null` while `mergeStateStatus` is `UNSTABLE` (a PR with no reporting checks). A null rollup with `UNSTABLE` is a `handoff`: `a hidden or non-reporting check blocks the merge` *(spike S1, S3)*. A first `UNKNOWN` read is common; `inventory` re-reads it before `decide` runs.

A **rebase in flight** is a PR whose head equals the `to` of its newest rebase marker, and
whose checks are pending.

### C9. The fast-lane diff ceiling (`pr_handoff`)

```sh
git diff --numstat origin/<base>...HEAD
```

Skip each path that matches the default `exclude_paths` regexes of the hygiene counters
(`review-merge.fabro`, `hygiene`, the `default.json` block). Sum added plus deleted lines,
and count files. A binary file (`-` in numstat) counts as one file and 0 lines. The PR is
inside the ceiling when lines ≤ 200 and files ≤ 10. Over it, `pr_handoff` writes `watch`
and posts one PR comment:
`fast-path: this change is <L> lines in <F> files, over the fast-lane ceiling of 200 lines or 10 files, so this run merges it after CI instead of queueing it.`

### C10. The run results (`/tmp/fabro/mq/results.json`, written by `act`)

One entry per plan entry: `{"number": N, "action": "<planned>", "outcome":
"merged|rebased|reran|handed_off|waiting|failed|skipped_cap", "detail": "<≤300 bytes>"}`.
`summarize` reads it. A tick with no `merged`, `rebased`, `reran`, `handed_off` or `failed`
entry is **quiet**: its summary is empty, and Discord posts nothing.

## Rules (they become invariants in task 09)

1. **Only `report_queued` adds `merge-on-green`, and only after its marker comment posted.**
   A label without a marker is handed off. So a failure between the two leaves a PR that is
   safe and visible.
2. **Every merge-phase entry removes `merge-on-green`.** A second review of the same PR must
   approve again before the queue merges it.
3. **The queue merges only the expected head (C7).** Any other push makes the PR a hand-off.
4. **The queue never waits, never checks out a PR and never pushes.** It merges, rebases and
   reruns through the GitHub API, and it ends the run.
5. **Every queue action is idempotent across overlapping sweeps**: `--match-head-commit`,
   `expectedHeadOid`, "already merged" counted as merged, and the guard in `prep`.
6. **`merge-queue` runs are excluded** from every ops tool that reads a fixed number of runs.

## Tasks

| Task | What | Where | Depends on |
|---|---|---|---|
| 01 | Spike: confirm the GitHub and fabro facts this design assumes | host, GitHub | none |
| 02 | Merge phase queue mode, `report_queued`, hooks, the Discord `queued` kind | `_shared/review-merge/`, three `workflow.toml`, notify script, checker | 01 |
| 03 | `backlog` fast lane: `route_lane`, edges, `pr_handoff` mode and ceiling | `backlog/` | 02 |
| 04 | Demotion at rework round 6, and `apply_split` drops `fast-path` | `backlog/`, `_shared/triage/` | 03 |
| 05 | `merge-queue` package skeleton: `workflow.toml`, `prep` with guard and switches | `merge-queue/` | 01 |
| 06 | `inventory` and `decide` (C7, C8) | `merge-queue/` | 05 |
| 07 | `act`: merge, rebase, rerun, hand off (C5, C6, C10) | `merge-queue/` | 06 |
| 08 | `summarize`, Discord summary with de-duplication, retention, monitor and tool exclusions | `merge-queue/`, `ops/` | 07 |
| 09 | Host: cap 4 → 6, environments, automations, docs and invariants | `ops/`, `docs/`, `AGENTS.md` | 02-08 |
| 10 | Validate, go live, measure | host, GitHub | 09 |

Tasks 02-04 and 05-08 are two independent chains after 01. Each task's own file has its
outcome, the change, acceptance and tests.

## Open questions (none blocks task 01)

- **Answered (spike S2):** `REBASE` succeeded with a `workflow`-scoped token after `main` changed a `.github/workflows/` file. The HOST token's scopes are unverified, so task 07 handles a refusal as a `retryable:` hand-off, and the `MERGE` method (accepted by `merge_gate` check 6) is the fallback if it happens.
- Ordering of `fast-path` issues in the scheduler's queue is unchanged. A human can add
  `priority` to put them first.
- #31 (PR shepherd) can later re-review `retryable:` hand-offs from the queue. This series
  does not depend on it.
