# Direct providers — Overview & canonical contracts

**Read this first.** Every task in this folder assumes the findings and contracts
recorded here. ADR 0007 carries the decision and its reasoning. This document carries the
plan.

## What this is

Four tasks that remove LiteLLM from the fabro path. Fabro then names each inference
endpoint as its own provider.

ADR 0007 records the decision and is **proposed, not accepted**. Two consequences follow.
Task 01 and task 02 need no new secret and no new policy call. Task 03 moves two provider
keys onto the fabro host, which is the operator's decision and not an implementation
detail. Task 04 removes the old rows and waits for task 03.

Nothing in this series is live-on-merge in the sense `.fabro/workflows/` is. Every task
edits server state. The overlay is at `/storage/.home/settings.toml` inside the fabro
container, and task 03 also writes to the fabro vault.

## The gate: nothing in this repository covers a provider change

`fabro validate` reads graphs and workflow TOMLs. `ops/check-routing-schemas.py` reads node
routing. Neither reads the overlay. A provider change is correct only when the overlay
loads and the server reports it. Every task therefore ends by loading the overlay and
reading the result back.

The restart rule from `ops/README.md` applies only to `max_concurrent_runs`, which the
server copies out once at startup. A provider or a model added to the overlay takes effect
with no restart, because `replace_runtime_settings` rebuilds the catalog on the 5s poll.
**Do not restart the container to apply a provider change.** A restart fails every
in-flight run (scheduler overview finding 5). Use the start-time check in `ops/README.md`
only if a task changes `max_concurrent_runs`, and no task here does.

## The mapping this series replaces

Read from the live LiteLLM on 2026-09-19 through `/model/info`. Nine fabro model rows reach
five upstreams through one provider.

| fabro model id | upstream | key |
|---|---|---|
| `coders` | `http://10.10.0.29:8000/v1` | none |
| `coders-a` | `http://10.10.0.29:8000/v1` | none |
| `coders-b` | `http://10.10.0.56:8000/v1` | none |
| `long-context` | `http://10.10.0.30:8888/v1` | none |
| `glm-4.7` | `https://api.z.ai/api/coding/paas/v4` | `ZAI_API_KEY` |
| `glm-5.3` | `https://api.z.ai/api/coding/paas/v4` | `ZAI_API_KEY` |
| `high-reasoning` | `https://api.z.ai/api/coding/paas/v4` | `ZAI_API_KEY` |
| `kimi-for-coding` | `https://api.kimi.com/coding` | `KIMI_API_KEY` |
| `kimi-k3` | `https://api.kimi.com/coding` | `KIMI_API_KEY` |

`coders` has two deployments and LiteLLM balances them. The scheduler does not use it, and
no node resolves to it except the server's own `default_model`. See task 04.

## Findings established against the live deployment

Measured on 2026-09-19 against the fabro container, LiteLLM, and the two boxes.

### 1. The fabro container reaches every upstream directly

`wget` from inside `fabro-fabro-1` returns the model list from
`http://10.10.0.29:8000/v1/models` and `http://10.10.0.56:8000/v1/models`, and returns
HTTP 401 from `https://api.z.ai/api/coding/paas/v4/models`. A 401 is proof of reachability
and proof that fabro sent no credential, so the only missing piece for z.ai is the key.

### 2. The hop is a Kubernetes ingress on a separate VLAN

`litellm.herrington.services` resolves to `10.10.98.18`. The boxes are on `10.10.0.x`. So a
request for a box leaves the host, crosses the ingress, and returns to the LAN. That ingress
returned the 502 that ended each of three hangs in run `01M2X5G600S0T1C5XMN0HFEB5G`. ADR
0007 carries the measurement.

### 3. The settings reference sets the shape of a provider

From the server settings reference:

| Key | Value that matters here |
|---|---|
| `adapter` | `openai-compatible` for the three Local boxes and z.ai |
| `codec` | `openai-chat` for the same four, and `anthropic-messages` for Kimi |
| `auth` | `{ type = "none" }` for a Local box, `{ type = "bearer" }` otherwise |
| API key | `<PROVIDER>_API_KEY`, upper case with `-` and `.` as `_` |

A model row takes `api_model`, `aliases`, `limits`, `capabilities`, `pricing`, `family` and
`small_default`. It has **no `base_url`**. The provider is the only level that names an
endpoint, which is why each endpoint needs its own provider.

### 4. No provider key is on the fabro host today

`~/fabro/.env` holds `FABRO_PORT`, `SESSION_SECRET`, `FABRO_WEB_URL` and `FABRO_VERSION`.
`~/fabro/scheduler.env` holds `GITHUB_TOKEN` and `FABRO_API_TOKEN`. Neither holds `ZAI_API_KEY`
or `KIMI_API_KEY`. Those live in the cluster for LiteLLM. Task 03 moves them.

### 5. `coders` is the server `default_model`

The overlay sets `default_model = "coders"`, and the server uses it for utility calls such
as generated run titles. Task 04 must give that name a target before it removes the
LiteLLM provider.

## Contracts

### A model id must not change

`coder_pool`, the root `model_stylesheet` in all three graphs, and `[run.model.fallbacks]`
in two workflow TOMLs all address models by id. The migration keeps every id from the table
above. A provider swap changes which `base_url` answers and nothing else. Any task that
changes an id breaks the stylesheets, and no gate in this repository would report it.

### A provider name is new, so pick it once

`box-a`, `box-b`, `spark`, `zai` and `kimi` are names this series introduces. They appear
in the overlay and in nothing else, because no graph names a provider. Keep them.

### Reading the result back

Run both commands after every task, from the Mac:

```
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/.home/settings.toml'
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 fabro model list'
```

The first shows the file as written. The second shows what the server resolved. A
provider that fails to resolve appears in neither the catalog nor a run. The failure mode
is a run that dies at its first agent stage.

## Tasks

| # | Task | Blocked by |
|---|---|---|
| 01 | Point the three Local boxes at their own providers | — |
| 02 | Bound a hung inference request | — |
| 03 | Move the hosted models to `zai` and `kimi` | operator acceptance of the key move |
| 04 | Retire the fabro-side LiteLLM model rows | 03 |

Task 01 and task 02 are independent and can land in either order. Task 02 is the one that
recovers the measured 47 minutes, so if only one task lands, land that one.

## Prerequisites, both unverified

Both need a scratch copy of the overlay. No test covers either.

**Duplicate model ids across providers.** The settings reference says two providers may use
the same id. So `coders-a` must not exist on both `litellm` and `box-a`, and something must
decide which one an unqualified request reaches. Whether the cutover is a swap or a
qualified `provider/model` selector. No test covers which one applies. Task 01 resolves
this before it writes.

**Whether llama.cpp accepts the `api_model` sent.** The boxes report
`deepseek-v4-flash-0731-iq3-xxs` from `/v1/models`, and `api_model` defaults to the model id.
The answer is probably yes, and task 01 proves it with one request.
