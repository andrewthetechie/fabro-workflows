# Sandbox profile images

The fabro server runs command and agent stages inside per-environment Docker
**profile images**, registered as server-managed environments (`docker` provider):

| Environment id | Image tag | Dockerfile | Base | Size |
|---|---|---|---|---|
| `python` (jelly-swipe) | `fabro-python:local` | `Dockerfile.python` | `python:3.13.15-trixie` | 486 MB |
| `python-node` (lawncare-saas) | `fabro-python-node:local` | `Dockerfile.python-node` | `node:22.23.2-bookworm-slim` | 593 MB |
| `rust-node` (writers-app) | `fabro-rust-node:local` | `Dockerfile.rust-node` | `rust:1.98.1-trixie` | 3.6 GB |
| `ts` (womens-fantasy-sports) | `fabro-ts:local` | `Dockerfile.ts` | `oven/bun:1.4.2` | 568 MB |

Every profile image contains `git`, `jq`, `gh` (GitHub CLI v2.100.0) and `bash`: the
clone stage needs `git`, the gates need `jq`, the acquire/claim/validate/open_pr stages
need `gh`. Command stages run as POSIX `sh` — no bashisms.

## Provenance — reconstructed and verified, 2026-09-14

The original Dockerfiles were never saved. These were reconstructed from the built
images' own layer history (`docker history --no-trunc <image>`), which preserved each
image's single custom `RUN` layer verbatim, plus the base image's `ENV` markers
(`PYTHON_VERSION`, `NODE_VERSION`, `RUST_VERSION`, the bun `image.version` label).

**They are verified, not guessed.** Each was rebuilt on the host and compared against
the live image on OS codename and the version of every installed tool
(`git`, `jq`, `gh`, `python3`, `node`, `bun`, `cargo`, `uv`):

```
python       live: os=trixie  git=2.47.3 jq=1.7 gh=2.100.0 python=3.13.15 uv=0.12.13   MATCH
python-node  live: os=bookworm git=2.39.5 jq=1.6 gh=2.100.0 node=v22.23.2  uv=0.12.13   MATCH
rust-node    live: os=trixie  git=2.47.3 jq=1.7 gh=2.100.0 node=v20.19.2 cargo=1.98.1   MATCH
ts           live: os=trixie  git=2.47.3 jq=1.7 gh=2.100.0 bun=1.4.2                    MATCH
```

One subtlety worth keeping: `python-node` uses the **slim** node base. The full
`node:22.23.2-bookworm` ships python 3.11.2 via buildpack-deps and builds to 1.76 GB;
the live image has no system python3 and is 593 MB. Python for that profile comes from
uv's managed downloads at runtime (`UV_PYTHON_DOWNLOADS=true`). The slim rebuild
reproduces 593 MB exactly.

## Build

Tags are referenced by the registered environments, so build with these exact names:

```sh
cd ops/profile-images
docker build -f Dockerfile.python      -t fabro-python:local      .
docker build -f Dockerfile.python-node -t fabro-python-node:local .
docker build -f Dockerfile.rust-node   -t fabro-rust-node:local   .
docker build -f Dockerfile.ts          -t fabro-ts:local          .
```

`rust-node` is the slow one (~3.6 GB; GTK/WebKit dev headers for a Tauri build). It is
also why the `rust-node` environment runs at 4 CPU / 8 GB — a cold cargo build has to
finish inside the validate stage's 20-minute `./.fabro/ci.sh` timeout.

These Dockerfiles pin `linux_amd64` for the `gh` tarball. On an arm64 host, change that
URL (and re-verify) before building.

## Confirm an image has what a workflow needs

```sh
docker run --rm --entrypoint sh fabro-python:local -c \
  'git --version; jq --version; gh --version | head -1; bash --version | head -1'
```

## Rebuilding is not enough on its own

The images are only half of a profile. The **environment records** that point at them
(image tag, CPU/memory, network mode, lifecycle, `repo` label) are server state, not
config — see `../README.md` § "Server-side state" for how to recreate them.
