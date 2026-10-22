# gate_tests

Offline tests for the shell embedded in the Fabro graphs' command nodes, and for the host
scripts the automations lean on. `fabro validate` checks that a graph parses; it never runs a
node's `script`. These tests do: each node's script is extracted from its graph verbatim,
`/tmp/fabro` is rebased onto a scratch directory, and the script runs under `sh` (dash, when it
is installed, because the profile images are Debian) against fixtures and stubbed tools.

This is the pytest port of `ops/test-task-gates.sh`, which it replaced on 2026-10-22 (read the
old suite with `git show d67f31c:ops/test-task-gates.sh`). Every check kept its name, expected
value and observed value: the port was verified against the shell suite's own record of all 2,420
checks, and against a mutated copy of the graphs, where both suites failed the same 17 checks.

## Running

```sh
./ops/test-gates.sh                      # everything, in parallel (~35s)
./ops/test-gates.sh -k dependabot        # one area
./ops/test-gates.sh -n 0 -k test_claim   # one test, serially
```

Two tests need tools that only the profile images carry, and are skipped without them:
`test_fabro_code` (a real `codegraph`) and the `fabro-io` tests (`test_refute_guard`,
`test_improve_end_to_end`, `test_fabro_io_probe`). To run the `fabro-io` ones on the Mac, build
it (`cargo build --release` in `ops/fabro-io`) and put `ops/fabro-io/target/release` on PATH.

## Layout

| Module | Covers |
|---|---|
| `harness.py` | Extract, stage, run; `jq`, `grep` and `count` over the real tools; `run_node`, `routing_check` |
| `conftest.py` | The `ck` fixture, the dash shim, the git canary, and the check that the run left this checkout alone |
| `test_backlog_*.py` | backlog: the task queue, the issue lifecycle, per-task nodes, the final stretch |
| `test_entry_nodes.py` | The entry nodes' code-index lines and agent-guide install |
| `test_triage.py`, `test_arch_review.py` | The shared triage phase and arch-review |
| `test_merge_*.py`, `test_receipts.py` | The shared review-merge phase, and Stage I/O receipts |
| `test_fast_path.py`, `test_merge_queue.py` | The fast path and the merge-queue package |
| `test_dependabot.py`, `test_scout.py`, `test_ralph.py` | Those packages |
| `test_task_validation.py`, `test_ops_scripts.py`, `test_tools.py` | Task validation, host scripts, sandbox tools |

## Writing a test

One test is one section of related checks. `ck(name, expected, actual)` records a check and
carries on, so a failing test lists every failed check by name. Give each test its own
`tmp_path`; a test reads nothing another test wrote.

- **Call the real `jq` and `grep`.** Use `jq(filter, ...)` and `count(pattern, ...)` with the
  filter or pattern you would write in shell. A pattern translated into Python can silently
  match nothing, and then every check that expects `"0"` passes for the wrong reason.
- **Mind stdin and output pipes.** `sh()` and `run()` give the node an empty stdin. A node
  that leaves a background child holding its stdout (a `timeout`-killed `ci.sh`) must run
  through `run_files()`, which writes to files the way the shell suite did, or the test waits
  for that child.
- **Stub, don't sleep.** Put a `sleep` stub first on PATH for any node with a retry delay.
- **Real git where git is under test**, in a scratch repository, never this checkout.

`GATE_TESTS_CHECK_LOG=<file>` appends one JSON line per check (section, name, expected,
actual, ok). That record is how the port was compared with the shell suite.
