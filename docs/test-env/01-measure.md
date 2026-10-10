# 01. Measure M-env and M-run, and record the baseline

## Outcome
`ops/fabro-agent-tools.py` reports C7's M-env and M-run beside its existing metrics.
`docs/test-env/baseline-<date>.txt` records them over recent `backlog` runs, before task 05
changes any prompt.

## Why
"This happens often" needs a number to beat. Run `01M4HVKKAHPFK3X9KJ18D8YDZG` is one case,
and the coder recovered by narrowing its run, so a run's outcome alone does not show the gap.

## Change
1. Add an `M-env` block. Count agent sessions with any `agent.tool.completed` output
   (shell, `baseline_check`, `run_tests`) containing either `ConftestImportFailure` or a
   connection refusal on port 5432 (`port 5432 failed: Connection refused`,
   `connect ECONNREFUSED 127.0.0.1:5432`, `Connect call failed ('127.0.0.1', 5432)`).
   Report sessions and runs, per repository (from `run.created` `properties.git.origin_url`)
   and per stage.
2. Add an `M-run` block. A test invocation is a shell command whose classified segment is
   `pytest`, `vitest`, `cargo test`, `npm test`/`npm run test…`, or `bun run … test…`, or a
   `run_tests`/`baseline_check`-with-target call, or a `fabro-test` shell call. Report the
   share that went through `run_tests` or `fabro-test`.
3. Collect the `backlog` runs from the last 7 days on all four repositories (at least 20),
   and commit the output as `baseline-<date>.txt`. Do **not** commit event files: they
   contain issue text.

## Acceptance
- On `01M4HVKKAHPFK3X9KJ18D8YDZG`, M-env counts `coder@1` (and no other session unless its
  output shows the same failure), and M-run counts 0 of its test invocations as `run_tests`.
- The baseline file records M-env per repository. C7's target column is unchanged unless
  the baseline makes it meaningless; if so, say why in the baseline file.

## Tests
Extend `ops/tests/fixtures/agent-tools/` with hand-written sessions (no real issue text):
a pytest conftest failure on 5432, an asyncpg refusal, a `run_tests` call, a
`fabro-test` shell call, a `cargo test` call, and a `pytest` inside `uv run`. Assert both
blocks in `ops/tests/test_fabro_agent_tools.py`.

## Depends on
Nothing. Must finish before task 05 is pushed.
