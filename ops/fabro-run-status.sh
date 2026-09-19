#!/usr/bin/env bash
# fabro-run-status.sh — LLM-free health check for an in-flight fabro run.
#
# Usage:
#   ./ops/fabro-run-status.sh <run_id> [--json]
#
# What it answers:
#   1. Is the run alive?          (last event vs now)
#   2. Where in the graph is it?  (most recent stage transition)
#   3. Is it making progress?     (last edge transition vs now)
#   4. Any signs of trouble?      (error, sandbox, pending human input)
#
# Exit codes:
#   0  healthy (running, events flowing, no failed nodes)
#   1  warning (stale events, stuck on a stage, or pending human input)
#   2  failed / not found / sandbox broken
#
# Requires: ssh access to the fabro host (see AGENTS.md), jq, curl, python3.
# The fabro dev token is read from the container — never hardcoded.

set -euo pipefail

RUN_ID="${1:?usage: fabro-run-status.sh <run_id> [--json]}"
JSON_OUT="${2:-}"

HOST="${FABRO_HOST:-andrew@10.10.0.32}"
API_PORT="${FABRO_API_PORT:-32276}"
STALE_MINUTES="${FABRO_STALE_MINUTES:-15}"     # no event for this long => stale
PROGRESS_MINUTES="${FABRO_PROGRESS_MINUTES:-10}" # no edge for this long => stuck

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die() { echo "ERROR: $*" >&2; exit 2; }

# Parse a "HH:MM:SS" timestamp from today's event log into epoch seconds.
# Handles the midnight wrap (timestamp in the future => yesterday).
ts_to_epoch() {
    python3 -c "
import datetime, sys
ts = datetime.datetime.strptime('''$1''', '%H:%M:%S')
today = datetime.datetime.now(datetime.timezone.utc)
ts = ts.replace(year=today.year, month=today.month, day=today.day,
                tzinfo=today.tzinfo)
if ts.timestamp() > today.timestamp() + 60:
    ts -= datetime.timedelta(days=1)
print(int(ts.timestamp()))
" 2>/dev/null || echo 0
}

# ---------------------------------------------------------------------------
# Fetch run record
# ---------------------------------------------------------------------------

TOK=$(ssh "$HOST" 'docker exec fabro-fabro-1 cat /storage/server.dev-token' 2>/dev/null) \
    || die "cannot read dev token from $HOST"

API="http://${HOST##*@}:${API_PORT}/api/v1"
RUN_JSON=$(curl -fsS -H "Authorization: Bearer $TOK" "$API/runs/$RUN_ID" 2>/dev/null) \
    || die "run $RUN_ID not found or API unreachable"

# ---------------------------------------------------------------------------
# Fetch event log (via container CLI)
# ---------------------------------------------------------------------------

EVENTS=$(ssh "$HOST" \
    "cd ~/fabro && docker compose exec -T fabro fabro events $RUN_ID -p" 2>/dev/null) \
    || die "cannot read event log for $RUN_ID"

# ---------------------------------------------------------------------------
# Parse fields
# ---------------------------------------------------------------------------

status=$(jq -r '.lifecycle.status.kind' <<<"$RUN_JSON")
error=$(jq -r '.lifecycle.error // empty' <<<"$RUN_JSON")
pending=$(jq -r '.lifecycle.pending_control // empty' <<<"$RUN_JSON")
last_event_at=$(jq -r '.timestamps.last_event_at // empty' <<<"$RUN_JSON")
wall_ms=$(jq -r '.timing.wall_time_ms // 0' <<<"$RUN_JSON")
sandbox_kind=$(jq -r '.sandbox.kind // "unknown"' <<<"$RUN_JSON")
wf_name=$(jq -r '.workflow.graph_name // .workflow.slug // "?"' <<<"$RUN_JSON")
automation=$(jq -r '.automation.id // "?"' <<<"$RUN_JSON")
repo=$(jq -r '.repository.name // "?"' <<<"$RUN_JSON")

