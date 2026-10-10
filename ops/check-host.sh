#!/usr/bin/env bash
# check-host.sh — the gates that need the fabro host and container (opt-in, LAN).
#
# There is no `fabro` binary on the Mac, and the host CLI resolves `@prompts` the
# way the server does, so the graphs are validated in the fabro container on
# `andrew@10.10.0.32`. This needs ssh to that host, so it is opt-in: an agent or
# operator with LAN access runs `make check-host`; a pre-push hook never does.
#
# It rsyncs `.fabro/` up, runs the routing-schema checker in the container, and
# runs `fabro validate` on all four packages, comparing each node/edge count to
# the AGENTS.md *Validating* baselines (the one place to update them). It also
# checks the live settings overlay for the openai Agent profile (ADR 0017).
#
# Environment:
#   HOST=andrew@10.10.0.32   ssh target (default)
#
# Exit 0 = every host gate passed. Exit 1 = a baseline mismatch or a failed check.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HOST=${HOST:-andrew@10.10.0.32}

say() { builtin printf '\n== %s ==\n' "$*"; }

# The AGENTS.md *Validating* node/edge baselines, keyed by package name. fabro
# validate prints `Workflow: Backlog (69 nodes, 162 edges)`; match on the counts.
declare -A BASELINE=(
    [backlog]="69 nodes, 162 edges"
    [pr-review]="34 nodes, 73 edges"
    [issue-triage]="16 nodes, 36 edges"
    [arch-review]="24 nodes, 54 edges"
)

say "rsync .fabro/ to $HOST:/tmp/check/"
rsync -a --delete .fabro/ "$HOST:/tmp/check/"

say "copy the routing-schema checker"
scp -q ops/check-routing-schemas.py "$HOST:/tmp/check-routing-schemas.py"

say "mount the tree into the fabro container"
ssh "$HOST" 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'

say "ops/check-routing-schemas.py"
# pipefail: a non-zero exit from python3 (a mismatch) fails the pipeline.
ssh "$HOST" 'cd ~/fabro && python3 /tmp/check-routing-schemas.py \
    /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro' | tail -1

say "parity: fabro parse against check-graph-invariants.py --dump, six files (ADR 0019, D2)"
scp -q ops/check-graph-invariants.py "$HOST:/tmp/check-graph-invariants.py"
PARITY_BAD=0
for f in workflows/backlog/workflow.fabro workflows/pr-review/workflow.fabro \
         workflows/issue-triage/workflow.fabro workflows/arch-review/workflow.fabro \
         workflows/_shared/review-merge/review-merge.fabro workflows/_shared/triage/triage.fabro; do
    # Both sides are normalized on the host: fabro's {"Str": v} wrappers, and the reader's plain strings.
    if ssh "$HOST" "cd ~/fabro && docker compose exec -T fabro fabro parse /tmp/check/$f </dev/null \
            | python3 /tmp/check-graph-invariants.py --normalize > /tmp/parity-fabro.json \
        && python3 /tmp/check-graph-invariants.py --dump /tmp/check/$f > /tmp/parity-reader.json \
        && cmp -s /tmp/parity-fabro.json /tmp/parity-reader.json"; then
        echo "  ok   $f"
    else
        echo "  FAIL $f: fabro parse and the reader disagree"
        PARITY_BAD=$((PARITY_BAD + 1))
    fi
done
ssh "$HOST" 'rm -f /tmp/parity-fabro.json /tmp/parity-reader.json /tmp/check-graph-invariants.py' || true
if [ "$PARITY_BAD" -ne 0 ]; then
    echo "  FAIL: $PARITY_BAD file(s) differ between fabro parse and the reader"
    exit 1
fi

say "fabro validate on all four packages (node/edge vs AGENTS.md baselines)"
for w in backlog pr-review issue-triage arch-review; do
    OUT=$(ssh "$HOST" "cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/$w/workflow.toml" 2>&1)
    LINE=$(printf '%s\n' "$OUT" | grep -E '^Workflow: ' | head -1)
    echo "  $LINE"
    case "$LINE" in
        *"${BASELINE[$w]}"*) : ;;
        *)
            echo "  FAIL: $w baseline changed (expected '${BASELINE[$w]}'). Update AGENTS.md *Validating*."
            printf '%s\n' "$OUT" | grep -E '^(Workflow|warning|Validation|Error)' | sed 's/^/    /'
            exit 1
            ;;
    esac
    if ! printf '%s\n' "$OUT" | grep -qE '^Validation: OK'; then
        echo "  FAIL: $w did not validate OK"
        printf '%s\n' "$OUT" | grep -E '^(Workflow|warning|Validation|Error)' | sed 's/^/    /'
        exit 1
    fi
done

say "check-agent-profiles.py against the live settings overlay (ADR 0017)"
scp -q ops/check-agent-profiles.py "$HOST:/tmp/check-agent-profiles.py"
if ssh "$HOST" 'umask 077; o=$(mktemp) && docker exec fabro-fabro-1 cat /storage/.home/settings.toml >"$o" && python3 /tmp/check-agent-profiles.py "$o"; rc=$?; rm -f "$o"; exit $rc'; then
    :
else
    echo "  FAIL: live overlay check-agent-profiles failed" >&2
    ssh "$HOST" 'rm -f /tmp/check-agent-profiles.py' || true
    exit 1
fi
ssh "$HOST" 'rm -f /tmp/check-agent-profiles.py' || true

printf '\nPASS: make check-host — all host gates green\n'
