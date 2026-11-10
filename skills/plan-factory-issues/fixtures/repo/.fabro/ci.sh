#!/usr/bin/env bash
# The fixture repository's gate. Every route kind the lint follows appears once.
set -euo pipefail
test_env=$(fabro-io test-env); eval "$test_env"

uv run ruff check src tests
uv run pytest -q tests/
bash scripts/check-migrations.sh
make typecheck

cd frontend
npm run lint
CI=1 npm test
