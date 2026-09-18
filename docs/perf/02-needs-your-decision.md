# Changes that need your call

Nothing here is applied. Each one either touches how the workflow works, changes
the operator's model-quota policy, or reaches outside this repository.

Ordered by measured value.

---

## 1. Give `glm-5.3` a request timeout in LiteLLM

**Worth: 60 minutes on run `01M2RX2SGJJT09WK5FS8GYJCDE`. The single largest item
in this document.**

Twice in that run, a `glm-5.3` call never returned. LiteLLM answered
`502 Bad Gateway` after **15m39s** both times — the same interval to the second,
which is a gateway timeout, not a coincidence:

```
00:52:28  agent.llm.started            model=glm-5.3
01:08:07  agent.llm.retry  attempt=1   502 Bad Gateway   delay_secs=0.29   (+15m39s)
01:21:57  stage.failed     "handler timed out after 1800000ms"             (+30m00s)
01:22:01  stage.started    attempt 2 of 2 — model=glm-5.3 again
```

Both stages succeeded on the retry, so the run reported success and the hour is
invisible in its outcome. Run `01M2RB5QBNBPS6C0F9904V25AX` had **zero** such
hangs across 305 `glm-5.3` calls — it is intermittent, which is why it has
survived this long.

Fabro cannot fix this. There is no per-request LLM timeout in its configuration;
the only backstop is the node's `timeout`, and the retry after a 502 re-requests
the same model with a 0.29s delay. This matches the `glm-5.3` stall already
handed off for investigation.

**The change:** set a request timeout on the `glm-5.3` model row in LiteLLM at
`litellm.herrington.services`, in the region of 120s. A hung upstream then
returns in two minutes instead of fifteen, and fabro's existing retry and
fallback machinery gets a chance to act.

**Why you and not me:** that gateway serves more than fabro, and I am not going
to change a shared service's routing without you saying so. Tell me to and I
will.

**Cheap insurance to pair with it:** `[run.model.fallbacks]` in
`backlog/workflow.toml` already lists `"glm-5.3" = ["litellm:kimi-k3"]`. It never
fired here, because the failure arrives as a stage-level `transient_infra`
timeout rather than an LLM error the fallback plan responds to, and a stage retry
restarts the plan at position 0 — the same model. Worth knowing that the fallback
chain does **not** protect against this failure shape.

---

## 2. Move the review classes off `glm-5.3`

**Worth: the same 60 minutes, by a different route — but it costs you something.**

The stylesheet in `backlog/workflow.fabro`:

```
.decomp          { model: glm-5.3; }
.improve         { model: glm-5.3; }
.review          { model: glm-5.3; }
.coder-t4        { model: glm-5.3; }
```

`glm-5.3` was the most-used model in run 1 — 251 of 451 calls — and every stall
was on it.

I did not do this on my own, for two reasons. The evidence is genuinely mixed:
305 `glm-5.3` calls in run 2 were fine, with a *better* p90 than `coders`. And
the obvious substitute, `glm-4.7`, is the same z.ai coding plan behind the same
LiteLLM, so it may share the fault — swapping could buy nothing and spend your
quota differently for it. The stylesheet comment records "z.ai before kimi" as a
deliberate quota policy, and that is yours to set.

**If you want it:** `.decomp`, `.improve` and `.review` → `glm-4.7` is one commit
and stays inside the z.ai plan. `.coder-t4` is harder — `.coder-t2` is already
`glm-4.7`, so reusing it would collapse two rungs of the escalation ladder into
one, and the only untaken alternatives are `kimi-k3` and `long-context`, both of
which your fallback comment says never take a coder role.

**There is a better lever you already built.** `settings.toml` defines a
`high-reasoning` **role alias** — "Resolves to glm-5.3 in LiteLLM with kimi-k3 as
overflow", with ADR 0003 saying workflows should ask for the role "so the model
behind it can change without a graph edit and a deploy". The review classes ask
for `glm-5.3` by name and bypass it. Pointing them at `high-reasoning` would turn
this from a graph edit into a LiteLLM routing change — which is what you designed
it for. It is still a model change, so it is still your call.

