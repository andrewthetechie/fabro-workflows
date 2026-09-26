# Fabro upgrade 0.354 → 0.362: overview and contracts

**Status: applied 2026-09-25.** Tasks 01–05 are done. Task 06's documentation parts are done,
and its canary watch (Part E) is in progress. The host runs `0.362.0-nightly.0`.

**Read this file first.** Every task in this folder assumes the decisions, facts and rules in
this file. Each task repeats what it needs. If a task and this file disagree, this file is
correct: stop and report the conflict. Do not guess.

**When you write or change a `.fabro` file or a `workflow.toml`, invoke the `/fabro-workflow`
skill first.** This file adds only the facts specific to this upgrade.

This series moves the fabro server on `10.10.0.32` from **`0.354.0-nightly.0`** (build
`d6fc85b`, 2026-09-12, pinned since 2026-09-15) to **`0.362.0-nightly.0`** (tag commit
`a192bce20`, published 2026-09-20), and makes every workflow, the coder scheduler and the ops
tooling work on it. The operator's goal is to use newer fabro features, so this is a
**push-forward** upgrade: runs may break temporarily, and the only reason to roll back is a
fabro bug that makes 0.362 unusable.

Source: a grilling session on 2026-09-25, and a rehearsal the same day against a copy of the
live database (recorded below as findings R1–R6). The roadmap entry is
`docs/factory-roadmap/B8-fabro-upgrades.md`.

## Operator decisions (settled, do not change them)

| # | Decision |
|---|---|
| 1 | Target is exactly `0.362.0-nightly.0`, image `ghcr.io/fabro-sh/fabro:0.362.0-nightly.0` (verified to exist). Not `nightly`, not upstream `main`. Upstream `main` after 0.362 is a different engine (Petri) and is a later series. |
| 2 | **Fabro's run history is dropped** during the upgrade (finding R1). Secrets, variables, environments, automations, MCP servers and auth rows are **kept**. Before dropping, a slim JSON export of the `runs` table is saved for the scorecard backfill (`docs/factory-roadmap/B1`). |
| 3 | **No drain, no canary phase.** At cutover: stop the scheduler, cancel every in-flight run, do the work, start everything. The **first two `backlog` runs on each coder box are the canaries**, watched in task 06. |
| 4 | **Auto-merge stays on** throughout. Do not touch `FABRO_AUTO_MERGE` or the per-repo switches. |
| 5 | Commit identity for every run: **`andrews-ai-agent <andrews-ai-agent@users.noreply.github.com>`**, set as `[run.git.author]` in the server settings overlay and mirrored in `ops/settings.toml.example`. |
| 6 | Include the optional fixes this upgrade enables: `docs/factory-roadmap/H-housekeeping.md` **H5** (`[run.run_branch] enabled = false` on `issue-triage` and `arch-review`) and **H6** (the scheduler reads the sandbox through the fabro API, and the Docker-socket mount is removed), plus a re-check of `docs/research_improvements/04-confirmed-dead-ends.md` on 0.362. |
| 7 | **An LLM executes every task, host steps included.** Take the backups in task 01 and task 04 exactly as written, and keep the reversal runbook (task 01) current. Stopping services and breaking runs temporarily is allowed. The end state must work. |
| 8 | **Roll back only for a fabro bug that blocks the upgrade outright** (the server cannot start or cannot run any workflow, and no fix is available in configuration or in this repo). Otherwise fix forward. |
| 9 | **Nothing is posted upstream** (no GitHub issue on `fabro-sh/fabro`). |

## Findings (verified 2026-09-25 by rehearsal on a copy of the live database)

