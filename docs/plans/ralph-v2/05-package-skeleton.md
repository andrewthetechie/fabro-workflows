# Task 05: the `ralph-v2` package skeleton

Read `00-overview-and-contracts.md` first (E1, P1, P13-P15, *Models*, *Graph*). **Invoke
`/fabro-workflow`.** Read `docs/agents/invariants-ralph.md` and R10, R11, R13, R15 and R18 in
`ops/check-graph-invariants.py`.

## Goal

A package that is v1 minus the fourth rung, with v2's models, that passes every offline gate. Nothing
dispatches it: the scheduler learns the name in task 10.

## Steps

1. **Copy** `.fabro/workflows/ralph/` to `.fabro/workflows/ralph-v2/`.
2. **Graph** (`workflow.fabro`):
   - `digraph RalphV2`; the goal starts with `RalphV2:` and adds "each Task planned and reviewed on the box".
   - Replace the stylesheet by the one in *Models*, with one comment line per class naming its decision.
   - Replace v1's prose above each copied node by `// copy: ralph.<node>` (P14). Keep the graph-attribute
     comments, shortened to the rule and the reason.
   - Delete `iterate_t4` and its edges.
   - `pick`: `-ge 8` becomes `-ge 6`, and `is blocked after 8 misses` becomes `is blocked after 6 misses`.
     Nothing else in it changes in this task.
   - `resolve_merge`: `class="merge-resolve"`, `timeout="60m"`; its comment says v1 runs it on
     `glm-5.3` for 30m, that number is void on the box, and 60m is unmeasured.
   - `stop_reason`: drop `iterate_t4` from the agent list.
   - Expected: **58 nodes, 151 edges** (`python3.11 ops/check-graph-invariants.py --counts`).
3. **`workflow.toml`**: the fallbacks from *Models*; the header comments rewritten to point at ADR 0034
   and `docs/plans/ralph-v2/00`; the `io-guard` matcher without `iterate_t4`. Every other section and hook
   stays, `[run.run_branch]` included. Diff the file section by section against `ralph`'s and list in your
   report every difference.
4. **Prompts**: unchanged copies of `plan`, `plan_review`, `standards`, `spec`, `refute`, `resolve_base`,
   `resolve_merge`, `learn` and (until task 06) `iterate`.
5. **Manifest** (`_io/manifest.json`): add workflow `ralph-v2`: v1's stages with the prompt paths of this
   package, without `iterate_t4`, and `resolve_merge` with `"box": true`. Add `"ralph-v2"` after `"ralph"`
   to `WF_ORDER` and `CODER_LOOP_WORKFLOWS` in `ops/fabro-io-manifest.py`, and to
   `CODER_LOOP_PACKAGES` in the checker. Generalise rule 6 to `{"ralph": (1, 2, 3, 4), "ralph-v2": (1, 2, 3)}`,
   its messages naming the workflow.
   Add **rule 7** to the generator's `check`: each generated manifest is under 120,000 bytes (Linux caps
   one environment string at 131,072). Then `generate`. Every package that carries `coder_box_stages`
   changes; that is expected and is data only.
6. **R10 for v2** in `ops/check-graph-invariants.py`:
   - `RALPH_V2_GRAPH`, `R10_V2_COPIES` (rows `(node, replacements)`, the original is the same node of
     `RALPH_GRAPH`) and `R10_V2_PROMPTS` (node ids), and `r10_v2_copies(root)`, called from `run`. Its
     violations name the package `ralph-v2`. It is silent when either graph is missing.
   - It also fails when a **command node of `ralph-v2` that `ralph` also has** is in neither
     `R10_V2_COPIES` nor `R10_V2_OWN = {"pick", "record"}`.
   - Rows with no replacement: `claim`, `prep`, `plan_gate`, `plan_review_prep`, `plan_review_gate`,
     `excerpts`, `task_block_gate`, `autofix`, `check`, `record`, `resolve_merge_gate`, `refute_prep`,
     `refute_gate`, `review_prep`, `review_gate`, `local_review_prep`, `local_review_gate`, `final_merge`,
     `final_check`, `criterion_checks`, `hygiene`, `open_pr`, `file_remainder`, `pr_handoff`,
     `merge_prep`, `deliver`, `merge_gate`, `watch_checks`, `merge`, `remerge_base`, `resolve_base_gate`,
     `ci_task`, `report_merged`, `report_blocked`, `report_queued`, `learn_prep`, `learn_post`,
     `mark_needs_human`, `close_noop`, `return_to_triage`, `mark_stuck`. Rows with replacements: `stop_reason`
     (` | iterate_t4` removed) and `pick` (the two edits above). Tasks 07-09 move rows as they say.
     In this task `R10_V2_OWN` is empty.
   - Prompt rows: `plan`, `plan_review`, `standards`, `spec`, `refute`, `resolve_base`, `resolve_merge`,
     `learn`, `local_standards`, `local_spec`.
7. **`ops/check-host.sh`**: `[ralph-v2]="58 nodes, 151 edges"` (tasks 07-09 raise it), the parity list
   (eighteen files), the validate list, and "fifteen packages" -> "sixteen packages" in both places.
8. **Tests**:
   - `harness.py`: `RAPH2 = WF / "ralph-v2" / "workflow.fabro"`.
   - Add `(RAPH2, "ralph-v2")` to the three `parametrize` lists in `test_backlog_issue.py` and
     `"ralph-v2"` to the list in `test_tools.py`.
   - `test_merge_gates.py`: `"ralph-v2"` in the package tuple, and `elif pkg == "ralph":` becomes
     `elif pkg in ("ralph", "ralph-v2"):`.
   - `test_receipts.py`: the five `ralph` pairs for `ralph-v2` too. `test_repo_orientation.py`:
     `("ralph-v2", "plan")`, and `prep_reader(RAPH2)` equals the other two.
   - New `test_ralph_v2.py`: every `script` node passes `sh -n` (under dash when installed); no script holds
     `<<<`, `read_text` or an unexpanded `{{` other than `claim`'s inputs; the graph file is under
     350,000 bytes; the edge list has no `iterate_t4`.
   - `ops/tests/test_check_graph_invariants.py`: `r10_v2_copies` on a drifted copy, a missing copy, an
     unlisted shared node and a clean pair. `ops/tests/test_fabro_io_manifest.py`: rule 6 per package, rule 7.
   - Run `python3.11 ops/check-payload.py`: `ralph-v2` under 1,500,000 bytes.

## Done when

- `make check` passes. Counts are 58/151.
- Commit: `feat(ralph-v2): package skeleton, dispatched by nothing (ralph-v2 05)`.
