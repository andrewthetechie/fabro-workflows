# fix(jobs): keep a manual retry's field reset in one place

The manual retry and the scheduler's retry reset different fields, so a manually retried job keeps a stale error.

## Context

- `src/jobs/retry.py:12` resets `retry_count`, `status` and `error`; `src/jobs/admin.py:40` resets only `status`.

## Requirements

1. Both retry paths call `reset_for_retry`.

## Acceptance criteria

- [ ] A test pins that a manual retry persists exactly `reset_for_retry`'s field set: retry_count 0, status pending, error NULL.
- [ ] The manual snapshot button still creates its file at the configured path, with no manual rebuild.
- [ ] No production code outside `src/jobs/` changes, and nothing under `.github/workflows/` changes.
- [ ] `src/jobs/admin.py` no longer compares job ids by hand; it calls `same_job`.

## Non-goals

- Changing the retry schedule.

## Test expectations

- pytest: retry a failed job through each path; both leave `error` NULL.
