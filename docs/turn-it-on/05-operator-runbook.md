# Task 05 — `~/.fabro-deploy/docs/OPERATOR-RUNBOOK.md`

**Depends on:** 04. **Blocks:** nothing (but 06 is nicer with it). **LLM level:** yes.

The private companion to the user guide: everything the guide deliberately omits.
Host addresses, paths, credential retrieval, recovery recipes. It lives at
`~/.fabro-deploy/docs/OPERATOR-RUNBOOK.md` — the existing mode-600 private tree beside
the deployment log — and **never in git**. Decision 2.

The discipline that makes this file safe to write: it is written *against the live
host*. Every command in it is executed (read-only) or verified by inspection during
this task. A runbook with a wrong path is worse than no runbook — it gets followed
mid-incident.

## Contents

### Access

- The host: `ssh andrew@10.10.0.32`. What the box is (trusted single-tenant), the
  compose project at `~/fabro`, the API on `:32276`, the web UI URL (read
  `FABRO_WEB_URL` from `~/fabro/.env`).
- The two Mac checkouts (`~/Documents/code/fabro-workflows` for working sessions,
  `~/.fabro-deploy/fabro-workflows` the deploy checkout — pull before, push after) and
  the private tree itself.

### Credentials — retrieval, not values

The runbook records **how to get** each secret, never the secret:

- fabro dev token: `ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/server.dev-token'`
  — one token, shared by the CLI, the bridge, `discord-notify.sh`, and the monitor;
  rotating it breaks all four (ops/README's secrets section).
- Discord webhook: `docker exec fabro-fabro-1 cat /storage/secrets/discord_webhook_url`.
- `GITHUB_TOKEN`, `LITELLM_API_KEY`, `FABRO_API_TOKEN`: fabro vault — listable by name,
  not readable; re-set with `fabro secret set` from the values in
  `~/.fabro-deploy/env` where present.
- The heartbeat URL: `~/fabro/.env` as `FABRO_HEARTBEAT_URL` (task 03).
- `~/fabro/.env` keys and what each feeds (`FABRO_VERSION` pin, `FABRO_PORT`, …).

### Layout

- Host: `~/fabro/` (compose, `.env`), `~/bin/` (two sweepers, the monitor, the
  auto-merge switch, the fire wrapper), crontab (all three entries), logs and state in
  `~/.local/state/`.
- Container: `/storage/` — `server.dev-token`, `secrets/discord_webhook_url`,
  `db/fabro.sqlite3` (plus the pre-migration backup convention), `.home/settings.toml`,
  `scripts/discord-notify.sh`, `scratch/`.

### Standing operations

- Fire a pr-review against a PR: `~/bin/fabro-fire-pr-review.sh andrewthetechie/<repo> <N>`
  (and the warning from AGENTS.md: never hand-build `POST /automations/<id>/runs` with
  a body — it drops the body and 422s).
- Fire a backlog run manually: the body-less curl (AGENTS.md has it; the runbook may
  carry the concrete host/port).
- Read a run: `fabro events <run> -p` inside the container; the web UI.
- Kill switches, concrete: the two auto-merge switches with real commands, disabling a
  schedule (the GET+PUT `If-Match` recipe from task 06), the blunt `FABRO_API_TOKEN`
  bridge kill.
- Monitor operations: force a condition (the env knobs), read the state file, mute the
  monitor (remove the cron line — and that doing so leaves the heartbeat alerting,
  which is correct).
- Incident entry points: server down (compose up, then `fabro validate` both — no,
  three — workflows), bad merge (the merge already happened: revert on the target
  repo, then kill switches), disk pressure (sandbox sweeper, branch sweeper), upgrade
  and rollback (pointer to ops/README's section — do not duplicate the runbook).

### Verification

A final section: the six drift checks from AGENTS.md plus the monitor's, as one block
to paste after any deploy.

## Acceptance

- Every command in the file was executed or inspected against the live host during
  this task.
- The file contains no secret *value* — retrieval commands only. Check by reading it
  as an attacker would: knowing everything in the runbook grants nothing without ssh.
- `~/.fabro-deploy/docs/` stays mode 600; the file is not copied, symlinked, or
  committed anywhere tracked.