| # | Finding | Consequence |
|---|---|---|
| R1 | 0.362's startup migration `sqlite_run_history_activation` re-serialises **every** stored run summary through the new `Run` type and compares it byte-for-byte with `runs.summary_json`. 0.362 renamed `Run.billing` to `Run.usage` (`lib/foundation/fabro-types/src/run_summary.rs`), so all 809 rows mismatch, and startup fails with `stored run summary 01M2DNC05XWQ5B0N4KD491910N has inconsistent field summary_json` (the oldest row, checked first). This is the same failure that stopped the 0.357 attempt on 2026-09-15. The check is unchanged on the whole 0.357–0.362 line. | Delete all rows from `runs` and `run_events` (and `run_session_records` if it exists) **before** the first 0.362 start. Activation then passes: `source_runs=0 … target_runs=0`. |
| R2 | 0.362's catalog loader (commit `3e065b806`, "migrate catalogs to the codecs schema") **rejects** the key `codec` and the adapter ids `openai-compatible`, `openai`, `anthropic`, `gemini`. On a cold start the server **refuses to boot**: `catalog layer settings [llm] produced an invalid catalog … provider box-a: codec = "openai-chat" is now codecs = ["openai-chat"]`. The live overlay uses them in `kimi`, `box-a`, `box-b` and `spark`. | Rewrite those four providers (contract C1) before the first 0.362 start. |
| R3 | With R1 and R2 applied, 0.362 starts on the copy with all 16 automations, 5 environments, 2 variables and 5 secrets intact. | The upgrade is feasible in place. |
| R4 | `fabro validate` on 0.362 reproduces the `AGENTS.md` baselines **exactly**: `Backlog (61 nodes, 143 edges)` with the one `issue_number` warning, `PrReview (30 nodes, 65 edges)` with the one `pr_number` warning, `IssueTriage (13 nodes, 26 edges)` clean, `ArchReview (21 nodes, 44 edges)` clean. `fabro parse` still exists, so `ops/check-routing-schemas.py` keeps working. | No graph change is required to run on 0.362. |
| R5 | On 0.362 every stylesheet model id resolves to the intended provider: `coders-a`→`box-a`, `coders-b`→`box-b`, `long-context`→`spark`, `glm-5.3` (alias `high-reasoning`) and `glm-5.3-flash`→`zai`, `kimi-k3` and `kimi-for-coding`→`kimi`. 0.362 ships new built-in providers that also carry `glm-5.3` and `kimi-k3` (`fireworks`, `openrouter`, `vercel`, `venice`). All are **unconfigured** (no key), so they do not compete. | New invariant (task 06): never store a key for those providers without re-checking resolution with `fabro model test`. |
| R6 | The scheduler's fabro API surface is unchanged: `POST /workflow-versions`, `POST /runs` with a `RunIntent` (the schema is identical in both versions; only the legacy manifest body was removed, and the scheduler never used it), `POST /runs/{id}/start`, `GET /runs/{id}`, `/events`, `/stages`, `/cancel`. The event names it reads (`run.failed`, `stage.failed`, `run.completed`, …) and the `Run` fields the tooling reads (`repository.origin_url`, `sandbox.instance.runtime.id`, `labels`) are unchanged. Only `billing`→`usage` changed (API: `/runs/{id}/billing`→`/runs/{id}/usage`), and nothing here reads billing. | No scheduler change is *required*. H6 is the only scheduler change, and it is by choice. |

## Behaviour changes to expect on 0.362 (read before debugging a canary)

