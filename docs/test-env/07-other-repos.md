# 07. lawncare-saas, jelly-swipe, writers-app

## Outcome
The other three target repositories declare their targets, and lawncare-saas declares its
Test environment.

## Change
One hand-written PR per repository, in this order, each after the previous one's first
`validate` has passed:
1. **lawncare-saas:**
   - `[env]`: `CI = "1"`, `DATABASE_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`, `JWKS_URL`, `CRON_SECRET` (the values in today's `ci.sh`).
   - `[prepare]`: `run = ["fabro-pg-ensure"]`, `inputs = []`.
   - `[targets.backend]`: `run = "uv run pytest -q"`, `value_flags = ["-k"]`, `flags = ["-x", "-v"]`.
   - `[targets.frontend]` if its typecheck has a narrow form.
   - `ci.sh` loses its block and exports, exactly as in task 06.
2. **jelly-swipe:** no `[prepare]`.
   - `[targets.backend]`: `run = "uv run pytest -q"`.
   - `[targets.frontend]`: `cwd = "frontend"`, `run = "npm test --"`.
   - `ci.sh` gains the `test_env` line for symmetry. It prints nothing.
3. **writers-app:** no `[prepare]`.
   - `[targets.rust]`: `run = "cargo test --all-features"`.
   - `[targets.frontend]`: `run = "npm run test:unit --"`.
   - `ci.sh` gains the `test_env` line.

## Acceptance
For each: `fabro-io test-env --check` passes in its image, `./.fabro/setup.sh &&
./.fabro/ci.sh` passes there, one `fabro-test` call per target passes, and the first
`validate` after merge passes.

## Depends on
04. Task 06 merged and verified first.
