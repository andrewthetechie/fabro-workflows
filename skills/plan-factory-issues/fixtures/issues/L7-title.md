# Add CSV export for invoices

Operators want to export an invoice.

## Context

- `src/invoices/routes.py:18` mounts the router.

## Requirements

1. An export route.

## Acceptance criteria

- [ ] `GET /invoices/7/export.csv` returns status 200 and `text/csv`.

## Non-goals

- PDF export.

## Test expectations

- pytest route test.
