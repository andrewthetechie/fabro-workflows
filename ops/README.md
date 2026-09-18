# fabro host ops

Everything needed to replicate the fabro server host (`andrew@<HOST>`, a trusted
single-tenant box). This
directory is **ops tooling** — it is deliberately outside `.fabro/workflows/`, which
is the only tree the `andrewthetechie/fabro-workflows` automations read. Adding or
changing files here does **not** affect any live automation.

Deterministic shell / compose / config only. **No secrets live here** — see the
secrets note in `README.md`. Nothing in this directory is allowed to contain a key,
token, or webhook URL; use `${VAR}`/`.env` indirection and reference the secret
locations instead.

## Contents

| File | Purpose |
|---|---|
| `fabro-branch-sweep.sh` | Deletes leaked `fabro/run/*` and `fabro/meta/*` branches from the target repos (daily cron). Deterministic; see its header comments. |
| `fabro-sandbox-sweep.sh` | Removes exited `fabro-run-*` sandbox containers, which fabro stops but never deletes (daily cron). Deterministic; see its header comments. |
| `fabro-monitor.sh` | Out-of-band health monitor (every-15-minute cron): dead container/API/scheduler, stuck runs, empty work queue, disk pressure — the gap the in-run Discord hooks cannot cover. Health-only; run *failures* stay hook-owned (ADR 0004). Contract: `../docs/turn-it-on/00-overview-and-contracts.md`. |
| `docker-compose.yaml` | Runs the `fabro` server container (the only container that is `Up`). |
| `.env.example` | Env key names for the compose file. Copy to `.env` beside the compose file and fill in real values. |
| `settings.toml.example` | Server settings overlay (`/storage/.home/settings.toml` in the container): env catalog, model map, sandbox providers. |
| `profile-images/` | The four sandbox profile-image Dockerfiles, `build-images.sh` (clone, warm from real lockfiles, build, verify), `fabro-pg-ensure.sh` (in-sandbox PostgreSQL, because fabro cannot start a service beside a sandbox) and `warm-build-backend.sh`. Start at its `README.md`. |
| `provision-server-state.sh` | Recreates the twelve **automations** (three per repo: `backlog`, `pr-review`, `issue-triage`) — server state that lives in fabro's store, not in `settings.toml`. Without this a restored host has settings but nothing to run. It does **not** create environments; see step 6. |
| `provision-litellm-models.sh` | Creates the `high-reasoning` model group and its `kimi-k3` fallback in LiteLLM. Scoped to that one group; the other model rows predate this workflow and are reported, never corrected. The `coders` pool's concurrency tuning is recorded under *Server-side state*, not managed here. |
| `README.md` | This file — bring-up, install, and replication steps. |

**Not here, deliberately:** `fire-pr-review.sh` is tracked at
`.fabro/workflows/backlog/scripts/fire-pr-review.sh` and *deployed* to
`~/bin/fabro-fire-pr-review.sh` on the host. `backlog`'s `trigger_review` stage executes
it from a fresh clone of `main`, so it is code a live automation runs — and `ops/` is
the tree no automation reads, which is what makes editing `ops/` safe. Moving it here
for tidiness would turn every `ops/` edit into a live deploy. Its absolute-path
deployment and drift check are in the main `AGENTS.md`.

## Secrets — where they live, never in this repo

- `FABRO_DEV_TOKEN`, `SESSION_SECRET`, `FABRO_LITELLM_KEY`, `GITHUB_TOKEN` →
  `~/.fabro-deploy/env` on the Mac (chmod 600) and the fabro vault.
- `FABRO_API_TOKEN` → the fabro vault, and **only** the vault. `backlog`'s
  `workflow.toml` injects it into the sandbox with
  `[run.environment.env]` so `trigger_review` can create a `pr-review` run through the
  API. The vault entry fails **closed**: if it is missing, every `backlog` run aborts at
  startup before its sandbox exists, on all four repos at once. That is also the fastest
  kill switch for the bridge, and the bluntest — it stops `backlog` entirely.
- The bridge reuses the server's own dev token because fabro supports exactly **one**
  (`dev_token: Option<String>`, compared against a single expected value). It therefore
  **cannot be rotated independently**: revoking the bridge's access means rotating the
  server token, which also breaks the CLI and `discord-notify.sh`. Accepted, not
  overlooked.
