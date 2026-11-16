# Task 03: `apply`, the in-run `recheck`, `post` and `record`

Read `00-overview-and-contracts.md` first (D6, D11, D14, D15, D18; C8, C10, C11). **Invoke
`/fabro-workflow`.** Read `issue-precheck`'s `gate` and `post`, `precheck-calibration`'s
`record`, `ops/check-graph-invariants.py` `R10_CALIBRATION_COPIES`, and `_shared/triage`'s
`improve_gate` body edit.

## Goal

Write a converged proposal to the issue with a record of what was there before, judge the new
body at once with the precheck's own check, and leave one comment that says what happened,
whatever the ending.

## Steps

1. **`apply`**: C8 steps 1–3. Re-point `audit_gate`'s `converged` edge here, and its `capped`
   and `churn` edges to `post`.
   - Stale goes to `post` with `outcome=stale`.
   - A failed original-body comment fails the node. The unconditional edge then goes to
     `record` with `outcome=failed`, and the scheduler retries (D12). Nothing was edited.
   - A failed `gh issue edit`, after the original-body comment posted, also fails the node. A
     retry finds the same body hash and posts a second original comment. That is accepted: the
     newest one is the record.
   - On success, write `applied_hash` and `outcome=applied`.
2. **`recheck_prep`**: writes `/tmp/fabro/sharpen/recheck/issue.md` and `open_issues.md` for the
   applied body, in the precheck's format, and its `main_sha` and `body_hash`.
3. **`recheck`** and **`recheck_gate`**: C10.
   - Copy the prompt to `prompts/recheck.md.j2` and register the R10 entry, then run
     `make check` to prove the copy is exact.
   - The manifest stage for `recheck` is `box: true`, with inputs at the replaced paths and
     output at `/tmp/fabro/sharpen/recheck.json` against the `precheck` schema. If R10's
     replacement cannot express the output path, add the replacement pair; do not fork the gate.
   - Both nodes take their failure to `post`, not to `record`, with `recheck=-` (C10).
4. **`post`**: C8 step 4.
   - The `fabro:sharpen` comment is written for `applied`, `capped`, `churn` and `stale`.
   - After a `recheck` that passed `recheck_gate`, also write the precheck marker for the
     applied body. Use the same code as `issue-precheck`'s `post`, with `main=` the recheck's
     `main_sha`, `body=` the applied hash, and `run=` this run.
   - The 60,000-character cut keeps the marker and the change table (C8).
   - Redact tokens as the precheck does.
   - A failed comment write is one stderr line. It does not fail the run: the body is already
     applied, and `record` carries the outcome.
5. **`record`**: finish C11. `sharpen_outcome` comes from `/tmp/fabro/sharpen/outcome`, with
   `failed` when the run reached `record` through a failure edge, which `record` detects because
   no outcome file exists. Always exit 0.
6. **The run's ending.** A run whose outcome is `failed` must **end failed** in fabro, so the
   lease release sees a failure (D12). Every other outcome ends succeeded. Do it by giving
   `record` a conditional edge to a `fail` node (a command that exits 1) when
   `sharpen_outcome=failed`, and `exit` otherwise. Say so in a graph comment.
7. **`AGENTS.md`**: add `IssueSharpen (N nodes, E edges)` to the *Validating* baselines as
   "expected, not yet verified by `make check-host`". Record the expected warnings: the same
   class as the box-only packages (`coder_pool` unbound in `claim`, with `issue_number` and
   `model_pool` folded into it). Never bind them in `[run.inputs]`.
8. **Gate tests**, added to `test_issue_sharpen.py`:
   - `apply`: the stale path; the original-comment failure (no edit attempted); the edit failure;
     success writes both comments in order and the applied hash.
   - `recheck_prep`: the format.
   - `post`: the marker for each state; editing in place; the cut order; the precheck marker
     after a recheck; no precheck marker after a failed recheck; human actions outside the body.
   - `record`: each outcome; `failed` with no outcome file; the `fail` edge.
   - The applied body equals `audit/proposed.md` byte for byte.

## Done when

- `make check` passes, with the R10 entry.
- The commit is `feat(issue-sharpen): apply, in-run recheck and the sharpen comment (issue-sharpening 03)`.
