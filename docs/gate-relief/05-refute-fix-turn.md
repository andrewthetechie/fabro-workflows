# 05: one fix turn after a refuter `not_met` (#29)

Read G6-G11 again. Read `review_fix`, `fix_gate` and the rework ladder in
`review-merge.fabro` before you write anything: copy their commit and receipt patterns,
do not invent new ones. Read `docs/agents/invariants-graph.md` on timeouts and the
breaker: a new agent node must fit the run's total budget.

## Steps

1. **Schema.** `_io/schemas/refute_fix.schema.json` (G10). Copy the style of
   `fix_result.schema.json` (`$id`, `description`, `additionalProperties:false`).
2. **Manifest.** Add a `refute_fix` stage to `_io/manifest.json`: inputs
   `refute_findings.json`, the linked issues (`refute_issues.json`, already stripped of
   post-deploy checks by task 04), the diff; output `refute_fix.json`; `git: write`;
   sealed paths include `review/refute.json`, `review/refute_verdict`,
   `review/refute_reason.txt`, `review/refute_findings.json`, `review/hygiene.json`. Look at
   how another write stage (`review_fix`, `ci_fix`) declares them. Regenerate the
   `FABRO_IO_MANIFEST` blocks for all three importers.
3. **refute_gate.** When the verdict is `fail`: write `$R/refute_findings.json` =
   `{not_met: [criteria with status not_met, {criterion, evidence}], defects: [...]}`.
   Compute `NM` = count of `not_met`; read `FA=$(cat /tmp/fabro/refute_fix_attempts 2>/dev/null || echo 0)`.
   Emit `context_updates` `{refute_verdict, refute_not_met: NM>0, refute_fix_attempts: FA}`.
   Keep the existing `refute_reason.txt` and the invalid-twice branch unchanged.
4. **Prompt.** `_shared/review-merge/prompts/refute_fix.md.j2`: you are given criteria an
   independent reviewer found not met, with evidence; change the code so each is met; do
   not edit tests to make them pass (hygiene counts it, and it blocks); you cannot see the
   verdict and nothing you write changes it; submit `refute_fix.json`. Keep it short, in
   the style of `review_fix.md.j2`.
5. **Nodes.** `refute_fix` (G9). `refute_fix_gate` (command): receipt check (the
   `_io.visit` pattern from `refute_gate`), write `1` to `/tmp/fabro/refute_fix_attempts`,
   commit the change the way `fix_gate` does. **Set the counter before the agent runs**
   (in a tiny command node or in `refute_gate` when choosing the route) so a crashed fixer
   cannot loop. Put the decision where it is simplest; one write, never reset in-run.
6. **Edges** (add next to `:1409-1412`, weights so the fix edge wins only when it applies):
   ```
   refute_gate -> refute               [condition="outcome=failed", weight=10]          // existing
   refute_gate -> refute_fix           [condition="context.refute_verdict=fail && context.refute_not_met=true && context.refute_fix_attempts=0", weight=5]
   refute_gate -> deliver                                                               // existing
   refute_fix  -> refute_fix_gate      [condition="outcome=succeeded"]
   refute_fix  -> deliver
   refute_fix_gate -> validate         [condition="outcome=succeeded"]
   refute_fix_gate -> deliver
   ```
   Check the condition grammar in the `/fabro-workflow` skill (booleans as context values,
   `&&`, numeric compare). Make sure `refute_prep` does not delete
   `refute_fix_attempts` (G8). Check that `validate`'s own counters (`fix_attempts`) are
   fine to re-enter; a red CI after the fix goes through the existing `review_fix` loop.
7. **Stylesheets** (G9): add `.refute-fix` to `backlog`, `pr-review`, `dependabot`
   `workflow.fabro`. Add fallback rows per `invariants-host.md` if the class needs them.
8. **Report.** In `render.jq`, when `refute_fix.json` exists, add one line under the
   refuter section: "One fix turn ran after the refuter's first verdict: N addressed, M not."
   so the operator can tell a second-pass verdict from a first.
9. **Header comment.** Update the `refute_gate` comment (`:615-620`) — it currently says
   nothing routes back to a fix. Cite the ADR 0013 D5 amendment.

## Tests (`ops/test-task-gates.sh`; Done-when 2 of #29)

- refute_gate, first pass, one `not_met` -> `refute_not_met=true`, `refute_fix_attempts=0`,
  `refute_findings.json` has it.
- refute_gate, `unverifiable` only -> `refute_not_met=false`.
- refute_gate, defects only -> `refute_not_met=false`.
- refute_gate, second pass (`refute_fix_attempts` file = 1), `not_met` -> attempts=1, so
  the fix edge cannot match (the **second-`not_met` stop**).
- refute_prep leaves `refute_fix_attempts` in place.
- refute_fix_gate: no receipt -> fails; good receipt -> counter 1.
If the suite has an edge/route simulator or uses `ops/check-graph-invariants.py`, add a
route case for `refute_gate -> refute_fix` and for the stop.

## Done when

`make check` passes. Commit: `feat(review-merge): one coder fix turn after a refuter not_met (#29)`.
