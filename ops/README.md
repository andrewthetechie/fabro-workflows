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
| `docker-compose.yaml` | Runs both containers: the `fabro` server and the `scheduler` (the coder scheduler, built from `./scheduler/`). It is also the only place the scheduler's port and volume are declared. |
| `.env.example` | Env key names for the compose file. Copy to `.env` beside the compose file and fill in real values. |
| `settings.toml.example` | Server settings overlay (`/storage/.home/settings.toml` in the container): env catalog, model map, sandbox providers. |
| `scheduler/` | The **coder scheduler**: a FastAPI service that owns admission to the two coder instances, so four uncoordinated automations stop fighting over two single-slot boxes. Reads `repos.toml`, serves `/health`; the GitHub client, the fabro client and the queue are drafts 06, 07 and 08. Deploy and verify under *The coder scheduler* below. |
| `profile-images/` | The four sandbox profile-image Dockerfiles, `build-images.sh` (clone, warm from real lockfiles, build, verify), `fabro-pg-ensure.sh` (in-sandbox PostgreSQL, because fabro cannot start a service beside a sandbox) and `warm-build-backend.sh`. Start at its `README.md`. |
| `provision-server-state.sh` | Recreates the twelve **automations** (three per repo: `backlog`, `pr-review`, `issue-triage`) — server state that lives in fabro's store, not in `settings.toml`. Without this a restored host has settings but nothing to run. It does **not** create environments; see step 6. |
| `provision-litellm-models.sh` | Creates the `high-reasoning` model group and its `kimi-k3` fallback in LiteLLM. Scoped to that one group; the other model rows predate this workflow and are reported, never corrected. The `coders` pool's concurrency tuning is recorded under *Server-side state*, not managed here. |
| `provision-coder-groups.sh` | Creates the per-box `coders-a` and `coders-b` model groups that the coder scheduler pins a run to, and adds both names to the fabro key's model allowlist. Leaves the load-balanced `coders` rows alone. Idempotent, `DRY_RUN=1` by default. |
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
11. **Coder scheduler** — `rsync -a --delete ops/scheduler/ andrew@<HOST>:~/fabro/scheduler/`
   then `cd ~/fabro && docker compose up -d --build scheduler`. It builds
   `fabro-scheduler:local` from `./scheduler`, resolved relative to the compose file, which
   is why the tree has to sit beside it. Nothing from steps 3 or 9 is needed for `/health`;
   the GitHub and fabro credentials are drafts 06 and 07's. See *The coder scheduler*
   below.

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

**LiteLLM coder deployments** — four rows, all
`openai/deepseek-v4-flash-0731-iq3-xxs`: the load-balanced `coders` group, and
one pinned group per box.

| model_name | deployment | api_base | timeout | max_parallel_requests |
|---|---|---|---|---|
| `coders` | `ee9cf2cb…` | `http://10.10.0.29:8000/v1` | **600.0** | 1 |
| `coders` | `c4504835…` | `http://10.10.0.56:8000/v1` | **600.0** | 1 |
| `coders-a` | `8be38bd3…` | `http://10.10.0.29:8000/v1` | **600.0** | 1 |
| `coders-b` | `ba5c2fc4…` | `http://10.10.0.56:8000/v1` | **600.0** | 1 |

`coders-a` and `coders-b` were created 2026-09-18 by
`./provision-coder-groups.sh`. They exist so the coder scheduler can pin a run to
one box by name — a run input selects a stylesheet model, and a model is the only
per-run routing lever fabro has (`docs/scheduler/00-overview-and-contracts.md`,
finding 3). The two `coders` rows are untouched and remain the fallback for every
unpinned and hand-fired run.

`timeout` was `120.0` on both `coders` rows until 2026-09-18. fabro's coder turns
averaged **95 seconds**, so the gateway was cutting the long ones — that is where the
`502 Bad Gateway` responses on `coders` came from, and the agent then retried the
whole turn. 600s is generous against the measured turn distribution while still
releasing llama.cpp's single slot on a genuinely hung upstream.

`max_parallel_requests = 1` mirrors llama.cpp's one slot per box: whoever holds
it blocks everything else for a full turn. `coders` itself still has no
affinity, so with two concurrent runs a request routinely lands on the slot the
other run is holding and waits out its whole turn. Sandcastle avoided this by
locking one repo to one instance; the two new groups are the same fix, applied
per run by the scheduler rather than per repo.

Two things about these rows are invisible to every read API, and a third gates
whether a run can reach them at all. All three break a row silently rather than
loudly:

