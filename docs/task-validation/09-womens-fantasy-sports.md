# 09. Declare Task validation in womens-fantasy-sports

## Outcome
womens-fantasy-sports' `.fabro/test.toml` declares Task validation. Its `backlog` tasks
then run scoped, and the trial (10) starts on the day this merges.

## Why
D14. It is the repository with the most work queued and the slowest suite: Alembic and the
seed in preparation, pytest under coverage, and the frontend build.

## Change
In `andrewthetechie/womens-fantasy-sports`, on a branch, as one PR. It trips
`config_changed`, so the operator merges it by hand.

1. `[task]`:
   - `skip = ["frontend/tests/**", "frontend/tests-deployed/**"]`: Playwright specs that vitest excludes (`frontend/vitest.config.ts`).
   - `full = ["backend/app/alembic/**", "backend/app/seed/**", "backend/app/initial_data.py", "content/**", "backend/scripts/**"]`: preparation inputs and the scripts `ci.sh` calls. A change to what the database holds cannot be scoped by imports.
2. `[targets.backend]`: add `tests = ["backend/tests/**"]`. Its `cwd` is already `backend`.
3. `[targets.frontend]`: set `cwd = "frontend"`, `run = "bun run test:unit --"` (the workspace
   script is `vitest run`), and `tests = ["frontend/src/**"]`. Check that
   `fabro-test frontend src/<some>.test.tsx` still passes before you commit, and update
   `about`.
4. `[checks.prek]`: `run = "uv run prek run --from-ref \"$FABRO_TASK_BASE\" --to-ref HEAD --show-diff-on-failure"`,
   `watch = ["**"]`, `files = false`, `timeout = 300`. Read `.pre-commit-config.yaml` first: if
   prek already runs the frontend's eslint and tsc, no other check is needed.
5. `[checks.typecheck]`, only if prek does not cover it: `cwd = "frontend"`,
   `run = "bunx tsc --noEmit -p ."` (or the repo's own typecheck script),
   `watch = ["frontend/src/**"]`, `files = false`.
6. Leave `ci.sh` unchanged. The build and `coverage report` stay in Final validation.
7. Check it offline in a 0.5.0 image container against the branch:
   - `fabro-io test-env --check` passes with zero ownership warnings
   - `fabro-io task-plan --base origin/main` on three throwaway edits gives the expected plan:
     - a backend route: scoped, backend tests
     - a frontend component: scoped, its `.test.tsx`
     - `backend/uv.lock`: full

   Paste the three plans into the PR body.

## Acceptance
- The PR is merged. Record the merge time (UTC) in `10-trial.md` as the switch time.
- The first `backlog` run after the merge shows `task_validation_mode: scoped` on a
  `validate` visit, `task_check` called by the coder, and a green Final validation, or a red
  one routed by #35 as designed.
- Within 24 hours, check the scorecard's unchecked-files count. A large count means `watch`
  globs are missing: fix them in a follow-up PR before the trial window gets far.

## Depends on
07, 08 (pushed), and 01 (deployed and re-summarized).
