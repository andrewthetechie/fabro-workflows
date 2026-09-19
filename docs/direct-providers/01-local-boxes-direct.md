# Point the three Local boxes at their own providers

## Outcome

`coders-a`, `coders-b` and `long-context` resolve to a provider whose `base_url` is the
box. One run reaches its first agent stage on each box.

## Context Pack

- Overview: the mapping table, findings 1 and 3, the model-id contract, and the
  duplicate-id prerequisite.
- ADR 0007: the decision, and the measured 47-minute cost of the hop.
- `backlog` and `pr-review` select a box through `{{ inputs.coder_pool }}` in the root
  `model_stylesheet`. Neither graph changes.
- The overlay is `/storage/.home/settings.toml` inside `fabro-fabro-1`.
  `ops/settings.toml.example` is the tracked template and carries no provider table for a
  box.

## Implementation Contract

Add three provider tables. Copy `limits` and `capabilities` from the existing `coders-a`
row, which task 04 removes, so only the endpoint changes.

```toml
[llm.providers.box-a]
display_name = "Local coder A (10.10.0.29)"
base_url = "http://10.10.0.29:8000/v1"
adapter = "openai-compatible"
codec = "openai-chat"
auth = { type = "none" }

[llm.providers.box-a.models."coders-a"]
display_name = "Local coders A"
api_model = "deepseek-v4-flash-0731-iq3-xxs"
limits = { context_tokens = 262144, max_output_tokens = 38400 }
capabilities = { text = true, tools = true, reasoning = true }
```

- `box-b` follows, at `http://10.10.0.56:8000/v1`, with the model row `coders-b`.
- `spark` follows, at `http://10.10.0.30:8888/v1`, with the model row `long-context`. Its
  upstream wire name is `deepseek-v4-flash-0731`, and it keeps `small_default = true`.
- Remove `coders-a`, `coders-b` and `long-context` from the `litellm` provider in the same
  edit. Keeping both is the duplicate-id problem the overview names.
- Keep `coders` on `litellm`. Task 04 owns that row.
- Change no model id, no graph, and no workflow TOML.
- Apply with no restart.

## Acceptance Criteria

- [ ] `fabro model list` reports `coders-a`, `coders-b` and `long-context` exactly once.
- [ ] `fabro model test coders-a` and `fabro model test coders-b` pass. A box serves one
      request at a time, so run these when no run holds the box.
- [ ] `fabro validate` reports the recorded baselines. The graphs did not change, so any
      difference means something else moved.
- [ ] One dispatcher-started run reaches `coder v1` on each box.

## Dependencies

None. Independent of task 02.

## Risk

3. No test covers `api_model` against `llama.cpp`, and a wrong value fails every coder stage.
Both boxes change at once, and the fallback for a broken box is a hosted model.
