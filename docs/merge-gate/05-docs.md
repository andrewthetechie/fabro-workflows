# The operator docs describe the two new gates

## Tracer-Bullet Outcome
`AGENTS.md` carries the new validation baselines, the new test count, the layout row for
`docs/merge-gate/`, and three new deployment invariants. The two roadmap records and
this series' overview say what is applied. A reader who meets a PR blocked by
`hygiene` or `refute` can find out why from the repository alone.

## User Story
As the next person or agent to change the merge phase, I want the invariants that these
gates depend on written where every change starts, so that nobody silently weakens them.

## Description
Read `docs/merge-gate/00-overview-and-contracts.md` first. This task needs tasks 01 to 04.
It changes documentation only. **Do not push.**

1. `AGENTS.md`: five edits (below).
2. `docs/factory-roadmap/A4-diff-hygiene-gate.md` and `A5-cross-family-refuter.md`:
   replace the Status line.
3. `docs/factory-roadmap/00-overview.md`: no change. Its table has no status column.
4. `docs/merge-gate/00-overview-and-contracts.md`: replace the Status line.
5. `CONTEXT.md`: no change. **Tamper counter**, **Erosion counter** and **Refuter** were
   added on 2026-09-26, with the ADR.

## Context Pack
- Source decisions: ADR 0013 (all), overview decisions 2, 6 and 11.
- Repo facts: the `AGENTS.md` lines to change are quoted in each edit below. The
  validation counts were measured on 2026-09-26 by running `fabro validate` in the
  container against a scratch copy of the series: `Backlog (65 nodes, 151 edges)` and
  `PrReview (34 nodes, 73 edges)`, each with only its existing deliberate warning.
  `ops/check-routing-schemas.py` reported 0 mismatches.
- Non-goals: do not rewrite unrelated `AGENTS.md` prose. Do not add a dated deployment
  log entry; task 06 does that.

## Delivery Strategy
- Shape: Documentation
- Valid-state scope: local commit only.

## Implementation Contract
- Expected files: `AGENTS.md`, `docs/factory-roadmap/A4-diff-hygiene-gate.md`,
  `docs/factory-roadmap/A5-cross-family-refuter.md`,
  `docs/merge-gate/00-overview-and-contracts.md`.

### Edit 1: the layout table

Insert this row immediately after the row that begins `` | `docs/architecture-review/` | ``:

```
| `docs/merge-gate/` | The task series for ADR 0013: two gates in the shared merge phase between `validate` and `deliver`. `hygiene` counts test tampering (deleted tests, added skips, removed or tautological assertions) and code erosion over the PR's added lines, in shell; any tamper counter blocks the auto-merge. `refute` is an agent that sees only the issue, the diff and the counters and tries to prove the work is not done; `refute_gate` computes its verdict, and anything but `pass` blocks. `00-overview-and-contracts.md` first. |
```

### Edit 2: the validation baselines

In the paragraph that begins `Baselines as of 2026-09-24`, replace
`` `Backlog (61 nodes, 143 edges)` `` with `` `Backlog (65 nodes, 151 edges)` ``, replace
`` `PrReview (30 nodes, 65 edges)` `` with `` `PrReview (34 nodes, 73 edges)` ``, replace
`Backlog and PrReview both include the ~21` with `Backlog and PrReview both include the ~25`,
and replace the opening words `Baselines as of 2026-09-24, re-confirmed on 2026-09-25 against`
with `Baselines as of 2026-09-26 (ADR 0013 added four merge-phase nodes), against`.

### Edit 3: the test count

Replace:

```
./ops/test-task-gates.sh      # 335 checks, offline — no host, container or network
```

with:

```
./ops/test-task-gates.sh      # 402 checks, offline — no host, container or network
```

In the paragraph that follows it (it begins `It needs only`), append this sentence to the
end of the paragraph: `Since 2026-09-26 it also covers the merge phase's diff-hygiene
counters, each with a real-git fixture, and `refute_prep`, `refute_gate`, `merge_gate`
checks 13 and 14, and the review comment's Refuter and hygiene sections (ADR 0013).`

### Edit 4: the deployment invariants

Add these three rows at the end of the *Deployment invariants* table:

```
| The diff-hygiene counters read `.fabro/hygiene.json` from `origin/<base>`, never from the PR head, and `config_changed` counts a PR that touches it | A PR that could edit the rules that judge it would loosen them: raise `erosion_threshold`, drop a test path, and pass its own check. Nothing overrides a hygiene block (ADR 0013 D2): the override is a human merge. The counters' patterns are bracket expressions (`[.]`, `[(]`) because `\"` is the only backslash a `.fabro` file may hold, and they run under **mawk**. The fixtures in `ops/test-task-gates.sh` are their contract. |
| `ci_fix_gate` runs `/tmp/fabro/hygiene.sh` again before it pushes, and `hygiene` is the only node that writes that file | `ci_fix` changes code after `merge_gate` has approved the diff, and a CI fixer is the stage most likely to skip a failing test. A second copy of the counter program in `ci_fix_gate` would drift from the first (ADR 0013 D3). |
| `refute` sees no other agent's output, `refute_gate` computes its verdict, and a missing verdict blocks | The Refuter's value is independence: a prompt that hands it `standards.json`, `spec.json` or `fix_result.json` turns it into a fourth reviewer that agrees with the other three. An agent-written `verdict` key is ignored. The verdict is `pass` only when every criterion is `met` and there is no defect. A timed-out Refuter goes straight to `deliver`, and check 14 blocks on the absent `refute_verdict` rather than letting a provider outage through. `.refute` needs a rule in **both** stylesheets, and it is on `glm-5.3` only until a non-GLM provider exists (ADR 0013 D6); a fallback for its later model must not be GLM. |
```

### Edit 5: roadmap and series status

Replace each Status line below. The old and new text of each is in a code block.

`docs/factory-roadmap/A4-diff-hygiene-gate.md`, old, then new:

```
**Status:** planned 2026-09-26 as ADR 0013 (`docs/merge-gate/`).
**Status:** applied 2026-09-26 as ADR 0013 (`docs/merge-gate/`). The issue-body override was not adopted.
```

`docs/factory-roadmap/A5-cross-family-refuter.md`, old, then new:

```
**Status:** planned 2026-09-26 as ADR 0013 (`docs/merge-gate/`).
**Status:** applied 2026-09-26 as ADR 0013 (`docs/merge-gate/`), on `glm-5.3` until a non-GLM provider is added. The seeded-defect suite was not built.
```

`docs/merge-gate/00-overview-and-contracts.md`, old, then new:

```
**Status:** planned 2026-09-26. Nothing here is implemented yet.
**Status:** applied 2026-09-26. Tasks 01 to 05 are done, and task 06 records the first runs.
```

If task 06 is not done on 2026-09-26, use the date it is done.

## Acceptance Criteria
- [ ] `grep -c '65 nodes, 151 edges' AGENTS.md` prints `1`, and so does
      `grep -c '34 nodes, 73 edges' AGENTS.md`.
- [ ] `grep -c '402 checks' AGENTS.md` prints `1`.
- [ ] The *Deployment invariants* table has the three new rows.
- [ ] Both roadmap records and the overview say `applied`.

## Test Expectations
None: documentation only. `./ops/test-task-gates.sh` still prints `PASS: 402 checks`.

## Dependencies
- Blocked by: 01, 02, 03, 04
- Blocks: 06

## Labels
`docs`

## Estimate
Small

## Risk
1

## Validator Stopping Point
The four `grep` checks in Acceptance Criteria pass.