- Server env: `/storage/server.env` and `/storage/.home/server.json` in the container.
- Discord webhook URL: `/storage/secrets/discord_webhook_url` in the container volume.
- Compose env: `~/fabro/.env` on the host (ready from `.env.example`).

## Install (from a fresh host)

1. **Server compose** — copy `docker-compose.yaml` and `.env` to `~/fabro/`, then
   `cd ~/fabro && docker compose up -d`. It mounts `fabro-storage:/storage`,
   `/var/run/docker.sock` (host-root-equivalent — trusted single-tenant), and a
   `tmpfs` `/workspace` (required for `sandbox=false` hooks, whose cwd is
   `/workspace`).
2. **Settings overlay** — place `settings.toml.example` at
   `/storage/.home/settings.toml` inside the container (or
   `/var/lib/docker/volumes/fabro_fabro-storage/_data/.home/settings.toml` on the
   host), `docker compose restart fabro`. Adjust `base_url` / model list to the real
   provider.
3. **Notify script** — deploy `discord-notify.sh` (tracked under
   `.fabro/workflows/backlog/scripts/`) to `/storage/scripts/discord-notify.sh` and
   write the webhook URL to `/storage/secrets/discord_webhook_url`. It lives under
   `backlog/scripts/` but is called by hooks in **both** workflows by absolute path
   inside the container — do not tidy it into `pr-review/scripts/`; hooks reference
   the `/storage/scripts/` path.
4. **Sweepers** — `cp fabro-branch-sweep.sh fabro-sandbox-sweep.sh ~/bin/ && chmod +x ~/bin/fabro-branch-sweep.sh ~/bin/fabro-sandbox-sweep.sh`, then install both crons (below).
5. **Profile images** — `cd profile-images && ./build-images.sh`. It clones each
   target repository, warms that repository's caches from its own lockfiles, and
   gates each image on an offline cache check plus a live run of the repository's
   `.fabro/setup.sh`. Two of the four carry a PostgreSQL server; see
   `profile-images/README.md`. Install the nightly rebuild cron at the same time.
6. **Environments** — create the five (`default`, `python`, `python-node`, `ts`,
   `rust-node`) with `POST /api/v1/environments`, matching the table below. Nothing
   in this repo provisions them: `provision-server-state.sh` only reads
   `environment_id` to detect drift, and an automation pointing at an environment
   that does not exist fails at run admission. **Do this after step 5**, because
   each environment names a profile image tag. Verify with
   `GET /api/v1/environments`.
7. **Automations** — `FABRO_API_URL=http://<HOST>:32276/api/v1 \
   FABRO_DEV_TOKEN=<dev token> ./provision-server-state.sh`. This creates the eight
   automations, four per workflow. `FABRO_API_URL` includes the `/api/v1` prefix;
   the script appends `/automations` to it.
8. **Vault secrets** — `fabro secret set GITHUB_TOKEN <...>` and
   `fabro secret set LITELLM_API_KEY <...>` on the host. `settings.toml` references
   these by name; the values are never in config.
9. **Bridge token** — `fabro secret set FABRO_API_TOKEN "$(cat
   /storage/server.dev-token)"`, read from the volume rather than retyped, so the
   bridge reuses the server's single dev token exactly. Confirm **by name only** with
   `fabro secret list | grep FABRO_API_TOKEN`. This is a hard startup dependency of
   every `backlog` run — see the Secrets section — so do it before step 10, and before
   any `backlog` automation fires.
10. **Contract scripts** (`.fabro/setup.sh`, `.fabro/ci.sh`) live in each target repo,
   not this repo. Every target repo must have both or every run fails at `prep`.

## Server-side state (not in `settings.toml`)

`settings.toml` holds the server's *configuration*. Two things a working host needs are
**state in fabro's own store** and are invisible to a settings backup:

**Environments** — what a sandbox actually is. `[run.environment] id = "default"` in
settings.toml only *selects* one; it does not define any.

| id | image | cpu | memory | `repo` label |
|---|---|---|---|---|
| `default` | `buildpack-deps:noble` | 2 | 4GB | — |
| `python` | `fabro-python:local` | 2 | 4GB | jelly-swipe |
| `python-node` | `fabro-python-node:local` | 2 | 4GB | lawncare-saas |
| `ts` | `fabro-ts:local` | 2 | 4GB | womens-fantasy-sports |
| `rust-node` | `fabro-rust-node:local` | 4 | 8GB | writers-app |

