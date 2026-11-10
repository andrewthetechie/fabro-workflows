# fix(billing): stop double-charging a retried billing run

A billing run retried after a timeout charges every customer it already charged on the first attempt.

## Context

- `src/billing/run.py:88` loops over `due_customers()` and calls `charge(customer)` with no record of earlier attempts.
- `src/billing/models.py:30` already has `BillingAttempt(customer_id, run_id, status)`.

## Requirements

1. A retried run skips every customer whose `BillingAttempt` for the same `run_id` has status `charged`.

## Acceptance criteria

- [ ] Running the same run id twice over three due customers calls `charge` three times in total.
- [ ] A test in `tests/billing/test_run.py` covers the retry.
- [ ] `uv run pytest -q tests/` passes (`.fabro/ci.sh:7`).

## Non-goals

- Changing how a failed charge is retried.

## Test expectations

- pytest, with `charge` replaced by a fake that counts calls: run `"run-1"` twice over three customers; the count is 3.
