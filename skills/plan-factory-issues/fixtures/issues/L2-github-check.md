# fix(ui): restore the end-to-end login flow

The end-to-end login test fails after the session cookie rename.

## Context

- `e2e/login.spec.ts:12` still reads the old cookie name.

## Requirements

1. The spec reads the new cookie name.

## Acceptance criteria

- [ ] The `UI end-to-end` CI job passes on the PR.

## Non-goals

- Rewriting the spec.

## Test expectations

- The existing spec.
