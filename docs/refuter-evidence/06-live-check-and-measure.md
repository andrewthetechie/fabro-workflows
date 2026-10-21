# 06: live check and measure (operator, after push + `make deploy-scheduler`)

Task 05 ends this series' local work. Task 06 is what happens **after** the reviewer pushes
tasks 01-05 to `main` and runs `make deploy-scheduler`. This file describes the steps; it is
carried out by the operator. Do not run them from this working session.

## 1. Live check, first 5 Refuter visits after deploy

For each of the first 5 Refuter visits after the deploy:

- `fabro events <run> -p` shows `refute_prep` and the `validation` input served;
- every command criterion is `met` citing a `.fabro/ci.sh` line, or `unverifiable` with a
  stated reason;
- **no behavioural criterion** is credited to CI.

If any behavioural criterion is credited to CI, revert the push.

## 2. Measurement at deploy + 7 days

Use `./ops/fabro-scorecard.sh` for:

- the new unverifiable-only row per day;
- fast-lane held-by-Refuter per day;
- the CI-credited count against operator fixes and reverts.

Compare against the done-when criteria and record the result in the deployment log and on
issue #40. Stream summaries; never export raw events.

## 3. Out-of-scope classes

Check whether the three out-of-scope classes (diagnosis comment, "tests pass unchanged",
lint probe) recurred (D1).

## Result of step 1 (2026-10-16): passed, with one rule addition

The first 5 Refuter visits after the deploy were all `backlog` runs. Every visit was served
its `validation` input. That validation was found on both paths: the merge-phase
`validate` passing, and the skip after final validation.

| Run | PR | First verdict | CI-credited | Notes |
|---|---|---|---|---|
| `01M50RDCF2` | lawncare-saas#2847 | unverifiable-only | 1 | **False hold.** "`uv run alembic check` reports no drift" was marked `unverifiable` because `ci.sh` does not name `alembic`. But `ci.sh:15` runs `pytest tests/`, and `tests/test_migration_drift.py` runs `uv run alembic check`. Hand-merged. |
| `01M50R92GY` | womens-fantasy-sports#2125 | fail (not_met + defect), then pass after `refute_fix` | 4 | pytest, `check-docs.sh` and `prek` were credited through `ci.sh:52` and `:32`, the script and the hook. |
| `01M50RD9BX` | lawncare-saas#2848 | fail (not_met + defect) | 0 | Real findings. |
| `01M5193WCA` | womens-fantasy-sports#2126 | unverifiable-only | 0 | Correct hold. wfs#2113 puts a deployed-suite run on dev under Acceptance criteria, not under `## Post-deploy checks`. |
| `01M51B03W4` | writers-app#1046 | pass | 1 | "All existing tests pass unmodified" was credited to `cargo test` (the D1 "tests pass unchanged" class came back and was credited). It cites `ci.sh:12` for a line that is `:13`, an off-by-one error. |

No behavioural criterion was credited to CI, so 7080221 stays. The false hold on
lawncare-saas#2847 is the #40 symptom: an `alembic check` criterion appears in about 20
lawncare-saas issues. The rule (D7, the ADR 0013 addendum) now also counts a test that
`ci.sh` runs when that test runs the command itself, and the Refuter must search for one
before it marks a command criterion `unverifiable`. That change makes the window start
on 2026-10-17: measure with `--from 2026-10-17 --to 2026-10-23`.

## Result of steps 2 and 3 (2026-10-21): the window ended early

The operator ended the window at 2026-10-21 19:25Z, two days early, so that the `ralph`
trial's precondition P2 (ADR 0029) does not wait for it. The window covers runs dispatched
from 2026-10-17 to 2026-10-21 19:25Z: 279 runs and 101 Refuter visits, all summarized. The
numbers are in `result-2026-10-21.txt`.

- **C1:** `unverifiable`-only first verdicts per day were 0, 7, 8, 2 and 1. 10-18 and 10-19
  are over 5. **C2:** fast-lane PRs held by the Refuter were 0, 5, 7, 0 and 0, with no fast
  run dispatched on 10-20 or 10-21. **C3** holds: there were no reverts, and no operator fix
  traces to a criterion credited to CI.
- No command criterion that `ci.sh` runs was held, against 17 holds before the fix. The 3
  command holds left are correct under D7.
- 13 of the 18 holds are process criteria from one writers-app tech-debt template ("run
  `eslint --prune-suppressions` and commit", "load the ms-rust guidelines first"). This is
  step 3's lint-probe class recurring. It moves to #52. The other two D1 classes did not
  recur.
- D9 did not fire (2 days over 5, not 3). The model stays gpt-6-luna.
