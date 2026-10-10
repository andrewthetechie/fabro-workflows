# 05. Point the agents at it, and guard the config

## Outcome
The Agent guide and the coder prompt name `run_tests`. The merge phase treats an edit to
`.fabro/test.toml` or `.fabro/ci.sh` as a config change (C6). `docs/agents/` records the
new invariants.

## Why
D6 and D7. A tool the prompt never mentions is used by accident, if at all
(docs/coder-tweaks M1). And `run_tests` and `validate` both read the branch's own files,
so a branch that edits them changes the rules that judge it.

## Change
1. `_io/agent-guide.md`: one row, `| whether your change passes its tests | run_tests
   TARGET [paths] |`, and one sentence: "If the shell says a test needs a database or a
   service, use `run_tests`; the repository declares how to start it." Regenerate (`ops/fabro-io-manifest.py generate`)
   and stay under 3,000 bytes.
2. `backlog/prompts/coder.md.j2`, the "Do not run the project's full test suite" paragraph:
   run tests with `run_tests TARGET <test files or node ids>`, and through shell only when
   `run_tests` reports no targets. A typecheck or a lint, which no target covers, stays
   on the shell (amended 2026-10-10: the first wording sent every check through
   `run_tests`, which forbade typecheck and lint in any repository that declares
   targets). Make the same change wherever
   `rework*.md.j2` and the merge phase's `ci_fix`/`review_fix` prompts tell the agent how to
   run a test.
3. `_shared/review-merge/` `hygiene`: C6. The comment above the node and the review
   comment's `config_changed` sentence name all three files.
4. `ops/test-task-gates.sh`: a real-git fixture each for `.fabro/test.toml` and
   `.fabro/ci.sh` tripping `config_changed`, and one for `.fabro/test.toml.example` not
   tripping it.
5. Deployment invariants (one table row each):
   - the `ci.sh` assignment form (C2): `docs/agents/invariants-graph.md`,
   - images before any target repository's `ci.sh` change (D8): `docs/agents/invariants-host.md`,
   - `config_changed` covering three files (C6): `docs/agents/invariants-merge.md`.

## Acceptance
- `fabro validate` baselines unchanged in node and edge counts.
- The routing checker, `ops/fabro-io-manifest.py check` and `./ops/test-task-gates.sh`
  all pass.
- The task 01 baseline is committed before this is pushed.

## Depends on
04, and the task 01 baseline.
