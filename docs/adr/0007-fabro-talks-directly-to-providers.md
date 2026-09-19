# Fabro talks to the inference providers directly; LiteLLM leaves the workflow path

**Status:** proposed (2026-09-19)

Every workflow here reaches every model — two llama.cpp boxes on the LAN, a vLLM box on
the LAN, z.ai, and Kimi — through one fabro provider whose `base_url` is a Kubernetes
ingress: `[llm.providers.litellm] base_url = "https://litellm.herrington.services/v1"`,
with nine model rows underneath it. A request for a box on `10.10.0.29` leaves the host,
crosses the ingress at `10.10.98.18`, and comes back to the LAN.

ADR 0003 chose that shape to move routing out of the graphs — "the routing decision moves
to LiteLLM, where it can be changed without touching a graph". Three things have since
made that obsolete.

**LiteLLM no longer routes anything the scheduler cares about.** Load balancing was the
point: `coders` is two deployments and LiteLLM balances them. The scheduler exists
because that balancing was the problem. It leases one box per run and passes `coder_pool`,
and `coders-a` and `coders-b` have one deployment each — so for every coder stage LiteLLM
is a name lookup and a hop. The group it *would* balance is deliberately unused, because
dispatching through it "would reintroduce the contention the scheduler exists to remove"
(`ops/README.md`).

**The fallback ladder moved into this repository, and now exists twice.** Both TOMLs carry
a full `[run.model.fallbacks]` chain keyed on the requested model name. LiteLLM still
carries the older copy — `fallbacks: [{"high-reasoning": ["kimi-k3"]}]`, `num_retries: 2`
(`ops/README.md`) — exactly the duplicate ADR 0003 forbade in the other direction: "a
second fallback chain in Fabro would race it and make the effective route unknowable from
either side." The route is now defined in both places, and the copy in git is the one that
runs.

**Per-box providers need no graph change.** `[llm.providers.<id>]` takes a `base_url`, an
`adapter` (`openai-compatible`), a `codec` (`openai-chat`) and an `auth` scheme including
`{ type = "none" }`; a model's identity is the **pair `(provider, model id)`**. A model row
has no `base_url` of its own — the provider is the only level that names an endpoint —
which is why "one provider, each model pointed at its own box" is not available.

**What the hop cost, measured.** Run `01M2X5G600S0T1C5XMN0HFEB5G`, `coder v2` on
`coders-a`: one stall, then `agent.llm.retry` at 938s, `agent.llm.retry` at 940s,
`agent.error` at 940s — all `kind: server`, `status: 502`, body
`<center><h1>502 Bad Gateway</h1></center><hr><center>nginx/1.31.1</center>`. Roughly 47
minutes of an 82-minute stage, in three identical hangs, terminated by the **ingress**
rather than by the box or by LiteLLM. The box was healthy throughout (`/health` →
`{"status":"ok"}`, `/slots` → `is_processing: false` while the run waited) and the prompt
was 51,820 tokens against a 262,144 context, so neither a sick box nor prefill explains it.

## Consequences

- **Name providers directly, one per endpoint:** `box-a` at `http://10.10.0.29:8000/v1`,
  `box-b` at `http://10.10.0.56:8000/v1`, `spark` at `http://10.10.0.30:8888/v1`, each
  `auth = { type = "none" }`; `zai` at `https://api.z.ai/api/coding/paas/v4` and `kimi` at
  `https://api.kimi.com/coding`, both bearer. Model ids do not change, so `coder_pool` and
  the stylesheets do not change — `coders-a` simply resolves to a provider whose `base_url`
  is the box. **Decision 12 is preserved exactly**; nothing in the graphs, the queue or the
  lease table is touched.
- **Removing the gateway removes the only thing bounding a hung request, and that must be
  replaced in the same change.** LiteLLM's own `timeout = 600.0` did *not* cut off the
  stalled streaming request (`stream_timeout` unset, router timeout `6000.0`), and nothing
  in `lib/components/fabro-llm/src` sets a request timeout on an agent call — the only
  `timeout` in that crate is in `probe.rs`, and `ClientOptions::apply` configures the
  `lithos_llm` `ClientBuilder` with an HTTP client, retry middleware and attachments, and
  no timeout. The 940s bound *was* the ingress. Where the replacement lives is unresolved:
  there is no `[llm.*]` or `[run.model.*]` knob for it (the only `[llm]` timeouts are
  `startup_timeout` and `tool_timeout`, both MCP), so it is probably a timeout on that
  `lithos_llm` builder, possibly an upstream change. The migration is incomplete without it.
