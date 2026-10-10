# 07. Fix today's violations, put the checker in `make check`, shorten the rows (#12, part 2)

## Outcome
`ops/check-graph-invariants.py` reports no violation on `main`, `make check` runs it on every
push, and each invariant row it covers is one line that names the check. Issue #12 can close.

## Why
A checker that fails on `main` cannot gate a push. D5 fixes each violation instead of
allowing it, so the checker has no exception list beyond D3's two.

## Change
Invoke `/fabro-workflow` first. Make each graph fix its own commit, so that a regression
points at one change.

1. **R1.** `issue-triage/workflow.fabro`: add `loop_restart_signature_limit=20` to the
   `graph [...]` block, with a `//` comment naming the triage phase's gate retry loops and
   `docs/agents/invariants-graph.md`.
2. **R5.** `backlog/workflow.toml`, hook `discord-rescue`: `matcher = "^human_rescue$"`.
   `human_rescue` is a root node of `backlog`, not an imported one.
3. **R6, comments.** In `open_pr_prep`, `validate_input`, `merge_gate`, `ci_fix_gate` and
   `remerge_base`, move every `#` comment line out of `script=` into `//` comments above the
   node. `merge_gate`'s numbered checks (`# 1. kill switch file` and the rest) become a
   numbered `//` list in the same order. Other docs cite "check 6" and "check 2b", so keep the
   numbers. Change no other line of a script.
4. **R6, backslashes.** `watch_checks`, line 917 of `review-merge.fabro`: join the
   continued `jq ... \` line with the `| sed` line that follows it. `triage.fabro` line 167:
   reword the comment so that it names the newline escape without writing it.
5. **Wire.** `ops/check.sh`: replace `# TODO(#12)` with a step that runs
   `"$PY" ops/check-graph-invariants.py`. Add it to the `make check` help line in the
   `Makefile`. Remove the `expectedFailure` from task 06's test of the real tree.
6. **Retire.** If task 06's parity step has passed once, delete `ops/check-routing-schemas.py`
   and its step in `ops/check-host.sh`, and change the `docs/agents/validating.md` lines
   that name it. If parity has not passed yet, leave both and say so in the commit.
7. **Rows.** In `docs/agents/invariants-graph.md` and `invariants-merge.md`, each row that R1-R8
   covers becomes one line: the rule, then "Checked by `ops/check-graph-invariants.py` R<N>."
   Move each row's incident history (run ids, dates, the cost) into a dated section of
   `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`, and do not lose any of it. Rows that the
   checker does not cover stay as they are. `docs/agents/validating.md` gets a paragraph on
   what the checker covers and what it does not (ADR 0019, *Consequences*).

## Acceptance
- `python3.11 ops/check-graph-invariants.py` prints `VIOLATIONS: 0` on the tree.
- `make check` runs it, and a push of a graph that breaks any rule is refused by the pre-push
  hook. Show it once with a scratch commit that you never push.
- `make check-host`: baselines unchanged (69/162, 34/73, 16/36, 24/54), parity passes.
- `make check` passes in full, because steps 3 and 4 change command-node scripts.

## Tests
No new test beyond task 06's. Step 3 changes script text that `ops/test-task-gates.sh`
extracts. If a check there matched on a comment line, change the check to match behaviour,
never the comment.

## Depends on
06. Live on push (graph and hook changes).
