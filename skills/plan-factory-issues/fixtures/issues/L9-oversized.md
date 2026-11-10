# feat(estimates): service catalogue with packages and restrictions

Operators define services, packages and restrictions, and use them in estimates.

## Context

- `src/estimates/` has no catalogue.

## Requirements

1. Services, packages and restrictions, end to end.

## Acceptance criteria

- [ ] An operator can create a service with a price.
- [ ] An operator can create a package of services with its own price.
- [ ] A restriction is shown before a customer selects the service.
- [ ] An estimate can include a package.

## Non-goals

- Enforcing restrictions.

## Test expectations

- pytest and vitest for each part.

## Expected files

- `src/catalogue/models.py`
- `src/catalogue/routes.py`
- `src/catalogue/service.py`
- `src/estimates/service.py`
- `alembic/versions/0042_catalogue.py`
- `frontend/src/catalogue/ServiceForm.tsx`
- `frontend/src/catalogue/PackageForm.tsx`
- `frontend/src/estimates/EstimateForm.tsx`

## Task map

1. The catalogue models and migration.
2. The service routes.
3. The package routes.
4. The restriction text.
5. The service and package forms.
6. Packages in estimates.
