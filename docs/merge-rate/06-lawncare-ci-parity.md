# lawncare-saas: `.fabro/ci.sh` runs the backend tests and the API-client staleness check

> **Work in the `andrewthetechie/lawncare-saas` repository**, not in fabro-workflows.
> Open a normal PR there against `main`. The last step (image verification) is for the
> operator on the fabro host.

## Tracer-Bullet Outcome
When a fabro `backlog` run validates a lawncare-saas change, it runs the same backend
pytest suite and API-client staleness check that GitHub CI runs. A change that breaks a
backend test, or leaves the generated API client stale, fails inside the run, where the
coder can still fix it. Today it fails only after the PR is open, where fabro's CI-fix
agent is not allowed to touch the files involved.

## User Story
As the operator, I want lawncare-saas's local validation to match its GitHub CI, so that
agent PRs arrive green instead of failing `test` or `api-staleness` and waiting for me.

## Description
Replace the body of `.fabro/ci.sh` in lawncare-saas. Keep its existing checks (`ruff`,
`typecheck`, `build`) and add:

- a PostgreSQL server, from the fabro sandbox image's `fabro-pg-ensure` helper
- the environment that `.github/workflows/test.yml`'s "Run tests" step sets, plus `CI=1`
- `uv run pytest -n auto tests/`
- `scripts/check-api-stale.sh`

## Context Pack
- Source decisions: fabro-workflows `docs/merge-rate/00-overview-and-contracts.md`
  decision 6. ADR 0011 D4.
- Evidence: three merge blocks came from checks that `ci.sh` never ran:
  - #2606: `tests/test_cron_tick.py` and `tests/test_isolated_writes.py` patched
    `backend.services.cron_steps.cross_account_session`, which the PR had removed.
  - #2607: `api-staleness` needed a regenerated `frontend/src/api/*.gen.ts`, and `test`
    failed too.
  - womens-fantasy-sports#1237: the same class of failure, in another repository.

  Across 33 fabro validation runs of lawncare-saas, none failed, because the failing
  check was never there.
- Repo facts (lawncare-saas):
  - `.fabro/ci.sh` today, verbatim:
    ```bash
    #!/usr/bin/env bash
    set -euo pipefail
    # Validation: backend ruff (offline; pytest needs Postgres -> excluded here) +
    # frontend typecheck & production build. Exit non-zero on failure.
    uv run ruff check src tests
    npm run typecheck
    npm run build
    ```
  - `.fabro/setup.sh` runs `uv sync --frozen` and `npm ci`. fabro runs `setup.sh` and
    then `ci.sh` from the repository root.
  - `.github/workflows/test.yml`, "Run tests" step, verbatim:
    ```yaml
          - name: Run tests
            env:
              DATABASE_URL: "postgresql+asyncpg://test:test@localhost:5432/test"
              JWT_ISSUER: "lawncare"
              JWT_AUDIENCE: "https://lawncare.example.com"
              JWKS_URL: "https://lawncare.example.com/.well-known/jwks.json"
              CRON_SECRET: "test-secret"
            run: uv run pytest -n auto tests/
    ```
  - `tests/conftest.py` detects CI with
    `return bool(os.environ.get("CI")) or bool(os.environ.get("GITHUB_ACTIONS"))`. When
    neither is set, it starts a **testcontainers** PostgreSQL, which needs a Docker
    socket that a fabro sandbox does not have. So `CI=1` is required.
  - `.github/workflows/frontend.yml`'s `api-staleness` job runs
    `scripts/check-api-stale.sh` from the repository root, after `uv sync --frozen` and
    `npm ci`. The script generates the OpenAPI schema from the backend (no server, no
    database), diffs it against the committed client, and **if the client is stale,
    regenerates it in place and exits 1**. Inside a fabro run, that means the
    regenerated files are already in the working tree when the rework agent starts,
    which is the fix.
  - The fabro sandbox image for this repository is `fabro-python-node:local`. It has
    `/usr/local/bin/fabro-pg-ensure` and PostgreSQL 17. `fabro-pg-ensure` starts the
    cluster and guarantees a superuser `test` with password `test` and a database
    `test` on port 5432: exactly the `DATABASE_URL` above. It is idempotent.
  - Measured in that image (fabro-workflows `docs/perf/03-repo-contract-changes.md`,
    2026-09-18): `2430 passed, 4 skipped` in 135 s. fabro's
    `validate` stage has a 20-minute timeout.
