# Sandbox profile images

The fabro server runs command and agent stages inside per-environment Docker
**profile images**, registered as server-managed environments (`docker` provider).
One image per target repository, carrying that repository's toolchain, its warmed
dependency caches, and — where its tests need one — a PostgreSQL server.

| Environment id | Repository | Image tag | Base | Toolchain | Database |
|---|---|---|---|---|---|
| `python` | jelly-swipe | `fabro-python:local` | `python:3.13.15-trixie` | python 3.13.15, **node 20.19.5**, uv | — |
| `python-node` | lawncare-saas | `fabro-python-node:local` | `node:22.23.2-bookworm-slim` | node 22.23.2, uv + **baked CPython 3.13** | **PostgreSQL 17** |
| `ts` | womens-fantasy-sports | `fabro-ts:local` | `oven/bun:1.4.2` | bun 1.4.2, **uv + baked CPython 3.14** | **PostgreSQL 18** |
| `rust-node` | writers-app | `fabro-rust-node:local` | `rust:1.98.1-trixie` | rust 1.98.1, **node 22.23.2** | — |

Every image contains `git`, `jq`, `gh` (v2.100.0) and `bash`: the clone stage needs
`git`, the gates need `jq`, the acquire/claim/validate/open_pr stages need `gh`, and
fabro evaluates **every** sandbox command with `/bin/bash` and has no `sh` fallback.
Command stages are written as POSIX `sh` regardless — no bashisms.

**Versions track each repository's own CI, not convenience.** `node-version:` in a
repo's workflow files is the authority. Three of the four were wrong before:
jelly-swipe had no node at all while its `ci.sh` ran `npm ci`, writers-app built on
node 20 against a CI that pins 22, and neither node-based image had a Python its
repo could use.

## Building

```sh
./build-images.sh                 # all four
./build-images.sh python ts       # a subset
DRY_RUN=1 ./build-images.sh       # assemble contexts, build nothing
```

Run it on the host. The script clones each target repository, assembles a build
context from its tracked manifest and lock files, builds, and then **verifies the
result offline** (below). Tags are referenced by the environment records and are
never pushed anywhere: sandbox-driver's `ensure_image` checks `image_present()`
and returns before it would pull, so a locally built tag is used as-is.

Nightly, clear of the schedules:

```
23 3 * * *  /home/andrew/profile-images-build/build-images.sh >> ~/.local/state/profile-images.log 2>&1
```

A failed build leaves the previous image tagged and in use, so a bad night
degrades to yesterday's cache rather than to a missing environment.

These Dockerfiles pin `linux_amd64` for the `gh` and `node` tarballs. On an arm64
host, change those URLs (and re-verify) before building.

## The offline gate

After each build, `build-images.sh` runs that repository's own
`.fabro/setup.sh` inside the new image with `--network=none`. If it cannot
install, the build fails.

This is not belt-and-braces; it has already caught two real gaps that a green
`docker build` did not:

- `uv sync --frozen --no-install-project` warms the dependency closure but skips
  the project, and therefore skips the project's **build backend**. The real
  `setup.sh` is plain `uv sync --frozen`, which does build the project, and it
  went to PyPI for `hatchling` — then, once that was warmed, for `editables~=0.3`,
  which hatchling requests from `get_requires_for_build_editable` and never
  declares.
- `cargo fetch` refuses a manifest whose declared targets have no files, so
  writers-app's registry cache was silently empty.

`SKIP_VERIFY=1` disables it, for a repository whose `setup.sh` genuinely needs
the network.

## Caches

Baked image layers at fixed paths, with fixed `ENV` variables — see
`common.env.md`. They replace the `cache.mounts` Sandcastle used, which fabro
cannot express at all: `volumes` was removed from environment settings in the
2026-06-13 changelog, and the one place fabro builds a `DockerProviderConfig`
(`fabro-sandbox/src/docker.rs:47`) hardcodes every field but `auto_pull`,
discarding the driver's `binds` support.

