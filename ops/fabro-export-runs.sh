#!/usr/bin/env bash
# fabro-export-runs.sh — list a window of fabro runs and export each run's events (#14, ADR 0020).
#
# Usage:
#   ops/fabro-export-runs.sh --since ISO [--until ISO] [--workflow NAME|all] [--status terminal|all] [OUTDIR]
#
# --since is inclusive and --until exclusive, by creation time. A bound given to the minute
# (2026-10-10T00:20Z) covers that whole minute, so --until 00:20Z keeps a run created at 00:20:57.
#
# Writes OUTDIR/<id>.jsonl (`fabro events <id> --json`), OUTDIR/<id>.err for a run that
# failed, and OUTDIR/index.tsv, which is also printed. A run whose .jsonl exists and is not
# empty is kept, so a rerun resumes. The exit is 1 when any run failed.
#
# Runs on the Mac. The token is read on the host, inside the remote command, and never
# printed here. Event files hold issue text: OUTDIR must be outside every git work tree.
#
# Requires: ssh, jq. Env: HOST (default andrew@10.10.0.32), SSH (default ssh; tests stub it),
#           FABRO_API_PORT (default 32276).

set -euo pipefail

HOST="${HOST:-andrew@10.10.0.32}"
SSH="${SSH:-ssh}"
API_PORT="${FABRO_API_PORT:-32276}"
PAGE=100

die() { echo "ERROR: $*" >&2; exit 2; }

usage() {
    sed -n '2,/^$/s/^# \{0,1\}//p' "$0"
    exit 2
}

# ISO timestamp to YYYY-MM-DDTHH:MM:SS, the width of the API's created_at prefix.
norm_ts() {
    [[ "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2} ]] || die "not an ISO timestamp: $1"
    local s="${1%Z}"
    [[ ${#s} -eq 16 ]] && s="$s:00"
    echo "${s:0:19}"
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

SINCE="" UNTIL="" WORKFLOW="backlog" STATUS="terminal" OUTDIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --since) SINCE="${2:?--since needs a value}"; shift 2 ;;
        --until) UNTIL="${2:?--until needs a value}"; shift 2 ;;
        --workflow) WORKFLOW="${2:?--workflow needs a value}"; shift 2 ;;
        --status) STATUS="${2:?--status needs a value}"; shift 2 ;;
        -h|--help) usage ;;
        -*) die "unknown option: $1" ;;
        *) [[ -z "$OUTDIR" ]] || die "one OUTDIR only"; OUTDIR="$1"; shift ;;
    esac
done

[[ -n "$SINCE" ]] || die "--since ISO is required"
[[ "$STATUS" == terminal || "$STATUS" == all ]] || die "--status is terminal or all"
SINCE_KEY=$(norm_ts "$SINCE")
# --until is exclusive. A bound given to the minute (2026-10-10T00:20Z) covers that whole
# minute, so it ends before 00:21:00 and keeps a run created at 00:20:57.
UNTIL_KEY="" UNTIL_MINUTE=false
if [[ -n "$UNTIL" ]]; then
    UNTIL_KEY=$(norm_ts "$UNTIL")
    [[ "$UNTIL" =~ T[0-9]{2}:[0-9]{2}Z?$ ]] && UNTIL_MINUTE=true
fi
[[ -n "$OUTDIR" ]] || OUTDIR="${TMPDIR:-/tmp}/fabro-runs-$(echo "$SINCE" | tr -cd '0-9')"

command -v jq >/dev/null || die "jq is required"

# Refuse an OUTDIR inside a git work tree before anything is written. The nearest existing
# ancestor decides, because OUTDIR may not exist yet.
probe="$OUTDIR"
while [[ ! -d "$probe" ]]; do probe=$(dirname "$probe"); done
if git -C "$probe" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    die "OUTDIR is inside a git work tree (event files hold issue text): $OUTDIR"
fi
mkdir -p "$OUTDIR"