- **`max_parallel_requests = 1` per box is lost.** LiteLLM serialises a single-slot box
  today. The lease means one run targets a box and a run's stages are sequential, so this
  is largely redundant — but it is a safety net removed rather than replaced, and the
  settings reference offers no per-provider concurrency cap.
- **Key custody moves onto the fabro host, for the hosted models only.** Today fabro holds
  one credential with a model allowlist and the upstream secrets live in the cluster;
  afterwards `ZAI_API_KEY` and `KIMI_API_KEY` live in the fabro vault. The local boxes need
  no key. This is the one part that is a trade rather than a simplification, and it is why
  the hosted models migrate last.
- **This supersedes ADR 0003**, whose closing warning — that LiteLLM keeps models in
  Postgres, so "a run's actual model is then only discoverable from LiteLLM, not from this
  repository" — inverts, since the overlay is in git and drift-checked. Its *goal* survives
  (a model row takes an `aliases` list, so the model behind a role changes without a graph
  edit); its mechanism does not. Its consequence that `issue-triage` "deliberately carries
  **no** `[run.model.fallbacks]` entry for `high-reasoning`" also reverses.
  `.fabro/workflows/issue-triage/workflow.toml`, `docs/issue-triage/00`, `02` and `05`,
  `docs/perf/02` and `ops/README.md` cite 0003 and need dated corrections.
- **Two wire shapes replace LiteLLM's translation.** Kimi's coding endpoint is
  Anthropic-shaped (`anthropic/kimi-for-coding` via LiteLLM) and needs
  `adapter = "anthropic"` with `codec = "anthropic-messages"`; z.ai is OpenAI-shaped.
  Getting it wrong is a per-run failure, not a routing change.
- **`coders` needs a home.** It is the server `default_model`, used for utility calls such
  as generated run titles. Pointing it at one box would quietly reintroduce unpinned box
  use, which is what the scheduler prevents; the `long-context` model carries
  `small_default = true` and its `spark` box is the more honest target.
- **LiteLLM keeps running.** It serves `StrixQwen27B` and `StrixQwen35B`
  (`10.10.0.29:8080`) and `deepseek` (`10.10.0.30:8888`), none of which fabro references.
  This takes fabro off LiteLLM; it does not decommission it.
- **Order:** the three local boxes first (`auth = "none"`, no keys, verifiable with one run
  per box — this is where the measured 47 minutes is recovered), then the timeout, then the
  hosted models, then the fabro-side LiteLLM model rows, so nothing can silently route
  through the gateway again.

## Rejected alternatives

**Keep LiteLLM but drop the ingress** — point `base_url` at the LiteLLM service rather than
its hostname. This is the smallest change that would have prevented the 47 minutes, which
is a real argument for it. It is rejected because it keeps two routing layers to provide one
name lookup, keeps the duplicate fallback definition ADR 0003 itself called unknowable, and
buys nothing fabro does not already have: per-box pinning belongs to the scheduler, the
ladder is in `[run.model.fallbacks]`, and the group that would benefit from balancing is
unused. A shorter path to the same gateway is still a gateway.

**Keep pinning in LiteLLM and drop the scheduler's lease.** One model group per box, with
fabro choosing by name. Rejected by ADR 0006: a `base_url` is server-global settings,
`RunIntent` can only name a provider id, and a model group cannot express a lease. The
scheduler's job is that the box-to-run mapping is visible to the queue, and LiteLLM cannot
answer "which run holds `coders-a`".

## Not yet done

Nothing here has been applied. Two things need a scratch copy of the overlay before step
one. Whether **duplicate model ids across providers** resolve unambiguously — the reference
says two providers may use the same id, so `coders-a` should not exist on both `litellm`
and `box-a` and be expected to pick one, and whether the cutover is a swap or a qualified
`provider/model` selector is untested. And whether llama.cpp accepts the `api_model` sent:
the boxes report `deepseek-v4-flash-0731-iq3-xxs` from `/v1/models` and `api_model` defaults
to the model id, so probably a non-issue, but probable is not validated.

Neither gate in this repository covers a provider change — `fabro validate` reads graphs and
workflow TOMLs, `ops/check-routing-schemas.py` reads node routing. The overlay is settings,
and the only way to know it resolves is to load it.
