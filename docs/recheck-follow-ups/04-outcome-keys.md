# 04. Publish `merge_block_reason`, `autofix_ran` and `autofix_rc` (#13, items 1 and 2)

## Outcome
A run's events say why its merge stopped and whether `autofix` ran, without `gh` and
without reading command output blobs.

## Why
The reason lives only in sandbox files and the PR comment. On 2026-10-09 it took 16 PR
comments read through `gh`. `autofix`'s "no .fabro/fix.sh" was visible only as
`output_bytes=60`, and it went unnoticed for weeks in two repositories
(womens-fantasy-sports#1450, lawncare-saas#2747).

## Change
Invoke `/fabro-workflow` first. Read `docs/agents/invariants-merge.md` and the `rescue_brief`
row of `docs/agents/invariants-backlog.md`.

1. `_shared/review-merge/review-merge.fabro`, `report_blocked` and `mark_needs_human`: add
   `output_schema="routing"`, and follow C4's output rule. Keep every side effect (outcome
   file, push, label, comment) and its order. Remove `report_blocked`'s
   `echo 'Merge blocked for PR ...'` from stdout. The key now carries it. Send `gh` output
   and warnings to `/tmp/fabro/report.log` (append).
2. `pr-review/workflow.fabro`, `entry_failed`: the same, from `needs_human_reason`.
3. `backlog/workflow.fabro`, `autofix`: add `output_schema="routing"`, send `fix.sh` output to
   `/tmp/fabro/autofix.log`, print C4's tail, and end with
   `{"context_updates":{"autofix_ran":…,"autofix_rc":…}}`. Update the `//` comment above the
   node: it says "No output_schema: this node publishes no context key".
4. `docs/agents/invariants-merge.md`, *Reporting*: add the row "A terminal node of the merge
   phase publishes `merge_block_reason` and prints only its routing object", with the reason
   (C4) and the test that covers it.

## Acceptance
- `make check` passes, including the new `ops/test-task-gates.sh` checks below.
- `make check-host` shows the baselines unchanged: 69/162, 34/73, 16/36, 24/54.
- In `ops/test-task-gates.sh`, for each of the four nodes: stdout's last line is valid JSON
  with the expected key, and no other line of stdout or stderr has a brace.

## Tests
In `ops/test-task-gates.sh`, extract each script the way the existing report-node checks do
and run it with `gh` stubbed:
- a reason file with `{`, `}`, a double quote, a single quote, a tab and three lines: the key
  is one line, under 301 bytes, and parses with `jq`.
- no reason file: the fallback text.
- `gh` that prints JSON with braces and exits 1: still one routing object, and the braces are
  in `report.log` only.
- `autofix` with no `fix.sh` (`false`, `-1`), a `fix.sh` that exits 0 and prints `{}`
  (`true`, `0`), and one that exits 3 (`true`, `3`).

## Depends on
Nothing. Live on the next fire after the push.