**Be clear about what this buys.** Measured on jelly-swipe in the built image, a
cold install with the network available is 1s for `uv sync` and 4s for `npm ci`.
Warm, it is about a second each. Dependency caching on this host is worth
**seconds per run, not hours** — see `../../docs/perf/00-overview-and-measurements.md`
for why the Sandcastle result does not transfer (a fabro run gets one sandbox for
its whole life, so only the first install of a run is ever cold).

What it does buy, and what makes it worth keeping: every install becomes
network-independent, so a PyPI or npm blip can no longer fail `validate` and send
a task into the rework ladder; and roughly 140 MB of downloads per run stops
crossing the wire. The cargo registry is the one case where it is also
substantial time — 1.2 GB for writers-app.

Staleness degrades speed, never correctness: every path above is a directory the
tool treats as a cache, so a lockfile that has moved past what was baked fetches
the delta. The nightly rebuild keeps the drift small.

## PostgreSQL

`fabro-pg-ensure`, in the images that carry a server. Idempotent; call it from
`.fabro/setup.sh`, `.fabro/ci.sh`, or both.

```sh
fabro-pg-ensure                          # cluster up, superuser `test`, database `test`
PG_EXTRA_DBS="lawncare_migration_test" fabro-pg-ensure
```

It guarantees exactly what a `postgres:N` GitHub Actions service gives a job,
because that is what the repositories' `conftest.py` files are written against:
a superuser `test` with password `test` and a database `test` on `PGPORT`
(default 5432).

Fabro cannot provide this itself. `GET /runs/{id}/sandbox/services` is
*discovery* — it shells `ss -H -ltnp` and reports the ports it finds — so it
lists a server, it never starts one. The docker sandbox-driver does support
`sidecars`, and fabro discards that along with `binds`. And there is no
Docker-in-Docker inside a sandbox, so the `docker run postgres:17` path the
repositories use on a developer machine is unavailable too.

So the cluster is `initdb`'d at **image build time** and started as an ordinary
process, which is the same fallback `Sandcastle-loop/pg-ensure.sh` takes for the
same stated reason. Two differences: the build-time initdb means a run pays about
a second instead of several, and the sandbox runs as **root**, where postgres
refuses to start, so every server command drops to the `postgres` user.

Databases are dropped and recreated on every call. The suites commit rows and
create tables they never clean up — a GitHub Actions service is a fresh container
per job — so a reused database fails the *second* `validate` of a run with
`DuplicateTable` or `UniqueViolation`, not the first.

Verified in `fabro-python-node:local`, offline: cluster ready in 1s, then
**2430 passed, 4 skipped in 135s** of lawncare-saas's backend suite — the suite
its `ci.sh` currently excludes.

## Using it from a repository

The images only make the capability available. A repository's `.fabro/ci.sh` has
to ask for it. For lawncare-saas that is:

```sh
fabro-pg-ensure
export CI=1 DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test"
uv run pytest -n auto tests/
```

`CI=1` matters: that repository's `conftest.py` spins up a testcontainers
PostgreSQL unless `CI` or `GITHUB_ACTIONS` is set, and testcontainers needs the
Docker socket the sandbox does not have.

See `../../docs/perf/02-repo-contract-changes.md` for the per-repository diffs.

## Confirming an image has what a workflow needs

```sh
docker run --rm --entrypoint bash fabro-python:local -c \
  'git --version; jq --version; gh --version | head -1; node -v; python3 -V; uv --version'
docker inspect fabro-python:local --format '{{index .Config.Labels "fabro.warmed-from"}}'
```

The `fabro.warmed-from` and `fabro.warmed-at` labels record which commit the
cache was warmed against, so a stale image is identifiable rather than guessed at.

## Rebuilding is not enough on its own

The images are half of a profile. The **environment records** that point at them
(image tag, CPU/memory, network mode, `repo` label) are server state, not config
— see `../README.md` § "Server-side state" for how to recreate them.