- **`litellm_params.api_key` is mandatory and `GET /v1/model/info` never shows
  it.** The boxes are plain-http llama.cpp with no auth, so the value is a
  placeholder — but a row built from the `/v1/model/info` view alone has no
  `api_key` field at all, and then *every* call to it fails with
  `500 litellm.AuthenticationError: The api_key client option must be set`.
  That happened on the first pass of `provision-coder-groups.sh`, which copied
  the nine visible fields and watched both new groups answer 500. `/model/new`
  with a dummy `api_key` fixes it; confirm with a real completion, not a listing.
- **A virtual key's `models` allowlist gates model names independently of the
  rows.** `LITELLM_FABRO_KEY` listed seven models and neither new one, so a run
  pinned to a box would have failed with `403 key not allowed to access model`
  — verified 2026-09-18 by calling `deepseek`, a model that exists, with that
  key. Both names are now on the list. `provision-litellm-models.sh` documents
  the same trap for `high-reasoning`, which cost a live `issue-triage` fire on
  2026-09-16.
- **fabro will not route to a model its own catalog does not list**, and these
  rows are *not enough on their own* to pin a run. LiteLLM serves `coders-a`, but
  a run that asks for it fails before any call is made: `fabro model test -p
  litellm -m coders-a` answers `× Unknown model: coders-a` while only the LiteLLM
  rows exist (verified 2026-09-18). Both names need an
  `[llm.providers.litellm.models."…"]` block in `settings.toml.example` *and* in
  the live `/storage/.home/settings.toml`; both are there now, with `api_model`
  equal to the LiteLLM `model_name` and `limits`/`capabilities` copied from
  `coders`. `fabro validate` does not check the catalog — a stylesheet naming an
  unlisted model passes it — so validation cannot stand in for these blocks.
  Full recipe: `docs/issue-triage/02-fabro-model-catalog.md`.

  **These two entries need no restart.** `max_concurrent_runs` is the exception,
  not the rule: `replace_runtime_settings` (the 5s settings poll) rebuilds the LLM
  `catalog` along with the other run defaults, and only `max_concurrent_runs` is
  copied out once at startup. Verified 2026-09-18 by adding both blocks to the live
  overlay with `docker inspect -f '{{.State.StartedAt}}'` unchanged at
  `2026-09-17T15:31:24Z`, then watching a run pinned to `coders-b` reach its coder
  stage and call the box. The restart paragraph above is about the cap.

**How a run asks for a box.** Both root stylesheets carry the same two rules:

```
.coder  { model: {{ inputs.coder_pool | default('coders') }}; reasoning_effort: medium; }
.rebase { model: {{ inputs.coder_pool | default('coders') }}; reasoning_effort: medium; }
```

`backlog` has both, `pr-review` only `.rebase` (its `review`/`fix` classes stay on
`glm-5.3`). The scheduler will pass `args.inputs.coder_pool = "coders-a"` on the intent;
a hand-fired run passes nothing and gets the load-balanced `coders` group, which is why
that group is retained. The `default()` is not cosmetic — the stylesheet renders strict
at run time, so without it a fire carrying no `coder_pool` fails at `POST /runs` with
`422 run_compile_invalid`. Verified 2026-09-18 by registering the identical package with
the filter removed: `201` on the version, then `422 run_compile_invalid`
("run intent could not be compiled: Validation failed") on a no-input fire, against
`201` for the real package.

Changing any of this needs the **master key**, not `FABRO_LITELLM_KEY`: that one is an
`internal_user` and `POST /model/update` answers 403. The master key is the
sealed secret `litellm-secrets.masterkey`, readable from the running pod:

```sh
kubectl -n litellm exec deploy/litellm -- printenv PROXY_MASTER_KEY
```

Send the **full** current `litellm_params` with only the field you mean to
change. `POST /model/update` replaces the map, so a partial body drops `api_base`,
`api_key` and `max_parallel_requests`.

Do not rebuild that map from `/v1/model/info`: it omits `api_key`, and a row
without one lists fine and 500s on every call. `GET /credentials/by_model/{id}`
is not a substitute either — it is empty for all four coder rows even though
their `litellm_params.api_key` is set. The field's *presence* (not its value,
which is encrypted at rest) is visible only in the database:

```sh
kubectl -n litellm exec litellm-db-0 -- psql -U litellm -d litellm -A -F'|' -c \
  "select model_name, k from \"LiteLLM_ProxyModelTable\", jsonb_object_keys(litellm_params) k \
   where model_name like 'coders%' order by 1, 2;"
```

