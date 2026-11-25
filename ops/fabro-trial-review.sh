#!/usr/bin/env bash
# fabro-trial-review.sh — the numbers for a ralph-vs-backlog trial review
# (docs/ralph/13-reviewing-the-arms.md).
#
# Usage:
#   ./ops/fabro-trial-review.sh                          # the trial so far: 2026-10-22 to today
#   ./ops/fabro-trial-review.sh --from 2026-10-26 --to 2026-11-01
#   ./ops/fabro-trial-review.sh --arms ralph,ralph-v2     # the ralph-v2 trial (ralph-v2 11)
#   ./ops/fabro-trial-review.sh --traces [...]           # also each ralph run's routing edges
#   ./ops/fabro-trial-review.sh --trace RUN_ID           # one run's routing edges, either arm
#
# `--arms A,B` names the two arms the review compares, default `backlog,ralph` (the ralph
# trial), and `ralph,ralph-v2` reads the ralph-v2 trial instead. The ralph-only sections run
# for each name that starts with `ralph`, so the default output is what it always was.
#
# Both dates are inclusive (UTC). A window holds the runs dispatched in it, as the
# scorecard's does, so the first window also holds the backlog runs of 2026-10-22
# before the switch at 21:28:06Z (the operator's choice, 2026-10-23).
#
# Prints, in order: the scorecard's `--by-workflow` table; the review tables that
# ops/fabro-trial-review.py builds inside the scheduler container (every run of both
# arms, O3's metric and guardrail, where each ralph lease went, the longest nodes per
# arm, failovers, an estimate of hosted spend); and with --traces, each ralph run's
# edges from `fabro events -p`, filtered on the host. Nothing but text reaches the Mac:
# the 2026-10-11 retro exported raw events here and filled the disk.
#
# Read-only: the database is opened with mode=ro, and nothing is written anywhere.
# Requires: ssh to the fabro host (AGENTS.md), curl.

set -euo pipefail

HOST="${FABRO_HOST:-andrew@10.10.0.32}"
HERE="$(cd "$(dirname "$0")" && pwd)"
FROM="2026-10-22"
TO="$(date -u +%Y-%m-%d)"
ARMS="backlog,ralph"
TRACES=0
TRACE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --from) FROM="${2:?--from needs a date}"; shift 2 ;;
        --to) TO="${2:?--to needs a date}"; shift 2 ;;
        --arms) ARMS="${2:?--arms needs two workflow names}"; shift 2 ;;
        --traces) TRACES=1; shift ;;
        --trace) TRACE="${2:?--trace needs a run id}"; shift 2 ;;
        -h|--help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "fabro-trial-review.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

for d in "$FROM" "$TO"; do
    if ! [[ "$d" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        echo "fabro-trial-review.sh: not a YYYY-MM-DD date: $d" >&2
        exit 2
    fi
done

# The pair the review compares, as `A,B`. The reducer checks it too; this catches the
# operator's typo here, where the message names the script they ran.
case "$ARMS" in
    [A-Za-z0-9-]*,[A-Za-z0-9-]*) ;;
    *) echo "fabro-trial-review.sh: --arms needs two workflow names, e.g. ralph,ralph-v2 (got '$ARMS')" >&2
       exit 2 ;;
esac

# The routing edges of one run, with the Iteration loop's plumbing left out: `→ record`
# keeps `check`'s outcome, `→ iterate_tN` the rung, `✗` a failed or timed-out attempt.
trace() {
    case "$1" in '' | *[!0-9A-Z]*) echo "fabro-trial-review.sh: not a run id: $1" >&2; exit 2 ;; esac
    # shellcheck disable=SC2029  # $1 is checked above and meant to expand here
    ssh "$HOST" "cd ~/fabro && docker compose exec -T fabro fabro events $1 -p 2>&1" \
        | grep -E '→ |✗' \
        | grep -v -E '→ (excerpts|task_block_gate|autofix|claim|prep|hygiene|refute_prep|review_prep|final_merge|plan_gate|merge_prep|file_remainder|pr_handoff|learn_prep|learn_post|exit) |→ pick unconditional' \
        | sed -E 's/  +/ /g' | cut -c1-160
}

reducer() {
    # shellcheck disable=SC2029  # the dates were checked above and are meant to expand here
    ssh "$HOST" "docker exec -i fabro-scheduler python -I - $*" < "$HERE/fabro-trial-review.py"
}

if [ -n "$TRACE" ]; then
    echo "## Trace of $TRACE"
    echo
    echo '```'
    trace "$TRACE"
    echo '```'
    exit 0
fi

echo "# Scorecard by workflow, $FROM to $TO"
echo
"$HERE/fabro-scorecard.sh" --by-workflow --from "$FROM" --to "$TO"
echo
reducer --from "$FROM" --to "$TO" --arms "$ARMS"

if [ "$TRACES" = 1 ]; then
    # The ralph-only section, for each arm that is one: `--ids` prints one workflow's ids.
    for ARM in ${ARMS//,/ }; do
        case "$ARM" in
            ralph*)
                echo
                echo "## $ARM routes"
                reducer --from "$FROM" --to "$TO" --ids "$ARM" | while read -r RUN WHAT; do
                    echo
                    echo "### $RUN ($WHAT)"
                    echo
                    echo '```'
                    trace "$RUN" < /dev/null
                    echo '```'
                done
                ;;
        esac
    done
fi
