# Task 02 — Deploy the monitor, cron it, shake down all six conditions

**Depends on:** 01. **Blocks:** 03, 06. **LLM level:** no.

The monitor is useless until it is on the host and in cron, and untrusted until every
condition has been observed firing and resolving. This task deploys it exactly like a
sweeper and then proves each of the six conditions with the env knobs — never by
breaking something real.

## 1 — Deploy

```sh
cd ~/Documents/code/fabro-workflows && git pull --ff-only   # main carries task 01
scp ops/fabro-monitor.sh andrew@10.10.0.32:~/bin/fabro-monitor.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-monitor.sh'
```

Add the drift check to AGENTS.md's verification block (the "every one of these should
print nothing" list), alongside the two sweepers:

```sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-monitor.sh' | diff - ops/fabro-monitor.sh
```

And the deploy block gains the `scp`/`chmod` pair above, next to the sweeper lines.

## 2 — Cron

`7-59/15 * * * *` — offset 7 so the monitor never shares a minute with a backlog fire
(`2/5/8/11-59/15`) and lands between triage fires (`:30/:35/:40/:45`). Log next to the
sweeper logs:

```sh
ssh andrew@10.10.0.32 '( crontab -l 2>/dev/null; \
  echo "7-59/15 * * * * DRY_RUN=0 /home/andrew/bin/fabro-monitor.sh >> /home/andrew/.local/state/fabro-monitor.log 2>&1" \
) | crontab - && crontab -l'
```

`DRY_RUN=0` on the cron line, matching the sweepers. Wait for one cron tick (or run
`DRY_RUN=0 ~/bin/fabro-monitor.sh` by hand once) and confirm: exit 0, log line
written, **no state file** (nothing is firing — schedules are still disabled, so C3 is
inert), no Discord message. A monitor that announces health is noise; silence is the
correct baseline.

## 3 — Shakedown: force every condition

Each condition is forced with its env knob or a scoped fake, observed end to end —
Discord message received, state stamp written, re-alert suppressed inside the window,
resolved message on clear — and then restored. Do all six. The checklist:

- **C6 disk-pressure:** `DRY_RUN=0 DISK_PCT=1 ~/bin/fabro-monitor.sh` fires
  immediately (disk is at 52%). Confirm the 🟠 message and the stamp. Run again
  immediately: no second message (dedup). Then `DISK_PCT=99`: the ✅ resolved message
  fires and the stamp clears.
- **C4 stuck-run:** `STUCK_HOURS=0` fires if any non-terminal run exists; if the store
  is clean, this condition cannot be forced by knob — note it and move on; it will be
  exercised naturally during the observation window or not at all. Do **not**
  manufacture a stuck run by starting one and abandoning it: a forgotten test run is
  how the last "leak" investigation started.
- **C3 dead-scheduler:** inert today by design (no enabled schedules). Prove the gate
  logic instead: `IDLE_HOURS=0` with schedules disabled must still *not* fire — the
  enabled-schedule gate, not the clock, is the guard. The live firing is proven by
  task 06: after schedules are enabled, the next `IDLE_HOURS=0` dry fire produces the
  message. Task 06's checklist includes it.
- **C2 api-unreachable:** point at a dead port —
  `DRY_RUN=0 FABRO_PORT=1 ~/bin/fabro-monitor.sh`. Confirm the 🔴 message and stamp,
  then a normal run resolves it.
- **C1 container-down:** do **not** stop the container to test this. The C1→C2
  suppression logic is reviewable in code and C2 is proven above; record in the
  deployment log that C1 was verified by inspection, and let the heartbeat (task 03)
  carry the real host-death coverage. Deliberate gap, recorded, not papered over.
- **C5 starvation:** cannot fire truthfully today (jelly-swipe has 11 `agent` issues —
  aggregate is non-empty). Force the negative: temporarily run with
  `FABRO_MONITOR_REPOS="andrewthetechie/writers-app"` — no, that proves per-repo
  empty, not aggregate-empty. The honest proof is `DRY_RUN=1` with the repo list set
  to four empty stand-ins is impossible; instead verify the evaluation prints the
  per-repo counts correctly, and accept that C5's first live firing happens when the
  factory actually drains. Record the gap in the log alongside C1.

After the shakedown, `~/.local/state/fabro-monitor.state` must be empty or absent —
every forced condition resolved.

## 4 — `ops/README.md`

Add the monitor to the contents table and a short section beside the sweeper cron
blocks: what it is, the cron line, the state/log files, the env knobs, and the
pointer to `docs/turn-it-on/00-overview-and-contracts.md` for the condition contract.
One section, terse — the detail lives in 00.

## Acceptance

- `~/bin/fabro-monitor.sh` deployed; the AGENTS.md drift check prints nothing.
- Cron installed; one clean tick observed (log written, no state, no Discord).
- C2, C6 observed firing, deduped, and resolved end to end. C3's gate proven inert
  with schedules disabled. C1, C4, C5 gaps recorded in the deployment log (task 10
  collects them) with their reasoning.
- `ops/README.md` updated.
