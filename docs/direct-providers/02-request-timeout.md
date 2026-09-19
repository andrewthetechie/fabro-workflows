# Bound a hung inference request

## Outcome

A stalled inference request ends in bounded time and the run records it. The bound today is
the Kubernetes ingress at roughly 940s, and tasks 01 and 03 remove that ingress from the
path.

## Context Pack

- ADR 0007 carries the measurement and the consequence.
- No `[llm.*]` or `[run.model.*]` knob sets a request timeout. The only timeouts in the
  `[llm]` tables are `startup_timeout` and `tool_timeout`, and both cover MCP.
- The only `timeout` in `lib/components/fabro-llm/src` is in `probe.rs`. `ClientOptions::apply`
  sets an HTTP client, retry middleware, and inline attachments on the `lithos_llm`
  `ClientBuilder`, and no timeout.
- `context/fabro` holds the fabro source. `lib/components/fabro-llm/src/client.rs` holds the
  builder.
- LiteLLM's per-deployment `timeout = 600.0` did not cover a streaming request, and the
  router timeout is `6000.0`.

## Implementation Contract

The location remains open, and finding it is the first job. Three candidates, in order of
preference:

1. A timeout on the `lithos_llm` `ClientBuilder`, set from `fabro-llm`. This changes fabro,
   which is a separate repository, so this series cannot complete it.
2. A new fabro setting for a request timeout. The same repository problem applies.
3. A bound at the endpoint. `llama.cpp` has no per-request deadline, so this needs whatever
   sits in front of the box.

If the answer is candidate 1 or 2, record it and stop. Do not hold task 01 or task 03 for
this task. The ingress bounds a hang at roughly 940s today, which is poor, and an unbounded
hang is worse. If the bound cannot move into fabro, keep a proxy in front of the boxes until
it can.

## Acceptance Criteria

- [ ] Record the chosen location, with the file and line that sets the bound.
- [ ] A deliberately stalled request ends before the bound, and the run records
      `agent.llm.retry` or `agent.error`.
- [ ] The bound is shorter than the 940s the ingress allowed.

## Dependencies

None. Read it before task 01 or task 03 lands.

## Risk

4. This may need an upstream change, and then nothing in this repository closes it. The
recovery is a proxy in front of the boxes, which is the LiteLLM shape this series removes.
