# Task 01 — `ops/fabro-monitor.sh` + ADR 0004

**Depends on:** 00. **Blocks:** 02, 04. **LLM level:** no.

Write the monitor. The condition table, thresholds, dedup discipline, and message
format are all contracted in `00-overview-and-contracts.md` — this task is
implementation of that contract, plus the probes that pin the contract to the live
API. The house style is the sweepers': POSIX `sh`, `set -u`, `DRY_RUN=1` default,
header comments that carry the why, mechanical tests only.

## 1 — Probe before you parse

Finding 7: the runs-list shape is not guaranteed. Establish, against the live server,
and record the answers in the script's header comments:

```sh
TOK=$(ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/server.dev-token')
curl -fsS -m 10 -H "Authorization: Bearer $TOK" \
  http://10.10.0.32:32276/api/v1/runs | jq '.data[0] | keys'
curl -fsS -m 10 -H "Authorization: Bearer $TOK" \
  http://10.10.0.32:32276/api/v1/runs | jq '.data[0]'
```

What to determine: whether list rows carry `created_at` and a `status`/`state` field
and what the terminal-state values are spelled like; whether the list is newest-first
or needs sorting; whether `limit` is a parameter. If timestamps are absent or
unreliable, use the ULID fallback — the first 10 characters of a run id are 48 bits of
epoch milliseconds in Crockford base32, decodable in five lines of `python3` (present
on the host). The monitor must work from whichever source the probe proves real, and
the header must say which.

Also probe the failure shapes: `GET /api/v1/system/info` with a garbage token (expect
401 with a JSON error body — distinguishable from a connection refused), and
`docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' fabro-fabro-1`
(confirm the health field exists on this container; the compose file defines a
healthcheck, so it should).

## 2 — The script

`ops/fabro-monitor.sh`. Contract (from 00, restated as implementation requirements):

- **Inputs from the environment, all optional:** `DRY_RUN` (default 1 — print the
  alerts that would send, send nothing, write no state), `IDLE_HOURS` (2),
  `STUCK_HOURS` (3), `DISK_PCT` (85), `FABRO_HOST` (`10.10.0.32`), `FABRO_PORT`
  (`32276`), `FABRO_MONITOR_REPOS` (space-separated, defaults to the four, same
  `:-` fallback pattern as the sweeper's `FABRO_SWEEP_REPOS`), and — from task 03 —
  `FABRO_HEARTBEAT_URL` (unset: no heartbeat, not an error).
- **Secret material is read, never stored.** The script runs **on the host** (the repo
  copy is deployed to `~/bin` and executed by the host's cron — the ssh in §1's probes
  is only how *you* reach the host from the Mac). So the script reads the dev token
  with `docker exec fabro-fabro-1 cat /storage/server.dev-token` once per run, and the
  webhook the same way from `secrets/discord_webhook_url`. Neither value is written to
  disk by the script.
- **Six conditions**, exactly as the table in 00: C1 container-down (suppresses C2's
  connection failures, never its 401), C2 api-unreachable (401 gets its own message
  about token rotation), C3 dead-scheduler (self-activating: gate on any enabled
  schedule in `GET /api/v1/automations`, then newest run older than `IDLE_HOURS`),
  C4 stuck-run (non-terminal older than `STUCK_HOURS`; the message names the run id,
  its status, and its age), C5 starvation (aggregate across repos; the message says
  what to do — file issues labeled `needs-triage` or `agent`), C6 disk-pressure.
- **State file** `~/.local/state/fabro-monitor.state`, one `C<n>=<epoch>` line per
  firing condition. Re-alert intervals per the 00 table (4h default, 7d C5, 24h C6).
  A condition that clears with a stamp present sends exactly one
  `✅ fabro monitor: <condition> resolved` and loses its stamp.
- **Messages** lead with the condition's emoji and the literal prefix
  `fabro monitor:` — visually distinct from the in-band hook messages, which never
  say `monitor`.
- **Failure discipline:** a failed Discord POST is logged to stderr (cron appends to
  the log) and the run continues; the script exits 0 when evaluation completed even if
  sends failed, and non-zero only when evaluation itself failed (no token, no `gh`,
  API unparseable) — which is what the heartbeat `/fail` ping keys on.
- **Heartbeat:** if `FABRO_HEARTBEAT_URL` is set, `curl -fsS -m 10` it at the end of
  every run; on evaluation failure, ping `${FABRO_HEARTBEAT_URL}/fail` instead. A
  failed heartbeat ping is logged, never fatal.

## 3 — ADR 0004

Write `docs/adr/0004-monitoring-is-out-of-band.md`, matching the format of
0001-0003. The decision: run-failure alerting stays in-band (the hooks); the monitor
is health-only and never duplicates it; the host-death gap is closed by an external
heartbeat, not by moving alerting in-band. The alternatives weighed: monitor
duplicates failure alerts (double pings train the operator to ignore the channel);
monitor replaces the hooks (loses enrichment — repo, issue, PR links, review run id);
fabro self-monitors (circular). Record decision 5 and decision 8 as one ADR — they are
the same decision at two layers.

## 4 — Local verification

No live sends in this task. Required, in order:

```sh
sh -n ops/fabro-monitor.sh
shellcheck ops/fabro-monitor.sh    # if installed; fix or justify every finding
DRY_RUN=1 ops/fabro-monitor.sh     # from the Mac — must fail cleanly on the ssh/docker hops
```

Then from the host (deploy to `/tmp` by hand for this test, not `~/bin` — task 02 owns
the real deploy):

```sh
scp ops/fabro-monitor.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'sh -n /tmp/fabro-monitor.sh && DRY_RUN=1 /tmp/fabro-monitor.sh'
```

The host dry run must print a clean evaluation of all six conditions with no alerts
(nothing is firing today — schedules are disabled, so C3 is inert; that is the correct
output and the shakedown in task 02 forces firing via the env knobs).

## Acceptance

- `sh -n` clean; shellcheck clean or every finding justified in a comment.
- Host dry run evaluates all six conditions, sends nothing, writes no state file.
- The header records the probed API shapes and which timestamp source the parser uses.
- ADR 0004 exists and cross-references this task series.