A z.ai row additionally has a real credential behind it, and rebuilding it from
`/model/info` output silently creates a model with no key.

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

The cap is **4**, raised from 2 on 2026-09-18 alongside the coder scheduler that now
owns admission (ADR 0005). It is a backstop, not an allocator: it stops a runaway, it
does not choose what runs. `issue-triage` no longer consults it at all — its
`check_capacity` stage was deleted in the same change, because every triage stage
resolves to `high-reasoning` and never to a coder instance, so the old check stood down
whenever *any* two runs existed. The value is server state, not a graph change: it lives
in the settings overlay, so changing it means editing `/storage/.home/settings.toml` in
the container and `docker compose restart fabro` — and that restart fails every in-flight
run (`docs/scheduler/00-overview-and-contracts.md`, finding 5), which is why it belongs
in a quiet window.

**Editing the overlay without restarting is worse than not editing it, because the API
will say it worked.** fabro polls `settings.toml` every 5 seconds and calls
`replace_runtime_settings`, which re-reads the file and republishes the *reported*
settings — so `GET /api/v1/settings` starts answering 4 within seconds of the edit. But
that function rewrites only five things (`manifest_run_defaults`, `manifest_run_settings`,
`server_settings`, `effective_web_url`, `catalog`). `max_concurrent_runs` is not one of
them: it is copied out of the resolved settings **once, at startup**
(`serve.rs:723` → `build_app_state`), stored as a plain `usize` on `AppState`, and read
only by the admission loop (`server.rs:4524` at `v0.354.0-nightly.0`). So after an
in-place edit the reported cap and the enforced cap disagree, and `GET /settings` — the
obvious way to check — is the one endpoint that cannot tell you.

Two consequences. First, the change that raised this cap was staged on 2026-09-18 and
the restart is the operator's to take, so **check the start time before assuming 4 is in
force** — the overlay has said 4 while the server enforced 2 for exactly this reason.
Second, do not verify the cap from `/settings`. Verify that the process is newer than
the file it read:

```sh
ssh andrew@<HOST> 'docker inspect -f "{{.State.StartedAt}}" fabro-fabro-1; \
  docker exec fabro-fabro-1 stat -c "%y" /storage/.home/settings.toml'
```

The container's start time must be **later** than the overlay's mtime; if it is earlier,
the file has been edited and the change is not in force. (Note the reloader *does*
rebuild the LLM `catalog`, so `[llm.providers.litellm.models.*]` entries — unlike the
cap — do take effect without a restart. Verified 2026-09-18: `coders-a`/`coders-b`
were added to the live overlay with `StartedAt` unchanged at `2026-09-17T15:31:24Z`,
and seconds later `fabro model test -p litellm -m coders-a` answered `ok` where it had
answered `Unknown model: coders-a` before.)

Confirmation that the enforced cap is still 2, and what it looks like when it is
reached — observed 2026-09-18, the day the overlay was edited to 4:

```
GET /api/v1/system/info   ->  {"active": 3, "scheduler_slots_used": 2, "total": 12}
```

A third run stays `runnable` with `queue_position: null` while two are `running`, and
nothing reports an error. `scheduler_slots_used` is the count that is capped; `active`
also counts the queued ones.

~~Two drifts~~ The drift previously recorded here (`backlog-writers-app` missing its
`api:manual` trigger) was corrected by hand on 2026-09-16; the script now compares the
full trigger set and the schedule expressions, and reports zero drift.

`provision-server-state.sh` recreates the automations. The environments above are
created by hand (step 6).

## The coder scheduler

`ops/scheduler/` — a FastAPI service that owns admission to the two llama.cpp coder
instances. It inventories `agent`-labelled issues from GitHub, orders them, and creates
one `backlog` run at a time, so four automations on independent schedules stop fighting
over two single-slot boxes. Decisions and the contracts each later draft consumes:
`../docs/scheduler/00-overview-and-contracts.md`.

Today it is the **skeleton** (draft 04): it reads `repos.toml`, serves `/health`, and does
nothing else. There is no GitHub client, no fabro client and no queue yet. Nothing an
automation reads changes when this tree changes — it is `ops/`, not `.fabro/`.

It is a second service in the **same compose project**, so `docker compose ps` shows the
whole factory in one place and a host rebuild brings it back with everything else.

