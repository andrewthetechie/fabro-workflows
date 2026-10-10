# 06. `ops/check-graph-invariants.py`: reader, rules, fixtures and parity (#12, part 1)

## Outcome
A stdlib checker reads every `.fabro` file, splices the imports, and reports R1-R8 (C6).
`make check-host` proves that its reader agrees with `fabro parse`. The checker is not yet in
`make check`, because today's graphs break R1, R5 and R6. Task 07 fixes them and wires it in.

## Why
The invariants that cost #7 are mechanical, and prose is their only enforcement (ADR 0019).
`fabro validate` checks none of them.

## Change
1. `ops/check-graph-invariants.py`, to C6. Start from
   `.scratch/recheck-follow-ups/dotproto.py` and `rules.py` if they exist in your checkout.
   They are untracked scratch, not a contract. Structure it as: the tokenizer, the reader
   (returns statements in `fabro parse`'s shape), the splice, one function per rule, and
   `main`. Each rule's docstring names its row in `docs/agents/invariants-*.md`.
2. R2 needs durations: accept `Ns`, `Nm` and `Nh` only, and report any other `timeout` or
   `stall_timeout` value as a violation, so that a new unit fails closed.
3. R1's cycle test: a cycle exists when a command node reaches itself over the edges of the
   spliced graph. A root graph with an import needs no cycle test.
4. R5 compiles each matcher with Python's `re`. The set of names it may match is the running
   node ids (root ids and `<import>.<id>`) plus `agent` and `shell`.
5. `ops/check-host.sh`: after the routing-schema step, copy the checker to the host and, for
   each of the six `.fabro` files, compare normalized `fabro parse` JSON with
   `check-graph-invariants.py --dump` (C6, *Parity*). Run the comparison on the host with
   `python3`, as the routing checker does.
6. Fixtures and tests (C6). Each `r<N>` fixture is the `ok` package with one change.

## Acceptance
- `python3.11 ops/check-graph-invariants.py` on today's tree reports exactly these, and no
  other violation (`measure-2026-10-10.txt`):
  - R1 `issue-triage`.
  - R5 `backlog` `discord-rescue`.
  - R6 the `#` lines in `open_pr_prep`, `validate_input`, `merge_gate`, `ci_fix_gate` and
    `remerge_base`, the backslash at the end of line 917 of `review-merge.fabro`
    (`watch_checks`), and the backslash in the comment at line 167 of `triage.fabro`.
- A test asserts that the spliced counts are 69/162, 34/73, 16/36 and 24/54.
- `make check-host` passes its new parity step on all six files.
- Each `r<N>` fixture reports R<N> and nothing else, and `ok` reports nothing.

## Tests
`ops/tests/test_check_graph_invariants.py` (unittest, discovered by `make check`): the
fixtures, the counts, the tokenizer on a script holding `if [ -z \"$X\" ]; then echo '{}'`
and `//` inside a string, and the refusal of `subgraph` and `node [shape=box]`. Until task
07, the test of the real tree is marked `expectedFailure` with the violation list above.

## Depends on
Nothing.