The images are rebuilt nightly rather than being static artefacts, because their
value is a dependency cache warmed from each repository's current lockfiles. Add
to the host crontab, clear of the schedules:

```
23 3 * * *  /home/andrew/profile-images-build/build-images.sh >> ~/.local/state/profile-images.log 2>&1
```

**LiteLLM `coders` deployments** — two rows, one per llama.cpp box, both
`openai/deepseek-v4-flash-0731-iq3-xxs`:

| deployment | api_base | timeout | max_parallel_requests |
|---|---|---|---|
| `ee9cf2cb…` | `http://10.10.0.29:8000/v1` | **600.0** | 1 |
| `c4504835…` | `http://10.10.0.56:8000/v1` | **600.0** | 1 |

`timeout` was `120.0` on both until 2026-09-18. fabro's coder turns averaged
**95 seconds**, so the gateway was cutting the long ones — that is where the
`502 Bad Gateway` responses on `coders` came from, and the agent then retried the
whole turn. 600s is generous against the measured turn distribution while still
releasing llama.cpp's single slot on a genuinely hung upstream.

`max_parallel_requests = 1` mirrors llama.cpp's one slot per box: whoever holds
it blocks everything else for a full turn. **There is no affinity**, so with two
concurrent runs a request routinely lands on the slot the other run is holding
and waits out its whole turn. Sandcastle avoided this by locking one repo to one
instance. Splitting `coders` into per-box groups and pinning environments is
designed but not built — see `../docs/perf/02-needs-your-decision.md`.

Changing these needs the **master key**, not `FABRO_LITELLM_KEY`: that one is an
`internal_user` and `POST /model/update` answers 403. The master key is the
sealed secret `litellm-secrets.masterkey`, readable from the running pod:

```sh
kubectl -n litellm exec deploy/litellm -- printenv PROXY_MASTER_KEY
```

Send the **full** current `litellm_params` with only the field you mean to
change. `POST /model/update` replaces the map, so a partial body drops `api_base`
and `max_parallel_requests`. These two rows have no credential attached
(`GET /credentials/by_model/{id}` is empty) because they are plain-http LAN
endpoints — but a z.ai row does, and rebuilding one from `/model/info` output
silently creates a model with no key.

Apply it while the target box's slot is idle (`GET http://<box>:8000/slots`,
`is_processing == 0`) — that gap between turns is the safe window when a run is
live.

**Automations** — two per target repo, one for each workflow, all resolving
`workflow_source` to `andrewthetechie/fabro-workflows@main` at fire time. Read back
from the live server 2026-09-16:

| id | target | environment | auto_merge | triggers |
|---|---|---|---|---|
| `backlog-jelly-swipe` | `andrewthetechie/jelly-swipe` | `python` | — | `api:manual` enabled, `schedule:every-15m` disabled (`2-59/15 * * * *`) |
| `backlog-lawncare-saas` | `andrewthetechie/lawncare-saas` | `python-node` | — | `api:manual` enabled, `schedule:every-15m` disabled (`5-59/15 * * * *`) |
| `backlog-womens-fantasy-sports` | `andrewthetechie/womens-fantasy-sports` | `ts` | — | `api:manual` enabled, `schedule:every-15m` disabled (`8-59/15 * * * *`) |
| `backlog-writers-app` | `andrewthetechie/writers-app` | `rust-node` | — | `api:manual` enabled, `schedule:every-15m` disabled (`11-59/15 * * * *`) |
| `pr-review-jelly-swipe` | `andrewthetechie/jelly-swipe` | `python` | `true` | `api:manual` enabled — **configuration only, never fired** |
| `pr-review-lawncare-saas` | `andrewthetechie/lawncare-saas` | `python-node` | `true` | `api:manual` enabled — **configuration only, never fired** |
| `pr-review-womens-fantasy-sports` | `andrewthetechie/womens-fantasy-sports` | `ts` | `true` | `api:manual` enabled — **configuration only, never fired** |
| `pr-review-writers-app` | `andrewthetechie/writers-app` | `rust-node` | `true` | `api:manual` enabled — **configuration only, never fired** |