| File | What it is |
|---|---|
| `scheduler/repos.toml` | The work list: one `[[repo]]` table per target repository. `priority` is a signed integer and **smallest wins**; `environment_id` names a fabro environment; `enabled = false` keeps the row out of scheduling. |
| `scheduler/src/fabro_scheduler/config.py` | The parser. Fails loudly: a duplicate repo name, an unknown key, a missing `priority`, a `true` where an integer belongs — each is an error naming the key, never a silently-applied default. |
| `scheduler/src/fabro_scheduler/app.py` | `/health` and the entrypoint. Validates the config *before* binding the port, so a broken file fails the container healthcheck instead of serving nothing. |
| `scheduler/tests/` | `uv run pytest` from `ops/scheduler/`. Pure — no network, no container, no host. |
| `scheduler/Dockerfile` | Two stages. Runs as uid 1000, bakes `repos.toml` in, and creates `/data` for the SQLite file drafts 06+ will use. |

### Deploying it

```sh
# a local `uv run pytest` leaves a `.venv` holding Mac binaries. It never reaches the
# image (.dockerignore), but there is no reason to send it to the host on every deploy.
rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/
scp ops/docker-compose.yaml   andrew@10.10.0.32:~/fabro/docker-compose.yaml
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d --build scheduler'
```

`up -d scheduler` names **one** service, so it never recreates `fabro` — which matters,
because a fabro restart fails every in-flight run. `--build` is what picks up a code or
`repos.toml` change; without it compose reuses the `fabro-scheduler:local` image that is
already there. `rsync --delete` is safe because nothing in `~/fabro/scheduler/` is
generated on the host.

### Verifying it

```sh
ssh andrew@10.10.0.32 'cat ~/fabro/docker-compose.yaml'   | diff - ops/docker-compose.yaml
ssh andrew@10.10.0.32 'cat ~/fabro/scheduler/repos.toml'  | diff - ops/scheduler/repos.toml
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose ps scheduler'
curl -fsS http://10.10.0.32:32280/health | jq -r '.repos[] | "\(.priority)  \(.name)"'
```

The last one prints the four rows in scheduling order. The port is **32280**, beside
fabro's 32276; both are LAN-only and unauthenticated (decision 16), consistent with
fabro's own API. `/health` therefore publishes neither `fabro_api_url` nor anything from
the container environment.

A duplicate repo name is a hard failure, not last-wins, and it is worth seeing once:

```sh
ssh andrew@10.10.0.32 'printf "[[repo]]\nname=\"o/a\"\npriority=0\nenvironment_id=\"python\"\n[[repo]]\nname=\"o/a\"\npriority=1\nenvironment_id=\"ts\"\n" > /tmp/bad.toml \
  && docker run --rm -v /tmp/bad.toml:/tmp/bad.toml:ro fabro-scheduler:local \
     python -m fabro_scheduler --config /tmp/bad.toml; echo "exit=$?"'
```

That prints `repo[1].name: duplicate repo 'o/a', already declared at repo[0]` and
`exit=1`.

### Configuring it

Everything that is not the repo list comes from the environment, because `env_file:` is
how this project already passes per-host values:

| Variable | Default | What it does |
|---|---|---|
| `SCHEDULER_PORT` | `32280` | Both the host port compose publishes and the port the container binds. **One** variable on both sides, resolved in `environment:` as well as in `ports:`, so a value set in the shell cannot leave the mapping pointing at a closed port. |
| `FABRO_API_URL` | `http://10.10.0.32:32276/api/v1` | The base URL the fabro client (draft 07) calls. Same name and shape `fire-pr-review.sh` uses. |
| `FABRO_API_TOKEN`, `GITHUB_TOKEN` | — | Neither is in `~/fabro/.env` today: `fabro secret list` holds both in the **fabro vault**, and that file carries only `FABRO_PORT`, `FABRO_VERSION`, `FABRO_WEB_URL` and `SESSION_SECRET`. So the `env_file:` block does not actually deliver them, and drafts 06 and 07 have to decide where the scheduler reads them — copy them into `.env`, or have it read the vault through the API. This row is the reminder. Neither belongs in `repos.toml`; this repository is public. |

The `env_file:` block as written hands this container everything in `~/fabro/.env`, which
today means `SESSION_SECRET` — fabro's session-cookie signing key — for a service that has
no use for it. Narrowing that is part of the same decision drafts 06 and 07 owe; it is
called out here rather than left to be discovered.

`coders-a` and `coders-b` — the two per-box LiteLLM groups — are deliberately not
configurable: they are decision 12's shape for this service, and `/health` reports them so
there is no doubt which pair a run will be pinned to. The load-balanced `coders` group is
absent on purpose; dispatching through it would reintroduce the contention the scheduler
exists to remove.

`fabro-monitor.sh` does not watch this container yet — draft 12 adds the conditions.

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
