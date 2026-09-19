# Shakedown and deployment-log entry

## Status, 2026-09-19 (session 1 of 2) — the setup is done, the window has NOT started

The in-session setup this draft calls for was executed and is committed: the pre-flight
(the four `backlog` rows schedule-off / api-on, the two stale draft-08 lease rows, both
variable names in `~/fabro/scheduler.env`), the deploy and bring-up, the verification
against `/health`, `/api/queue`, `/api/pools` and the page, the measurement recipe below as
a runnable script, the dated deployment-log section, and the doc corrections.

**The 24-hour window did not run.** The loop was armed at `2026-09-19T04:18:21Z`, dispatched
two real runs in its first three seconds, and both died at the second node: `claim` writes
`gh issue view … > /tmp/fabro/issue.json` and draft 10 (`ca39b8f`) deleted `acquire`, the only
node that ran `mkdir -p /tmp/fabro`. Every `backlog` run fails there —
`ops/fabro-fire-backlog.sh` included — and the node's unconditional edge parks the run at
`human_rescue` for 4h while it holds its coder lease and its repo. The container was stopped
at `04:23:29Z`, five minutes in, and both runs were answered `[X] Abandon` at their gates so
their boxes would not be held for four hours. **No acceptance criterion in this file is met
or in progress; every one is unstarted**, and no number from those five minutes may be
counted toward any of them. Before a window can start, the missing `mkdir` has to land on
`main` (the scheduler clones `main` at dispatch, so a local commit changes nothing) and the
container has to be restarted, which is when its recovery pass releases the two leases and
requeues `jelly-swipe#353` and `lawncare-saas#2277`.

The recipe is the *The measurement recipe — NOT YET MEASURED* section (§7) of
`~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`'s dated 2026-09-19 draft-14 section. Every
command in it was run against the live run store, and the two ways of bounding tool calls to
a stage were cross-checked against each other. **Corrected in the Context Pack:** the
baseline figures are *not* in `docs/perf/00-overview-and-measurements.md`. They are in
`docs/scheduler/00-overview-and-contracts.md` (0.57 and 1.33 minutes per tool call, the
~1.7× contention component and the 1.38× `reasoning_effort` part) and in
`docs/perf/04-compaction-and-task-sizing.md` (the 17.4 tok/s box ceiling).

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
