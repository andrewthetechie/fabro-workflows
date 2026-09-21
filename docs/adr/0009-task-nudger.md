# A task nudger steers coders that livelock, and stops before it fails anything

**Status:** proposed (2026-09-20, revised the same day against the fabro source)
THIS IS NOT YET IMPLEMENTED. Holding it in case we run into tasks stalling again but we seem to be in better shape now

A coder stage can spend its whole wall clock on read-class tool calls and write nothing.
Context grows until `timeout="180m"` fires. Sandcastle guarded that with a livelock
watchdog and an idle timeout, both progress-based. Fabro has one idle timeout and it
cannot see this failure, for the reasons in the next section but one. We will add an
out-of-band **task nudger**. It is a small service that watches running coder stages,
detects the livelock signature, and steers the agent back to work through fabro's
steering API. It escalates on a bounded ladder that ends in a Discord alert, and it never
cancels a run.

## This signature is not yet measured here

The run store records no livelock in this deployment. The one recorded 180-minute overrun
was something else. On run `01M2SBJG92TX0DBRKY5881VM34` the coder ran 90 minutes, fabro
retried it, and it ran 90 more. It made 55 tool calls, then 77. The last event before the
first timeout, 11 seconds before it fired, was `edit_file -> "Successfully edited"`
(`.fabro/workflows/backlog/workflow.fabro:481-491`).

`docs/perf/04` files that run as a task-size problem. `CONTEXT.md` names it an **Oversized
task**, and records that steering cannot help one. That is the category this ADR excludes,
and it is also the only long-coder failure this repository records.

So the first build sends no steer. It runs the detector and reports each candidate to
Discord with its evidence. Rungs 1 and 2 turn on after a human reads one reported
candidate and confirms it is a real read loop. The detector is cheap and the steer is not
the risky part, but shipping a corrective action for an unobserved failure is.

## What fabro already does

Fabro has an idle timeout. `stall_timeout` is a graph attribute and it defaults to 30
minutes (`context/fabro/lib/foundation/fabro-types/src/graph.rs:685-696`). A watchdog
cancels the whole run when no event lands inside that window
(`lib/components/fabro-workflow/src/pipeline/execute.rs:78-116`). It does not close this
gap, for two reasons.

1. It re-arms from `Emitter::last_activity()`, and a busy agent emits an event for each
   stream delta (`execute.rs:92-107`). A livelocked reader emits constantly, so the
   deadline never expires.
2. It cancels the run. It cannot fail one stage, so it cannot feed the rework ladder.
   `.fabro/workflows/backlog/workflow.fabro:443-445` already records exactly this.

Correction, 2026-09-20: an earlier draft said fabro had neither a livelock watchdog nor an
idle timeout. The second half was wrong. The accurate claim is narrower. Fabro has no
per-node, progress-based bound.

Fabro also emits `agent.loop.detected`
(`context/fabro/lib/foundation/fabro-types/src/run_event/agent.rs:77`). What raises it,
and under what rule, is open question 2 below.

## The ladder

Three rungs. Each one is harder to reverse than the one before it.

1. **Nudge.** `POST /runs/{id}/steer {text}` with no `interrupt`. Fabro injects the text
   as a user-role turn and drains it before the next LLM call
   (`context/fabro/docs/public/human-tools/steering.mdx`). A plain nudge cannot stop work
   that is happening.
2. **Interrupt and nudge.** The same endpoint with `interrupt: true`. Fabro cancels the
   active API round first, then queues the text. Use it only after a plain nudge produced
   no write.
3. **Discord alert, then stop.** `fabro nudger: <run> <repo>#<issue> <signature> <nudges
   sent>`. The nudger then stays quiet on that run. It never cancels the run and it never
   changes a GitHub label.

`POST /runs/{id}/interrupt` on its own is not a rung. It cancels the round and leaves the
session waiting for a later steer (`handler/steer.rs:44-50`). That is a pause, not a
recovery.

### Any refusal ends the ladder

`POST /runs/{id}/steer` refuses in five ways (`handler/steer.rs:82-131`):

