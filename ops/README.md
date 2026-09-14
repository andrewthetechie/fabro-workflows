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
| `profile-images/` | Reference for the Docker sandbox profile images. **Gap:** the exact Dockerfiles are not persisted on the host; see that README. |
| `README.md` | This file — bring-up, install, and replication steps. |

## Secrets — where they live, never in this repo

- `FABRO_DEV_TOKEN`, `SESSION_SECRET`, `FABRO_LITELLM_KEY`, `GITHUB_TOKEN` →
  `~/.fabro-deploy/env` on the Mac (chmod 600) and the fabro vault.
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
5. **Profile images** — see `profile-images/README.md`.
6. **Contract scripts** (`.fabro/setup.sh`, `.fabro/ci.sh`) live in each target repo,
   not this repo.

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