now_epoch=$(python3 -c 'import time; print(int(time.time()))')

# last_event_at can have >6 fractional digits (e.g. ...53.497517997Z) which
# fromisoformat rejects; trim to microseconds.
event_age=$(python3 -c "
import re, datetime
raw = '''$last_event_at'''
m = re.match(r'(.*\.\d{6})\d*(Z|[+-]\d{2}:?\d{2})?$', raw)
cleaned = (m.group(1) + '+00:00') if m else raw.replace('Z', '+00:00')
last = datetime.datetime.fromisoformat(cleaned)
now = datetime.datetime.now(datetime.timezone.utc)
print(int((now - last).total_seconds()))
" 2>/dev/null || echo 0)

# Event-log stats
total_lines=$(wc -l <<<"$EVENTS" | tr -d ' ')
fail_markers=$(grep -c '✗' <<<"$EVENTS" || true)
# Compactions, attributed to the stage they fired in.
#
# A compaction is not a fault and costs little on its own: measured on run
# 01M2TFEHPYXXTQST6VPXREA854, one compaction took 5m16s out of an 8h29m run
# (1.0% of wall). It is worth surfacing for what it REVEALS, not what it costs.
#
# Fabro compacts at 80% of the model's context window. On an implementation
# stage that means the coder reached ~210k tokens and hundreds of turns on one
# task -- which is the earliest cheap evidence that `decompose` emitted a task
# too large to land in one stage. In that same run the compaction fired at
# 18:24, 2h29m into a `coder` stage that was then killed by its 180m wall clock
# at 18:55. So it is a sizing signal, not an in-run rescue: by the time it
# fires the stage is usually most of the way to its timeout.
#
# The banner-tracking awk is what makes the distinction possible -- a
# compaction during `decompose` or a review stage means a large issue or diff,
# which is a different (and mostly benign) thing. Anchor both patterns to the
# start of a line: agent prose mentioning "compaction" would otherwise inflate
# the count, which a bare `grep -c compaction` did.
compaction_lines=$(awk '
/^[0-9][0-9]:[0-9][0-9]:[0-9][0-9] ▶ / { line=$0; sub(/^[0-9:]+ ▶ /, "", line); stage=line; next }
/^[0-9][0-9]:[0-9][0-9]:[0-9][0-9][ \t]+compaction:/ {
    d=$0; sub(/^[0-9:]+[ \t]+compaction:[ \t]*/, "", d); print stage "\t" d
}' <<<"$EVENTS" || true)
compaction_count=$(grep -c . <<<"$compaction_lines" || true)
# `coder` is labelled "Implement task (tier 1)"; rework_t1..t4 are
# "Rework (tier N: <model>)". Those are the stages where a compaction means the
# task was oversized.
oversize_compactions=$(grep -cE '^(Implement task|Rework \(tier)' <<<"$compaction_lines" || true)
compaction_json='[]'
if [ "$compaction_count" -gt 0 ]; then
    compaction_json=$(jq -R -s \
        'split("\n") | map(select(length > 0) | split("\t") | {stage: .[0], detail: .[1]})' \
        <<<"$compaction_lines")
fi

# Stage transitions (edges): lines like "→ <node> <kind>"
transition_count=$(grep -c '→' <<<"$EVENTS" || true)
last_transitions=$(grep '→' <<<"$EVENTS" | tail -3 || true)
last_transition_line=$(grep '→' <<<"$EVENTS" | tail -1 || true)

# Current stage: the last "▶ <stage label>" banner
current_stage=$(grep '▶ ' <<<"$EVENTS" | tail -1 | sed 's/^[0-9:]* *//' || true)

# ---------------------------------------------------------------------------
# Task progress: read /tmp/fabro/tasks.json + task_index from the sandbox.
# task_index is incremented AFTER a task is selected, so completed = index-1.
# Follow-up tasks merged by extra_gate keep tasks.json authoritative.
# ---------------------------------------------------------------------------

SANDBOX_ID=$(jq -r '.sandbox.instance.runtime.id // empty' <<<"$RUN_JSON")
task_total=""
task_completed=""
current_task_id=""
current_task_title=""
task_pct=""
oversized_queued=0
current_task_covers=0
if [ -n "$SANDBOX_ID" ]; then
    # Lines 5 and 6 read `covers`, the per-task size declaration that
    # `decompose_gate` warns on. Reading it here is what makes the declaration
    # actionable while the run is still going: the compaction signal below only
    # fires ~2.5h into a 3h stage, whereas an oversized CURRENT task is visible
    # the moment that task is selected, before its coder has spent anything.
    # See docs/perf/04-compaction-and-task-sizing.md.
    TASK_SNAPSHOT=$(ssh "$HOST" \
        "docker exec $SANDBOX_ID sh -c '
            jq length /tmp/fabro/tasks.json 2>/dev/null || echo 0
            cat /tmp/fabro/task_index 2>/dev/null || echo 0
            jq -r \".id // empty\" /tmp/fabro/current_task.json 2>/dev/null
            jq -r \".title // empty\" /tmp/fabro/current_task.json 2>/dev/null
            jq \"[.[] | select((.covers | length) > 3)] | length\" /tmp/fabro/tasks.json 2>/dev/null || echo 0
            jq \".covers | length\" /tmp/fabro/current_task.json 2>/dev/null || echo 0
        '" 2>/dev/null || true)
    task_total=$(sed -n 1p <<<"$TASK_SNAPSHOT")
    task_index=$(sed -n 2p <<<"$TASK_SNAPSHOT")
    current_task_id=$(sed -n 3p <<<"$TASK_SNAPSHOT")
    current_task_title=$(sed -n 4p <<<"$TASK_SNAPSHOT")
    oversized_queued=$(sed -n 5p <<<"$TASK_SNAPSHOT")
    current_task_covers=$(sed -n 6p <<<"$TASK_SNAPSHOT")
    # A sandbox that has gone away, or a pre-`covers` run, yields blanks here.
    case "$oversized_queued" in ''|*[!0-9]*) oversized_queued=0 ;; esac
    case "$current_task_covers" in ''|*[!0-9]*) current_task_covers=0 ;; esac
    # task_index 0 = no task selected yet; N = task N of task_total is in flight
    if [ -n "$task_total" ] && [ "$task_total" -gt 0 ] 2>/dev/null; then
        task_completed=$(( task_index > 0 ? task_index - 1 : 0 ))
        task_pct=$(python3 -c "print(f'{$task_completed / $task_total * 100:.0f}')")
    else
        task_completed=""
    fi
fi

# Last tool/agent activity: lines starting with HH:MM:SS and 💬/⚙/✓/✗.
# Long LLM turns emit nothing for minutes — that is normal, not a stall.
last_activity_line=$(grep -E '^[0-9]{2}:[0-9]{2}:[0-9]{2}\s+(💬|⚙|✓|✗)' <<<"$EVENTS" | tail -1 || true)
if [ -n "$last_activity_line" ]; then
    last_activity_at=$(cut -d' ' -f1 <<<"$last_activity_line")
    last_activity_epoch=$(ts_to_epoch "$last_activity_at")
    activity_age=$(( now_epoch - last_activity_epoch ))
else
    # No activity lines at all (edge case: log is only banners) — use event age.
    activity_age=$event_age
fi

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

issues=()
verdict=0

if [ "$status" != "running" ]; then
    issues+=("run status is '$status' (not running)")
    verdict=2
fi

if [ -n "$error" ]; then
    issues+=("run error: $error")
    verdict=2
fi

if [ "$sandbox_kind" != "ready" ]; then
    issues+=("sandbox kind is '$sandbox_kind' (expected ready)")
    [ "$verdict" -lt 1 ] && verdict=1
fi

if [ "$event_age" -gt "$(( STALE_MINUTES * 60 ))" ]; then
    issues+=( "last event was ${event_age}s ago (threshold ${STALE_MINUTES}m)" )
    [ "$verdict" -lt 1 ] && verdict=1
fi

# Progress = time since the last graph edge transition. A long edge gap is
# only a problem when the agent is ALSO silent — a coder stage can
# legitimately run for an hour+ while its agent calls tools continuously.
edge_stalled=false
edge_desc=""
if [ -n "$last_transition_line" ]; then
    last_transition_at=$(cut -d' ' -f1 <<<"$last_transition_line")
    last_transition_epoch=$(ts_to_epoch "$last_transition_at")
    edge_age=$(( now_epoch - last_transition_epoch ))
    if [ "$edge_age" -gt "$(( PROGRESS_MINUTES * 60 ))" ]; then
        edge_stalled=true
        edge_desc=$(cut -d' ' -f2- <<<"$last_transition_line" | xargs)
    fi
else
    edge_age=0
fi

if [ "$edge_stalled" = true ] && [ "$activity_age" -gt "$(( STALE_MINUTES * 60 ))" ]; then
    issues+=( "stuck: no graph edge in $(( edge_age / 60 ))m and no tool activity in $(( activity_age / 60 ))m (stage: $current_stage)" )
    [ "$verdict" -lt 1 ] && verdict=1
fi

if [ -n "$pending" ]; then
    issues+=( "pending_control: $pending" )
    [ "$verdict" -lt 1 ] && verdict=1
fi

# Oversized task, declared. `covers` says how many distinct parent requirements
# a task carries; more than three has not yet landed in one coder stage on this
# host. This fires as soon as the task is SELECTED, which is the whole point --
# the compaction signal below only arrives once the stage is nearly out of time.
if [ "${current_task_covers:-0}" -gt 3 ]; then
    issues+=( "oversized task in flight: '$current_task_id' declares $current_task_covers requirements (>3) — improve should have split it" )
    [ "$verdict" -lt 1 ] && verdict=1
elif [ "${oversized_queued:-0}" -gt 0 ]; then
    issues+=( "$oversized_queued queued task(s) declare more than three requirements — expect a split or a long coder stage" )
    [ "$verdict" -lt 1 ] && verdict=1
fi

# Oversized task, observed. See the compaction block above for why this is a
# warning and not an error: the run is healthy, the decomposition was not.
# Reported as a warning so it shows up in `issues` without ever being mistaken
# for a stall -- the stage is still working, and it is normal for it to keep
# working.
if [ "$oversize_compactions" -gt 0 ]; then
    issues+=( "oversized task: $oversize_compactions compaction(s) on an implementation stage — decompose emitted a task too big for one coder stage" )
    while IFS=$'\t' read -r c_stage c_detail; do
        [ -n "$c_stage" ] || continue
        case "$c_stage" in
            "Implement task"*|"Rework (tier"*) issues+=( "  ↳ $c_stage: $c_detail" ) ;;
        esac
    done <<<"$compaction_lines"
    [ "$verdict" -lt 1 ] && verdict=1
