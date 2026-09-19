# Speed and reliability: what the runs actually measure

Written 2026-09-18 from two complete `backlog` runs on the live server, read from
`/api/v1/runs/<id>/events` (full event stream, `since_seq` paginated) and
`/runs/<id>/stages`. Every number here is measured, not estimated.

Read this before `01-`, `02-`, `03-`, `04-`. It exists because the three obvious
explanations for a four-hour run — dependency installation, review count, and
"the graph is too big" — are all wrong, and the measurements say where the time
actually goes.

## The two runs

| | `01M2RX2SGJJT09WK5FS8GYJCDE` | `01M2RB5QBNBPS6C0F9904V25AX` |
|---|---|---|
| repo | jelly-swipe | jelly-swipe |
| outcome | succeeded, PR blocked at CI | failed |
| wall | **245 min** | **243 min** |
| active (inference + tools) | 143 min | 187 min |
| inference | 135 min | — |
| **tool time** | **8.7 min** | — |
| dead wall (wall − active) | **102 min** | 56 min |
| tasks implemented | 3 | 6 |
| LLM calls | 451 | 517 |

`tool_time_ms` covers every sandbox command the agents ran *and* every command
node. **8.7 minutes out of 245.** Nothing in the sandbox is the bottleneck.

## Where the time goes

### 1. Time-to-first-token is half of all inference

Per-call gap between `agent.llm.started` and `agent.llm.first_output`:

| model | run 1: n / total / median / p90 / max | run 2: n / total / median / p90 / max |
|---|---|---|
| `coders` | 170 / **35.8 min** / 3.0s / 30.9s / **194.6s** | 187 / **40.5 min** / 3.8s / 31.5s / **249.0s** |
| `glm-5.3` | 251 / 27.6 min / 5.4s / 11.8s / 27.9s | 305 / 27.5 min / 5.4s / 6.3s / 16.9s |
| `kimi-k3` | 30 / 2.7 min / 4.6s / 10.3s / 17.6s | 25 / 1.3 min / 3.2s / 4.0s / 4.3s |

**66 minutes of run 1's 135 minutes of "inference" is waiting for the first
token.** The median is fine (3–5s); the tail is not. `coders` — "Local coders
(strix ×2)" — has a p90 of ~31s and a p100 of 3–4 minutes in both runs, while
the two hosted models hold a p90 under 12s. A long tail on a local two-GPU box
with a healthy median is the signature of **queueing**, not slow generation.
`server.scheduler.max_concurrent_runs = 2`, and both runs' agents contend for the
same two GPUs.

This is the largest single cost in the system and it is entirely outside the
graphs. See `03-infrastructure.md`.

### 2. `glm-5.3` hangs, intermittently, for exactly 15m39s

Run 1 lost **60 minutes** to two identical incidents, both on `glm-5.3`:

```
00:52:28  agent.llm.started            model=glm-5.3
01:08:07  agent.llm.retry  attempt=1   502 Bad Gateway from LiteLLM   (+15m39s)
01:21:57  stage.failed     "handler timed out after 1800000ms"        (+30m00s)
01:22:01  stage.started    attempt 2 of 2 — model=glm-5.3 again
```

and again at 02:40:34 → 03:07:42. Both stages succeeded on the retry, so the run
reported success and the hour is invisible in the outcome.

Three things compound:

- The first call never returns. LiteLLM answers `502 Bad Gateway` after 15m39s.
- The retry after the 502 has `delay_secs: 0.29` and re-requests **the same
  model**, so it hangs again.
- Only the node's own `timeout="30m"` stops it. The stages this hit — `quality`
  and `review_merge.standards` — actually run 2–6 minutes. A 30-minute timeout on
  a 4-minute stage means a stall costs 30 minutes before anything notices.

Run 2 had **zero** glm-5.3 hangs across 305 calls. It is intermittent, which is
why it has survived: it does not reproduce on demand and it never fails a run.

### 3. `ci_fix` burns 20 minutes at a time and usually produces nothing

| run | stage | attempts | cost | result |
|---|---|---|---|---|
| 1 | `review_merge.ci_fix_t1` | 2 × 20 min | **40 min** | failed → `report_blocked` |
| 2 | `review_merge.ci_fix_t2` | 20 min + 13 min | **33 min** | failed, then cancelled |

Both ran on `coders`. In run 1 the agent *was* making progress — the 194.6s
time-to-first-token above is inside this stage — it just could not finish 20
minutes of wall clock when a third of it is spent waiting for first tokens. The
timeout is not generous; the model is queued.

### 4. Dependency installation is not a cost worth optimising

`validate` runs `./.fabro/setup.sh && ./.fabro/ci.sh`:

- run 1: 85s, 59s, 61s (+ 61s in the merge phase)
- run 2: ~60s every time, seven times
- `prep`, which does the first install: **0s and 5s**

