# Task 02 — Fabro's model catalog: the `high-reasoning` entry and the restart

**Depends on:** 01. **Blocks:** 03, 08. **LLM level:** ordinary.

Fabro will not route to a LiteLLM model it does not know about. The catalog is the
`[llm.providers.litellm.models.*]` block in the server's settings overlay, tracked here
as `ops/settings.toml.example` and live at `/storage/.home/settings.toml` inside the
container.

## Part 1 — the entry

Add to `ops/settings.toml.example`, after the `glm-4.7` block:

```toml
# Role alias, not a model. Resolves to glm-5.3 in LiteLLM with kimi-k3 as overflow;
# see docs/adr/0003-high-reasoning-alias.md. Workflows ask for the role so the model
# behind it can change without a graph edit and a deploy.
[llm.providers.litellm.models."high-reasoning"]
display_name = "High reasoning (glm-5.3, kimi-k3 overflow)"
api_model = "high-reasoning"
limits = { context_tokens = 202752, max_output_tokens = 16384 }
capabilities = { text = true, tools = true, reasoning = true }
```

`limits` are the **minimum** of the pool, not glm-5.3's: a request LiteLLM overflows to
`kimi-k3` must still fit, and kimi-k3's 262144-token window is the larger of the two, so
202752/16384 is the safe pair. If a future member has a smaller window, this number comes
down with it.

Do not set `small_default`. That belongs to `long-context`, which serves utility calls
such as generated run titles.

## Part 2 — deploy it

The live file is not synced from this repository:

```sh
scp ops/settings.toml.example andrew@10.10.0.32:/tmp/settings.toml
# merge the new block into the live file rather than overwriting it — the live copy
# has real host addresses and a real base_url where the tracked one has placeholders
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/.home/settings.toml' > /tmp/live-settings.toml
```

Then write the merged file back and restart:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose restart fabro'
```

**Restart only when the host is idle.** ADR 0002: `Blocked` is in
`RECONCILABLE_STATUSES`, so a restart fails every in-flight run and is the one thing
that silently destroys a `backlog` run parked on `human_rescue`. Check first, and wait
if it is not zero:

```sh
curl -fsS -H "Authorization: Bearer $FABRO_DEV_TOKEN" \
  http://10.10.0.32:32276/api/v1/system | jq .runs.scheduler_slots_used
```

## Acceptance

- `python3.11 -c 'import tomllib; tomllib.load(open("ops/settings.toml.example","rb"))'`
  parses. macOS ships 3.9, which has no `tomllib`; `fabro validate` does not check this
  file at all, and a dotted key that loses its quotes becomes a nested table that fails
  at fire time with a 422.
- The quotes around `"high-reasoning"` are present in both files. The name contains a
  hyphen, not a dot, so TOML tolerates it bare — quote it anyway, to match every
  neighbour and to survive a rename to something dotted.
- After the restart, the model is visible to the server:
  `ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro model list'`
  (or the API's model endpoint) shows `high-reasoning`.
- The live file and the tracked example differ **only** in the placeholder host values.