fi

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

wall_hours=$(python3 -c "print(f'{$wall_ms / 3600000:.1f}')")

if [ "$JSON_OUT" = "--json" ]; then
    jq -n \
        --arg run_id "$RUN_ID" \
        --arg status "$status" \
        --arg workflow "$wf_name" \
        --arg automation "$automation" \
        --arg repo "$repo" \
        --arg sandbox "$sandbox_kind" \
        --arg last_event_age_s "$event_age" \
        --arg last_activity_age_s "$activity_age" \
        --arg last_edge_age_s "$edge_age" \
        --arg wall_hours "$wall_hours" \
        --arg current_stage "$current_stage" \
        --arg task_progress "${task_completed:--1}/${task_total:--1}" \
        --arg task_current_id "$current_task_id" \
        --arg task_current_title "$current_task_title" \
        --argjson task_current_covers "$current_task_covers" \
        --argjson task_oversized_queued "$oversized_queued" \
        --argjson transitions "$transition_count" \
        --argjson events "$total_lines" \
        --argjson failed_tools "$fail_markers" \
        --argjson compactions "$compaction_count" \
        --argjson oversize_compactions "$oversize_compactions" \
        --argjson compaction_detail "$compaction_json" \
        --argjson verdict "$verdict" \
        --argjson issues "$([ ${#issues[@]} -gt 0 ] && printf '%s\n' "${issues[@]}" | jq -R . | jq -s . || echo '[]')" \
        '{
            run_id: $run_id,
            status: $status,
            workflow: $workflow,
            automation: $automation,
            repo: $repo,
            sandbox: $sandbox,
            wall_hours: ($wall_hours | tonumber),
            last_event_age_s: ($last_event_age_s | tonumber),
            last_activity_age_s: ($last_activity_age_s | tonumber),
            last_edge_age_s: ($last_edge_age_s | tonumber),
            current_stage: $current_stage,
            tasks: (if ($task_progress | startswith("-1")) then null else {
                completed: ($task_progress | split("/")[0] | tonumber),
                total: ($task_progress | split("/")[1] | tonumber),
                pct: ($task_progress | split("/") | map(tonumber) | .[0] / .[1] * 100 | floor),
                current_id: $task_current_id,
                current_title: $task_current_title,
                current_covers: $task_current_covers,
                oversized_queued: $task_oversized_queued
            } end),
            totals: {
                transitions: $transitions,
                events: $events,
                failed_tools: $failed_tools,
                compactions: $compactions,
                oversize_compactions: $oversize_compactions
            },
            compactions: $compaction_detail,
            verdict: (if $verdict == 0 then "healthy" elif $verdict == 1 then "warning" else "critical" end),
            issues: $issues
        }'
    exit "$verdict"