---

## 3. `coders` queues — the biggest *systematic* cost, and not a workflow problem

**Worth: ~40 minutes per run, in both runs measured.**

Time from `agent.llm.started` to `agent.llm.first_output`:

| model | run 1 median / p90 / max | run 2 median / p90 / max |
|---|---|---|
| `coders` | 3.0s / 30.9s / **194.6s** | 3.8s / 31.5s / **249.0s** |
| `glm-5.3` | 5.4s / 11.8s / 27.9s | 5.4s / 6.3s / 16.9s |
| `kimi-k3` | 4.6s / 10.3s / 17.6s | 3.2s / 4.0s / 4.3s |

**Half of all "inference" time in run 1 — 66 of 135 minutes — is waiting for a
first token.** A healthy median with a p90 ten times larger and a p100 of three
to four minutes is the signature of queueing, and `coders` is "Local coders
(strix ×2)" — two local GPUs. The hosted models, on the same LiteLLM, hold a p90
under 12s.

This is also what killed `ci_fix_t1`: it was making progress, it just could not
fit 20 minutes of wall clock when a third of it went to first-token waits.

I have not touched it because I cannot tell from the run store whether the queue
is inside LiteLLM's concurrency settings for the `coders` pool or on the strix
boxes themselves. **Where to look:** the `coders` deployment's `max_parallel_requests`
/ router settings in LiteLLM, against `server.scheduler.max_concurrent_runs = 2`
and the number of agent sessions each run holds. If two concurrent runs are
enough to saturate two GPUs, lowering fabro's concurrency to 1 would make each
run faster while halving throughput — probably the wrong trade, but it is the
measurement to take first.

---

## 4. The environments are sized for a much smaller box

**Worth: unmeasured, probably small for wall time. Cheap and low-risk.**

| environment | now | the host has |
|---|---|---|
| `python`, `python-node`, `ts` | 2 CPU / 4 GB | 16 CPU / 30 GB |
| `rust-node` | 4 CPU / 8 GB | |

With `max_concurrent_runs = 2`, peak commitment is 8 CPU and 16 GB on a box with
16 and 30. Raising the three small profiles to 4 CPU / 8 GB keeps peak at 16 GB.

This matters more now than before: with Postgres inside the sandbox and full test
suites in reach, `validate` stops being a one-minute lint. lawncare-saas's
backend suite runs `pytest -n auto` — 2430 tests in 135s at 2 CPUs.

It is server state, not config, so it is a `PUT /api/v1/environments/<id>` rather
than a commit — which is why it is here rather than done. It cannot make a run
slower.

---

## 5. Repository contract changes — `03-repo-contract-changes.md`

Four other repositories, live and deploying. The images now support tests those
repositories currently exclude; taking them up means editing `.fabro/ci.sh` in
each. Separate document, separate decision, and I have not pushed anything.

---

## What I deliberately did not propose

- **Fewer or cheaper reviews.** Out of scope by your rule, and the measurements
  do not support it anyway: all 13 agent reviews in run 1 cost 46 minutes
  together, against 100 minutes of pure waste.
- **A smaller graph.** Command nodes and gates cost 0s each. `tool_time_ms` for
  the whole of run 1 was 8.7 minutes. There is nothing there.
- **Mount-based caching, or a Postgres service.** Not available — see
  `00-overview-and-measurements.md`. They would need a patch to fabro itself,
  and it is a small one: plumbing environment config through to the
  `DockerProviderConfig` that `fabro-sandbox/src/docker.rs:47` currently
  hardcodes would expose the driver's existing `binds` *and* `sidecars` support
  and make both possible. Worth an upstream issue; the driver side already works.
