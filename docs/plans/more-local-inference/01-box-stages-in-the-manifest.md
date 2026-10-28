# Task 01: mark every box stage in the Stage manifest, and check it

Read `00-overview-and-contracts.md` first (D2, D4; C1, C5). **Invoke `/fabro-workflow`**, then
read `docs/agents/invariants-graph.md` *Stage I/O* and `ops/fabro-io-manifest.py` in full.

## Goal

The Stage manifest says, for every agent stage of every workflow, whether it runs on the run's
coder box. Two new checker rules keep that true: R13 against the stylesheets, R14 against the
fallback rows. Nothing at runtime reads it yet.

## Steps

1. **`.fabro/workflows/_io/manifest.json`.** Add `"box"` to every stage entry, using the
   values from the overview's *Facts* (box stages today):
   - `true` for `backlog` `coder`, `rework_t1`, `resolve_merge`, `extra_decompose`;
     `ralph` `iterate_t1`, `learn`, `resolve_base`; `pr-review` `rebase_agent_t1`.
   - `"plan-arm"` for `backlog` `plan` and `ralph` `plan`.
   - For the shared `review-merge` phase, a per-importer map: `refute_fix` and
     `resolve_base` get `{"backlog": true, "pr-review": true, "dependabot": false}`. Every
     other phase stage gets `false`.
   - `false` everywhere else, written explicitly so a reviewer sees every stage was decided.
2. **`ops/fabro-io-manifest.py`.**
   - Validate the field: a bool, `"plan-arm"`, or (phases only) a map from each importing
     workflow to a bool.
   - `generate` flattens a phase map per importing workflow, so each `FABRO_IO_MANIFEST`
     block carries a plain `true`, `false` or `"plan-arm"` per stage, under the stage's
     prefixed id (`review_merge.refute_fix`).
   - `check` fails on drift as it does today.
   - Bump `min_binary` only if task 02 needs the new field parsed, and say so in that commit.
3. **`ops/check-graph-invariants.py` R13.** For each package root graph:
   - Compute the set of agent nodes whose stylesheet class resolves to
     `{{ inputs.coder_pool ... }}`, with imported phase nodes resolved through the
     importer's stylesheet.
   - A class inside a `{% if %}` block counts once per branch. A node that is on the box in
     some branches and not in others must be `"plan-arm"`.
   - Fail when the set differs from the manifest's `box` values in either direction.
   - Also fail when a `"plan-arm"` stage's `{% if %}` condition is not exactly
     `(inputs.issue_number | default(0) | int) // 2 % 2 == 1`. That pins the arm rule that
     task 02 hard-codes.
4. **R14.** No `workflow.toml` `[run.model.fallbacks]` key may end in `-bg`, and no `-bg` name
   may appear as a fallback target.
5. **Fixtures.** In `ops/tests/test_check_graph_invariants.py`, one passing and one failing
   case for each of these: a missing `box`, an extra `box`, a wrong arm condition, a phase
   stage whose importer map disagrees, and a `-bg` fallback row.

## Done when

- `make check` passes, with R13 and R14 listed in the checker's module docstring.
- The generated `FABRO_IO_MANIFEST` blocks in `backlog`, `ralph` and `pr-review` carry `box`.
- Commit: `feat(io): mark box stages in the Stage manifest; R13 and R14 (more-local-inference 01)`.
