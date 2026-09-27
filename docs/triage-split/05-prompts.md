# 05 · `decompose` reads the map as a hint; triage `improve` leaves the generated sections alone

Read `00-overview-and-contracts.md` first. The contract for this task is C2 (ADR 0015 D2).

## `backlog/prompts/decompose.md.j2`

Under `## Inputs`, after the `issue.json` bullet:

> - If the issue body has a `## Proposed task map` section, triage drafted it against an
>   earlier `main`. Start from it. It gives a slicing and the files that are likely to be
>   involved. Check every file and claim against the checkout. When you keep a slice, keep
>   its `id` exactly as written. When you change a slice's scope, give it a new `id`. You may
>   merge, split, reorder or drop entries. Where the map and the checkout disagree, the
>   checkout is correct. The size budget below applies to your output, not to the map.

Add nothing else. `decompose` stays the author of `decomposition.json`, and `decompose_gate`
is unchanged.

## `_shared/triage/prompts/improve.md.j2`

The body improver rewrites the whole body. Add this rule: keep the sections
`## Decisions made during triage`, `## Proposed task map` and `## Split into` word for word,
and keep a first-line `<!-- fabro:… -->` marker in place. The workflow owns them.
(`improve_gate` already restores the `arch-candidate` marker. This rule covers the rest.)

## Acceptance

- `fabro validate` for `backlog` gives the same baseline, with the one `issue_number`
  warning.
- `grep -c 'Proposed task map'` is 1 in `decompose.md.j2`, and it matches in `improve.md.j2`.
