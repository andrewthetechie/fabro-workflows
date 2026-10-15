# Handoff: finish verifying the Task validation series (2026-10-14)

The implementation is done: tasks 01–09 of `docs/task-validation/` are committed, deployed
where they needed a deploy, and pushed to `main`. Three acceptance checks are live-run
checks that take 30 minutes to 4 hours to reach, so they are handed to whoever next has the
runs in front of them. Everything below is the exact command, the run id to point it at,
and what "pass" looks like.

Start from a clean checkout at `origin/main` (the series' own rule: pull before you start).
`docs/task-validation/00-overview-and-contracts.md` is the contract; `02-evidence.md` holds
the outputs that were already checked.

## What is already verified and where

| Task | Commit(s) | Verified |
|---|---|---|
| 01 scorecard rows + `resummarize` | `eb532a0` | Deployed; history re-summarized at 20:16Z for 433 runs. `make check`. `ops/fabro-scorecard.sh --repo andrewthetechie/womens-fantasy-sports --from 2026-10-07 --to 2026-10-14` printed 220 runs / 220 summarized, `Task validation: visits by mode ... legacy 421 (5.8/6.8 min)`, `Final validation: reached / first route | 211 / pr 54 (red 74%)`. `02-evidence.md`, `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`. |
| 02 `fabro-code tests --json` + `list-tests` | `72b355c` | Container checks in `02-evidence.md`. |
| 03 `test.toml` Task validation keys | `aa2c94d` | `cargo test`; all four repos' live `test.toml` parse (exit 0). |
| 04 `fabro-io task-plan` | `04767db` | Container acceptance in `02-evidence.md`. |
| 05 `task-check`, `task_check`, `fabro-test --task`, 0.5.0 | `2eba37b` | Container acceptance in `02-evidence.md` (with the fresh-checkout caching note). |
| 06 images 0.5.0 + `min_binary` | `54095ad`, `f5ebaa5` | `make deploy-images`, `make verify-host`, `make check-host`; warning counts in `02-evidence.md`. |
| 07 Task validation node + merge skip | `786ccea` | `make check`, `make check-host` (baselines unchanged), `ops/test-task-gates.sh` 1864 checks. |
| 08 agents, guide, M-run, invariants, records | `23c3deb` | `make check` (guide 2109 B < 3000), `ops/tests` 80 tests. |
| 09 womens-fantasy-sports `test.toml` | PR [#2088](https://github.com/andrewthetechie/womens-fantasy-sports/pull/2088) | Branch `task-validation` at `ce6ee55` on top of `c2a4be0`, pushed by hand. Offline checks (zero ownership warnings, `setup.sh` exit 0, `fabro-test frontend ...` 12 passed, the three plans) are in the PR body. |

Deploys run: `make deploy-scheduler` (`eb532a0`) + `resummarize`, `make deploy-images`
(`2eba37b`). Both are dated in `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`.

## Live check 1 — task 07: the gate runs and routes (operational)

The workflow change is live on the push at **2026-10-14T22:22:17Z** (`786ccea`). Any
`backlog` run dispatched after that time has the new graph. The first candidates recorded at
handoff:

- `01M4Y9BS3SFFNHD0ES12TG8FB` — womens-fantasy-sports, dispatched 22:43:39Z
- `01M4Y9K6QC0VDB9W6ACX3B1R9K` — lawncare-saas, dispatched 22:47:42Z

To find the first one for any repository:

```sh
ssh andrew@10.10.0.32
cd ~/fabro
docker compose exec -T scheduler python -c '
import sqlite3
c = sqlite3.connect("/data/scheduler.db"); c.row_factory = sqlite3.Row
for r in c.execute("select run_id, repo, dispatched_at from run_history where dispatched_at > ? order by dispatched_at", ("2026-10-14T22:22:17",)):
    print(tuple(r))'
```

Then read the `validate` visit and the edge it took:

```sh
docker compose exec -T fabro fabro events <RUN> --json \
  | jq -c 'select(.node_id=="validate") | {event, updates: .properties.context_updates}'
docker compose exec -T fabro fabro events <RUN> --json \
  | jq -c 'select(.event=="edge.selected" and .properties.from_node=="validate") | {to: .properties.to_node, reason: .properties.reason}'
```

**Pass:** the `stage.completed` carries
`{"task_validation_mode":"full","task_validation_reason":"no_config","task_validation_ok":true,...}`
(no repository declares task keys yet, so every visit is `full`), and the selected edge is
`validate -> review`. **Fail:** `context_updates` is only `command.output` (the old node), or
no `task_validation_*` key is present, or the edge is not `review` on a green visit — stop and
report with the event lines.

A run dispatched before the push does not count. `01M4Y7WZF8EXBSPVA6F8A9ESJ9` (22:18:05Z) is
one of those: its `validate` published only `command.output`, which is the old graph working.

**The merge-phase skip** needs a run that reaches `review_merge` with `review_fix` making no
change, i.e. `/tmp/fabro/final_validated_tree` equals a clean HEAD when its `validate` starts.
Read the merge phase's output for any run that reaches it:

```sh
docker compose exec -T fabro fabro events <RUN> -p | grep -E 'review_merge\.validate|validate: skipped'
```

**Pass:** the line `validate: skipped, this tree passed final validation in open_pr_prep (<tree>)`
and the merge-phase `validate` publishing `ci_ok:true`. If no run reaches it with an unchanged
tree, the next such run is the evidence; do not force it with a hand fire unless the operator
asks.

## Live check 2 — task 09: the switch (after the operator merges PR #2088)

The operator merges `andrewthetechie/womens-fantasy-sports#2088` by hand (it trips
`config_changed`). Immediately after the merge:

1. **Record the merge time (UTC)** at the top of `docs/task-validation/10-trial.md` as the
   switch time, replacing the pending line. That timestamp is `D` for the measurement below
   (`gh pr view 2088 --repo andrewthetechie/womens-fantasy-sports --json mergedAt`).
2. The first `backlog` run dispatched after `D` and in that repository must show, in
   `fabro events <run> --json`:
   - a `validate` visit with `task_validation_mode: "scoped"` (not `full`),
   - a `task_check` tool call by the `coder` stage, and
   - a green Final validation (`open_pr_prep` publishes `final_route: "pr"`), or a red one
     routed by #35 exactly as designed.
3. Within 24 hours, read the scorecard's `Task validation: untested files` for that
   repository (`unchecked` is always 0 there: `prek` watches `**`; 00 A3). A large count
   means changed files select no test: find which in the `validate` logs and decide whether
   a `tests` or `full` glob is missing, in a follow-up PR before the trial window gets far.

## Task 10 — the trial measurement

`D` is the merge time recorded above. The baseline window is the 7 days before the switch,
but never earlier than **2026-10-14** (00 A6: no run before #35 recorded a `final_route`, so
an earlier window has no red final rate); the after window is the 7 days from it. Both commands need the scorecard rows for those windows
to have been built (C9), which they are once the collector has summarized the runs (it does
2 runs / 30 s, newest first).

```sh
cd <a checkout of this repo at origin/main>

# Baseline: max(D-7d, 2026-10-14) .. D-1d  (dates are inclusive)
./ops/fabro-scorecard.sh --repo andrewthetechie/womens-fantasy-sports \
  --from <max(D-7d, 2026-10-14)> --to <D-1d>

# After: D .. D+6d
./ops/fabro-scorecard.sh --repo andrewthetechie/womens-fantasy-sports \
  --from <D> --to <D+6d>
```

Run the after command **seven days after `D`** (on `D+6d` inclusive, so the morning of
`D+7d`). If either side has fewer than 15 runs, extend the after window one day at a time and
say so in the result. Save both outputs to
`docs/task-validation/result-<date>.txt` with the three targets from C10:

- (a) the median per-visit `validate` minutes after the switch is at most 50% of the
  baseline median (the `Task validation: visits by mode` row's `all` p50 over the window);
- (b) the red final rate after the switch is at most the baseline rate + 5 points (the
  `Final validation: verdicts / first route` row, which counts only runs that recorded a
  route);
- (c) box-hours per merged PR after the switch is no higher than the baseline (the
  `Box-hours per merged PR` row): `task_check` moves test time into the coder stage.

Also record, from `00-overview-and-contracts.md` C10: the after window split by mode
(`scoped` against `full`/`legacy`), the full visits by reason class (a high
`too_many_tests` share means `max_tests` is the next lever), the untested and unchecked
counts, `task_validation_prepare_s` per scoped visit, and the coder and rework stage minutes
before and after.
On a pass, mark ADR 0025 `accepted (<date>)`, update the `docs/README.md` row and the series
status, and comment the result on #26. On a fail, open the rollback PR (delete the keys from
the repository's `test.toml`) and record the cause; leave ADR 0025 `proposed`.

## Known discrepancies and unverified items (do not silently "fix" these)

- **`clippy --all-targets -- -D warnings` is not clean** under the Mac's rustc 1.98/1.99,
  because of pre-existing lints in `serve.rs`, `gitsafe.rs`, `gitguard.rs`, `code.rs` and
  `tests/common/mod.rs`. Task 03's and 05's new code adds **no** clippy warning (verified by
  reading the per-file lint list). The image build runs `cargo test --locked`, not clippy.
  Task 03's acceptance says "clippy is clean"; it was not before the change either. Decide
  with the operator whether to clean the pre-existing lints in a separate commit.
- **Task 05's "a second call does not prepare again" is off by one on a fresh checkout.**
  The preparation imports `backend/app/seed/**` and `backend/app/alembic/**`, so Python
  writes `__pycache__/*.pyc` inside `prepare.inputs`; the stamp records the fingerprint from
  before the steps, so the call after a first-ever prepare re-prepares once. From the third
  call it is cached. This is ADR 0018's fingerprint, not task 05's code; `02-evidence.md` has
  the outputs. A candidate fix (out of this series' scope) is to exclude `__pycache__` in
  `input_hashes`, or set `PYTHONDONTWRITEBYTECODE` in the repository's `[env]`.
- **`backend/uv.lock` does not exist** in womens-fantasy-sports, so task 04's and 09's
  literal example could not be used. The built-in `**/uv.lock` glob was demonstrated with the
  root `uv.lock` (`full_glob:uv.lock`) and `bun.lock` (`full_glob:bun.lock`).
- **The image build's `test-env --check` cannot see ownership warnings**: its verify
  container runs as root over an andrew-owned read-only mount, so `git ls-files` sees no
  tracked files and the log is empty. The parse check still runs. The warning counts in
  `02-evidence.md` were taken by running the check with `git safe.directory` set.
- **`task_validation_selected`** is defined as the number of test files passed as target
  arguments (the plan's `targets[].args`); a `whole` target contributes 0. Nothing reads it
  for the trial; it is on the summary line and the scorecard ignores it.
- **C7's `reason` wording.** The overview's C7 step 6 says every key is written "on both
  branches, `reason` empty and the counts 0 on the full path". Read as "`reason` is empty on
  the scoped path" (where the plan's reason is `""`) and "the counts are 0 on the full path";
  the full path **does** publish its fallback reason, because C9's scorecard and task 10's
  diagnosis read it. If that reading is wrong, the fix is one jq program in the node.
- **Not verified live:** the merge-phase skip (live check 1, part 2) and the task 09 post-merge
  behaviour (live check 2). Everything else is covered by the offline suites or the container
  acceptance already recorded.

## If something routes unexpectedly

Stop and report with `fabro events <run> -p` output rather than pushing a speculative fix to
`main`: a `main` push is live on the next fire, and the series' rules say an unexpected route
is a stop point.
