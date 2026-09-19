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

---

## Findings & resolution — recorded 2026-09-19

**Determination: candidate 1 (a total request timeout on the `lithos_llm` `ClientBuilder`, set
from `fabro-llm`). Record it and stop — it needs a change in `fabro-sh/fabro`, a separate
repository this series does not modify.**

### Where the bound must be set (the chosen location)

The mechanism exists and is unused. `lithos_llm`'s `ClientBuilder` carries a total call
budget that fabro never sets:

* **`lithos_llm::ClientBuilder::default_timeout`** — the total budget for a call, including
  middleware, credentials, retries, and stream consumption. Set at
  `lib/client/mod.rs:385` in the `lithos-llm` checkout (`a1e3fd3`); the default is
  **`None`** (`lib/client/mod.rs:350`), i.e. no bound.
* fabro's wiring point that must call it is **`ClientOptions::apply`** in
  `lib/components/fabro-llm/src/client.rs:141` — the method that stamps an HTTP client,
  retry middleware and attachment inlining onto the builder, and currently nothing else
  (it is where `builder.http(...)` and the retry `middleware(...)` land, lines 143/145).
  `ClientOptions` (client.rs:94) has no `default_timeout` field.

### The path is exercised, and it sets no bound

* The server builds its LLM client with `http_client: None`
  (`lib/apps/fabro-server/src/serve.rs:801`), so `options.http` is `None`
  (`lib/apps/fabro-server/src/server.rs:1695` / `resolve_llm_client_from_source`,
  server.rs:1690), and `factory_http::http_client()`
  (`lib/foundation/fabro-http/src/lib.rs:196`) builds a `reqwest` client with **no**
  `read_timeout`. The `HttpClientBuilder::read_timeout` that exists (lib.rs:183) is never
  applied to the LLM path — but even it only bounds idle reads, not a slow-dribbling
  stream.
* What fabro leaves to lithos defaults: `connect_timeout` 30s, `stream_idle_timeout`
  **300s** (`lithos-llm lib/adapter.rs:20`), `default_timeout` `None`. So on the direct
  path, a *fully silent* provider is bounded at ~300s by lithos, but a request that
  connects and then streams without ever completing — the shape ADR 0007 measured at
  940s — has **no total bound**. Removing the ingress (tasks 01/03) removes even that
  940s backstop for exactly that shape.
* Nothing in `lib/components/fabro-llm/src` sets a request timeout on an agent call; the
  only `timeout` in the crate is the probe in `probe.rs` (test endpoints), per ADR 0007.
* Candidate 2 (a new fabro **setting** for the request timeout) resolves to the same
  repository: it is the source of the value, while `default_timeout` is where it is
  consumed. Both are `fabro-sh/fabro` changes and both are out of this series' reach.
* Candidate 3 (a bound at the endpoint) is not available for the boxes that lost the 47
  minutes: they are `llama.cpp`, which has no per-request deadline. Only the `spark` box
  is vLLM (`--api-server-timeout`), and it is not the measured loss.

### Recommended upstream change (for when fabro is next touched)

Add `default_timeout: Option<Duration>` to `fabro_llm::ClientOptions`, then in `apply`
stamp it on the builder (`builder = builder.default_timeout(...)` when set, beside the
`http`/retry calls at client.rs:141-150), and surface a setting so operators can bound a
hung stream without redeploying fabro. Until then keep an interim bound in front of the
boxes; the right interim value is a total timeout shorter than the 940s the ingress
allowed (for example a reverse proxy's `proxy_read_timeout`), matching the task's
acceptance `agent.llm.retry`/`agent.error` on stall.

**Acceptance status:** the *location* criterion is met here. The stalled-request and
shorter-than-940s criteria cannot be demonstrated from this repository because setting the
bound is an upstream `fabro-sh/fabro` change. Per the contract, task 02 is recorded and
stops; it does not hold task 01 or task 03.