- `409 use_answer_endpoint`. The run is blocked on a human gate.
- `409 run_not_steerable`. The run is not `Running`, or it is already terminal.
- `409 agent_not_steerable`. Every live agent stage uses a non-steerable backend.
- `409 no_active_steerable_session`. An interrupt found no live session to cancel.
- `503 worker_control_unavailable`. The worker control channel timed out or is closed.

None of these means the agent ignored a nudge. On any `409` the nudger ends the ladder for
that stage and stays silent. On `503` it retries once, then ends the ladder. Only a `202`
that produces no write counts toward escalation.

`agent_not_steerable` should never appear. The agent backend this deployment runs declares
`SessionCapability::Steer`
(`lib/components/fabro-workflow/src/handler/llm/pebble.rs:702`), and that capability is
what the endpoint gates on. Treat the code as a result that invalidates the design, and
report it.

### Why rung 3 reports rather than cancels

There is no stage-fail primitive in the API. The nearest control is
`POST /runs/{id}/cancel`, and it is the wrong tool for three reasons.

1. A cancel requeues. `ops/scheduler/src/fabro_scheduler/reconcile.py:109-113` puts
   `cancelled` in `REQUEUE_REASONS`, and `should_requeue` returns true on the reason alone
   (`:165-186`). Scheduler decision 15 makes that deliberate.
2. A requeue re-dispatches the same task up to `MAX_REQUEUES = 3` times
   (`reconcile.py:138`). Each attempt takes a coder box for a failure that steering has
   already failed to fix.
3. A cancel throws away the partial work. `allow_partial=true` on `coder` exists to keep
   that work and hand it to the review ladder (`workflow.fabro:493-514`).

Correction, 2026-09-20: an earlier draft rejected the cancel on two grounds. It said the
scheduler does not requeue a cancel, and that the issue would sit on `agent-in-progress`
until a human restored `agent`. Both halves were wrong. `_requeue` removes
`agent-in-progress` and adds `agent` (`reconcile.py:502-519`).

A livelock is an agent-shaped failure, and scheduler decision 10 routes those to a human.
The in-band ladder therefore owns termination. That ladder is `timeout`, then the stage
retry, then rework, then `human_rescue`. The nudger recovers cheaply, or it reports
loudly.

## Detection

Two detectors. The livelock detector is the point of the service. The stall detector is a
cheap backstop.

### Livelock

Trigger on a run of read-class tool calls with no write-class tool since the stage started
or since the last write. The bound is 20 calls or 15 minutes. Confirm with
`GET /runs/{id}/files?scope=uncommitted`, which must show no changed file.

The scope matters. The default is `committed`, which diffs against the run's `base_sha`
(`context/fabro/lib/apps/fabro-server/src/run_files.rs:511-513`). Every earlier stage
checkpoint sits inside that diff, so after task 1 it can never read zero. `uncommitted`
diffs the working tree against `HEAD` (`:515-524`), which is what "written since the last
checkpoint" means.

Two errors follow from that endpoint, and neither has a fix inside it.

1. **False positive on new files.** Sandbox-backed responses exclude untracked files
   (test `working_tree_scope_uses_one_git_diff_and_excludes_untracked_files`,
   `run_files.rs:1721`). An agent that only creates new files reads as zero progress.
2. **False negative on a retry.** A node retry resumes in the same sandbox, and the
   previous attempt's edits are still uncommitted (`workflow.fabro:466-473`). Attempt 2
   shows net change even when it writes nothing.

So `/files` corroborates and it is not ground truth. The write-class `agent.tool.completed`
event is the primary progress signal. `/files` only raises or lowers confidence, and it is
never polled on a timer.

Fifteen minutes of reading with no write is unambiguous in a stage whose median is 24
minutes and whose p90 is 47 minutes (`docs/perf/04`). That sentence is true for `coder` and
for `rework_t1` to `rework_t4`. It is false for two other stages a model-name classifier
would select, which the next section excludes.

### Which stages the livelock detector watches

Classify by the `requested_model` field on `agent.llm.started`
(`lib/components/fabro-store/src/run_state.rs:742`). The field is nested. The props carry
`event.LlmRequestStarted.requested_model`, not a top-level `model`
(`run_event/agent.rs:280-290`). `agent.tool.started` nests the same way, at
`event.ToolCallStarted.tool_name` and `event.ToolCallStarted.arguments`.

