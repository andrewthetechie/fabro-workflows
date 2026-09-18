# Cache layout shared by every profile image

One set of paths and one set of `ENV` variables, identical in all four images, so
`.fabro/setup.sh` needs no per-repo knowledge of where a cache lives.

| Path | Tool | Variable |
|---|---|---|
| `/opt/cache/uv` | uv package cache | `UV_CACHE_DIR` |
| `/opt/uv-python` | uv managed interpreters | `UV_PYTHON_INSTALL_DIR` |
| `/opt/cache/npm` | npm | `NPM_CONFIG_CACHE` |
| `/opt/cache/bun` | bun install cache | `BUN_INSTALL_CACHE_DIR` |
| `$CARGO_HOME/registry` | cargo crate registry | (rust image only; `CARGO_HOME` stays at its base-image default) |

They are **baked image layers**, not mounts. The docker sandbox provider exposes no
mounts at all — `volumes` was removed from environment settings in fabro's
2026-06-13 changelog, and the one place fabro builds a `DockerProviderConfig`
(`lib/components/fabro-sandbox/src/docker.rs:47`) hardcodes every field but
`auto_pull`, discarding the driver's `binds` support. See
`docs/perf/00-overview-and-measurements.md`.

A baked cache is in some ways better than the mount Sandcastle used: it is read
by every run at once with no lock contention, it cannot be corrupted by a run,
and it costs nothing to reset — rebuild the image.

It is also *only* a cache. Every variable above points at a directory the tools
treat as a cache, so a lockfile that has moved past what was baked simply fetches
the delta over the network. Staleness degrades speed, never correctness, and
`build-images.sh` on a nightly cron keeps the drift small.

`ENV` is the only way to set these. Fabro evaluates sandbox commands with a
**non-login** `bash`, so `/etc/profile.d/*.sh` and `~/.bashrc` are never sourced
(`docs/public/execution/environments.mdx`, Docker section).
