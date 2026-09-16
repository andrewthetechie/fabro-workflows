# Task 01 — Enforce `risk` in `fix_gate`

**Depends on:** nothing. **Blocks:** 03. **LLM level:** local is fine.

Make `risk` a validated field instead of a documented suggestion.

## The problem

`review_fix.md.j2:188` already states the contract:

> `risk` is 0-5: how much residual risk a human merging this PR is taking after your
> fixes. 0 is nothing to worry about, 5 is do not merge without reading carefully.

But nothing checks it. `fix_gate` (`.fabro/workflows/pr-review/workflow.fabro:265`)
validates `outcome`, and validates that `fixed` carries a non-empty `fixes_applied` —
and stops there. The renderer treats the field as optional:

```
+ (if ($F.risk // null) != null then " - risk " + ($F.risk|tostring) + "/5" else "" end)
```

So a report that silently omits `risk` renders without it and nobody notices. Once
the merge gate turns on that number, "silently omits" becomes "silently never
auto-merges, while the report still says complete". That is the failure mode this
deployment keeps paying for — the missing `output_schema="routing"` that made every
gate's routing inert with no error reported anywhere.

## The change

In `fix_gate`, after the existing `fixed` / `fixes_applied` check and before the
`invalid` handling, add:

```sh
if [ \"$O\" != invalid ]; then
  R=$(jq -r 'if (.risk | type) == \"number\" and (.risk >= 0) and (.risk <= 5) and ((.risk | floor) == .risk) then .risk else \"bad\" end' $F 2>/dev/null || echo bad)
  if [ \"$R\" = bad ]; then
    echo 'fix_result.json has no integer risk in 0-5; every report must rate residual merge risk' >&2
    O=invalid
  fi
fi
```

It reuses the existing retry machinery exactly: `O=invalid` increments
`fix_result_attempts`, exits non-zero on the first failure so the agent gets one
repair turn, and routes to `mark_needs_human` on the second. No new node, no new
edge, no new context key.

Note `(.risk | floor) == .risk` — the contract says 0-5, and `2.5` is not a rating.
`jq` has no integer type, so the floor comparison is the check.

## The `own_findings` accounting, for the same reason

`merge_gate` check 10 blocks a merge when an error-severity `own_findings` entry is not
cited in `fixes_applied`. The prompt never asked for that: `own_findings` is documented
as holding problems "whether or not you fixed them", and the citation requirement is
scoped to "every finding **from both reviewers**".

So an agent following the contract exactly — finding a real bug itself, fixing it,
citing the reviewer finding it was working on — got the merge refused under the message
"An error-severity finding was not fixed", which was false. Observed live on
`jelly-swipe#379`, run `01M2NC5XBC16B2TD4YTN41JB77`, on the first attempt to exercise
that path. Fails closed, so nothing unsafe happened; but it suppresses auto-merge on
exactly the PRs where the fixer did the most work, and it sends a human to redo it.

Fix it in the same two places `risk` is fixed, for the same reason. The prompt gains the
requirement; `fix_gate` gains the check:

```sh
M=$(jq '[.own_findings[]? | select(.severity == \"error\") | .id] - ([.fixes_applied[]?.finding_id] + [.not_fixed[]?.finding_id]) | length' $F 2>/dev/null || echo 99)
```

`not_fixed` counts as accounting for it here even though check 9 blocks the merge on a
non-empty `not_fixed` — the two gates ask different questions. `fix_gate` asks whether
the report is *complete*; `merge_gate` asks whether it is *clean*. A finding the agent
consciously declined is a complete report and an unmergeable PR.

Enforcing at `fix_gate` is what gives the agent a repair turn. `merge_gate` has none: it
is the last node before the merge, so a bookkeeping slip there is a silent refusal at
the end of a run. `merge_gate` keeps the check as a fail-closed backstop, with wording
that says what it tests rather than asserting the fix never happened.

## Prompt reinforcement

In `.fabro/workflows/pr-review/prompts/review_fix.md.j2`, the `risk` bullet moves from
the descriptive list into the **"Rules — a validation gate reads this file and rejects
it on any violation"** list, and gains the consequence:

> - `risk` is a whole number 0-5: how much residual risk a human merging this PR is
>   taking after your fixes. 0 is nothing to worry about, 5 is do not merge without
>   reading carefully. **A gate rejects the file without it.** A PR rated 3 or below
>   that has no outstanding findings is merged automatically, so this number decides
>   whether a human ever reads the change. Rate the residual risk honestly; do not
>   inflate it to force review, and do not deflate it to force a merge.

That last sentence matters. Telling a model its score triggers an automatic merge
invites both failure directions, so name both.

## Why the gate and not the merge predicate

The alternative was to leave `fix_gate` alone and let a missing `risk` simply fail the
merge predicate, so the review still delivers. Rejected: that is silent. The PR quietly
stops auto-merging, the report still says "complete", and the cause is a field nobody
looks at. Enforcing at the gate makes the failure loud and gives the agent a repair
turn, which is what the gate is for.

The cost is real and accepted: a model that omits one integer twice turns a good review
into `mark_needs_human`. Task 09 checks that cost has not become routine.

## Acceptance

- A `fix_result.json` with `"risk": 2` passes, unchanged from today.
- A file with no `risk` key fails the first visit with the stderr line above, and the
  agent gets one repair turn.
- `"risk": 6`, `"risk": -1`, `"risk": 2.5` and `"risk": "2"` all fail.
- `"risk": 0` passes. Zero is a legitimate rating, not a missing value.
- `outcome: "needs_human"` still short-circuits to `needs_human_reason` as before —
  a run that is asking for a human must not also be failed for a missing score.
- The embedded `jq` program compiles standalone (`jq -n 'PROGRAM'`), per the rule that
  `sh -n` is blind inside `jq '...'`.