A model name alone selects too much. `coders-a` is backlog's `*` rule, and `.coder`,
`.improve` and `.rebase` all resolve to it (`workflow.fabro:81-88`). That set includes two
stages the 15-minute window does not fit:

- `improve`, `timeout="15m"`, slowest measured execution 180 seconds
  (`workflow.fabro:312-317` and `:334`). The window is the node's whole budget.
- `ci_fix_t1`, `timeout="20m"`, whose work starts by reading CI logs
  (`.fabro/workflows/_shared/review-merge/review-merge.fabro:534`). That stage should open
  with a run of reads.

So arming needs two conditions together. The stage's `requested_model` is `coders-a` or
`coders-b`, **and** the stage's own `timeout` is at least three times the detection window.
Today that selects `coder` at 180 minutes and `rework_t1` to `rework_t4` at 45 minutes. It
excludes `improve` and `ci_fix_t1`.

`coders` is not in the set. The scheduler's pools are `coders-a` and `coders-b`, and
`coders` is deliberately absent (`ops/scheduler/src/fabro_scheduler/config.py:38-41`). No
stylesheet rule in any of the three graphs names it.

### Stall

Trigger on `agent.llm.started` with no `agent.llm.first_output` for 10 minutes, then
interrupt and nudge. This detector arms on any agent stage.

Ten minutes clears the local time-to-first-token tail, whose p100 is about 3 to 4 minutes
under contention (`docs/perf/00`). It also leaves at least 5 minutes on the shortest agent
node in the repository, which is 15 minutes. Agent node timeouts here are 15m, 20m, 30m,
45m and 180m, so there is no single node timeout to size against. Thirty minutes is the
graph `stall_timeout` default and it is a different mechanism.

This rung sends **one** interrupt for each stage and never a second. A hung round emits
nothing, so the graph stall watchdog would cancel the run at 30 minutes. An interrupt
emits `agent.interrupt.injected` and `agent.steering.injected`, and those re-arm that
deadline (`execute.rs:92-107`). A ladder of interrupts would postpone the only protection
fabro has for this exact signature.

The glm-5.3 hang is this signature. It ran 15 minutes 39 seconds before a 502
(`docs/perf/00`, item 2). Its root cause was never isolated. The 502 came from LiteLLM and
ADR 0007 has since removed that path, so whether the hang survives is unmeasured.

### Context is a corroborator and never a trigger

`agent.warning context_window` near the threshold can tip a borderline streak over.
Context percentage alone must not nudge. The oversized task in `docs/perf/04` reached 80
percent and compacted correctly, and that was real work.

### Suppression

After any nudge, suppress re-detection for 15 minutes or until the next write-class tool
event, whichever comes first. Rapid-fire steers already collapse into one delivery
(`steering.mdx`), so this bounds judgment rather than traffic. The steering queue is also
bounded and drops on overflow (`AgentSteerDroppedReason::QueueFull`), which is a second
reason to send few messages.

## The nudge text

One fixed template, filled from the event envelope and the run inputs. No model writes it.

> Task nudger: `<node_label>` has made no file changes in 15 min (`N` read-only tool
> calls). Stop exploring and implement the change. If you are blocked, say what blocks
> you.

Name the symptom, give one directive, offer one escape. The agent already holds its issue
and its task brief, so the nudge redirects it. It never re-prompts it.

## Scope and placement

**Where.** A small Python service in the `~/fabro` compose project, built from
`ops/nudger/`, following `ops/scheduler/` in shape and in deploy.
`docker compose up -d --build nudger` names one service and leaves `fabro` alone, which
matters because a fabro restart fails every run in flight.

**Why not the scheduler.** The scheduler owns admission (ADR 0005). It also cancels a live
run under decision 15, so the boundary is blast radius rather than novelty. The nudger can
steer, interrupt, and post one message. It holds no GitHub token, so it cannot change a
label, and it has no cancel path.

**Why not cron.** Fifteen-minute granularity finds a livelock when the stage is already
half spent.