- Non-goals: do not add the frontend `lint` or `test:coverage` jobs, and do not add the
  `deploy-guard` bash tests. Do not change `setup.sh` or any GitHub workflow.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft (lawncare-saas `main`)

## Implementation Contract
- Expected files: `.fabro/ci.sh` (replace the contents, keep mode 755).
- Exact new content:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  # Validation mirrors .github/workflows/test.yml (backend pytest) and
  # .github/workflows/frontend.yml (api-staleness), plus ruff and the frontend
  # typecheck and production build. Exit non-zero on failure.
  #
  # PostgreSQL comes from the fabro sandbox image (`fabro-pg-ensure`): a fabro
  # sandbox has no Docker socket for testcontainers and cannot start a database
  # beside itself. Outside that image, provide a PostgreSQL at localhost:5432 with
  # user/password/database `test` before running this script.
  # CI=1 is load-bearing: tests/conftest.py starts a testcontainers PostgreSQL
  # unless CI or GITHUB_ACTIONS is set.
  if command -v fabro-pg-ensure >/dev/null 2>&1; then
    fabro-pg-ensure
  fi
  export CI=1
  export DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test"
  export JWT_ISSUER="lawncare"
  export JWT_AUDIENCE="https://lawncare.example.com"
  export JWKS_URL="https://lawncare.example.com/.well-known/jwks.json"
  export CRON_SECRET="test-secret"

  uv run ruff check src tests
  uv run pytest -n auto tests/
  # Regenerates the client in place and exits 1 when it is stale, so the next
  # attempt starts with the regenerated files already in the tree.
  scripts/check-api-stale.sh
  npm run typecheck
  npm run build
  ```
- Interfaces and names: the environment variable names and values are copied from
  `test.yml` and must stay identical to it.
- Verified external contracts: `fabro-pg-ensure` (at
  `ops/profile-images/fabro-pg-ensure.sh` in fabro-workflows) guarantees
  `test`/`test`/`test` on port 5432. Checked 2026-09-24:
  `command -v fabro-pg-ensure` inside `fabro-python-node:local` prints
  `/usr/local/bin/fabro-pg-ensure`.
- Behavior rules: order is ruff, pytest, staleness, typecheck, build. The cheap static
  check comes first. `set -e` stops at the first failure.
- Error and security rules: the values are test-only placeholders, copied from the
  public workflow file. No real secret.

## Acceptance Criteria
- [ ] `.fabro/ci.sh` matches the content above, and `git ls-files -s .fabro/ci.sh` shows
      mode `100755`.
- [ ] With a PostgreSQL available at `localhost:5432` (user, password and database
      `test`), `./.fabro/setup.sh && ./.fabro/ci.sh` exits 0 on the branch.
- [ ] In a fresh clone, stale the client on purpose (edit one line of
      `frontend/src/api/types.gen.ts`), then run `scripts/check-api-stale.sh`. It exits
      1 and restores the file (`git diff --stat` is empty afterwards).
- [ ] Operator, on the fabro host after merge: the image check below exits 0.

## Test Expectations
- Local, with Docker available:
  `docker run -d --rm --name lc-pg -p 5432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test postgres:17`,
  then `./.fabro/setup.sh && ./.fabro/ci.sh; echo "rc=$?"`. Expected: pytest reports
  about `2430 passed`, and the last line is `rc=0`. Then `docker stop lc-pg`.
- Operator, on the fabro host, against the merged `main` (this is the check that
  matters, because it runs the real sandbox image):
  ```sh
  git clone -q https://github.com/andrewthetechie/lawncare-saas /tmp/lc && \
  docker run --rm -v /tmp/lc:/src:ro --entrypoint bash fabro-python-node:local -c \
    'cp -a /src /w && cd /w && ./.fabro/setup.sh >/dev/null && ./.fabro/ci.sh; echo "rc=$?"'; \
  rm -rf /tmp/lc
  ```
  Expected last line: `rc=0`.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: None. After merge, the operator rebuilds `fabro-python-node:local` with
  `ops/profile-images/build-images.sh`, so the image's warm cache matches the lockfiles.

## Labels
`chore`, `ci`, `priority:high`

## Estimate
Small

## Risk
2 - Every lawncare-saas task now spends about 3 more minutes in `validate`, and
`validate` will fail where it used to pass silently. That is the purpose. If the image
lacked PostgreSQL, every task would fail `validate`, which is why the image check exists.

## Validator Stopping Point
The operator's image check prints `rc=0`.
