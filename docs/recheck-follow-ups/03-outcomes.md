# 03. `fabro-agent-tools.py --outcomes`, and the docs that point at the tools (#14, part 2)

## Outcome
`fabro-agent-tools.py --outcomes` prints the per-run outcome table and the failure sweep that
the 2026-10-09 recheck built with a throwaway `summ.py` and scratch `jq`. The coder-tweaks
docs tell the next recheck to use tasks 02 and 03 instead of hand-written commands.

## Why
The outcome half of a recheck (which runs merged, why the others stopped, where validate
failed, which providers failed over) is where the decisions in `result-2026-10-08.txt` and
`result-2026-10-09.txt` came from. It had no fixed script, so each recheck defined it again.

## Change
1. Add `--outcomes` and `--prs` to `fabro-agent-tools.py`, exactly to C3. Extend `load_run`
   so it keeps what C3 needs: `context_updates` from each `stage.completed`, the
   `run.created` labels and time, the `run.completed` time and status, and the failure
   events with their node and message.
2. `--prs` calls `gh` only for runs that have no `merge_block_reason`. Pass the PR URL as an
   argument, never through a shell string.
3. Docs:
   - `docs/coder-tweaks/08-deploy-verify.md`, *Measure*: the recheck is
     `ops/fabro-export-runs.sh --since <end of the last window>`, then
     `python3.11 ops/fabro-agent-tools.py --local-only --outcomes <OUTDIR>/*.jsonl`.
   - `docs/coder-tweaks/handoff.md`, *Finding runs to measure*: replace the `curl` line with
     the export script, and add the `fabro dump` answer from `measure-2026-10-10.txt`
     (command output is in `stages/<NNN>-<node>@<visit>/output.log`, about 35 MB a run).
     This answers #13 item 4.

## Acceptance
- On the 26 runs of `result-2026-10-09.txt`, the table gives the same outcome counts as that
  file: 4 merged, 14 `report_blocked`, 2 `mark_needs_human`, 2 `mark_stuck`, 2 failed and 2
  `close_noop`. Entries to `rework_router` are 35 from validate and 2 from `review_gate`.
- With `--prs` on the same runs, the reasons match that file's "First block reason" counts.
- Without `--outcomes` the output is byte-for-byte what it was before this task.

## Tests
Extend the fixtures from task 01 with a `stage.completed` that publishes `refute_verdict`,
`merge_eligible` and `merge_block_reason`, an `edge.selected` from `validate` to
`rework_router`, an `agent.route.failover` and a `stage.failed`. Assert each column and the
signature masking (`attempt 3 of 4` and `attempt 1 of 4` give one signature). Test `--prs`
with `gh` replaced by a stub on `PATH`, including a stub that exits 1.

## Depends on
01 (the same file and fixtures), 02 (its output is this task's input).
