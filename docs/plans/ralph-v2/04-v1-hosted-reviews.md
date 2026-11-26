# Task 04: v1 reviews on `glm-5.3`

Read `00-overview-and-contracts.md` first (E15, E18, A4, *Models*). **Invoke `/fabro-workflow`.**
Read `docs/agents/invariants-host.md` on built-in model ids.

Scope is the model of two stages and two fallback rows in `ralph`. The whole-branch box lenses do
**not** change (A4). After this task v1 is frozen until the result, except for an outage fix.

## Steps (`.fabro/workflows/ralph/`)

1. Stylesheet: `.review-standards` and `.review-spec` become `{ model: glm-5.3; }`. Rewrite their
   comment entries and the `.coder-t4` one's reference to the reviewers' quota.
2. `[run.model.fallbacks]`: the `"glm-5.3"` row becomes
   `["orfallback:hy4-preview", "openai:gpt-6.1-terra"]`. Delete the `kimi-k3` row (task 03 removed its
   last other user: check with `grep -n 'kimi-k3' workflow.fabro workflow.toml`; only comments may
   remain, and rewrite them). Leave `coders-*`, `gpt-6-luna`, `glm-5.3-flash` and `kimi-for-coding`.
   Rewrite the file's header comment about the two rows: the glm-5.3 chain is now the reviewers' and
   `.coder-t4`'s, its second rung costs about $0.70 a `standards` visit, and C11 (task 02) watches it.
3. Timeouts: keep `standards` 30m and `spec` 20m. Replace the comment's derivation with: `glm-5.3`
   emits about 45 output tokens a second against flash's 31, so the heaviest visits are shorter than the
   flash projection these numbers came from; re-time from `fabro-scorecard.sh` after two weeks.
4. `docs/agents/invariants-ralph.md`, *The package*: rewrite the row about the `kimi-k3` fallback order
   and the row that sizes `plan`, `standards` and `spec` for the `kimi-k3` chain.
5. `resolve_merge` stays on `glm-5.3` (`.coder-t4`) in v1.

## Tests

`grep -rn 'kimi-k3' ops/gate_tests ops/tests ops/scheduler/tests` and update only an assertion that reads
**ralph's** stylesheet or fallbacks. Add to `ops/gate_tests/test_ralph.py`: the two classes resolve to
`glm-5.3`, the fallback row is exactly the two entries, and no `kimi-k3` key exists (`tomllib`).

## Done when

- `make check` passes. The graph is still 59 nodes and 155 edges (the offline reader:
  `python3.11 ops/check-graph-invariants.py --dump .fabro/workflows/ralph/workflow.fabro`).
- Commit: `feat(ralph): standards and spec review on glm-5.3 (ralph-v2 04)`.
