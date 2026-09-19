# Fabro reaches the inference providers directly, and LiteLLM leaves the workflow path

**Status:** proposed (2026-09-19)

Every workflow in this repository reaches every model through one fabro provider. That
provider has `base_url = "https://litellm.herrington.services/v1"` and nine model rows. The
models are three boxes on the LAN, z.ai, and Kimi. A request for a box on `10.10.0.29`
leaves the host, crosses the ingress at `10.10.98.18`, and returns to the LAN.

ADR 0003 chose that shape to move routing out of the graphs. It said "the routing decision
moves to LiteLLM, where it can be changed without touching a graph". Three changes make
that reason obsolete.

LiteLLM no longer routes anything the scheduler needs. Load balancing was the reason for
it. The `coders` group has two deployments, and LiteLLM balances them. The scheduler exists
because that balancing was the problem.

The scheduler leases one box per run and passes `coder_pool`. The `coders-a` and `coders-b`
groups each have one deployment. So for every coder stage, LiteLLM resolves a name and adds
a hop. Nothing uses the group that LiteLLM would balance. `ops/README.md` says dispatching
through it "would reintroduce the contention the scheduler exists to remove".

The fallback ladder now lives in this repository, and it exists in two places. Both
workflow TOMLs carry a full `[run.model.fallbacks]` chain. The chain matches the requested
model name. LiteLLM still carries the older copy,
`fallbacks: [{"high-reasoning": ["kimi-k3"]}]`, with `num_retries: 2` (`ops/README.md`).

ADR 0003 forbade that duplicate, in the other direction. It said "a second fallback chain in
Fabro would race it and make the effective route unknowable from either side". Both places
now define the route, and the copy in git is the one that runs.

Per-box providers need no graph change. The settings reference gives `[llm.providers.<id>]`
a `base_url`, an `adapter`, a `codec`, and an `auth` scheme that includes
`{ type = "none" }`. A model identity is the pair `(provider, model id)`.

A model row has no `base_url` of its own. Only the provider names an endpoint. So one
provider cannot hold a separate box for each model.

The hop cost time in one measured run. Run `01M2X5G600S0T1C5XMN0HFEB5G` ran `coder v2` on
`coders-a`. One request stalled. Then `agent.llm.retry` followed at 938s, `agent.llm.retry`
at 940s, and `agent.error` at 940s. All three carried `kind: server`, `status: 502`, and
this body:

```
<center><h1>502 Bad Gateway</h1></center><hr><center>nginx/1.31.1</center>
```

Three identical hangs consumed roughly 47 minutes of an 82-minute stage. The ingress ended
each hang, not the box and not LiteLLM. The box stayed healthy. `/health` returned
`{"status":"ok"}`, and `/slots` returned `is_processing: false` while the run waited. The
prompt held 51,820 tokens of a 262,144 context. So neither a sick box nor prefill explains
the hangs.

`docs/direct-providers/` carries the plan: four tasks, the endpoint mapping, the provider
configuration, and the two checks that need a scratch overlay first.

## Consequences

- Name the providers directly, one provider for each endpoint. The model ids do not change,
  so `coder_pool` and the stylesheets do not change. `coders-a` now resolves to a provider
  whose `base_url` is the box, not to a model group inside a gateway. Decision 12 stands
  unchanged. Nothing in the graphs, the queue, or the lease table changes.
- Removing the gateway removes the only limit on a hung request. The same change must add
  another limit. LiteLLM's `timeout = 600.0` did not stop the stalled streaming request.
  `stream_timeout` is unset, and the router timeout is `6000.0`.
- Nothing in `lib/components/fabro-llm/src` sets a request timeout on an agent call. The
  only `timeout` in that crate is in `probe.rs`. `ClientOptions::apply` sets an HTTP client,
  retry middleware, and attachments on the `lithos_llm` `ClientBuilder`, and no timeout.
  The 940s bound was the ingress.
- The location of the replacement remains unresolved. No `[llm.*]` or `[run.model.*]` knob
  sets it. The only `[llm]` timeouts are `startup_timeout` and `tool_timeout`, and both
  cover MCP. The likely location is a timeout on the `lithos_llm` builder, and that may
  need an upstream change. The migration is incomplete without it.
- The change loses `max_parallel_requests = 1` for each box. LiteLLM serialises a
  single-slot box today. The lease means one run targets a box, and the stages of a run are
  sequential. So the setting is largely redundant. It is still a safety net that the change
  removes and does not replace. The settings reference offers no concurrency cap for one
  provider.
- Key custody moves to the fabro host, for hosted models only. Today fabro holds one
  credential with a model allowlist, and the upstream secrets live in the cluster.
  Afterwards `ZAI_API_KEY` and `KIMI_API_KEY` live in the fabro vault. The local boxes need
  no key. This part is a trade, not a simplification. It is why the hosted models migrate
  last.
- This ADR supersedes ADR 0003. ADR 0003 closed with a warning. LiteLLM keeps models in
  Postgres: "a run's actual model is then only discoverable from LiteLLM, not from this
  repository". That statement inverts, because the overlay is in git and subject to drift
  checks.
- The goal of ADR 0003 survives, because a model row takes an `aliases` list. The mechanism
  does not survive. The consequence that `issue-triage` deliberately carries no
  `[run.model.fallbacks]` entry for `high-reasoning` also reverses. Six files cite ADR 0003
  and need dated corrections: `.fabro/workflows/issue-triage/workflow.toml`,
  `docs/issue-triage/00`, `02` and `05`, `docs/perf/02`, and `ops/README.md`.
- Two wire shapes replace the translation that LiteLLM does. The Kimi coding endpoint is
  Anthropic-shaped, and LiteLLM reaches it as `anthropic/kimi-for-coding`. It needs an
  Anthropic adapter and codec. z.ai is OpenAI-shaped. A wrong pair fails a run.
- `coders` needs a new target. It is the server `default_model`, and utility calls such as
  generated run titles use it. A single box as the target would reintroduce unpinned box
  use, and the scheduler prevents that.
- LiteLLM keeps running. It serves `StrixQwen27B` and `StrixQwen35B` on `10.10.0.29:8080`,
  and `deepseek` on `10.10.0.30:8888`. Fabro references none of them. This ADR takes fabro
  off LiteLLM. It does not decommission LiteLLM.

## Rejected alternatives

Keep LiteLLM and drop only the ingress. That option sets `base_url` to the LiteLLM service
instead of its hostname. It is the smallest change that would have prevented the 47
minutes. This ADR rejects it for three reasons.

It keeps two routing layers for one name lookup. It keeps the duplicate fallback definition
that ADR 0003 called unknowable. It provides nothing that fabro lacks, because the scheduler
owns per-box pinning, the ladder is in `[run.model.fallbacks]`, and nothing uses the
balanced group.

Keep pinning in LiteLLM and drop the lease from the scheduler. That option gives each box
one model group, and fabro chooses the box by model name. ADR 0006 rejected it.

A `base_url` is server-global settings. `RunIntent` can only name a provider id. A model
group cannot express a lease. The job of the scheduler is to make the box-to-run mapping
visible to the queue. LiteLLM cannot answer "which run holds `coders-a`".
