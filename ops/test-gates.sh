#!/usr/bin/env bash
# test-gates.sh — the offline tests for the shell in the graphs' command nodes.
#
# The pytest port of ops/test-task-gates.sh (ops/gate_tests/). Each section of named checks is
# one test, and pytest-xdist runs them in parallel: ~35s on the Mac, against ~3 minutes for
# the serial shell suite. It needs uv (which provides pytest from ops/gate_tests/uv.lock), jq,
# git, awk, sed, grep and python3.11; when dash is installed every `sh` a test runs is dash.
#
# Usage:
#   ./ops/test-gates.sh                  # the whole suite
#   ./ops/test-gates.sh -k open_pr_prep  # any pytest arguments pass through
#
# Exit: pytest's (0 all passed).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Run from a git hook, git exports GIT_DIR and friends pointing at this repository; the
# fixtures commit and push in scratch repositories, and inherited they would act on this one
# (2026-10-10). Clear them here too; the harness also scrubs them and refuses to start if a
# scratch repository still resolves elsewhere.
# shellcheck disable=SC2046  # one word per variable name, by design
unset $(git rev-parse --local-env-vars)
# An active virtualenv makes uv warn on every run; the project has its own .venv.
unset VIRTUAL_ENV

command -v uv >/dev/null || { echo "ERROR: uv is required (brew install uv)" >&2; exit 1; }

cd "$REPO_ROOT/ops/gate_tests"
exec uv run --frozen --quiet pytest -n auto -q "$@"
