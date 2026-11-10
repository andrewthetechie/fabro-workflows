# perf(search): rank exact title matches first

Exact title matches appear below partial matches.

## Context

- `src/search/rank.py:20` scores title and body matches the same.

## Requirements

1. An exact title match scores above any partial match.

## Acceptance criteria

- [ ] A search for `Mowing` returns the invoice titled `Mowing` first.
- [ ] Gold-set Hit@5 does not drop.
- [ ] Line coverage must not drop below the current value.

## Non-goals

- A new ranking model.

## Test expectations

- pytest: a three-document index, query `Mowing`, the exact match is first.