The whole install-plus-test-suite cycle is about a minute, and the install is a
fraction of it. **Caching dependencies saves seconds per run, not hours.**

This is the opposite of the Sandcastle result, and the reason is structural, not
a missing feature: Sandcastle created a git worktree per attempt on the host, so
every attempt paid a cold install. A fabro run gets **one sandbox for the whole
run**, the workspace persists across all its stages, and `setup.sh` is
idempotent — so the second and later installs are already warm. The cache
Sandcastle needed is a cache fabro gets for free by not throwing the workspace
away.

Caching is still worth doing for **bandwidth** and for the *first* install of
each run, and it becomes worth more if `ci.sh` is un-hobbled to run the full test
suites (see `02-per-repo-images.md`) — but it is a 1–2% lever on wall time and
must not be mistaken for the fix.

## The arithmetic

Run 1, 245 minutes:

| | min | |
|---|---|---|
| coder stages (3 tasks) | 80 | real work |
| review stages (13 agent reviews) | 46 | real work |
| everything else productive | 17 | real work |
| **glm-5.3 hangs** | **60** | **waste** |
| **`ci_fix_t1` timeouts** | **40** | **waste** |
| scheduling, checkpoints, git | ~2 | |

**41% of the run was waste, and none of it was setup, installation, or review
count.** Removing both waste sources turns a 245-minute run into a ~145-minute
one without touching how the workflow works.

## What fabro can and cannot do (source-verified)

Checked against `context/fabro` and the pinned `sandbox-driver` rev, because
three of the obvious remedies turn out to be unavailable.

### Mount-based caching is impossible on this provider

`EnvironmentSettings` is `provider, cwd, image, resources, network, lifecycle,
labels, env` — `lib/components/fabro-environment/src/model.rs`, whose
`canonical_bytes` enumerates every key. There is no mounts field.
`docs/public/changelog/2026-06-13.mdx` records why: *"Environment `volumes`
settings were removed. Remove `volumes` from managed environment definitions; the
field is no longer accepted or applied."* The Daytona-only
`[[run.sandbox.daytona.volumes]]` from `2026-05-14` is legacy `[run.sandbox]`
config that is auto-migrated away, and this host runs the **docker** provider.

So the Sandcastle `cache.mounts` shape has no equivalent. Caches must be baked
into the image.

### The driver supports binds *and* sidecars; fabro discards both

`sandbox-driver-docker-config::DockerProviderConfig` carries `binds:
Vec<BindMount>`, `sidecars: Vec<Sidecar>`, `host_network`, `extra_hosts`, `dns`,
`cap_add`, `registry_auth`, `init`, `privileged`, `platform`.

fabro sets that struct in exactly one place, hardcoded —
`lib/components/fabro-sandbox/src/docker.rs:47`:

```rust
.provider_config(DockerProviderConfig { auto_pull: true, ..Default::default() }.into_value())
```

`grep provider_config lib/ --include='*.rs'` returns that line and one test. Every
other field stays at its default, and no config surface reaches it. Cache mounts
and a Postgres sidecar are each a few lines of upstream plumbing away, and
unreachable until someone writes them.

### "Fabro services" is discovery, not provisioning

`GET /api/v1/runs/{id}/sandbox/services` runs `ss -H -ltnp` inside the sandbox and
reports `{port, addresses, processes, preview_supported}` —
`lib/foundation/fabro-types/src/sandbox_services.rs` and
`docs/superpowers/plans/2026-05-10-sandbox-services-backend.md`. It **lists**
listening ports. It starts nothing. There is no declarative service block.

Postgres for `lawncare-saas` and `womens-fantasy-sports` therefore has to live
inside the image and be started by the repo's own scripts. Sandcastle already
solved exactly this: `Sandcastle-loop/pg-ensure.sh` falls back to a
user-owned cluster via `initdb`/`pg_ctl` precisely because *"there is no
Docker-in-Docker"* in a sandbox. That fallback is the pattern to copy.

### Per-repo images do work — as prebuilt local tags only

The provider mapping table in `docs/public/execution/environments.mdx` marks
`image.dockerfile` as *"Warning; ignored"* for the docker provider. Only
`image.docker` is honoured. But `ensure_image`
(`sandbox-driver-docker/src/lib.rs:194`) checks `image_present()` and returns
early, so a tag built locally on the host is used with **no registry pull**. That
is what the four `fabro-*:local` profile images already rely on.

Two constraints on those images: the image must provide `/bin/bash`, and commands
run in a **non-login** shell, so `/etc/profile.d`, `~/.bash_profile`, `nvm` and
`pyenv` initialisers are never sourced. Anything a tool needs on `PATH` has to be
set with Dockerfile `ENV`.