fi

# Human-readable output
echo "Run:        $RUN_ID"
echo "Workflow:   $wf_name  ($automation)"
echo "Repo:       $repo"
echo "Status:     $status"
echo "Sandbox:    $sandbox_kind"
echo "Wall time:  ${wall_hours}h"
echo "Last event: ${event_age}s ago"
echo "Last tool:  ${activity_age}s ago"
echo "Stage:      $current_stage"
echo "Edges:      $transition_count total, last $(( edge_age / 60 ))m $(( edge_age % 60 ))s ago"
if [ "$edge_stalled" = true ]; then
    echo "            (long edge gap, but agent is active — long stage in progress)"
fi
if [ -n "$task_total" ]; then
    echo "Tasks:      $task_completed/$task_total done (${task_pct}%)"
    if [ -n "$current_task_id" ]; then
        echo "Current:    $current_task_id"
        echo "            $current_task_title"
        if [ "$current_task_covers" -gt 3 ]; then
            echo "            ! declares $current_task_covers requirements (>3) — oversized"
        elif [ "$current_task_covers" -gt 0 ]; then
            echo "            covers $current_task_covers requirement(s)"
        fi
    fi
    if [ "$oversized_queued" -gt 0 ]; then
        echo "            $oversized_queued queued task(s) declare more than three requirements"
    fi
fi
echo "Events:     $total_lines lines, $fail_markers failed tool calls, $compaction_count compaction(s)"
if [ "$compaction_count" -gt 0 ]; then
    while IFS=$'\t' read -r c_stage c_detail; do
        [ -n "$c_stage" ] || continue
        case "$c_stage" in
            "Implement task"*|"Rework (tier"*)
                echo "            ! $c_stage: $c_detail (oversized task)" ;;
            *)
                echo "              $c_stage: $c_detail" ;;
        esac
    done <<<"$compaction_lines"
fi
echo ""
echo "Recent transitions:"
echo "$last_transitions" | sed 's/^/  /'
echo ""

if [ "${#issues[@]}" -gt 0 ]; then
    echo "Issues:"
    printf '  ! %s\n' "${issues[@]}"
else
    echo "No issues detected."
fi

exit "$verdict"
