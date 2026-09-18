# Shakedown and deployment-log entry

## Tracer-Bullet Outcome
The scheduler runs unattended for 24 hours across all four repos, and the
deployment log records what it actually did — runs dispatched per box, queue depth
over time, every requeue, and whether the coders were busier than they were before.

## User Story
As the operator, I want evidence that the thing works and a written record of what
it did, so that the next person to touch it — including me in a month — starts from
measurements rather than from this document's intentions.

## Description
The closing task. No new features: enable, observe, record, and correct any
document this series made stale.

It exists because the overview makes a falsifiable claim — that contention costs
about 1.7× — and nothing so far has tested it end to end under load.

## Context Pack
- Source decisions: overview decision 18 (all four repos at once; no canary,
  because zero of four observed runs produced a clean end-to-end merge, so there is
  no working baseline to protect).
- Repo facts: the operational history lives in
  `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac, mode 600,
  deliberately outside this public tree. **Append a dated section; never edit an
  earlier one** — it is a chronological record.
- Non-goals: new features; tuning `T`, poll cadences or `max_concurrent_runs`
  beyond recording what they were; fixing anything the shakedown finds unless it
  stops the factory (file it instead).

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files: `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` (appended, not in
  this repo); `docs/scheduler/00-overview-and-contracts.md` (corrections);
  `CONTEXT.md`, `AGENTS.md`, `ops/README.md` (corrections); `docs/USER-GUIDE.md`
  if it describes how work is picked up.
- Interfaces and names: None — no code.
- Verified external contracts: the measurement baseline this compares against,
  from 2026-09-18 and already recorded in `docs/perf/00-overview-and-measurements.md`:

  ```
  Sandcastle (one loop per repo)   0.57 minutes per tool call
  fabro (LiteLLM round-robin)      1.33 minutes per tool call
  claimed contention component     ~1.7x  (the rest was reasoning_effort, fixed in ce831c0)
  box throughput ceiling           17.4 tok/s
  ```

  Per-tool-call is computed from a run's event stream: count
  `agent.tool.started` events within the coder stage and divide the stage's
  `wall_time_ms` by that count. Events paginate with `since_seq`; `limit` alone
  truncates at 1000 and `meta.has_more` says to continue.

- Behavior rules — what the shakedown must record:
  - runs dispatched per coder pool, and total wall time leased vs idle per pool;
  - queue depth sampled hourly, and the maximum observed wait against `T`;
  - every requeue, with the run id and the failure category that triggered it;
  - every lease not released within 10 minutes of its run going terminal;
  - **per-tool-call minutes for at least one coder stage per repo**, compared with
    the 1.33 baseline above. This is the number that confirms or refutes the
    design's central claim.
  - whether any issue was worked twice, or any PR opened twice for one issue.
- Error and security rules: the deployment log is mode 600 and outside git. **No
  token, webhook URL or host credential may appear in any file this draft touches
  inside the repository** — the repo is public.

## Acceptance Criteria
- [ ] The scheduler ran ≥24 hours with all four repos enabled and no manual
      intervention.
- [ ] ≥1 run dispatched per repo, and ≥1 run on each of `coders-a` and `coders-b`.
- [ ] No issue was worked by two runs; no issue had two PRs opened for it.
- [ ] Every lease released within 10 minutes of its run going terminal.
- [ ] At least one infra-shaped failure was requeued and completed on retry — force
      one with `docker compose restart fabro` if none occurs naturally.
- [ ] Per-tool-call minutes recorded for ≥1 coder stage per repo, with the
      comparison against 1.33 written down **whether or not it improved**.
- [ ] A dated section appended to the deployment log covering all of the above.
- [ ] `docs/scheduler/00-overview-and-contracts.md` corrected wherever the
      shakedown contradicted it, with the correction dated.
- [ ] `CONTEXT.md` has no term describing something that no longer exists — check
      **Bridge**, **Cap**, **Coder pool**, **Quiet-exit** in particular. `Quiet-exit`
      describes a backlog run finding no issue and exiting at `acquire`; after
      draft 10 the scheduler only dispatches when work exists and `acquire` is
      gone, so that term is very likely stale.

## Test Expectations
No test framework — this is an observation window. The instrument is the run store.

Concrete command, run per dispatched run, with its expected shape:

```sh
TOK=$(ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/server.dev-token')
curl -fsS -H "Authorization: Bearer $TOK" \
  "http://10.10.0.32:32276/api/v1/runs/$RUN/stages" \
| jq -r '.data[] | select(.node_id=="coder") |
         "\(.node_id) v\(.visit) \((.wall_time_ms/60000)|floor)m \(.status)"'
```

Expected shape, e.g. `coder v1 47m succeeded`. Pair it with the coder stage's
`agent.tool.started` count from the events endpoint to get minutes per tool call,
and record both numbers.

## Dependencies
- Blocked by: "Add scheduler conditions to `fabro-monitor.sh`";
  "Turn off the four `backlog` automation schedules"
- Why blocked: unattended observation needs the alerting that would catch a wedge,
  and needs the old schedules off or the measurement is confounded by runs the
  scheduler did not dispatch.
- Blocks: None

## Labels
`docs`, `ops`, `priority:high`

## Estimate
Medium

## Risk
2 - observation and documentation. The risk is recording an optimistic story
rather than what happened; the explicit "whether or not it improved" criterion is
there to make that hard.

## Validator Stopping Point
24 hours unattended with every acceptance box ticked, a dated deployment-log
section appended, and no stale term left in `CONTEXT.md`.