# ---------------------------------------------------------------------------
# Listing: every page of GET /runs, newest first, stopped once a page reaches --since
# ---------------------------------------------------------------------------

# The token is read on the host. The API is on the host's loopback.
remote_page() {
    printf '%s' "TOK=\$(docker exec fabro-fabro-1 cat /storage/server.dev-token) && curl -fsSg -H \"Authorization: Bearer \$TOK\" \"http://127.0.0.1:${API_PORT}/api/v1/runs?page[limit]=${PAGE}&page[offset]=$1\""
}

list_runs() {
    local offset=0 page more oldest
    while :; do
        page=$("$SSH" "$HOST" "$(remote_page "$offset")" </dev/null) || die "cannot list runs from $HOST"
        printf '%s' "$page" | jq -e '.meta' >/dev/null 2>&1 || die "unexpected runs listing from $HOST"
        printf '%s' "$page" | jq -c '.data[]'
        more=$(printf '%s' "$page" | jq -r '.meta.has_more')
        oldest=$(printf '%s' "$page" | jq -r '(.data[-1].timestamps.created_at // "")[0:19]')
        [[ "$more" == true ]] || break
        [[ -n "$oldest" && "$oldest" < "$SINCE_KEY" ]] && break
        offset=$((offset + PAGE))
    done
}

# One TSV row per selected run, oldest first.
index_rows() {
    list_runs | jq -s -r \
        --arg since "$SINCE_KEY" --arg until "$UNTIL_KEY" --argjson minute "$UNTIL_MINUTE" \
        --arg wf "$WORKFLOW" --arg status "$STATUS" '
        map(select(.timestamps.created_at[0:19] >= $since))
        | map(select($until == "" or (if $minute then .timestamps.created_at[0:16] <= $until
                                      else .timestamps.created_at[0:19] < $until end)))
        | map(select($wf == "all" or .workflow.slug == $wf))
        | map(select($status == "all" or .timestamps.completed_at != null))
        | sort_by(.timestamps.created_at)
        | .[]
        | [ .id, .timestamps.created_at, .workflow.slug, .repository.name, .lifecycle.status.kind,
            (.labels.issue // .labels.pr // "-"),
            (.labels.workflow_sha // .automation.workflow_source.resolved_sha // "-") ]
        | @tsv'
}

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

export_run() {
    local id="$1" out="$OUTDIR/$1"
    [[ "$id" =~ ^[0-9A-Z]+$ ]] || { echo "bad run id: $id" >"$out.err"; return 1; }
    if [[ -s "$out.jsonl" ]]; then
        return 0
    fi
    local part="$out.jsonl.part" rc=0
    "$SSH" "$HOST" "cd ~/fabro && docker compose exec -T fabro fabro events $id --json </dev/null" \
        </dev/null >"$part" 2>"$out.err" || rc=$?
    if [[ "$rc" -eq 0 && -s "$part" ]]; then
        mv "$part" "$out.jsonl"
        rm -f "$out.err"
        return 0
    fi
    rm -f "$part"
    [[ -s "$out.err" ]] || echo "fabro events produced no output (exit $rc)" >"$out.err"
    return 1
}

ROWS=$(index_rows)
failed=0
while IFS=$'\t' read -r id _; do
    [[ -n "$id" ]] || continue
    export_run "$id" || { echo "failed: $id (see $OUTDIR/$id.err)" >&2; failed=$((failed + 1)); }
done <<<"$ROWS"

# The index lists the window whether or not every export succeeded.
{
    printf 'id\tcreated\tworkflow\trepo\tstatus\tissue\tworkflow_sha\n'
    printf '%s\n' "$ROWS" | sed '/^$/d'
} >"$OUTDIR/index.tsv.part"
mv "$OUTDIR/index.tsv.part" "$OUTDIR/index.tsv"
cat "$OUTDIR/index.tsv"

if [[ "$failed" -gt 0 ]]; then
    echo "$failed run(s) failed to export" >&2
    exit 1
fi
