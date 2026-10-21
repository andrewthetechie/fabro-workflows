#!/usr/bin/env bash
# fabro-scorecard.sh — print the weekly factory scorecard (issue #22) for a window.
#
# Usage:
#   ./ops/fabro-scorecard.sh                         # the last 7 days, up to today
#   ./ops/fabro-scorecard.sh --from 2026-09-25 --to 2026-10-11
#   ./ops/fabro-scorecard.sh --week                  # the current ISO week so far
#   ./ops/fabro-scorecard.sh --last-week             # the previous ISO week
#   ./ops/fabro-scorecard.sh --json [...]            # the JSON behind the table
#   ./ops/fabro-scorecard.sh --repo OWNER/REPO [...] # one repository only (ADR 0025, C10)
#
# Both dates are inclusive (UTC). A window holds the runs dispatched in it.
#
# The numbers come from the coder scheduler's `/api/scorecard`, which reads the
# summaries its collector builds on the host by streaming each released run's
# events from fabro. No event log, and nothing but this table, reaches this
# machine: the 2026-10-11 retro exported raw events here and filled the disk.
#
# The same numbers, with charts, are on the scheduler's Metrics tab
# (http://10.10.0.32:32280/metrics). See docs/scorecard/README.md for what each
# metric counts.
#
# Requires: curl, and python3 for the --week dates.

set -euo pipefail

SCHEDULER="${SCHEDULER_URL:-http://10.10.0.32:32280}"
FROM=""
TO=""
REPO=""
FORMAT="md"
ENDPOINT="api/scorecard"

iso_week() { # iso_week <weeks back>: "<monday> <sunday>" of that ISO week, UTC
    python3 -c '
import sys, datetime as d
t = d.datetime.now(d.timezone.utc).date()
m = t - d.timedelta(days=t.weekday()) - d.timedelta(weeks=int(sys.argv[1]))
print(m, m + d.timedelta(days=6))' "$1"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --from) FROM="${2:?--from needs a date}"; shift 2 ;;
        --to) TO="${2:?--to needs a date}"; shift 2 ;;
        --repo) REPO="${2:?--repo needs OWNER/REPO}"; shift 2 ;;
        --week) read -r FROM TO <<<"$(iso_week 0)"; shift ;;
        --last-week) read -r FROM TO <<<"$(iso_week 1)"; shift ;;
        --json) FORMAT="json"; shift ;;
        -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "fabro-scorecard.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

for d in "$FROM" "$TO"; do
    if [ -n "$d" ] && ! [[ "$d" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        echo "fabro-scorecard.sh: not a YYYY-MM-DD date: $d" >&2
        exit 2
    fi
done

QUERY="format=$FORMAT"
[ -n "$FROM" ] && QUERY="$QUERY&from=$FROM"
[ -n "$TO" ] && QUERY="$QUERY&to=$TO"
[ -n "$REPO" ] && QUERY="$QUERY&repo=$REPO"

curl -fsS --max-time 60 "$SCHEDULER/$ENDPOINT?$QUERY"
[ "$FORMAT" = json ] && echo
exit 0
