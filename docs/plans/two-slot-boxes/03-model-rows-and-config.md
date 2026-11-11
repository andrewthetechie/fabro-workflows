# Task 03: model rows, compose default, fallbacks and docs

Read `00-overview-and-contracts.md` first (F5, D1, D3, D6, D7). Read `docs/agents/invariants-host.md`,
`ops/settings.toml.example` whole, and `ops/check-agent-profiles.py` and
`ops/check-graph-invariants.py` wherever they name `coders-c`, `coders-d` or `-bg`. Then run
`grep -rn 'coders-[cd]' .fabro/workflows ops --include='*.toml' --include='*.fabro'
--include='*.py' --include='*.sh'` and read every hit.

## Goal

The repository describes the target layout, so task 04 only copies values to the host.
**The tracked files describe the end state (both boxes).** Task 04 sequences how the host
reaches it.

## Steps

1. **`ops/settings.toml.example`.**
   - `box-a` (10.10.0.29) carries `coders-a`, `coders-b` and `coders-a-bg`.
   - `box-b` (10.10.0.56) carries `coders-c`, `coders-d` and `coders-c-bg`.
   - Every one of those rows uses `api_model = "qwen3.8-flash-next"` and `limits = {
     context_tokens = 524288, max_output_tokens = 38400 }` (D6).
   - Delete `coders-b-bg` and `coders-d`'s freellm row. Delete the `freellm` provider if
     nothing else uses it.
   - Delete `coders-c`'s `spark` row. Keep the `spark` provider and `long-context`.
   - Rewrite the comments that describe the old layout (the coders-c/coders-d history and the
     "coders-a-bg or coders-b-bg" sentences).
2. **Fallback chains.** Find every `[run.model.fallbacks]` entry, and every stylesheet or
   prompt, that names `coders-c` or `coders-d` as a different model from `coders-a`. With
   all four pools now on the same weights, check each chain still makes sense. Report every
   change in the commit message. No `-bg` name gets a fallback (R14).
3. **Checkers.** Update any hard-coded pool→backend or `-bg` name list in
   `check-agent-profiles.py`, `check-graph-invariants.py` (R14) and their fixtures, so
   `coders-c-bg` is known and `coders-b-bg` is not required.
4. **`ops/docker-compose.yaml`.** Set the default of `SCHEDULER_BOX_HEALTH` to all four pairs
   (a and b on `10.10.0.29:5300`, c and d on `10.10.0.56:5300`). Rewrite its comment to say
   that pools sharing a URL are one box (D1). Do **not** change `.env` or anything on the host.
5. **Docs.** Update the lines that describe pools as one box each, or c/d as spark/freellm:
   - `ops/README.md`: the scheduler section, the background-lane runbook, and the
     `FABRO_BOX_HEALTH` and `SCHEDULER_BOX_HEALTH` rows;
   - `docs/agents/invariants-host.md`;
   - `docs/agents/server.md`;
   - `AGENTS.md`, if it names the layout.

   `docs/README.md` already lists this series. Leave its status *planned* until task 04.

## Done when

- `make check` passes.
- One local commit, not pushed.
