# Task 10 — Update the deployment log

**Depends on:** 09. **Blocks:** nothing — this closes the series. **LLM level:** no.

Append the turn-on entries to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`. The
standing rules apply: append-only, dated sections, no credentials, record what was
*not* proven as carefully as what was.

## What the entry covers

One dated section per phase, written as the phases complete — not reconstructed at the
end. If tasks 06–08 wrote their own dated entries as they went (they were told to),
this task writes the closing entry and verifies the earlier ones exist:

1. **Monitor deployment** (tasks 01–03): the shakedown results per condition —
   including the deliberate gaps (C1 verified by inspection, C4 and C5 awaiting their
   first natural firing) — the cron line, the heartbeat service and period/grace (no
   URL).
2. **Turn-on** (task 06): preflight numbers (validate baselines for all three
   workflows, environment/secret/variable state), the five rows enabled, the first
   hour's behavior, the forced-C3 proof and its honest reset.
3. **The observation window** (task 07): the daily lines are the substance — this
   entry summarizes them: PRs opened/merged on the canary, alerts and their causes,
   stop conditions hit (or none), the exit-criteria verdict.
4. **Expansion** (task 08): what was enabled in what order, the per-repo first-hour
   results, the cap-kept-at-3 reasoning, any repo left off and why, and the
   `lawncare-saas` auto-merge token made explicit.

## The closing state table

End the closing entry with the state of the whole system on the day the series
closes, in the same spirit as the auto-merge entries' "State on the day" sections:

- schedules enabled (per repo, per workflow);
- auto-merge switches (host variable value; per-repo tokens, explicit vs
  armed-by-absence);
- monitor: cron live, heartbeat live, any condition currently stamped;
- queues: `agent` / `needs-triage` counts per repo;
- what remains deliberately manual: the observation window's human checklist is the
  standing daily habit now, and anything task 07 or 08 deferred.

## What remains unproven

Carry forward, by name, the things this series still did not exercise — expected
examples: the `ci_fix` ladder end to end (open since the auto-merge stage), C1/C4/C5
first natural firings, a starvation alert's full cycle including its 7-day re-alert,
the heartbeat surviving an actual host outage. The next operator session should be
able to read this list and know exactly what "working" does not yet cover.

## Acceptance

- The log reads chronologically from monitor deploy to fleet expansion with no phase
  reconstructed from memory.
- The closing state table matches the live server (re-read it; don't transcribe).
- The unproven list is explicit. No credential appears anywhere in the entries.
