# Fabro Turn-It-On — Overview & Canonical Contracts

**Read this first.** Every task in this folder assumes the decisions, findings, and
contracts recorded here. This document is the single source of truth.

## What this is

The last stage of the fabro factory: making it a system you *operate* instead of a
system you *test*. Three workstreams, one series:

1. **Monitoring** — an out-of-band health layer on the host, so a dead server, a dead
   or wedged scheduler, a stuck run, an empty work queue, or a full disk surfaces in
   Discord instead of in a post-mortem.
2. **User guide** — the secrets-free operating manual (`docs/USER-GUIDE.md`) and its
   private companion, the operator runbook (`~/.fabro-deploy/docs/OPERATOR-RUNBOOK.md`).
3. **Turn-on** — enable the schedules, watch a canary repo produce real PRs for five
   days, then expand to the full fleet.

Everything before this stage built and proved the pipeline (issue → triage → PR →
review → merge). Nothing before it answered: *how does the operator know it is working?*

This series changes **no workflow graph** and no prompt. Nothing in it is live-on-merge.
The one deployable artifact (`ops/fabro-monitor.sh`) is installed by hand, exactly like
the two sweepers.

## Operator decisions (settled — do not re-litigate)

| # | Decision |
|---|---|
| 1 | The user guide is `docs/USER-GUIDE.md` in this repo — public, secrets-free, linked from AGENTS.md. Not the root README (oversells it to strangers), not the private tree (buys nothing — it holds no secrets). |
| 2 | The private companion is `~/.fabro-deploy/docs/OPERATOR-RUNBOOK.md` — host addresses, paths, credential-retrieval commands. It lives in the existing mode-600 tree beside the deployment log, not in `.scratch/`, which AGENTS.md defines as ephemeral working notes. |
| 3 | The taught path for adding work is: file an issue, label it **`needs-triage`**. Labeling `agent` directly is the documented expert shortcut. There is no required issue body format — triage exists to fix under-specified issues. |
| 4 | The monitor is a **host cron script** (`ops/fabro-monitor.sh` → `~/bin/fabro-monitor.sh`), the third member of the sweeper pattern. Not a fabro workflow (circular: a dead server means a dead monitor), not a SaaS (a new dependency and a new secret for a one-user system). |
| 5 | The monitor is **out-of-band health only**. It never alerts on a failed run — failure alerting stays hook-owned (`discord-notify.sh`'s `failed`, `triage-failed`, `rescue` kinds). Double pings train the operator to ignore the channel; removing the hooks would lose their enrichment. See ADR 0004. |
| 6 | "Nothing is happening" is **two different alerts**: dead scheduler (breakage — go fix the server) and starvation (zero `agent`-labeled issues anywhere — go write issues). Different thresholds, different text, different data sources, different responses. |
| 7 | One Discord channel, the existing webhook. Per-condition dedup in a state file: re-alert every 4h while a condition persists (7d for starvation, 24h for disk), one ✅ resolved message when it clears. `DRY_RUN=1` default, matching the sweepers. |
| 8 | A dead host is silent by construction, so the monitor gets a heartbeat: an external dead-man's ping (healthchecks.io free tier) on every run; absence of the ping is the host-death alert. The ping URL is a capability secret in `~/fabro/.env`, never in this repo. **Declined by the operator at task 03** — skipped deliberately; the runbook carries the manual host-death disambiguation instead. |
| 9 | Turn-on canary: all four `issue-triage` schedules **plus `backlog-jelly-swipe`**. jelly-swipe has real work (11 `agent` issues seeded 2026-09-16), branch protection, and no deploy-on-merge. The other three `backlog` schedules stay off until the observation window passes. |
| 10 | The observation window is a **human task**: five days, a daily checklist, a one-line deployment-log entry per day, explicit exit criteria gating expansion, explicit stop conditions disarming the canary. It is the one task in this series no agent executes. |
| 11 | Fleet expansion is **queue-driven**: a repo's `backlog` schedule turns on when it has work, `lawncare-saas` last because its merges deploy. `max_concurrent_runs` stays 3 — ADR 0001 revisited and kept: queueing is acceptable, `issue-triage` is designed to yield. |
| 12 | Auto-merge stays **armed** throughout turn-on. The canary is the schedule, not the merge switch — auto-merge was shaken down in `docs/auto-merge/`, and disarming it for turn-on would test a pipeline that is not the one being shipped. |
| 13 | The monitor's dead-scheduler condition **self-activates**: it gates on "any automation has an enabled schedule," read live each run. Before task 06 the condition is inert; after it, the alarm exists with no config change. |

## Findings established against the live deployment

Measured 2026-09-16 against the host, the API, and the four target repositories.

### 1. Work queues, measured

| Repo | `agent` | `needs-triage` |
|---|---|---|
| `jelly-swipe` | **11** (seeded this day) | 0 |
| `lawncare-saas` | 0 | 0 |
| `womens-fantasy-sports` | 1 | 10 |
| `writers-app` | 0 | 0 |

The canary has work. So does `womens-fantasy-sports` — its queue grows through triage
while it waits for fleet expansion (task 08). Starvation (C5) is aggregate across all
four repos, so it correctly stays quiet while any queue has `agent` work.

### 2. Everything is off, and armed

All eight schedules (`backlog` ×4 staggered `2/5/8/11-59/15`, `issue-triage` ×4 at
`:30/:35/:40/:45`) are `enabled: false`. The `pr-review` rows carry only their
`api:manual` trigger — fired by the bridge, never by cron. `FABRO_AUTO_MERGE=1`, and
`lawncare-saas` carries an explicit `auto_merge=true`; the other three are
armed-by-absence (auto-merge decision 10).

### 3. The run store is young

`GET /api/v1/system/info` reports `runs.total = 7` after a container restart ~4h before
measurement; the deployment log records dozens of runs the same day. Duration history
for the user guide's "when will it be done" section therefore comes from the
**deployment log**, not the API — task 04 mines
`~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`, it does not invent numbers.

### 4. The host toolkit is already everything the monitor needs

`jq`, `python3`, `curl`, and an authenticated `gh` are installed (the branch sweeper
already uses `gh` against the four repos). Cron precedent: `17 4` and `43 4` daily for
the sweepers, logs in `~/.local/state/`. The monitor needs **no new secret material**:
the dev token is `docker exec fabro-fabro-1 cat /storage/server.dev-token` and the
Discord webhook is `docker exec fabro-fabro-1 cat /storage/secrets/discord_webhook_url`
— the same sources `discord-notify.sh` uses from inside.

### 5. The in-band notification surface is already broad

`discord-notify.sh` fires seven kinds from hooks: `rescue`, `complete`, `failed`,
`review-triggered`, `merged`, `triage-question`, `triage-failed`. Every one fires from
inside a run. **Anything that prevents a run from starting or finishing never reaches a
hook** — that gap is exactly the monitor's condition table, and it is why decision 5
forbids the monitor from duplicating failure alerts.

### 6. Turn-on preflight is cheap because the host is verified whole

All five environments exist, all three vault secrets are set, the automations match the
provision script, disk is at 52%, zero stray sandboxes. Task 06's preflight re-verifies
rather than assumes — it is a checklist, not an investigation.

### 7. `GET /api/v1/runs` list rows may not carry timestamps

The list endpoint's summary objects were observed without `created_at`/`completed_at`
fields populated. Task 01 probes the live shape before writing a parser, and falls back
to the run id itself: ULIDs lead with 48 bits of epoch milliseconds, so the newest run
id *is* a timestamp. The monitor must not depend on a field the API does not guarantee.

## Architecture

### The monitor

`ops/fabro-monitor.sh`, deployed to `~/bin/fabro-monitor.sh`, cron `7-59/15 * * * *`
(offset 7 dodges the backlog fires at `2/5/8/11-59/15` and sits between triage fires),
logging to `~/.local/state/fabro-monitor.log`, state in
`~/.local/state/fabro-monitor.state`.

Each run evaluates eight conditions. Every condition is a mechanical test; there is no
judgment and no model. Firing means: send Discord, stamp the state file. Cleared with a
stamp present means: send one ✅ resolved, clear the stamp.

| ID | Condition | Signal | Threshold | Severity | Re-alert |
|---|---|---|---|---|---|
| C1 | container-down | `docker inspect fabro-fabro-1` — not running, or health not `healthy` | immediate | 🔴 | 4h |
| C2 | api-unreachable | `GET /api/v1/system/info` with the dev token fails, times out (10s), or 401s | immediate | 🔴 | 4h |
| C3 | dead-scheduler | any automation has an enabled schedule **and** newest run is older than 2h | 2h | 🔴 | 4h |
| C4 | stuck-run | any run in a non-terminal state older than 3h — includes `submitted`, which never executes (AGENTS.md) | 3h | 🔴 | 4h |
| C5 | starvation | zero open `agent`-labeled issues across all four repos (`gh issue list -R … --label agent --state open`) | immediate | 🟡 | 7d |
| C6 | disk-pressure | `df /` use ≥ 85% | 85% | 🟠 | 24h |
| C7 | scheduler-down | `GET $SCHEDULER_URL/health` does not answer 200 within 5s | immediate | 🔴 | 4h |
| C8 | scheduler-wedged | `/api/queue` non-empty **and** every entry in `/api/pools` has `lease == null` and `drained == false`, held for `WEDGE_MINUTES` | 20m | 🔴 | 4h |

`SCHEDULER_URL` defaults to `http://127.0.0.1:32280` (the scheduler's LAN-only,
unauthenticated surface — no token is read for C7/C8); `WEDGE_MINUTES` defaults to 20
and `WEDGE_MINUTES=0` fires on the first qualifying sample, which is the forced test.
C8's window lives in the state file under its own `C8_since=<epoch>` key and is cleared
the moment a sample stops qualifying. C7 and C8 arrived with the coder scheduler
(`docs/scheduler/12-monitor-conditions.md`), after the C1–C6 shakedown; that they read
the scheduler's container, not fabro's, is decision 17.

Rules:

- **C1 suppresses C2.** A down container fails the API check too; report the root
  cause, not its symptom. A 401 is *not* suppressed and gets its own message — the dev
  token rotated and the monitor's copy of how to read it is fine but the server
  disagrees.
- **C7 suppresses C8.** An unreachable scheduler cannot answer "queued work, idle
  boxes", so C7 reports the cause and C8 is skipped — its stamp and its `C8_since`
  window are left untouched rather than resolved. C7 and C8 are independent of C1: the
  scheduler is a *separate* container, so they are evaluated even when C1 is firing or
  the fabro API is unreachable, and a fabro outage neither hides nor invents a
  scheduler problem. Only C7 suppresses C8, never the reverse.
- **C3 self-activates** (decision 13): read the automation list every run, gate on any
  enabled schedule. Before turn-on the condition cannot fire; the monitor is deployed
  and soaking *before* task 06 so its quiet baseline is proven.
- **Thresholds are env knobs** — `IDLE_HOURS=2`, `STUCK_HOURS=3`, `DISK_PCT=85` — so
  the shakedown in task 02 forces each condition by setting a knob to an absurd value
  rather than by breaking something real.
- **Alerting must never wedge cron.** A failed send is logged, not fatal; the script
  exits 0 unless it could not evaluate conditions at all (and a wholesale evaluation
  failure is what the heartbeat's `/fail` signal is for, task 03).
- Message format: `fabro monitor: <condition> — <one line of detail>`, leading emoji
  from the table, prefixed so it is visually distinct from the in-band hook messages,
  which say `fabro …` without `monitor`. The starvation message says what to do: *file
  issues labeled `needs-triage` (or `agent`) on any of the four repos.* Closing the loop
  back to the operator is the point of the alert.

### The heartbeat

**Declined by the operator — task 03 skipped.** Nothing pings anywhere; the script
still honours `FABRO_HEARTBEAT_URL` if one is ever set, but `~/fabro/.env` carries
no such key and none is planned. The consequence is recorded in the risks table
and the runbook carries the manual host-death disambiguation.

What follows is the original contract, kept so the decision can be revisited
without re-deriving it: `FABRO_HEARTBEAT_URL` in `~/fabro/.env` (host-local,
never in this repo). The monitor pings it at the end of every run, `/fail` on an
evaluation error. healthchecks.io, or any URL-ping dead-man's service — the
script does not care which; the contract is "GET this URL every ~15 minutes,
alert if it stops." The human creates the check (task 03);
the script already supports it from task 01.

### The documents

`docs/USER-GUIDE.md` is organized around the operator's three questions: how do I add
work, how do I tell it's running and when it'll be done, how do I get notified when
something needs attention. It names no IP, path, or token — that is the runbook's job
(decision 2), and the separation is what keeps the guide in the public repo.

### Turn-on

Preflight → enable five schedules (4 triage + `backlog-jelly-swipe`) → five-day human
observation window → queue-driven expansion, `lawncare-saas` last. Schedule flips are
`GET` + full-body `PUT` with `If-Match` on the automation row — the same discipline
`fabro-auto-merge-switch.sh` already implements; task 06 writes the recipe out per row
rather than growing a new script for an action performed twice ever.

## Rules specific to this stage

The inherited golden rules are mostly about `.fabro` files and do not bite here — this
series touches none. What bites instead:

1. **POSIX `sh`, `DRY_RUN=1` default, mechanical skip rules, header comments that carry
   the why.** The sweepers' house style is the monitor's house style.
2. **No secret in a tracked file.** The monitor *reads* the dev token and webhook out of
   the container at runtime; it does not copy them to disk. The heartbeat URL lives in
   `~/fabro/.env`.
3. **The monitor is deployed, drift-checked, and croned like a sweeper** — AGENTS.md's
   deploy section and verification block gain it in task 02, not in the catch-up task.
4. **Every threshold and claim in the user guide is measured, not invented.** Task 04
   cites the deployment log for durations and the API/graphs for mechanics. A guide that
   guesses is worse than none.
5. **The observation window is human-executed.** Agents write everything else; task 07
   is a checklist the operator runs, because "watch that PRs get generated" is a
   judgment call and pretending otherwise is how a bad merge sails through a green
   dashboard.

## Known risks, recorded deliberately

| Risk | Detail |
|---|---|
| The monitor and the thing it monitors share a host | Host death is silence — the heartbeat that would close this was declined at task 03. The runbook carries a two-minute manual disambiguation (ssh + cron log + compose ps). Accepted deliberately by the operator, recorded so nobody assumes a ping exists. |
| The monitor reads the API with the single dev token | Same blast radius as `discord-notify.sh` and the bridge; rotating the token breaks the monitor loudly (C2's distinct 401 message), never silently. |
| Starvation re-alerts weekly forever on an empty factory | Intended — the alert *is* the reminder to write issues. The operator can mute by disabling the cron; that is a deliberate act, not drift. |
| The canary merges real PRs with auto-merge armed | Decision 12. jelly-swipe has branch protection and no deploy-on-merge; the stop conditions in task 07 disarm at the first anomaly. |
| C3 depends on schedule state read live | An operator who disables every schedule mid-incident also disables the dead-scheduler alarm — correct behavior (nothing is supposed to run), recorded here so nobody "fixes" it. |
| The run store is young | Duration estimates in the user guide are ranges from the deployment log, not percentiles. Marked as such in the guide. |

## Task index

| # | Task | Needs smarter LLM? |
|---|---|---|
| 00 | This document | — |
| 01 | `ops/fabro-monitor.sh` + ADR 0004 | no |
| 02 | Deploy the monitor, cron it, shake down all six conditions (C7/C8 were added later, by the scheduler series) | no |
| 03 | ~~The heartbeat~~ — **skipped**: operator declined healthchecks.io; no external dead-man's ping exists | no |
| 04 | `docs/USER-GUIDE.md` | **yes** |
| 05 | `~/.fabro-deploy/docs/OPERATOR-RUNBOOK.md` | **yes** |
| 06 | Turn on: preflight, enable 4 triage + `backlog-jelly-swipe` | no |
| 07 | Observation window — **human task**, five days, checklist | human |
| 08 | Expand the fleet, queue-driven, `lawncare-saas` last | no |
| 09 | Correct AGENTS.md and `ops/README.md` | no |
| 10 | Update the deployment log | no |

Do them in order. 02 needs 01. 03 was declined by the operator (no heartbeat; the
runbook carries the manual host-death check). 04 and 05 need 01 (the guide documents the
monitor's alert vocabulary) but not 02-03. 06 needs 02; the silent-host risk the
heartbeat would have covered is consciously accepted. 07 needs 06. 08 needs 07. 09 and
10 need 08.
