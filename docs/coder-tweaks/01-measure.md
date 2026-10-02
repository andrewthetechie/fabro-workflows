# 01. Commit the tool-use measurement and record the baseline

## Outcome
`ops/fabro-agent-tools.py` turns `fabro events <run> --json` files into the C7 metrics
for any set of runs. `docs/coder-tweaks/baseline-2026-10-02.txt` records its output for
the 20 baseline runs. Every later task, and task 08, measures with the same script.

## Why
The overview's numbers came from throwaway scripts in a session scratchpad. M2, M5 and M8
were never computed. Without one fixed script, "did it work" turns into a different
query every time.

## Change
1. `ops/fabro-agent-tools.py`: Python 3.11+, stdlib only. It runs on the Mac and on the
   host, never in a sandbox. Usage:
   `fabro-agent-tools.py [--local-only] [--since ISO] EVENTS.jsonl...`.
   - **Model per session:** from `agent.message` `properties.event.AssistantMessage.model`.
     "Local" means the model id starts with `coders-`.
   - **Tool calls:** from `agent.tool.started` / `agent.tool.completed`, joined on
     `tool_call_id`. The name is at `properties.event.ToolCallStarted.tool_name`, the
     arguments at `.arguments`, and the output at
     `properties.event.ToolCallCompleted.output`.
   - **Shell classification:** split `command` on `&&`, `||`, `;` and `|`. Drop a leading
     `cd X`, `timeout N` or `uv run`, then take the first word. Words that come from a
     grep pattern's `\|` alternation (`def`, `class`, `fn`) are not commands. Split only
     on a pipe outside quotes.
   - **Git:** C2's rule exactly: the same allowlist and the same exempt stages, read from
     `.fabro/workflows/_io/manifest.json` (`"git": "write"`) when that field exists, and
     otherwise the three stage ids in C2.
   - **Output:** one block per metric M1-M8, plus a per-stage tool table. M7 reuses
     `handoff.md`'s definition: from the coder `stage.started` to the first
     `edit_file`/`write_file`, per coder visit, reported as a median. M8 counts
     `review_gate` outcomes from `edge.selected` (the first `review` visit per task that
     routed to `integrate`), and the number of visits to each `rework_t*`.
   - **Exit code:** 0, unless the input is unreadable.
2. Collect the 20 baseline runs (the `backlog` runs from `01M3RXPP9CKVYQD9KXZX64J2A5` to
   `01M3X2GDWNYWTR4Q5SDW6SK3F3`). On the host, use the runs API (see `handoff.md`) and
   `fabro events <id> --json`. Run the script and commit its output as
   `docs/coder-tweaks/baseline-2026-10-02.txt`. Event files contain issue text: do
   **not** commit them.

## Acceptance
- On the baseline runs it reproduces the overview's numbers within ±2%. If it doesn't,
  explain the difference in the baseline file, then fix the overview table.
- M2, M5 and M8 have baseline values, which the overview's C7 table is updated to cite.
- `python3.11 ops/fabro-agent-tools.py --help` works; no third-party imports.

## Tests
A fixture events file under `ops/tests/fixtures/agent-tools/`, hand-written and with no
real issue text, covering: a local and a hosted session, native grep, shell grep inside a
pipeline, a `git stash` in `coder` (counted), `git merge --continue` in `resolve_merge`
(not counted), `git -C x status` (allowed), a whole and a ranged `read_file`, and two
coder visits for the median. Run it with a small `ops/tests/test_fabro_agent_tools.py`
written against the standard library's `unittest`, so it needs no install.

## Depends on
Nothing.
