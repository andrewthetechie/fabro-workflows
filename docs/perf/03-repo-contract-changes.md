# Repository contract changes

The profile images now carry things the four target repositories' `.fabro/ci.sh`
files were written around. Taking them up means editing those repositories. None
of this is pushed — they are live and two of them deploy on merge.

Everything below was run in the rebuilt image before being written down.

---

## jelly-swipe — nothing to change, and it is now urgent that you deploy the image

`.fabro/ci.sh` on `main @ 842df8a` already runs the frontend half. The repository
is correct; the image was not. Verified offline in the rebuilt
`fabro-python:local`:

```
661 passed  (backend, pytest)
356 passed, 1 skipped  (frontend, vitest)
ci.sh exit=0 in 43s
```

**Until `fabro-python:local` is rebuilt on the host, every jelly-swipe run fails
`validate` on every task** and burns the four-tier rework ladder on it. This is
the one item in this whole document with a deadline.

---

## lawncare-saas — turn the backend suite on

Today:

```sh
# .fabro/ci.sh
uv run ruff check src tests          # "pytest needs Postgres -> excluded here"
npm run typecheck
npm run build
```

2430 tests never run. Proposed:

```sh
#!/usr/bin/env bash
set -euo pipefail
# Validation mirrors .github/workflows/test.yml. Postgres comes from the profile
# image (fabro-pg-ensure), because a fabro sandbox has no Docker socket for the
# repo's usual `docker run postgres:17` path and fabro cannot start a service
# beside the sandbox.
fabro-pg-ensure
export CI=1
export DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test"
export JWT_ISSUER=lawncare
export JWT_AUDIENCE="https://lawncare.example.com"
export JWKS_URL="https://lawncare.example.com/.well-known/jwks.json"
export CRON_SECRET=test-secret

uv run ruff check src tests
uv run pytest -n auto tests/
npm run typecheck
npm run build
```

`CI=1` is load-bearing, not decoration: `tests/conftest.py` starts a
**testcontainers** PostgreSQL unless `CI` or `GITHUB_ACTIONS` is set, and
testcontainers needs the Docker socket the sandbox does not have. The rest of the
environment is copied from `test.yml`'s `Run tests` step.

**Measured**, offline, in `fabro-python-node:local`:

```
fabro-pg-ensure: postgres ready on port 5432        1s
2430 passed, 4 skipped, 55 warnings in 135.39s
```

`validate` has `timeout="20m"` and currently takes about 60s, so this takes it to
roughly 200s — comfortable.

**One thing to check before you merge it.** Sandcastle's `pg-ensure.sh` also reset
a `lawncare_migration_test` database, with the note that a newer migration
revision left by another branch otherwise makes an older branch fail to resolve
its revision. The 2430-test run above passed without it, so either the migration
tests were among the 4 skipped or they create it themselves. If they need it:

```sh
PG_EXTRA_DBS="lawncare_migration_test" fabro-pg-ensure
```

---

## womens-fantasy-sports — possible now, but not finished

Today:

```sh
# .fabro/ci.sh
# "Backend pytest is excluded here because it requires Postgres + Python 3.14 +
#  redis/kafka (runs in the repo own GitHub Actions instead)."
bun run --filter frontend test:unit
bun run --filter frontend build
```

`fabro-ts:local` now has uv, a baked CPython 3.14, and PostgreSQL 18 — so two of
the three stated blockers are gone. **I could not get the suite green, and I am
not going to claim otherwise.** What is left is repository-side:

- `.github/workflows/test-backend.yml` runs `docker compose up -d db mailcatcher`
  and then `uv run bash scripts/prestart.sh`. The `db` half is replaceable with
  `fabro-pg-ensure` — `compose.yml`'s `db` is `postgres:18`, which is the major
  in the image, chosen for that reason and because `backup-bats.yml` notes
  pg_dump refuses to dump a server newer than the client. The **mailcatcher**
  half has no equivalent, and I did not establish how much of the suite needs it.
- `scripts/prestart.sh` failed with a `tenacity.RetryError` wrapping a
  `TypeError` when pointed at the cluster with `POSTGRES_SERVER=localhost`
  `POSTGRES_USER=postgres` `POSTGRES_DB=test`. That looks like configuration, not
  the database — the cluster was up and accepting connections — but I did not
  chase it.
- `.env` is not shell-safe to `source` (line 101 is prose), so the test
  environment has to be assembled explicitly rather than sourced.

The suite also runs `pytest -n auto` where "every worker clones its own
PostgreSQL database", and `test-backend.yml` caps workers at 8 for exactly that
reason. `fabro-pg-ensure`'s cluster is trust-auth with a `postgres` superuser, so
per-worker `CREATEDB` works, but the cap should be carried over.

**Recommendation:** leave `ci.sh` alone for now and treat "run the wfs backend
suite in a fabro sandbox" as its own issue in that repository. The image no
longer blocks it, which was the part that needed doing here.

---

## writers-app — nothing to change, but re-read the results

`.fabro/ci.sh` is unchanged and correct. What changed is that it now runs on
**node 22.23.2** instead of node 20.19.2, which is what
`.github/workflows/ci.yml` pins in every job. `npm` goes from 9.2.0 to 10.9.x —
9.2.0 against node 20 came from Debian's package split and is not a pairing npm
ever shipped.

`npm run typecheck`, `lint`, `test:unit` and `build` have been mirroring a CI
they did not match. Expect the first run after the rebuild to surface real
differences rather than flakes; a failure there is information, not a regression.

`cargo fetch` is covered offline by the 1.2 GB registry baked into the image, so
`setup.sh` no longer pulls it per run — the one repository where cache warming is
worth real time rather than seconds.

---

## Order to do this in

1. **Rebuild the images on the host.** Removes the jelly-swipe landmine; changes
   nothing else about how anything runs.
2. Watch one jelly-swipe run and one writers-app run. writers-app is the one that
   might surface node-22 differences.
3. Then the lawncare-saas `ci.sh` change, as its own PR in that repository.
4. womens-fantasy-sports as a separate issue, if you want it at all.
