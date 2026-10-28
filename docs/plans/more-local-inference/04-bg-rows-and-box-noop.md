# Task 04: the `-bg` model rows and the `box-noop` package

Read `00-overview-and-contracts.md` first (D4, D9; C5, C6). **Invoke `/fabro-workflow`.** Read
`docs/agents/invariants-host.md` in full: a bad settings overlay kills every worker silently.

## Goal

Background runs have model names that reach the boxes and never fall back to a hosted model.
A trivial background package exists, so Phase 1 can measure cancel latency before any real job
is built.

## Steps

1. **`ops/settings.toml.example`.**
   - Add `[llm.providers.box-a.models."coders-a-bg"]` and
     `[llm.providers.box-b.models."coders-b-bg"]`. Each mirrors its box row: same `api_model`
     and `capabilities`, and a `display_name` that says background.
   - A comment explains why the names exist: they carry no fallback row, so a box error fails a
     background stage instead of reaching z.ai (C5, R14).
   - Do **not** touch the live overlay; that is task 12.
2. **`docs/agents/invariants-host.md`:** a row saying that every `-bg` name exists in the
   overlay on its box's provider, and that no workflow has a fallback row for it.
3. **`.fabro/workflows/box-noop/`**, a minimal background package, the template every later
   job copies:
   - `workflow.toml`:
     - `[run.inputs]` binds nothing;
     - `[run.model.fallbacks]` is absent;
     - the `io` MCP server and `io-stage` hook, as other agent packages have them;
     - `[run.environment.env]` gains nothing new.
   - `workflow.fabro`: `start -> claim -> work -> exit`.
     - `claim` writes `/tmp/fabro/box` from `{{ inputs.coder_pool }}` with the `-bg` suffix
       stripped.
     - `work` is an agent node, `class="bg"`, with
       `.bg { model: {{ inputs.coder_pool | default('coders-a-bg') }}; reasoning_effort: medium; }`.
     - Its prompt asks for one long, deterministic piece of text (for example: list the
       repository's files with a one-line purpose for each) and writes nothing to GitHub.
     - Timeout 30m.
   - Add `box-noop` to the Stage manifest with `"box": true`, and to `ops/fabro-io-manifest.py`
     `WF_ORDER`.
4. **`ops/check-graph-invariants.py`**: a package whose name is in a new `BACKGROUND_PACKAGES`
   list (start with `box-noop`) may name only `-bg`-defaulted classes. Any stage on a hosted
   model fails the check. This rule is R16; add fixtures for it.
5. **`docs/agents/invariants-graph.md`:** a short *Background packages* section that states R14
   and R16 and the safe-to-cancel rule (D9).

## Done when

- `make check` passes.
- `box-noop` validates offline (`check-graph-invariants`); its `fabro validate` count is
  checked by the operator in task 12.
- Commit: `feat(workflows): -bg model rows and the box-noop background package (more-local-inference 04)`.
