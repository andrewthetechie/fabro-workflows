# 01 · Make the step-1 measurement a checked-in script

Read `00-overview-and-contracts.md` first. This task depends on no other task. It is
first, so that the baseline is taken with the same code as the result.

## Why

The ADR 0014 evidence came from two throwaway scripts (`.scratch/b4-code-context/analyze.py`
and `extra.py`, untracked). Task 08 has to measure again with the same method, and B1's
scorecard can use it too.

## Change

`ops/fabro-exploration-share.py`: merge the two scripts into one. Input is a directory of
`fabro events <run> --json` files. It prints the tables in ADR 0014 "Evidence":

1. tool calls by stage and category (repo `read_file`, `/tmp/fabro` `read_file`, `grep`,
   `glob`, shell search, shell read, shell git, shell test/build, other, edit/write). Add
   **`fabro-code`** as its own shell category;
2. the estimated share of input tokens from exploration results. The method: bytes ÷ 4 ×
   the number of later LLM calls in the same session. Print the method in the output;
3. repeated `read_file` of the same path within one stage visit;
4. `agent.loop.detected` by stage;
5. exploration-only turn time;
6. visits per stage, so that task 08 can tell when there is enough data;
7. the dossier presence rate: of the `improve` visits that ended `ready` (read
   `improve_result.json` through the `write_file` arguments), the share that also wrote
   `/tmp/fabro/task-context.md`.

Keep the event field paths that are documented in the scripts. Add a `--since <ISO date>`
filter and a `--workflow` filter.

The fetch loop stays a documented shell snippet at the top of the file. The script itself
does not ssh anywhere.

## Acceptance

Run against the 9 runs of 2026-09-25/26, it reproduces the ADR's headline numbers within
±1 point: 58% of calls, ~39% of tokens, 52% of turn time, and 53% `coder` re-reads.
