#!/usr/bin/env bash
# check.sh — the offline gates that must pass before a push to `main`.
#
# A push to `main` is live on the next automation fire (AGENTS.md, *Pushing to
# `main` deploys*), so these run on every push: `make hooks` installs the pre-push
# hook (ops/hooks/pre-push) that runs this. They need no host, no container and no
# LAN — only jq, awk, git, python3.11 and (when the fabro-io tree changed) cargo —
# so they are cheap enough to gate every push.
#
# The gates are the commands AGENTS.md *Validating* used to list by hand. Each one
# is echoed as it starts; with `set -e` the script stops at the first failure, so
# `make check` exits non-zero exactly when a gate fails.
#
# Environment:
#   CHECK_CARGO=always|auto|never   when to run `cargo test` in ops/fabro-io/
#                                    (default auto: only when that tree changed)
#
# Exit 0 = every gate passed. Exit 1 = the first gate that failed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# When git runs a hook it exports GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and more,
# pointing at this repository. test-task-gates.sh builds real scratch repositories;
# with those variables inherited, its nested `git init`/`commit`/`push` act on THIS
# repository and its GitHub remote instead (2026-10-10: main rewritten locally and on
# origin). Clear every repository-local variable git lists, whether we are entered by
# a hook or by hand. test-task-gates.sh clears them too and refuses to run if a scratch
# repository still resolves elsewhere.
# shellcheck disable=SC2046  # one word per variable name, by design
unset $(git rev-parse --local-env-vars)

PY=python3.11

say() { builtin printf '\n== %s ==\n' "$*"; }

# 1. The command-node shell. `fabro validate` parses the graphs but never runs the
#    embedded `jq`/`awk` scripts; their bugs pass validation and fail at runtime.
say "ops/test-task-gates.sh"
ops/test-task-gates.sh

# 2. The Stage manifest (ADR 0016) is generated, never hand-edited. `fabro validate`
#    is blind to drift, so regenerate in memory and fail on any drift or Gate break.
say "python3.11 ops/fabro-io-manifest.py check"
"$PY" ops/fabro-io-manifest.py check

# 3. Every enabled provider must resolve to the openai Agent profile (ADR 0017),
#    against the tracked template (the live overlay is checked by make check-host).
say "python3.11 ops/check-agent-profiles.py ops/settings.toml.example"
"$PY" ops/check-agent-profiles.py ops/settings.toml.example

# 4. The ops/ Python unit tests.
say "python3.11 -m unittest discover -s ops/tests"
"$PY" -m unittest discover -s ops/tests

# 5. The dotted-key trap: a workflow.toml that only parses under TOML's dotted-key
#    semantics passes `fabro validate` but makes the fire endpoint return 422. Parse
#    every generated workflow.toml the way it will be read.
say "tomllib parse of every .fabro/workflows/*/workflow.toml"
n=0
for f in .fabro/workflows/*/workflow.toml; do
    "$PY" -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$f"
    n=$((n + 1))
done
echo "  ok: $n workflow.toml files parse"

# 6. The output schemas (ADR 0016 D7) are compiled in Rust: cargo test in
#    ops/fabro-io/ compiles every _io/schemas/*.schema.json under draft 2020-12.
#    Compiling Rust is slow, so by default this runs only when that tree (or the
#    schemas it reads) changed vs origin/main. CHECK_CARGO overrides: auto (default),
#    always (every push — the CI backstop), never.
say "cargo test (ops/fabro-io) — schemas compile"
case "${CHECK_CARGO:-auto}" in
    always) RUN_CARGO=1 ;;
    never)  RUN_CARGO=0 ;;
    auto)
        if git diff --quiet origin/main -- ops/fabro-io .fabro/workflows/_io/schemas 2>/dev/null; then
            echo "  skipped: ops/fabro-io and _io/schemas unchanged since origin/main (CHECK_CARGO=always to force)"
            RUN_CARGO=0
        else
            RUN_CARGO=1
        fi
        ;;
    *)
        echo "ERROR: CHECK_CARGO must be auto, always or never (got '$CHECK_CARGO')" >&2
        exit 1
        ;;
esac
if [ "$RUN_CARGO" = 1 ]; then
    (cd ops/fabro-io && cargo test)
fi

# TODO(#12): the graph-invariants checker slots in here once it exists.

printf '\nPASS: make check — all offline gates green\n'
