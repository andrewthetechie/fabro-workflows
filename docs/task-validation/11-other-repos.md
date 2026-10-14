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
   - `rust`: `watch = ["src-tauri/**"]` (whole target; inline `#[cfg(test)]` modules are invisible to the index). `frontend`: `tests = ["src/**"]`.
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
