# womens-fantasy-sports: open the "run the backend suite in `.fabro/ci.sh`" issue

> **Work in the `andrewthetechie/womens-fantasy-sports` issue tracker.** This task opens
> one GitHub issue. It changes no code in any repository.

## Tracer-Bullet Outcome
womens-fantasy-sports has one open issue that states exactly what stops its backend
pytest suite running in fabro's sandbox, and what "done" looks like. A human (or a later
plan) can then close the last CI-parity gap from ADR 0011 D4. Until that happens, backend
failures there still reach the PR before anyone sees them.

## User Story
As the operator, I want the remaining CI-parity gap recorded where the work will be done,
with everything already known about it, so that nobody has to rediscover it.

## Description
Create the issue with the `gh` CLI, using the exact title, labels and body below. Do not
edit the body's facts. If a label is missing, create the issue without it and report
which one.

## Context Pack
- Source decisions: fabro-workflows `docs/merge-rate/00-overview-and-contracts.md`
  decision 7. ADR 0011 D4.
- Evidence: womens-fantasy-sports#1237 was blocked because `test-backend` failed.
  `scripts/measure-schedule-shape.py:143` still called `gameweek_for(session, …)` after
  the PR removed the `session` parameter. fabro's `validate` never ran the backend suite,
  so the coder never saw it.
- Repo facts:
  - Labels verified present on 2026-09-24: `area:infra` ("Infrastructure, Docker,
    Ansible, CI") and `human-reserved` ("agent drafts, human signs off. Never merge
    unattended"). Use both. Do **not** use `agent` or `needs-triage`: the fix touches
    fabro-workflows' sandbox image, which an agent working in this repository cannot
    change.
  - The sandbox image for this repository is `fabro-ts:local`.
- Non-goals: do not change `.fabro/ci.sh` in this task. Do not attempt the fix.

## Delivery Strategy
- Shape: Normal tracer bullet (the deliverable is the issue)
- Valid-state scope: N/A (no code)

## Implementation Contract
- Command (run from any directory, with `gh` authenticated):
  ```sh
  gh issue create -R andrewthetechie/womens-fantasy-sports \
    --title "chore(ci): run the backend pytest suite in .fabro/ci.sh" \
    --label area:infra --label human-reserved \
    --body-file /tmp/wfs-backend-suite.md
  ```
- Write `/tmp/wfs-backend-suite.md` with exactly this content first:
  ````markdown
  ## Why

  fabro's `backlog` workflow validates every task with `./.fabro/setup.sh && ./.fabro/ci.sh`
  before the PR is opened. This repository's `ci.sh` does not run the backend suite:

  ```bash
  uv run prek run --from-ref origin/main --to-ref HEAD --show-diff-on-failure
  bun run --filter frontend test:unit
  bun run --filter frontend build
  ```

  so a change that breaks a backend test is only caught by `test-backend` after the PR is
  open. There, fabro's CI-fix agent may edit only files already in the PR, and the merge
  stops for a human. #1237 was blocked exactly that way:
  `scripts/measure-schedule-shape.py:143` still called `gameweek_for(session, …)` after
  the PR removed `session`. (fabro-workflows ADR 0011, D4.)

  ## What `test-backend.yml` does today

  1. `docker compose up -d db mailcatcher` (`db` is `postgres:18`)
  2. `uv run bash scripts/prestart.sh` in `backend/` (migrations)
  3. caps pytest-xdist workers at 8: `PYTEST_XDIST_AUTO_NUM_WORKERS=min(nproc, 8)`
  4. `uv run bash scripts/tests-start.sh "Coverage for <sha>"` in `backend/`
  5. `uv run coverage report --fail-under=90`

  ## What a fabro sandbox can and cannot do

  - It has **no Docker socket**, so `docker compose up` is impossible.
  - The image `fabro-ts:local` already has uv, CPython 3.14 and **PostgreSQL 18**, plus
    `fabro-pg-ensure`, which starts a cluster with superuser `test`/`test`, database
    `test`, on port 5432 (`PG_EXTRA_DBS="a b"` adds more databases). The cluster is
    trust-auth with a `postgres` superuser too, so per-worker `CREATEDB` works.
  - Known blockers, from an attempt on 2026-09-18 (fabro-workflows `docs/perf/03`):
    - **mailcatcher** has no equivalent in the image. How much of the suite needs it is
      not established.
    - `scripts/prestart.sh` failed with a `tenacity.RetryError` wrapping a `TypeError`
      when pointed at the local cluster with `POSTGRES_SERVER=localhost`
      `POSTGRES_USER=postgres` `POSTGRES_DB=test`. That looks like configuration, not the
      database: the cluster was up and accepting connections.
    - `.env` is not shell-safe to `source` (line 101 is prose), so the test environment
      has to be set explicitly.

  ## Done when

  - [ ] `./.fabro/ci.sh` runs the backend suite (migrations, then
        `tests-start.sh`, with workers capped at 8) inside `fabro-ts:local`, using
        `fabro-pg-ensure` instead of `docker compose`.
  - [ ] Whatever needs mailcatcher is either served in the sandbox or explicitly
        skipped there, with the reason in a comment in `ci.sh`.
  - [ ] It passes in the image:
        `docker run --rm -v <clone>:/src:ro --entrypoint bash fabro-ts:local -c 'cp -a /src /w && cd /w && ./.fabro/setup.sh && ./.fabro/ci.sh'`
  - [ ] It fits inside fabro's 20-minute `validate` timeout.
  ````
- Interfaces and names: title and labels exactly as given.
- Verified external contracts: `gh issue create -R <owner/repo> --title --label
  --body-file` (standard `gh` flags).
- Behavior rules: create exactly one issue. Before creating it, check that no open issue
  with the same title exists:
  `gh issue list -R andrewthetechie/womens-fantasy-sports --state open --search "run the backend pytest suite in .fabro/ci.sh" --json number,title`.
  If one exists, do not create another. Report its number instead.
- Error and security rules: the body contains no secret.

## Acceptance Criteria
- [ ] Exactly one open issue in womens-fantasy-sports has the title above and the labels
      `area:infra` and `human-reserved`.
- [ ] The issue URL is reported back.

## Test Expectations
- `gh issue list -R andrewthetechie/womens-fantasy-sports --state open --label human-reserved --json title --jq '.[].title' | grep -c 'run the backend pytest suite'`
  prints `1`.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: None

## Labels
`chore`, `ci`, `priority:low`

## Estimate
Small

## Risk
1 - The task only creates an issue.

## Validator Stopping Point
The `gh issue list` check prints `1`.
