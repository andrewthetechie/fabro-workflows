# Add scheduler conditions to `fabro-monitor.sh`

## Tracer-Bullet Outcome
When the scheduler is dead, or wedged with queued work and idle boxes, a Discord
alert arrives within 15 minutes — sent by the monitor, from outside the scheduler.

## User Story
As the operator, I want something other than the scheduler to notice the scheduler
has stopped working, so that a silent wedge does not cost me a night.

## Description
Two new conditions in the existing monitor. Deliberately **not** self-reporting
from the scheduler: a process cannot alert on its own death, and ADR 0004 already
puts health out-of-band and run failures in-band.

## Context Pack
- Source decisions: overview decision 17. ADR 0004 ("monitoring is out-of-band")
  is the governing decision and must not be contradicted.
- Repo facts: `ops/fabro-monitor.sh` runs from cron every 15 minutes
  (`7-59/15 * * * *`) with `DRY_RUN=0`, and already implements C1–C6 with a state
  file at `~/.local/state/fabro-monitor.state` holding one `C<n>=<epoch>` line per
  firing condition. Its `fire()` appends to a temp file and the dispatch block
  re-alerts on an interval; `send()` prints **nothing** on success, which is why
  the log looks uniformly green even when alerts go out.
- Non-goals: alerting on individual run failures (hook-owned, ADR 0004); changing
  C1–C6; adding a second notification channel.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files: `ops/fabro-monitor.sh`, `ops/README.md`,
  `docs/turn-it-on/00-overview-and-contracts.md` if it enumerates the conditions.

- Interfaces and names — the existing condition table this extends, from the
  script's own header:

  ```
  #   C3 dead-scheduler   any schedule enabled AND newest run > IDLE_HOURS old  🔴 4h
  #   C4 stuck-run        non-terminal run older than STUCK_HOURS               🔴 4h
  #   C5 starvation       zero open agent-labeled issues across the repos       🟡 7d
  ```

  Add:

  ```
  #   C7 scheduler-down   GET :32280/health does not answer 200                 🔴 4h
  #   C8 scheduler-wedged queue non-empty AND every coder pool idle AND
  #                       not drained, for > WEDGE_MINUTES                      🔴 4h
  ```

  Existing helpers to reuse verbatim — do not reimplement:

  ```sh
  fire()  { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$tmp/firing"; }
  mark_evaluated() { printf '%s\n' "$1" >> "$tmp/evaluated"; }
  cond_name() { case "$1" in C3) printf '%s' "dead-scheduler" ;; ... esac; }
  ALERT_SECONDS="${FABRO_MONITOR_RE_ALERT_SECONDS:-14400}"   # 4h
  ```

  `cond_name` must gain `C7` and `C8` arms, or the resolved message prints a bare
  id.

- Verified external contracts: the scheduler's own API, defined in drafts 04 and
  11:

  ```
  GET /health      -> 200 {"status":"ok","repos":[...],"coder_pools":[...]}
  GET /api/queue   -> 200 [ {repo, number, title, priority, waited_seconds}, ... ]
  GET /api/pools   -> 200 [ {coder_pool, drained, lease: {...}|null}, ... ]
  ```

  `GET /api/pools` does not exist before this draft — **add it in draft 11's
  file** if it is missing, or this condition has nothing to read. Flag it rather
  than inventing a different shape.

- Behavior rules:
  - `SCHEDULER_URL` defaults to `http://127.0.0.1:32280`, overridable.
  - **C7** fires when `/health` does not answer `200` within 5s.
  - **C8** fires when `/api/queue` is non-empty **and** every entry in
    `/api/pools` has `lease == null` and `drained == false`, continuously for
    `WEDGE_MINUTES` (default 20). A single sample is not enough — a box is
    momentarily idle between runs, and the whole point of the design is that the
    gap is about 15 seconds.
  - C8 needs the duration tracked across cron invocations. Store the first-seen
    epoch in the existing state file under its own key; clear it as soon as a
    sample does not qualify.
  - If C7 is firing, **skip C8** — an unreachable scheduler makes it unevaluable,
    exactly as the script already skips C2–C4 when C1 fires.
  - Both conditions must call `mark_evaluated` when they produce a definitive
    answer, or the resolved message never sends.
- Error and security rules: no token needed — the scheduler is unauthenticated on
  the LAN (decision 16). A `curl` failure must not abort the whole monitor run;
  every other condition still has to be evaluated.

## Acceptance Criteria
- [ ] `sh -n ops/fabro-monitor.sh` exits 0.
- [ ] `DRY_RUN=1 SCHEDULER_URL=http://127.0.0.1:1 ~/bin/fabro-monitor.sh` prints a
      `would send` line naming `scheduler-down`.
- [ ] With the scheduler healthy and work in flight, neither C7 nor C8 fires and
      both print `ok` lines.
- [ ] C8 does not fire on a single idle sample; it requires `WEDGE_MINUTES`.
- [ ] When C7 fires, the output shows C8 skipped, not evaluated.
- [ ] `ssh andrew@10.10.0.32 'cat ~/bin/fabro-monitor.sh' | diff - ops/fabro-monitor.sh`
      prints nothing after deploy.

## Test Expectations
No unit-test framework — POSIX `sh`. Verification is `sh -n` plus forced
conditions, matching how C2/C6 are already exercised in
`~/.fabro-deploy/docs/OPERATOR-RUNBOOK.md`:

```sh
# C7: point at a dead port
DRY_RUN=1 SCHEDULER_URL=http://127.0.0.1:1 ~/bin/fabro-monitor.sh
# expected stdout to contain, literally:
#   DRY   would send: 🔴 fabro monitor: scheduler-down — ...

# C8: zero-length wedge window against a real idle scheduler with queued work
DRY_RUN=1 WEDGE_MINUTES=0 ~/bin/fabro-monitor.sh
```

## Dependencies
- Blocked by: "Reorder, drain and cancel in the web UI"
- Why blocked: C8 reads `drained` from `/api/pools`, which draft 11 defines.
- Blocks: "Shakedown and deployment-log entry"

## Labels
`enhancement`, `ops/monitor`, `priority:medium`

## Estimate
Small

## Risk
2 - additive to a working script. The real risk is a false-positive C8 training
the operator to ignore alerts, which the `WEDGE_MINUTES` window exists to prevent.

## Validator Stopping Point
`sh -n` passes, both forced conditions produce the expected `would send` lines,
and the `~/bin` drift diff is empty.
