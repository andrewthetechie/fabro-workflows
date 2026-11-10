# feat(invoices): export an invoice as CSV

As an operator, I want to download one invoice as a CSV file, so that I can load it into my
accounting tool without retyping it.

## Context

Verified facts at `main` (2026-11-10). Proposals are under *Requirements*.

- `src/invoices/service.py:42` defines the lookup the export reads:

  ```python
  def get_invoice(session: Session, invoice_id: int) -> Invoice | None:
  ```

- `src/invoices/routes.py:18` mounts the router at `/invoices`; every route takes `current_user`.
- `tests/invoices/test_routes.py:9` is the pattern for route tests (`client.get`, then status and body).

## Requirements

1. `GET /invoices/{id}/export.csv` returns the invoice as CSV, one row per line item, with the
   header `description,quantity,unit_price,total`.
2. An invoice the user cannot see returns 404, the same as `GET /invoices/{id}`.

## Acceptance criteria

- [ ] `GET /invoices/7/export.csv` for an invoice with two line items returns status 200, `Content-Type: text/csv`, and three lines: the header and one per item.
- [ ] `GET /invoices/7/export.csv` by a user who cannot see invoice 7 returns 404.
- [ ] A code comment at the CSV writer says why amounts are written with two decimal places.
- [ ] `uv run pytest -q tests/` passes (`.fabro/ci.sh:7`).

## Non-goals

- PDF export.
- Exporting more than one invoice at a time.

## Test expectations

- `tests/invoices/test_export.py` (pytest, the `client` fixture): an invoice with line items
  `("Mowing", 1, "40.00")` and `("Edging", 2, "15.50")` exports exactly
  `description,quantity,unit_price,total` / `Mowing,1,40.00,40.00` / `Edging,2,15.50,31.00`.
- The 404 case reuses the second user from `tests/invoices/conftest.py`.

## Expected files

- `src/invoices/export.py`: new, the CSV writer.
- `src/invoices/routes.py`: the new route.
- `tests/invoices/test_export.py`: new.

## Task map

1. The CSV writer and its unit test (`src/invoices/export.py`, `tests/invoices/test_export.py`), requirement 1.
2. The route and its access check (`src/invoices/routes.py`), requirements 1 and 2.

## Human actions

- None.

## Post-deploy checks

- [ ] The exported file opens in the operator's accounting tool.
