# Task 02: the three audit lenses, `audit_gate`, and the round loop

Read `00-overview-and-contracts.md` first (D6–D8, D10, D22; C3, C5, C6). **Invoke
`/fabro-workflow`** for loop edges and `loop_restart_signature_limit`. Read `ralph`'s review
round (`review_prep`, `standards`, `spec`, `review_gate`) and `docs/agents/invariants-ralph.md`
for how a fresh session and sealed inputs are built today. Read `docs/egm-research/01-egm-mechanisms.md`
for the finding lifecycle this adapts.

## Goal

An independent check of every draft, from three fresh sessions that see only the proposal. A
command node, not a model, decides whether the round is clean, whether to loop, and when to stop.

## Steps

1. **Edges.** Re-point `draft_gate`'s success edge to `comprehend`. Then
   `comprehend → verify → preserve → audit_gate`. From `audit_gate`:
   - `converged` → `apply` (for now `record`, until task 03 adds `apply`);
   - `open` → `draft`;
   - `capped` or `churn` → `post` (for now `record`).

   Every node keeps its unconditional edge to `record` (R12). Set `loop_restart_signature_limit`
   so three rounds fit with room to spare, and say in a graph comment why the number was chosen.
2. **The lenses** (`comprehend` and `verify` are `class="sharpen"`; `preserve` is
   `class="preserve"`; each `timeout="15m"`; each with its own prompt in `prompts/`):
   - Each prompt names its four inputs by path (C3) and says plainly that it has not seen how the
     draft was written and must not guess.
   - `comprehend`: paraphrase the proposed issue as a context-free implementer would; paraphrase
     how the code works today in that area, from the checkout; report a `major` where the issue
     reads as disconnected from the code, or two criteria ask for different things.
   - `verify`: give one verdict per proposed acceptance bullet (C5). `checkable_by_ci` must name
     the `ci.md` line. `checkable_from_diff` must say what in the diff shows it. Name the Refuter's
     rule: a command counts only when `.fabro/ci.sh` runs it on the exact tree.
   - `preserve`: one pair per change row, comparing `original` with `proposed`, where
     `to_post_deploy` is compared with its moved text. A narrower scope, a weaker threshold, a
     dropped condition, or a different behaviour is a `substitution`. Choosing between a
     criterion and a non-goal is `contradiction_resolved`. Cite the words that differ.
3. **Schemas and manifest.** Add `sharpen-comprehend`, `sharpen-verify` and `sharpen-preserve` to
   `_io/schemas/` (C5). Add three stages to `_io/manifest.json` with `box: true`, the four C3
   audit inputs, and outputs at `/tmp/fabro/sharpen/lens/<lens>.json`. Regenerate.
   **Each lens's inputs are exactly the four audit files.** Verify this in the generated manifest:
   no path outside `/tmp/fabro/sharpen/audit/`.
4. **Fresh sessions.** Each lens is its own agent node, so each is a new session. Confirm that
   `default_fidelity="truncate"` keeps the draft's conversation out of a lens's preamble, and
   write the confirmation in a graph comment. If fabro carries a previous stage's output into the
   next prompt, stop and report: D7 depends on it not doing so.
5. **`audit_gate`**: C5 and C6, in `jq`. Before each round's lenses run, `draft_gate` deletes
   `/tmp/fabro/sharpen/lens/`, so a lens file from an earlier round can never count. It:
   - writes `history.json`, `rounds/<n>.json` and `findings.md`;
   - emits `sharpen_result` and `round`;
   - writes `/tmp/fabro/sharpen/outcome` on `capped` or `churn`;
   - keeps the open findings for `post` in `/tmp/fabro/sharpen/open_findings.json`, and the
     Comprehension paraphrase in `/tmp/fabro/sharpen/paraphrase.md`.
6. **The draft in later rounds.** The draft prompt already reads `findings.md` (task 01). Check
   that `prep` does not run again in the loop: the edge back goes to `draft`, not to `prep`.
7. **Gate tests**, added to `test_issue_sharpen.py`:
   - a clean round (converged);
   - each lens missing in turn, and a lens with no receipt (each `not run`, open);
   - a blocker; a major; a nit only (clean);
   - an `unverifiable` criterion verdict; a proposed bullet with no verdict; a `substitution`; a
     `contradiction_resolved`; a change row with no pair;
   - recurrence: a finding fixed in round 2 that reappears in round 3 is re-opened and is churn;
   - alternating `proposed` text across rounds 1–3 is churn;
   - round 3 not clean is capped;
   - a reused message under a different `criterion_ref` is a new finding;
   - `findings.md` lists open findings before history;
   - the lens directory is cleared between rounds.

## Done when

- `make check` passes.
- The commit is `feat(issue-sharpen): three-lens audit and the round loop (issue-sharpening 02)`.
