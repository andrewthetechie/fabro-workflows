# Task 09 — Correct the docs

**Depends on:** 08. **Blocks:** 10. **LLM level:** no.

The turn-on changed what is true about the deployment. The standing docs were written
when everything was off. Fix them all, in the tree, so the next session reads
reality.

## AGENTS.md

- **"Two production Fabro workflows" → three.** The header line and any other mention;
  `issue-triage` shipped and is live. The layout table's `.fabro/workflows/<name>/`
  row lists two packages — add `issue-triage`, and add a `docs/turn-it-on/` row beside
  the other task-series rows.
- **"Nothing fires on a cron" is no longer true.** Wherever the docs describe the
  schedules as disabled (the AGENTS.md deploy section references, ops/README's
  automations section), the enabled set and the stagger are now the normal state.
  State which backlog schedules are enabled as of this task — or point at the API as
  the source of truth rather than freezing a list that will drift. Prefer the pointer
  with a snapshot date, matching how the automations table already does it ("Read back
  from the live server …").
- **The layout table** gains `docs/USER-GUIDE.md` (the public operating manual) with a
  one-line description and the secrets rule.
- The monitor deploy/drift lines were added in task 02 — verify they landed and match
  reality, including the crontab.

## ops/README.md

- **Automations table:** add the four `issue-triage-*` rows (they exist and are
  scheduled; the table currently lists eight rows) and refresh the schedule-enabled
  state for all twelve. Keep the "read back from the live server" date honest.
- **"Nothing in this deployment currently fires on a cron"** — delete or invert that
  line wherever it appears; the sweepers and now the workflows all fire on cron.
- **Monitor section:** confirm task 02's addition exists and task 03's heartbeat
  paragraph landed; the condition contract stays in
  `docs/turn-it-on/00-overview-and-contracts.md`, referenced not copied.
- **The concurrency note** ("re-enabling the staggered backlog schedules should be
  accompanied by revisiting the cap") resolves to task 08's recorded decision — update
  the pointer so it no longer reads as an open action item.

## CONTEXT.md

The turn-on terms were added when the series was planned (user guide, operator
runbook, monitor, starvation, heartbeat, canary, observation window). Verify they are
still accurate as built — particularly that "canary" reads as what actually happened,
not what was planned.

## Validation pass

Docs changes touch no workflow, but the series is about to be declared done — rerun
the container validation of all three workflows and confirm the baselines from task
06 still hold (any drift found now is someone else's uncommitted change; surface it,
don't absorb it).

## Acceptance

- AGENTS.md and ops/README.md describe the deployment as it stands: three workflows,
  twelve automations, schedules enabled as of task 08, monitor and heartbeat in place.
- No stale "disabled by default while development continues" framing survives.
- The validate baselines are re-recorded with today's date.
