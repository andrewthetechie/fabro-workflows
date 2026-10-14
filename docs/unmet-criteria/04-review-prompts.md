# 04: review and fix prompts (U11, U12)

Prompt changes only. No contract or routing change.

1. `backlog/prompts/spec.md.j2` (extra review): "Report an unmet requirement unless those
   sources accept the deviation" and "Do not re-litigate a design" stay for design choices, but
   never cover an acceptance criterion. A criterion not delivered as written is a finding
   that quotes the criterion. A deviation note in `completed.md` does not excuse it. Only the
   issue and the human discussion can do that. A finding's follow-up implements the
   criterion; it never asks for a relabel or a "more honest" substitute.
2. `_shared/review-merge/prompts/spec.md.j2`: the same rule. A criterion delivered as a
   different behaviour is `wrong` (or `missing`), and the finding quotes the criterion.
3. `backlog/prompts/extra_decompose.md.j2`: a follow-up for an unmet criterion implements that
   criterion. Never write a task that relabels, rewords or documents a Substitution.
4. `_shared/review-merge/prompts/review_fix.md.j2` and `refute_fix.md.j2`: a finding about an
   unmet criterion is fixed by meeting the criterion. When that cannot be done in scope, leave
   it in `not_fixed` / `not_addressed` with the reason. Never close it by renaming,
   relabelling or adding a comment that explains the gap.

## Done when

`make check-fast` passes (the manifest checker reads the prompts).
