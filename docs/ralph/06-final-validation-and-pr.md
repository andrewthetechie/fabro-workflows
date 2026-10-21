# 06. Final validation and the PR

## Outcome

A done or exhausted loop merges `main`, passes Final validation, runs the counters and goes
to the Refuter. A passed review opens the PR and files the remainder. A red Final validation
becomes one more Task, once; after that it stops the run without a PR. `pr_handoff` exists
and goes to `stop_reason` until task 07 (interim I7).

## Why

00: O7, O18, C7.

## Change

Read `backlog`'s `final_merge`, `open_pr_prep` (its refusals, `final_failures.txt`, the
`final_fix` route), `open_pr`, `file_remainder`, `pr_handoff`, the shared `hygiene`, and
`invariants-backlog.md`, *Opening the PR*.

### Nodes

- `final_merge`: a copy. Its `final_conflict=true` target is `resolve_merge`, whose gate
  now also routes `merge_return=pr` back to `final_merge`.
- `final_check`: C7. The two refusals copy `open_pr_prep`'s lines (name the source in the
  comment). The failing-test extraction copies `open_pr_prep`'s `final_failures.txt` lines.
- `hygiene`: a copy.
- `open_pr`, `file_remainder`, `pr_handoff`: copies. `open_pr`'s body gains the C7
  `### Unmet criteria` list when `loop_exhausted` is true (a replacement). `file_remainder`
  is unchanged: `plan_gate` writes `remainder.json` in its shape.
- `refute_gate` (task 04) gains C7's write of `fix_result.json` on `hold` with
  `loop_exhausted` true.

### Edges

```
pick -> final_merge               [condition="context.loop=done && context.phase=pre"]        // replaces I2
pick -> final_merge               [condition="context.loop=exhausted && context.phase=pre"]   // replaces I2
final_merge -> final_check        [condition="outcome=succeeded && context.final_conflict=false"]
final_merge -> resolve_merge      [condition="outcome=succeeded && context.final_conflict=true"]
final_merge -> stop_reason
resolve_merge_gate -> final_merge [condition="context.merge_ok=true && context.merge_return=pr", weight=10]
final_check -> hygiene            [condition="outcome=succeeded && context.final_route=pr"]
final_check -> pick               [condition="outcome=succeeded && context.final_route=fix"]
final_check -> mark_stuck         [condition="outcome=succeeded && context.final_route=stuck"]
final_check -> stop_reason
hygiene -> refute_prep            [condition="outcome=succeeded && context.phase=pre"]        // replaces I3
hygiene -> stop_reason
refute_gate -> open_pr            [condition="context.refute_route=hold && context.loop_exhausted=true"]   // replaces I5
review_gate -> open_pr            [condition="context.review_route=pass"]                                   // replaces I6
open_pr -> file_remainder         [condition="outcome=succeeded"]
open_pr -> stop_reason
file_remainder -> pr_handoff      [condition="outcome=succeeded"]
file_remainder -> stop_reason
pr_handoff -> stop_reason                                                                      // I7: task 07 → merge_gate
```

`hygiene` publishes nothing, so `context.phase` on its edge is the value `final_check`
published. Check the parsed edges in the test.

`file_remainder`'s `pr_draft` edges in `backlog` exist for `open_pr_prep`'s `draft` route,
which `ralph` does not have: `ralph` never opens a draft PR. Drop that edge and record it.

## Acceptance

- `make check` passes, including the new sections.

## Tests

`ralph — final_check` (real git, a bare `origin`, a stub `.fabro/setup.sh` and `ci.sh`):

1. Green: `final_route=pr`, `validated_tree` `<tree> final`, `final_validated_tree` written.
2. Red, phase `pre`, `final_fix_attempts` 0, cap not spent: `fix`, one `final_fix` Task with
   the failing names, `final_fix_attempts` 1.
3. Red again: `stuck`, `stuck_reason.md` names the failures.
4. Red, `iterations` at the cap: `stuck`.
5. Branch tree equals `origin/main`: `stuck` with the refusal text.
6. A non-merge commit touching `.github/workflows/x.yml`: `stuck`; a merge commit of main
   that does: not refused.
7. Red, phase `post`: routes for `mark_needs_human` (task 07 adds the edge) with both
   reason files.

`ralph — edges`: `final_check`'s routes are exclusive; `hygiene`'s phase edges are exclusive.

## Depends on

05.
