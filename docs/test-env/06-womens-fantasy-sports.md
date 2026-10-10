# 06. womens-fantasy-sports: `test.toml` and `ci.sh`

## Outcome
womens-fantasy-sports declares its Test environment, and its `ci.sh` reads it. A coder can
run `run_tests backend tests/api/routes/test_pwhl.py` against a migrated, seeded Postgres.

## Why
It is the observed case, and it has the most preparation: the role encoding, the seed, and
the serial-run truncation trap.

## Change
A hand-written PR in womens-fantasy-sports (never a factory run: D7 would block its
auto-merge anyway):
1. `.fabro/node24.sh`: the Node v24.11.0 download block moved out of `ci.sh` unchanged.
2. `.fabro/test.toml`:
   - `[env]`: the five `POSTGRES_*` values and `PGCLIENTENCODING = "UTF8"`.
   - `path`: the Node 24 bin directory.
   - `[prepare]`:
     - `run`: `fabro-pg-ensure`, the `ALTER ROLE … UTF8` psql line, `sh .fabro/node24.sh`, `cd backend && uv run bash scripts/prestart.sh`.
     - `inputs`: `backend/app/alembic/**`, `backend/app/seed/**`, `backend/app/initial_data.py`, `content/**`.
   - `[targets.backend]`: `cwd = "backend"`, `run = "uv run pytest -n 1 -q"`. Never `-n0`: the session `db` fixture would truncate the seeded template. `flags = ["-x", "-v", "--lf"]`, `value_flags = ["-k"]`.
   - `[targets.frontend]`: `run = "bun run --filter frontend test:unit --"`.
3. `.fabro/ci.sh`: replace the `fabro-pg-ensure` block, the exports and the Node block with
   `test_env=$(fabro-io test-env); eval "$test_env"`. Keep `prek`, the frontend build, the
   `PYTEST_XDIST_AUTO_NUM_WORKERS` cap, `tests-start.sh` and `coverage report`. `prestart.sh`
   now runs inside preparation, so remove its separate call. Delete the "outside that
   image" comments.

## Acceptance
- In the `fabro-python-node` image on the host, `fabro-io test-env --check` passes.
  `./.fabro/setup.sh && ./.fabro/ci.sh` passes. `fabro-test backend tests/seed/test_load.py`
  passes, then passes again without preparing.
- After merge, the first `backlog` run's `validate` passes, and its coder's test calls go
  through `run_tests` with no M-env hit.

## Depends on
04.