The `pr-review-*` rows are **not dead**. The bridge resolves each repo's
`environment_id` and `target` from its own `pr-review-<repo>` automation rather than
hardcoding a map, so a fifth repository needs no change to any graph, and deleting a row
silently breaks the bridge for that repo. They are read, never fired — and now they are
read for one more thing: the per-repo auto-merge kill switch. The `auto_merge` token
lives in the row's `description`, read by `fire-pr-review.sh`; "an absent token is on"
(decision 10). These rows are genuinely fireable through the same `api:manual` trigger
now that `watch_checks`→`merge` exists, but they are still never fired by anything on a
cron.

### LiteLLM coder pool — concurrency and retry tuning

Lives in LiteLLM's Postgres (`STORE_MODEL_IN_DB=True`), not in the Helm `values.yaml`
and not in this repo. `ops/provision-litellm-models.sh` deliberately does **not** manage
these — it owns `high-reasoning` only, and it reports drift rather than correcting it.
Recorded here so a rebuild can restore them by hand.

Set 2026-09-17 after a `backlog` run lost 45 minutes to a silent inference stall
(run `01M2PKP5YJ4YSZKR5YBTQT8SE6`). Root cause: **LiteLLM was configured to send four
concurrent requests to each coder box, and each box is llama.cpp with `total_slots: 1`**.
llama.cpp does not reject the excess — it queues the task internally, with no error and
no data, so the client sees a stream that emits a few tokens and then goes silent. The
router's 600s timeout and two retries then queued *more* work onto the same saturated
slot. Sandcastle never hit this because opencode talks to one box directly; LiteLLM is
the only fan-in point in the chain.

Per-deployment, on both `coders` rows (`POST /model/update`):

| field | was | now | why |
|---|---|---|---|
| `max_parallel_requests` | 4 | **1** | matches `total_slots: 1`, read live from each box's `/props`. With 1, the router knows a box is busy and routes to the other instead of stacking. |
| `timeout` | 600.0 | **120.0** | ten minutes of silence from a single-slot box means queued, and waiting does not help. |

`/model/update` **replaces** `litellm_params` rather than merging it — setting `timeout`
alone silently dropped `max_parallel_requests`. Always send `model`, `api_base`,
`timeout` and `max_parallel_requests` together, then read `/v1/model/info` back.

Router-wide (`POST /config/update`, and these affect **every** model group):

| field | was | now | why |
|---|---|---|---|
| `routing_strategy` | `simple-shuffle` | **`least-busy`** | shuffle picks randomly, so a retry can land back on the stuck box. `least-busy` is the awareness a 2×1-slot topology needs. In-memory tracking is sufficient at `replicas: 1`; a second replica would need Redis. |
| `cooldown_time` | 5 | **60** | five seconds is too short for a wedged box to leave rotation. |
| `model_group_retry_policy` | `{}` | **`{"coders": {all 0}}`** | retrying a queued streaming request adds load to the saturated thing, and once LiteLLM has committed HTTP 200 downstream a retry cannot reach the client anyway. Scoped to `coders`; global `num_retries` stays 2. |

`model_group_retry_policy` resolves per model group and falls back to `DefaultRetries`
(`litellm/router_utils/get_retry_from_policy.py`), so `DefaultRetries: 0` alone would
suffice; the specific error types are set to 0 as well for legibility.