1. **New agent runtime.** Every agent stage now runs on pebble's `CodingAgent`
   (`handler/llm/pebble.rs`; 0.354 used fabro's own `api.rs` loop). Tool sets per provider
   profile, loop detection, memory and skill discovery, MCP and failover all come from pebble.
   Anthropic-codec providers (`kimi`) get the Anthropic tool profile (`edit_file`), and
   OpenAI-chat providers (`box-*`, `spark`, `zai`) get the OpenAI profile. `[run.model.fallbacks]`
   still applies. The event `agent.failover` is now `agent.route.failover` on agent stages and
   `prompt.failover` on prompt stages.
2. **A checkpoint commit failure now stops the run** (it was best-effort on 0.354).
   Intermediate push failures are still warnings. A required final publish failure fails the
   run.
3. **Metadata branches are gone.** `[run.meta_branch]` is accepted and ignored. No new
   `fabro/meta/*` branches are created, and existing ones stay until `fabro-branch-sweep.sh`
   removes them.
4. **One git identity per run.** Without `[run.git.author]`, fabro derives it from the GitHub
   token's user and **fails the run at setup** if that lookup fails. Decision 5 sets it
   explicitly, which also skips the lookup.
5. **Usage replaces billing** in the API, the web UI tab and the event payloads
   (`usage.tokens.{input,output,reasoning,cache_read,cache_write}`).
6. **`GET /api/v1/settings` returns only the `server.*` layer.** It cannot show a `run.*`
   value such as `run.git.author`. Read a run's resolved settings with
   `GET /api/v1/runs/{id}/settings` instead.

## Canonical contracts

### C1. The settings overlay's `[llm]` providers on 0.362

The overlay lives **inside the fabro volume** at `/storage/.home/settings.toml` (read it with
`docker exec fabro-fabro-1 cat /storage/.home/settings.toml`). Provider keys live in the vault,
not in this file. After the upgrade these four tables must read:

```toml
[llm.providers.kimi]
display_name = "Kimi (coding plan)"
base_url = "https://api.kimi.com/coding"
enabled = true
codecs = ["anthropic-messages"]
auth = { type = "bearer" }

[llm.providers.box-a]
display_name = "Local coder A (10.10.0.29)"
base_url = "http://10.10.0.29:8000/v1"
auth = { type = "none" }

[llm.providers.box-b]
display_name = "Local coder B (10.10.0.56)"
base_url = "http://10.10.0.56:8000/v1"
auth = { type = "none" }

[llm.providers.spark]
display_name = "Local long-context (10.10.0.30)"
base_url = "http://10.10.0.30:8888/v1"
auth = { type = "none" }
default_model = "long-context"
```

Everything else in the overlay is unchanged, including the model rows under each provider,
`[llm.providers.moonshot] enabled = false`, `[server.scheduler] max_concurrent_runs = 4` and
`[server.slatedb]` (still valid at 0.362). `codecs` defaults to `["openai-chat"]`, so the three
local boxes need no codec line. The exact edit that the rehearsal proved (run it against the
file, then `diff` it against the backup):

```sh
sed -i \
  -e '/^adapter = "openai-compatible"/d' \
  -e '/^codec = "openai-chat"/d' \
  -e '/^adapter = "anthropic"/d' \
  -e 's/^codec = "anthropic-messages"/codecs = ["anthropic-messages"]/' \
  settings.toml
```

### C2. Commit identity (decision 5)

Appended to the overlay, and mirrored in `ops/settings.toml.example`:

```toml
[run.git.author]
name  = "andrews-ai-agent"
email = "andrews-ai-agent@users.noreply.github.com"
```

Read it back on a run, not on the server: `GET /api/v1/runs/{id}/settings` shows the author
(behaviour change 6), and `fabro events <id> -p` prints `Git identity: … explicit`.

### C3. Run-history drop (decision 2, finding R1)

Run with **fabro stopped**, against `/storage/db/fabro.sqlite3`, from a helper container
(`alpine:3` plus `apk add sqlite`, the fabro volume mounted). On a 0.354 database the table
`run_session_records` does not exist yet (migration `2026091101` creates it), so guard it:

```sql
BEGIN;
DELETE FROM run_events;
DELETE FROM runs;
COMMIT;
-- only if it exists (it does on a database a 0.357+ binary has already started against):
-- DELETE FROM run_session_records;
VACUUM;
```

`VACUUM` shrinks the 1.6 GB file. It takes minutes, and that is expected.

### C4. Host paths used by this series

| Path (host `10.10.0.32`) | Written by | Contents |
|---|---|---|
| `~/fabro-archive/0354/fabro.sqlite3` | task 01 | online `.backup` of the live database |
| `~/fabro-archive/0354/settings.toml` | task 01 | the 0.354 overlay |
| `~/fabro-archive/0354/runs-export.json` | task 01 | slim `runs` export (decision 2) |
| `~/fabro-archive/0354/env` | task 01 | copy of `~/fabro/.env`, **mode 600** (it holds `SESSION_SECRET`) |
| `~/fabro-archive/0354/docker-compose.yaml` | task 01 | the 0.354 compose file |
| `~/fabro-archive/0354/fabro-storage.tar.gz` | task 04 | full volume tarball, taken with fabro **stopped** |
| `~/fabro-archive/0354/REVERSAL.md` | task 01 | the reversal runbook, filled in with real paths |

`~/fabro-archive/` is outside this repository and outside any volume, and nothing sweeps it.

## Rules every task must follow

1. **This repository is public.** No token, key, webhook URL or `SESSION_SECRET` goes into a
   tracked file, a commit message or a doc. `~/fabro/.env` and the vault stay on the host.
2. **Pushing to `main` deploys** the graphs on the next run. Only push `workflow.toml` and
   graph edits that are valid on the version the host is running *at that moment*. Task 03's
   changes are valid on both versions. Task 05's are valid only on 0.362. **Never delete
   `[run.meta_branch]` while the host runs 0.354**: on 0.354 that re-enables metadata-branch
   pushes.
3. **`docker compose up -d` with no service name recreates `fabro`**, and a fabro restart
   fails every in-flight run. Name the service (`up -d --build scheduler`) unless the task says
   to restart fabro.
4. **After any overlay edit** on a running server, confirm
   `docker logs --since 1m fabro-fabro-1 | grep -c "Rejected reloaded"` prints `0`. A rejected
   hot reload keeps the old catalog and reports healthy while new workers die (`AGENTS.md`).
5. **Validate in the container**, as `AGENTS.md` "Validating" describes (rsync `.fabro/` to
   the host, `docker cp` it into the container, then `fabro validate` and the routing checker).
   Offline gates first: `./ops/test-task-gates.sh`, the `tomllib` parse of every
   `workflow.toml`, `sh -n` on `ops/*.sh`.
6. **Commit to `main` directly**, one commit per task, Conventional Commits subjects, ending
   with the attribution lines your session requires. Pull first. There are two checkouts
   (`AGENTS.md`, "The two checkouts"), so pull `~/.fabro-deploy/fabro-workflows` at the end
   too.
7. **Record what you did.** Task 06 appends one dated section to
   `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac (mode 600, not in this repo).
   Earlier tasks keep notes in their own report so task 06 can write it.

## Task index

The tasks run in order, one at a time.

| # | File | Where | Blocked by |
|---|---|---|---|
| 01 | `01-backups-and-reversal.md` | host | none |
| 02 | `02-rehearsal.md` | host | 01 |
| 03 | `03-repo-changes-before-cutover.md` | repo, verified on the live host | none (run it after 02 so the rehearsal result is known) |
| 04 | `04-cutover.md` | host | 01, 02, 03 |
| 05 | `05-repo-changes-after-cutover.md` | repo | 04 |
| 06 | `06-docs-and-canary-watch.md` | repo and host | 05 |

## Definition of done (the whole series)

- The host runs `0.362.0-nightly.0` (`docker exec fabro-fabro-1 fabro --version`), healthy,
  with `FABRO_VERSION=0.362.0-nightly.0` in `~/fabro/.env`.
- All offline gates pass. `fabro validate` of all four packages matches finding R4.
  `check-routing-schemas.py` is clean. `fabro model test` passes for every model id named by a
  stylesheet.
- The scheduler runs without the Docker socket and shows task progress on its page.
- The first two `backlog` runs on each coder box reach `open_pr` (or a legitimate block such as
  red CI or `architecture`) with no fabro-caused failure, and their commits carry the
  `andrews-ai-agent` identity.
- `AGENTS.md`, `ops/README.md`, `ops/settings.toml.example` and
  `docs/research_improvements/04` describe 0.362. The deployment log has its entry.