**Reading events.** Poll `GET /runs/{id}/events` with a `since_seq` cursor for each run,
every 15 to 30 seconds. There is no server-side event-name filter
(`handler/events.rs:249-289`). A measured run carries 451 to 517 LLM calls, plus one delta
event for each stream chunk (`docs/perf/00`). The cursor is what keeps the poll cheap.
`GET /runs/{id}/stages/{stage_id}/events` is the narrower read and it resets at each stage
boundary (`handler/events.rs:290-325`).

**Producer-agnostic.** The nudger reads fabro's run state, not the scheduler's lease
table, so it watches a hand-fired run the same as a dispatched one.

**Credentials.** The nudger reads the fabro dev token and the Discord webhook from
`~/fabro/nudger.env`, written once for each host. `fabro-monitor.sh` reads both with `ssh`
and `docker exec fabro-fabro-1` (`ops/fabro-monitor.sh:205-211`), and a sibling container
cannot do that. `ops/scheduler/` already uses the env-file pattern for the same reason. The
nudger holds no GitHub token, which is what keeps its blast radius to steer, interrupt and
one message.

## Alternatives weighed

- **Steer against fail-fast.** Sandcastle's watchdog killed the coder. Fabro already
  retries a failed stage, and `allow_partial=true` sends the partial work to the review
  ladder on an escalating model. Failing is reversible and it is already wired in. It
  still spends 45 minutes for each rework tier on a task the agent never started writing.
  Chosen: steer first, bounded so it can be no worse than failing.
- **Identity-based detection.** Sandcastle aborted after 5 consecutive identical tool calls
  against an unchanged worktree. Rejected. The failure we expect is a read loop over
  different files, and "identical" misses it. "No net file change" is the progress measure
  Sandcastle actually cared about and it degrades gracefully.
- **Context percentage as a standalone trigger.** Rejected. It false-positives on a
  legitimate large task (`docs/perf/04`).
- **Cancel the run as the terminal rung.** Rejected. See "Why rung 3 reports rather than
  cancels".
- **Raise `stall_timeout` instead.** Rejected as a substitute. It is a run-level cancel and
  it cannot see a livelock at all. `docs/research_improvements/01` proposes raising it to
  45 minutes for an unrelated reason, which is `watch_checks`. That change stays
  independent of this one.

## Consequences

- A third alert vocabulary joins the two that ADR 0004 keeps distinct. The forms are
  `fabro nudger: ...`, in-band `fabro ...`, and `fabro monitor: ...`. All three post to one
  webhook (`/storage/secrets/discord_webhook_url`), so the operator reads them in one place
  and tells the layers apart by prefix. ADR 0004 records that an alert is only useful at a
  rate a human will read, which is why the nudger must stay rare.
- The nudger is the first tool here that **steers** a live run. The scheduler already
  cancels one under decision 15.
- It reconciles with ADR 0004 rather than breaking it. The monitor stays judgment-free. The
  nudger carries judgment, but that judgment is a set of deterministic thresholds feeding a
  fixed template. No model reads the loop and no model writes the message.
- `ops/fabro-monitor.sh` gains one mechanical condition, C9 "nudger down". It copies C7's
  shape, which is a `GET /health` that must answer 200 inside 5 seconds. C9 is independent
  of C1, because the nudger is a separate container.
- The first build sends no steer. Rungs 1 and 2 turn on only after a human confirms one
  reported candidate.

## Open questions

Each of these needs a measurement, not a decision.

1. Does an `interrupt` abort a hung llama.cpp request? If it does not, the stall rung only
   re-points at a hung endpoint, and it still re-arms the stall watchdog.
2. What raises `agent.loop.detected`, and under what rule? If the agent runtime already
   detects a loop class, that event is either a free trigger or a rejected alternative.
3. How does a `stall_timeout` cancel classify? `Error::StallTimeout` becomes an engine
   error (`execute.rs:312-323`). If it classifies `deterministic`, the scheduler does not
   requeue it, and the issue strands on `agent-in-progress`.
4. Has a read-without-write livelock happened here? A run id and its event window would
   turn this ADR's premise from anticipation into measurement, and it is what releases
   rungs 1 and 2.
