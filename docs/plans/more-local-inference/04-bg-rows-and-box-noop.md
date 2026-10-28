# Task 04: the `-bg` model rows, the `box-noop` package, and the guard

Read `00-overview-and-contracts.md` first (D4, D9, D17; C1, C5, C6, C10). **Invoke
`/fabro-workflow`.** Read `docs/agents/invariants-host.md` in full: a bad settings overlay kills
every worker silently.

## Goal

Background runs have model names that reach the boxes and never fall back to a hosted model.
A trivial background package exists, with the guard every background package carries, so
Phase 1 can measure cancel latency and the guard before any real job is built.

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
     - the `io` MCP server and `io-stage` hook, as other agent packages have them (R11);
     - the `box-guard` hook exactly as C10 gives it, after `io-stage`, with a comment;
     - **no** `box-claim` hook (R15);
     - `[run.environment.env]` gains `FABRO_API_URL`, `FABRO_API_TOKEN` and
       `FABRO_BOX_HEALTH`, which the guard reads, written as `backlog`'s are;
     - a `stall_timeout` above every node timeout (R2).
   - `workflow.fabro`: `start -> claim -> work -> exit`.
     - `claim` writes `/tmp/fabro/box` from `{{ inputs.coder_pool }}` with the `-bg` suffix
       stripped, prints nothing to stdout, and never fails on that write.
     - `work` is an agent node, `class="bg"`, with
       `.bg { model: {{ inputs.coder_pool | default('coders-a-bg') }}; reasoning_effort: medium; }`.
       The stylesheet's `*` rule is also `{{ inputs.coder_pool | default('coders-a-bg') }}`, so
       no agent node can reach a hosted model.
     - Its prompt asks for one long, deterministic piece of text (for example: list the
       repository's files with a one-line purpose for each) and writes nothing to GitHub.
     - Timeout 30m.
   - Add `box-noop` to the Stage manifest with `"box": true` on `work`, and to
     `ops/fabro-io-manifest.py` `WF_ORDER`.
4. **`coder_box_stages` (C1).** `ops/fabro-io-manifest.py generate` writes it into the block of
   every package in `BACKGROUND_PACKAGES` (step 5), built from the `backlog`, `ralph` and
   `pr-review` stages whose `box` is `true` or `"plan-arm"`, with phase stages under their
   prefixed ids. `check` fails on drift. Add a generator test.
5. **`ops/check-graph-invariants.py` R16**, with a new `BACKGROUND_PACKAGES` list (start with
   `box-noop`). For each package in it:
   - every agent node and the `*` rule resolve to `{{ inputs.coder_pool | default('coders-?-bg') }}`;
     any hosted or literal model fails;
   - it has the `box-guard` hook exactly as C10 gives it, and no `box-claim` hook;
   - its `claim` node writes `/tmp/fabro/box`.

   Add passing and failing fixtures for each clause.
6. **`docs/agents/invariants-graph.md`:** a short *Background packages* section that states
   R14, R15's second half, R16, the guard, and the safe-to-cancel rule (D9: the last node writes
   the marker first).

## Done when

- `make check` passes.
- `box-noop` validates offline (`check-graph-invariants`); its `fabro validate` count is
  checked by the operator in task 12.
- Commit: `feat(workflows): -bg model rows, the box-noop package and its guard (more-local-inference 04)`.
