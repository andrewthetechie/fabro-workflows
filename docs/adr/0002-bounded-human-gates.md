# Human gates hold a scheduler slot, so every gate is bounded and degrades to the issue

**Status:** accepted (2026-09-16). ADR 0012 replaces it for `issue-triage`. It still applies to `backlog`'s `human_rescue` gate.

The `issue-triage` workflow asks a human the questions the repository cannot answer.
Fabro's mechanism for that is a `shape=hexagon` human gate, and the naive spelling —
a gate with no `timeout`, waiting until someone gets to it — is unusable in this
deployment. Four facts, read out of `context/fabro` at 0.354.0-nightly.0:

- **A blocked run occupies a scheduler slot.** `counts_toward_scheduler_capacity`
  matches `Starting | Running | Blocked { .. } | Paused { .. }`
  (`lib/apps/fabro-server/src/server.rs:2905-2912`), and a human gate is exactly
  `Blocked { HumanInputRequired }` (`fabro-types/src/status.rs:375-377`). With
  `max_concurrent_runs = 3`, three parked gates admit nothing: no `Runnable` run ever
  starts.
- **Nothing reaps a blocked run.** The sandbox is stopped only in FINALIZE, after a
  terminal state (`fabro-workflow/src/pipeline/finalize.rs:202-216`). The graph's
  `stall_timeout` does not help: the watchdog parks its deadline while the run is
  blocked and restarts a full budget on the first unblock
  (`pipeline/execute.rs:78-115`). There is no idle timeout and no maximum blocked
  duration anywhere in `[server.scheduler]`.
- **A gate outlives nothing.** `Blocked` is in `RECONCILABLE_STATUSES`, so a server
  restart appends `run.failed` — "Fabro server restarted before the run reached a
  terminal state" (`server.rs:3101-3138`). A gate held over a deploy is lost.
- **Only a live run can be answered.** Every answer path — REST, the web UI dock,
  `fabro run attach`, Slack, MCP — funnels through `claim_run_answer_transport`,
  which 409s when the worker is gone (`server.rs:3042-3059`). An answer typed after
  the run dies has nowhere to land.

So: **every human gate in this deployment carries an explicit `timeout` and a
`human.default_choice` that lands on an asynchronous path, and the questions are
published where they survive the run.** In `issue-triage` the gate waits 30 minutes
and its default choice is `post_questions`, which writes the batch to the GitHub issue
as a comment and exits. The 30 minutes buy the cheap outcome — someone at a keyboard
answers, the run re-triages immediately and finishes — and cost at most one slot for
half an hour when nobody is.

The alternative designs were an unbounded gate, rejected on the four facts above; and
no gate at all, posting questions and exiting every time, rejected because it throws
away the same-minute answer that makes an idle-capacity workflow feel responsive. A
timed-out gate with **no** `default_choice` is worse than either: `human.rs:315`
returns `retry_classify`, which re-asks the question rather than failing.

## Consequences

- `human.default_choice` must name a node that is also the target of an outgoing edge
  from the gate. Fabro sets `suggested_next_ids` straight from the attribute string
  with no validation against the graph, so a typo is a dangling route at runtime, not
  a validation error.
- The capacity gate in `issue-triage` makes gate starvation structurally impossible:
  a triage run starts only when `runs.scheduler_slots_used` is 1 (itself), so at most
  one triage run exists and at most one gate is ever blocked. This property is load
  bearing — weakening the capacity check re-opens the starvation this ADR is about.
- The same property gives stale-claim recovery for free. If `acquire` is running, no
  other triage run exists, so any `triage-in-progress` label it finds was abandoned
  (restart during a gate, most likely) and can be reclaimed without a timestamp.
- **`backlog`'s `human_rescue` gate is unbounded and inherits every fact above.** It
  holds one of three slots until someone answers and dies on the next deploy. That is
  pre-existing and is not changed here; it is recorded so the next person does not
  rediscover it during an incident.
- Discord cannot answer a gate — Fabro's chat integration for that is Slack. The
  Discord hook on `stage_start` fires just *before* the gate blocks, so it is a
  notification only, and the answer is a click into the web UI at `/runs/:id`. Slack
  is the upgrade if 30-minute windows are routinely missed.
