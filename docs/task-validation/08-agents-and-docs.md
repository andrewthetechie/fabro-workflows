# 08. Point the agents at `task_check`, and record the change

## Outcome
The coder and rework prompts tell the agent to run `task_check` before finishing. The Agent
guide lists it, and M-run counts it. The invariants and records describe Task validation.

## Why
D11 and D15. A tool the prompt never mentions goes unused (docs/coder-tweaks M1). And the
prompts currently say "a separate `validate` stage runs `./.fabro/setup.sh && ./.fabro/ci.sh`",
which stops being true.

## Change
1. `.fabro/workflows/_io/agent-guide.md`: C6's row. Add `fabro-test --task` to the shell
   fallback sentence. Regenerate (`ops/fabro-io-manifest.py generate`) and stay under 3,000
   bytes.
2. `backlog/prompts/coder.md.j2`, "Validation is external": a separate Task validation stage
   runs the checks and tests that cover the files you changed. Before you finish, call
   `task_check`, which runs the same selection, and fix what it reports. Do not run the
   project's full suite. `run_tests` stays the tool for a narrow run while you work. Make
   the same change in `rework.md.j2` (line ~93).
3. `rework.md.j2`, the feedback shapes (line ~31): `## Validation failed (exit N): …` now
   has two forms, `./.fabro/setup.sh && ./.fabro/ci.sh` and `task validation`. The second
   lists the failing checks and targets. Fix those, and do not disable, skip or delete a
   failing test. The scope rules apply to both.
4. The merge phase prompts (`ci_fix`, `resolve_base`, `review_fix`) keep `run_tests`. They run
   after Final validation, and their own `validate` is the full suite, so they get no
   `task_check` line.
5. `ops/fabro-agent-tools.py`: M-run counts a `task_check` tool call, and a shell
   `fabro-test --task` or `fabro-io task-check`, as tool test runs. Add a fixture line to
   `ops/tests/fixtures/agent-tools/` and an assertion in `test_fabro_agent_tools.py`.
6. Deployment invariants, one row each:
   - `docs/agents/invariants-backlog.md`: `validate` routes on `task_validation_ok` behind `outcome=succeeded`, and writes the placeholder `rework.md` first. A `final_fix` task and every fallback run the full suite. Keep the full branch's rework header.
   - `docs/agents/invariants-merge.md`: the merge phase's `validate` skips only a clean tree equal to `final_validated_tree`. Only `open_pr_prep` writes that file, and nothing deletes it.
   - `docs/agents/invariants-host.md`: no repository's `test.toml` uses Task validation keys before the images carry `fabro-io` 0.5.0.
7. Records:
   - the `docs/README.md` row for `docs/task-validation/` (added at design time) gets its new status
   - a dated line in `docs/plan-trial/README.md` saying womens-fantasy-sports' per-task `validate` failures change meaning from its switch date, in both arms
   - this file's Status line in `00-overview-and-contracts.md`
   - an `AGENTS.md` line only if the layout table or the baselines change (they should not)
8. Comment on issue #26 with a link to this series and ADR 0025.

## Acceptance
- `make check` passes: the manifest is in sync, the guide is under its cap, and the agent-tools tests pass.
- Pushed together with 07.

## Depends on
06.