Preserved through both writes, and worth checking after any future change:
`fallbacks: [{"high-reasoning": ["kimi-k3"]}]` (ADR 0003's overflow), `num_retries: 2`,
`allowed_fails: 3`, router `timeout: 6000`.

Verified after the change: all six model groups (`coders`, `glm-5.3`, `glm-4.7`,
`kimi-k3`, `high-reasoning`, `long-context`, `kimi-for-coding`) return `ok` from
`fabro model test -p litellm -m <name>`, and the settings read back from
`LiteLLM_Config.router_settings` in Postgres, so they survive a pod restart.

Read the live state back with:

```sh
kubectl -n litellm exec deploy/litellm -- python3 -c '
import os,json,urllib.request
k=os.environ["PROXY_MASTER_KEY"]
r=urllib.request.Request("http://127.0.0.1:4000/v1/model/info", headers={"Authorization":"Bearer "+k})
for m in json.load(urllib.request.urlopen(r)).get("data",[]):
    if m.get("model_name")=="coders":
        li=m["litellm_params"]
        print(li.get("api_base"), "timeout=",li.get("timeout"), "mpr=",li.get("max_parallel_requests"))'
```

### Auto-merge kill switches

Both must read enabled for any merge. Both fail closed on a value that is **present and
not exactly the enabled one** — empty, `false`, `0`, `TRUE`, malformed.

**Absence is not that case.** An absent per-repo `auto_merge` token means **on**
(decision 10 — the default is on and these switches turn it off), so an untouched
`pr-review-<repo>` row is **armed**, not disarmed. An absent `FABRO_AUTO_MERGE` variable
means no `pr-review` run is created at all — no merge, but no review either — which is
why `off` writes `0` rather than deleting it.

Mid-incident this is the distinction that matters: to disarm a repo you must write
`auto_merge=false`; finding no token there does not mean somebody already disarmed it.

```sh
# one repo (flip `auto_merge` in the pr-review-<repo> row's description)
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 ./ops/fabro-auto-merge-switch.sh andrewthetechie/<repo> off

# all four (the FABRO_AUTO_MERGE server variable)
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 ./ops/fabro-auto-merge-switch.sh host off
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro variable get FABRO_AUTO_MERGE'
```

The same script is deployed to the host at `~/bin/fabro-auto-merge-switch.sh`, so an
incident that starts in an ssh session does not also need a checkout:

```sh
ssh andrew@10.10.0.32
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 ~/bin/fabro-auto-merge-switch.sh host off
```

`DRY_RUN` defaults to 1 and prints the current value first, including `<absent>` —
which for the host switch means every `pr-review` run will fail to compile, not that
auto-merge is off.

Neither needs a deploy or a restart; both take effect on the next run. Neither undoes
a merge that already happened. Do **not** hand-build `PATCH /api/v1/automations/{id}`
(405) or add a `labels` object on `POST` (422) — the switch lives where fabro permits
it. Do not verify the host switch with `docker exec … echo $FABRO_AUTO_MERGE` — that
reads the server container, not the run sandbox; the hop that matters is
`/tmp/fabro/auto_merge` on a real run.

`provision-server-state.sh` reports drift rather than rewriting a row, so a hand-flipped
`auto_merge=false` survives a re-provision — the property an operator relies on
mid-incident.

`backlog-writers-app` carried **no `api:manual` trigger** until 2026-09-16, so it could
not be fired through the API at all — only its disabled schedule would have started it,
and the bridge was therefore live on three repos rather than four. Corrected in place
with `PUT /api/v1/automations/backlog-writers-app` (`If-Match` the row's ETag; the PUT
is a full replace, so the existing row is read, the trigger added to it, and the whole
thing sent back). All four rows now match.

Nothing reported that for however long it was true, because
`provision-server-state.sh` compared only `environment_id` on a row that already
existed. It now compares the trigger set as well, on the same report-do-not-correct
discipline as the rest of the script. A schedule's `enabled` flag is deliberately
excluded — it is the operator's to set — while `api:manual` being disabled is reported,
since a disabled trigger is exactly as dead as a missing one.

Every `backlog` schedule is **disabled** — an operator decision (2026-09-14) while
development and testing continue. The four schedules are **staggered** three minutes
apart (ADR 0001, implemented 2026-09-16) so a re-enabled fleet never fires four runs
in the same minute. The `pr-review` automations carry no schedule at
all by design: they are fired against a named PR, so there is nothing to poll.
Nothing in this deployment currently fires on a cron.

A `pr-review` run that reaches the merge phase holds a scheduler slot until CI settles,
bounded by the 60-minute merge budget. Because fabro queues rather than rejects
(ADR 0001), a `trigger_review` fire behind three merging runs is delayed, not dropped.
**Re-enabling the staggered backlog schedules should be accompanied by revisiting the
cap.** See `docs/adr/0001-concurrency-posture.md`.

~~Two drifts~~ The drift previously recorded here (`backlog-writers-app` missing its
`api:manual` trigger) was corrected by hand on 2026-09-16; the script now compares the
full trigger set and the schedule expressions, and reports zero drift.

`provision-server-state.sh` recreates the automations. The environments above are
created by hand (step 6).

## Upgrading the server

`FABRO_VERSION` in `~/fabro/.env` pins the image tag. Keep it pinned: `nightly` is a
moving tag, so an unpinned host upgrades itself on the next pull with no decision
behind it.

A fabro upgrade applies SQLite migrations to `/storage/db/fabro.sqlite3`, and the
previous binary **refuses to start** against a migrated database
(`migration <id> was previously applied but is missing in the resolved migrations`).
So rolling back is a database restore, not a tag change. Upgrade deliberately:

```sh
ssh andrew@<HOST>
cd ~/fabro
docker compose exec -T fabro fabro --version          # record the rollback point
docker compose ps                                     # confirm healthy before starting
sed -i 's/^FABRO_VERSION=.*/FABRO_VERSION=<new tag>/' .env
docker compose pull && docker compose up -d
sleep 10 && docker compose ps                         # must read "healthy", not "restarting"
docker compose logs --tail 40 fabro
```

Then re-validate both workflows against the new binary before trusting it — a version
bump can change parsing or validation:

```sh
docker compose exec -T fabro fabro validate /tmp/check/workflows/backlog/workflow.toml
docker compose exec -T fabro fabro validate /tmp/check/workflows/pr-review/workflow.toml
```

**Rollback**, when the new version crash-loops:

```sh
cd ~/fabro && docker compose stop
docker run --rm -v fabro_fabro-storage:/s alpine sh -c '
  cd /s/db
  cp -p fabro.sqlite3 fabro.sqlite3.migrated-<migration id>.bak   # keep the migrated db
  cp -p fabro.sqlite3.pre-migration.bak fabro.sqlite3
  chown 1000:1000 fabro.sqlite3 && chmod 600 fabro.sqlite3
  rm -f fabro.sqlite3-shm fabro.sqlite3-wal'
sed -i 's/^FABRO_VERSION=.*/FABRO_VERSION=<old tag>/' .env
docker compose up -d
```

fabro writes `fabro.sqlite3.pre-migration.bak` itself, immediately before migrating.
Removing the stale `-shm`/`-wal` files matters: they belong to the newer schema and
leave the restored database inconsistent. Pin to a **versioned** tag on the way back,
never a locally retagged `nightly` — a retag is invisible and the next pull silently
undoes it.

Known bad: nightly **0.357.x** applies migration `2026091101`, then crash-loops on
run-history activation with `stored run summary <id> has inconsistent field
summary_json`. Attempted and rolled back 2026-09-15; the host is pinned to
`0.354.0-nightly.0`.

## Cron — sweepers

```sh
( crontab -l 2>/dev/null; \
  echo '17 4 * * * DRY_RUN=0 /home/andrew/bin/fabro-branch-sweep.sh >> /home/andrew/.local/state/fabro-branch-sweep.log 2>&1' \
) | crontab -
crontab -l
```

04:17 daily. `DRY_RUN=0` (the script defaults to dry-run). Log rotates by hand when it
grows: `: > ~/.local/state/fabro-branch-sweep.log`.

```sh
( crontab -l 2>/dev/null; \
  echo '43 4 * * * DRY_RUN=0 /home/andrew/bin/fabro-sandbox-sweep.sh >> /home/andrew/.local/state/fabro-sandbox-sweep.log 2>&1' \
) | crontab -
```

04:43 daily, after the branch sweeper rather than alongside it — both are chatty and
neither is urgent.

## Cron — the health monitor

```sh
( crontab -l 2>/dev/null; \
  echo '7-59/15 * * * * DRY_RUN=0 /home/andrew/bin/fabro-monitor.sh >> /home/andrew/.local/state/fabro-monitor.log 2>&1' \
) | crontab -
```

Every 15 minutes at offset 7 — it never shares a minute with a backlog fire
(`2/5/8/11-59/15`) and lands between the triage fires (`:30/:35/:40/:45`).
`fabro-monitor.sh` is the third member of the sweeper pattern and the only
thing on the host that watches the *gaps between runs*: a dead container, a
dead API, a dead scheduler, a run wedged non-terminal, an empty `agent` queue
across the four repos, a full disk. It never alerts on a failed run — that
stays with the hooks (`discord-notify.sh`), which see the run context it
cannot (ADR 0004, `../docs/adr/0004-monitoring-is-out-of-band.md`).

- **Log:** `~/.local/state/fabro-monitor.log` — one `ok`/`skip` line per
  condition per run; a silent log means a dead monitor, not a healthy host.
  Rotate by hand when it grows.
- **State:** `~/.local/state/fabro-monitor.state` — one `C<n>=<epoch>` line
  per firing condition (dedup: re-alert 4h, starvation 7d, disk 24h). Absent
  or empty when nothing is firing; a clean tick writes no file at all.
- **Knobs** (force a condition without breaking anything real):
  `DISK_PCT=1` fires C6, `FABRO_PORT=1` fires C2, `IDLE_HOURS=0` fires C3
  once a schedule is enabled. `DRY_RUN=1` (the default) prints the alerts it
  would send and writes nothing.
- The condition table, thresholds, and message contract:
  `../docs/turn-it-on/00-overview-and-contracts.md`. An optional dead-man's
  heartbeat (`FABRO_HEARTBEAT_URL`) is supported by the script but **not
  configured — declined by the operator** (turn-it-on task 03 skipped): host
  death therefore looks like silence, and the runbook carries the manual
  two-minute disambiguation.

## Sandbox containers — fabro stops them, nothing removes them

Fabro creates one `fabro-run-<run_id>` container per run and stops it on terminal. It
never removes it. Measured 2026-09-16: **165 containers**, `docker system df` reporting
**16.17 GB reclaimable (94% of container storage)**, oldest three days old, one per run
forever. First sweep removed 148 and freed **8 GB** of disk — less than the 16.8 GB that
`docker ps --size` reports for writable layers, because that figure counts shared layers
once per container.

`stop_on_terminal` is **not** broken, which was the earlier reading of this. 164 of the
165 were `Exited (137)` with `FinishedAt` matching their run's `completed_at` to the
millisecond. The one still `Up` had stopped correctly at 15:19:11 on 2026-09-15 and was
started again **by hand** at 16:51:35 the same day, during the debugging session on the
run that exposed the pr-review reporting bug — `RestartPolicy=no`, `RestartCount=0`, so
Docker did not do it. There was no leak in fabro; there was a janitor missing from this
tree.

`fabro-sandbox-sweep.sh` is that janitor. It never touches a running container, by
design: it is either an in-flight run or a sandbox someone deliberately restarted to
look inside, and killing the second one silently is worse than leaving it. It reports
and moves on.

`/storage/scratch` inside the server container accumulates the same way — 178
directories, 9.6 MB. Left alone: at that rate it is not worth a script.

```sh
DRY_RUN=1 ~/bin/fabro-sandbox-sweep.sh                 # default: dry run, 48h grace
DRY_RUN=0 ~/bin/fabro-sandbox-sweep.sh                 # remove
DRY_RUN=0 GRACE_HOURS=168 ~/bin/fabro-sandbox-sweep.sh # keep a week of post-mortems
```

Skip rules (mechanical, no judgment): the name must be exactly
`fabro-run-<26-char ULID>`, which is what keeps `fabro-fabro-1` and this host's
unrelated `sandcastle-*` containers out of reach — a substring filter would not;
running containers are never touched; anything stopped within `GRACE_HOURS` is kept,
because the sandbox of the run that just failed is the one you want to open.

## Sweeper usage

```sh
DRY_RUN=1 ~/bin/fabro-branch-sweep.sh            # default: dry run, 24h grace
DRY_RUN=0 RUN_GRACE_HOURS=1 WORK_GRACE_HOURS=24 ~/bin/fabro-branch-sweep.sh  # backfill
FABRO_SWEEP_REPOS="owner/repo" ~/bin/fabro-branch-sweep.sh  # one repo only
```

Skip rules (mechanical, no judgment): heads of open PRs are never touched; branches
within grace are kept; `fabro/run/*` that actually changes the tree (non-empty diff vs
the default branch) gets the longer `WORK_GRACE_HOURS` (168 default); empty checkpoint
branches get the short `RUN_GRACE_HOURS` (24 default). Only `refs/heads/fabro/run` and
`refs/heads/fabro/meta` are ever touched — never widen the prefixes.
