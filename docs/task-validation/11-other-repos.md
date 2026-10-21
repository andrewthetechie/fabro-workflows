# 11. lawncare-saas, jelly-swipe, writers-app

## Outcome
The other three repositories declare Task validation. Each passes a 3-day check against its
own 7-day baseline, with the same two thresholds as C10.

## Change
One PR per repository, in this order, each merged by the operator. Each is checked offline
as task 09 step 7 describes, with three throwaway edits and the plans pasted into the PR.
Start the next repository only after the previous one's 3-day check passes.

1. **lawncare-saas:**
   - `backend`: `tests = ["tests/**"]`. Note: the sources are under `src/backend`, and the tests import `backend.*`.
   - `frontend`: `tests = ["frontend/src/**"]`. `frontend-admin`: `tests = ["frontend-admin/src/**"]`.
   - checks:
     - `ruff`: `uv run ruff check`, `files = true`, watch `src/**/*.py` and `tests/**/*.py`
     - `ruff-format`: `uv run ruff format --check`, `files = true`, same watch
     - `typecheck`: `npm run typecheck`, watch `frontend/**` and `frontend-admin/**`
   - `full`: `alembic/**` or wherever its migrations live (read the repository), plus `scripts/check-api-stale.sh` and the generated client's source spec. Check whether API staleness should also be a check that watches the routes.
2. **jelly-swipe:**
   - `backend`: `tests = ["tests/**"]`. `frontend`: `tests = ["frontend/src/**"]`. Confirm where the frontend tests live.
   - checks: `typecheck` and `lint`, with cwd `frontend`, from its `npm run` scripts.
   - Note that `ci.sh` runs `npm ci` itself. The scoped path never does, which is correct, because a lockfile change is a built-in `full` glob.
3. **writers-app:**
   - `rust`: `watch = ["src-tauri/**"]` (whole target; inline `#[cfg(test)]` modules are invisible to the index). Its 39 `src-tauri/tests/*.rs` integration tests are owned by that `watch` (00 A2), so they need no `skip`. `frontend`: `tests = ["src/**"]`.
   - Time a large selection for each `tests` target before choosing its `max_tests` and `timeout` (00 A1), as task 09 did.
   - checks:
     - `typecheck`: `npm run typecheck`
     - `lint`: `npm run lint`
     - `fmt`: `cargo fmt --all -- --check`, watch `src-tauri/**/*.rs`
     - `clippy`: `cargo clippy --all-targets --all-features -- -D warnings`, watch `src-tauri/**`
   - `cargo audit` stays in Final validation.

## Acceptance
- For each repository, `result-<date>.txt` gains a section: baseline against 3 days after, with the same two thresholds.
- On a fail, roll that repository back by deleting its keys, and record why.
- When all three are done, update the series status and the `docs/README.md` row, and close #26.

## Depends on
A pass in 10.

## Progress (2026-10-21): three PRs open, none merged

All three PRs were opened on the same day, at the operator's request, rather than one after
another. Each repository's 3-day check still runs against its own baseline, and the operator
decides the merge order.

| Repository | PR | Offline gate (`task-check --gate`) | Findings fixed in the PR |
|---|---|---|---|
| lawncare-saas | [#2974](https://github.com/andrewthetechie/lawncare-saas/pull/2974) | 37 s green (backend 14 files 22 s) | `max_tests = 40`, `timeout = 300`: a `db.py` edit selects 129 files, which ran past 120 s, while `ci.sh`'s `-n auto` runs everything in 108 s. `test_cron_steps.py`'s router-isolation guard failed in any small selection (it checked `sys.modules` against a collection-time snapshot); it now imports in a subprocess. |
| jelly-swipe | [#469](https://github.com/andrewthetechie/jelly-swipe/pull/469) | 15 s green | `setup.sh` now runs the frontend's `npm ci`. A fresh sandbox had no `frontend/node_modules` until `ci.sh`, so every scoped frontend check failed with `tsc: not found`, and so does today's `run_tests frontend`. The frontend tests sit in `frontend/` itself: `tests = ["frontend/*.test.ts", "frontend/*.test.tsx", "frontend/test/**"]`. |
| writers-app | [#1158](https://github.com/andrewthetechie/writers-app/pull/1158) | 47 s frontend-only, 94 s with Rust | Measured at 4 CPU / 8 GB. The frontend `tests` also owns `tests/eslint/**` and `scripts/**` (vitest's default include). No `max_tests` (largest selection 18 s). `lint` stays whole-repo because of `eslint-suppressions.json`. |

Each PR body has its plans and timings. Two gaps were found in both Python backends: a
router edit selects no test, because the API tests reach routers through the app and the
import index doesn't follow that. Final validation covers it.

Baselines, 7 days to 2026-10-21 (`ops/fabro-scorecard.sh --repo ... --from 2026-10-15 --to
2026-10-21`). The 3-day checks re-read the 7 days before each merge.

| Repository | Runs | `validate` p50 / p90 min | Red final rate | Box-h per merged PR |
|---|---|---|---|---|
| lawncare-saas | 76 | 8.10 / 8.55 | 1 of 60 (1.7%) | 2.87 |
| jelly-swipe | 1 | 2.05 / 2.10 | 0 of 1 | 1.1 |
| writers-app | 200 | 1.95 / 5.91 | 0 of 48 | 17.81 |

jelly-swipe ran once in that week, so its 3-day check needs more queued issues before it
can reach 15 runs a side.

Also on 2026-10-21: womens-fantasy-sports
[#2194](https://github.com/andrewthetechie/womens-fantasy-sports/pull/2194) adds
`.pre-commit-config.yaml` and `frontend/openapi-ts.config.ts` to `[task] full`. That closes
the trial's open item.
