# 01 · Measure input reading, before anything changes

Read `00-overview-and-contracts.md` first. This task changes no workflow.

## Why

The B3 numbers come from a throwaway script over three runs. Task 11 must compare the
result with a baseline that was measured by the same code. So the metrics go into the script
that ADR 0014 already uses, `ops/fabro-exploration-share.py`, before any stage migrates.

## Change

Add a section `input reads` to `ops/fabro-exploration-share.py`. An **input read** is a tool
call whose arguments contain `/tmp/fabro/`, with the tool `read_file`, or `shell` whose first
command is one of `cat`, `head`, `tail`, `sed`, `jq`, `wc` or `ls`. From `ToolCallStarted`
and `LlmRequestStarted`, grouped by `session_id`, report for each node id (strip the
`@visit` suffix; keep the import prefix):

| Column | Meaning |
|---|---|
| `sessions` | agent sessions of that node |
| `lead_turns` | median and p90 of the count of leading turns in which **every** tool call is an input read |
| `lead_secs` | median and p90 of the seconds from the session's first `agent.llm.started` to the first turn that is not a lead turn |
| `input_reads` | median input reads for each session |
| `partial_reads` | count of `read_file` input reads with `limit` or `offset` |
| `capped_reads` | count of `read_file` input reads whose output's last numbered line is 2000 or more |
| `sealed_reads` | count of input reads, in `review_merge.refute` sessions, of any path in the Refuter's Sealed list (C2 source, or the list in `refute.md.j2` before task 07) |
| `io_calls` | count of `mcp__io__inputs` and `mcp__io__submit` calls (0 before task 06) |

Also report, for each `/tmp/fabro` input path, the p50, p90 and maximum **size in bytes** of
the read's output. That is the evidence for the page budget (Decision 4).

Print a whole-run line: total turns, lead turns and their share, lead seconds and their
share of stage wall time (`stage.completed` `timing.wall_time_ms`).

## Baseline

Fetch every `backlog`, `pr-review`, `arch-review` and `issue-triage` run that completed since
2026-09-26T00:00Z, as the script's docstring shows. Run the script, and commit its output as
`docs/stage-io/baseline-2026-09-XX.txt`, with the run ids in its header. Put the page-budget
evidence in the file too. If the p90 of `diff.patch` or `refute_diff.patch` is more than
49152 bytes, record that in Decision 4 of the overview.

## Acceptance

- The script runs on the three B3 runs and reproduces B3's numbers: 97 lead turns in 1001,
  255 whole-file input reads, 5 partial, 0 capped. A difference of a few units caused by the
  stricter definition is acceptable. Say why in the commit message.
- The baseline file is committed.
