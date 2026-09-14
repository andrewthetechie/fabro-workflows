# Sandbox profile images

The fabro server runs command/agent stages inside per-environment Docker **profile
images**, registered as server-managed environments (`docker` provider):

| Environment id | Image tag | Runtime |
|---|---|---|
| `python` (`jelly-swipe`) | `fabro-python:local` | Python |
| `python-node` (`lawncare-saas`) | `fabro-python-node:local` | Python + Node |
| `rust-node` (`writers-app`) | `fabro-rust-node:local` | Rust |
| `ts` (`womens-fantasy-sports`) | `fabro-ts:local` | TypeScript/Node |

Every profile image must contain, at minimum: `git`, `jq`, `gh` (GitHub CLI, v2.100.0),
and `bash`, because the clone stage requires `git`, the workflow CLI uses `gh`/`jq`, and
command stages run as POSIX sh with those tools expected on PATH. Command stages run as
`sh` — no bashisms.

## Gap — the exact Dockerfiles are not persisted

**As of 2026-09-14, the authoritative Dockerfiles for these four images are NOT stored
anywhere** — not on the Docker host, not in a repo. Only the built images exist
(`docker images` on the host). They were built on the host during initial setup and
"rebuilt with git/jq/GitHub-CLI v2.100.0" in task 09, but the Dockerfile source was
never saved. `docker inspect` shows no build-label provenance (`labels = null`).

This is a replication gap. To rebuild a profile image from scratch on a new host you
must reconstruct the Dockerfile (see the sample below). If you can recover the original
Dockerfiles, **replace the samples in this directory with the real ones** and commit
them here so the gap closes permanently.

## Sample (recreate, not authoritative)

`Dockerfile.python.example` is a starting point for `fabro-python` based on the known
requirements (Debian base, git/jq/gh/bash, plus the Python runtime). Adjust the base
tag and the language toolchain versions to match what a given repository's
`.fabro/setup.sh` / `.fabro/ci.sh` needs. The image is used by reference
(`fabro-python:local`), so build it with `docker build -t fabro-python:local .` and
leave the tag name alone.

To confirm an image actually has the tools a workflow needs:

```sh
docker run --rm fabro-python:local sh -c 'git --version; jq --version; gh --version; bash --version | head -1'
```
