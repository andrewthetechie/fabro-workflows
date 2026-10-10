#!/bin/sh
# Stands in for ssh in ops/tests/test_fabro_export_runs.py. $1 is the host, $2 the remote command.
# Serves page-<offset>.json for a listing and events/<id>.jsonl for `fabro events`. Every remote
# command is logged to $EXPORT_STUB_LOG when that is set, so a test can count the calls.
dir="$(dirname "$0")"
[ -n "$EXPORT_STUB_LOG" ] && printf '%s\n' "$2" >>"$EXPORT_STUB_LOG"
case "$2" in
    *"fabro events "*)
        id=$(printf '%s' "$2" | sed -n 's/.*fabro events \([^ ]*\) --json.*/\1/p')
        if [ -f "$dir/events/$id.jsonl" ]; then cat "$dir/events/$id.jsonl"; else echo "no run $id" >&2; exit 3; fi ;;
    *"page[offset]="*)
        off=$(printf '%s' "$2" | sed -n 's/.*page\[offset\]=\([0-9]*\).*/\1/p')
        if [ -f "$dir/page-$off.json" ]; then cat "$dir/page-$off.json"; else echo "no page $off" >&2; exit 1; fi ;;
    *) echo "unknown remote command" >&2; exit 9 ;;
esac
