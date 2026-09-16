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
| `docker-compose.yaml` | Runs the `fabro` server container (the only container that is `Up`). |
| `.env.example` | Env key names for the compose file. Copy to `.env` beside the compose file and fill in real values. |
| `settings.toml.example` | Server settings overlay (`/storage/.home/settings.toml` in the container): env catalog, model map, sandbox providers. |
| `profile-images/` | The four sandbox profile-image Dockerfiles, reconstructed from image layer history and **verified by rebuild** against the live images. |
| `provision-server-state.sh` | Recreates the eight **automations** — server state that lives in fabro's store, not in `settings.toml`. Without this a restored host has settings but nothing to run. It does **not** create environments; see step 6. |
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
   write the webhook URL to `/storage/secrets/discord_webhook_url`.
4. **Sweeper** — `cp fabro-branch-sweep.sh ~/bin/ && chmod +x ~/bin/fabro-branch-sweep.sh`, then install the cron (below).
5. **Profile images** — build all four; see `profile-images/README.md`.
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

**Automations** — two per target repo, one for each workflow, all resolving
`workflow_source` to `andrewthetechie/fabro-workflows@main` at fire time. Read back
from the live server 2026-09-16:

| id | target | environment | triggers |
|---|---|---|---|
| `backlog-jelly-swipe` | `andrewthetechie/jelly-swipe` | `python` | `api:manual` enabled, `schedule:every-15m` disabled |
| `backlog-lawncare-saas` | `andrewthetechie/lawncare-saas` | `python-node` | `api:manual` enabled, `schedule:every-15m` disabled |
| `backlog-womens-fantasy-sports` | `andrewthetechie/womens-fantasy-sports` | `ts` | `api:manual` enabled, `schedule:every-15m` disabled |
| `backlog-writers-app` | `andrewthetechie/writers-app` | `rust-node` | `api:manual` enabled, `schedule:every-15m` disabled |
| `pr-review-jelly-swipe` | `andrewthetechie/jelly-swipe` | `python` | `api:manual` enabled — **configuration only, never fired** |
| `pr-review-lawncare-saas` | `andrewthetechie/lawncare-saas` | `python-node` | `api:manual` enabled — **configuration only, never fired** |
| `pr-review-womens-fantasy-sports` | `andrewthetechie/womens-fantasy-sports` | `ts` | `api:manual` enabled — **configuration only, never fired** |
| `pr-review-writers-app` | `andrewthetechie/writers-app` | `rust-node` | `api:manual` enabled — **configuration only, never fired** |

The four `pr-review-*` rows are **not dead**. The bridge resolves each repo's
`environment_id` and `target` from its own `pr-review-<repo>` automation rather than
hardcoding a map, so a fifth repository needs no change to any graph, and deleting a row
silently breaks the bridge for that repo. They are read, never fired.

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
development and testing continue. The `pr-review` automations carry no schedule at
all by design: they are fired against a named PR, so there is nothing to poll.
Nothing in this deployment currently fires on a cron.

Two drifts from what `provision-server-state.sh` would create, both left as-is:
`backlog-writers-app` has no `api:manual` trigger, and the script reports
`environment_id` drift only, not trigger drift.

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

## Cron — branch sweeper

```sh
( crontab -l 2>/dev/null; \
  echo '17 4 * * * DRY_RUN=0 /home/andrew/bin/fabro-branch-sweep.sh >> /home/andrew/.local/state/fabro-branch-sweep.log 2>&1' \
) | crontab -
crontab -l
```

04:17 daily. `DRY_RUN=0` (the script defaults to dry-run). Log rotates by hand when it
grows: `: > ~/.local/state/fabro-branch-sweep.log`.

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
