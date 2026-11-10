# test(reports): cover the remaining branches in the report renderer

The report renderer's branch coverage is below the rest of the package.

## Context

- `src/reports/render.py` has uncovered branches at lines 40-44 and 91-94.

## Requirements

1. Tests cover the listed branches.

## Acceptance criteria

- [ ] Every line and branch listed above is covered, or the PR explains why it was left (unreachable).
- [ ] The PR description lists before/after statement and branch % for each file this issue names.

## Non-goals

- Changing the renderer's behaviour.

## Test expectations

- pytest, one test per listed branch.
