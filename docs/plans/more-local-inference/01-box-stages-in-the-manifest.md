# Task 01: mark every box stage in the Stage manifest, and check it

Read `00-overview-and-contracts.md` first (D2, D4, D18; C1, C5). **Invoke `/fabro-workflow`**,
then read `docs/agents/invariants-graph.md` *Stage I/O* and `ops/fabro-io-manifest.py` in full.

**Do task 02 first.** This task's generated manifests can be loaded only by task 02's binary.

## Goal

The Stage manifest says, for every agent stage of every workflow, whether it runs on the run's
coder box. Two new checker rules keep that true: R13 against the stylesheets, R14 against the
fallback rows. Nothing at runtime acts on it yet.

## Steps

1. **`.fabro/workflows/_io/manifest.json`.** Add `"box"` to every stage entry, using the
   values from the overview's *Facts* (box stages today):
   - `true` for `backlog` `coder`, `rework_t1`, `resolve_merge`, `extra_decompose`;
     `ralph` `iterate_t1`, `learn`, `resolve_base`; `pr-review` `rebase_agent_t1`.
   - `"plan-arm"` for `backlog` `plan` and `ralph` `plan`.
   - For the shared `review-merge` phase, a per-importer map: `refute_fix` and
     `resolve_base` get `{"backlog": true, "pr-review": true, "dependabot": false}`. Every
     other phase stage gets `false`. The `triage` phase's stages get `false`.
   - `false` everywhere else, written explicitly so a reviewer sees every stage was decided.
   - Set `min_binary` to `0.9.0`, task 02's version, and say why in the commit body.
2. **`ops/fabro-io-manifest.py`.**
   - Validate the field: a bool, `"plan-arm"`, or (phases only) a map with exactly one bool
     per importing workflow. A missing `box` is an error in the source manifest.
   - `generate` flattens a phase map per importing workflow, so each `FABRO_IO_MANIFEST`
     block carries a plain `true`, `false` or `"plan-arm"` per stage, under the stage's
     prefixed id (`review_merge.refute_fix`).
   - `check` fails on drift as it does today.
   - `coder_box_stages` (C1) is task 04's, because no background package exists yet.
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
   - Also fail when any agent node resolves to a **literal** `coders-*` model, whether
     through its class or through the `*` rule (`backlog` and `ralph` have
     `* { model: coders-a; }`). Such a node runs on box-a whatever the lease, and no claim can
     cover it. No node does today.
4. **R14.** No `workflow.toml` `[run.model.fallbacks]` key may end in `-bg`, and no `-bg` name
   may appear as a fallback target.
5. **Fixtures.** In `ops/tests/test_check_graph_invariants.py`, one passing and one failing
   case for each of these: a missing `box`, an extra `box`, a wrong arm condition, a phase
   stage whose importer map disagrees, an agent node with no class under a `coders-a` `*`
   rule, and a `-bg` fallback row. In `ops/tests/test_fabro_io_manifest.py`, the generator's
   flattening and its rejection of a bad `box` value.

## Done when

- `make check` passes, with R13 and R14 listed in the checker's module docstring.
- The generated `FABRO_IO_MANIFEST` blocks in `backlog`, `ralph`, `pr-review` and `dependabot`
  carry `box`, and `min_binary` is `0.9.0`.
- Commit: `feat(io): mark box stages in the Stage manifest; R13 and R14 (more-local-inference 01)`.
  Its body says it must not be pushed until task 02's images are deployed.
