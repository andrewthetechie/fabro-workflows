#!/bin/sh
# fabro-run-prune.sh — archive finished merge-queue runs, delete old ones (ADR 0024 D8, P9).
#
# The merge queue creates one run per repository every 20 minutes, 288 a day. Nothing else
# deletes them, so the run store would grow without limit. This prunes only `merge-queue`
# runs, older than a week, through `fabro system prune`. It never names another workflow.
#
# Archive first (2026-10-15): the fabro UI pages through the whole default run listing on
# every load, about 73 requests at 1,864 sweeps, so a week of them made it take seconds.
# Every `succeeded` merge-queue run is archived (`fabro archive`, which hides it from that
# listing; `fabro unarchive` restores it). A failed or live sweep stays visible. `system
# prune` still deletes archived runs at the cutoff. A failed archive does not stop the
# prune, but fails the pass.
# `--workflow` is a substring match (spike S5): no other workflow's name contains
# "merge-queue". `fabro system prune` is a dry run without --yes.
#
# Fallback: if `--workflow` is ever missing from the fabro CLI, the script lists runs
# through GET /runs and deletes each TERMINAL merge-queue run older than the cutoff with
# DELETE /runs/{id}, at most MAX_DELETE per pass. A non-terminal run is never deleted.
#
# Env:
#   DRY_RUN=1          print what would be deleted (default). The cron passes DRY_RUN=0.
#   OLDER_THAN=7d      the age cutoff
#   FABRO_DIR=~/fabro  where docker-compose.yaml lives
#   MAX_DELETE=2000    cap per pass in fallback mode
#   ARCHIVE_BATCH=100  run ids per `fabro archive` call
set -u

DRY_RUN="${DRY_RUN:-1}"
OLDER_THAN="${OLDER_THAN:-7d}"
FABRO_DIR="${FABRO_DIR:-$HOME/fabro}"
MAX_DELETE="${MAX_DELETE:-2000}"
API="${API:-http://127.0.0.1:32276/api/v1}"
CONTAINER="${CONTAINER:-fabro-fabro-1}"
ARCHIVE_BATCH="${ARCHIVE_BATCH:-100}"

cd "$FABRO_DIR" || { echo "prune: cannot cd to $FABRO_DIR" >&2; exit 1; }

# archive_succeeded: list every page of the default listing first, then archive, so the
# offsets do not shift under the walk. Returns 1 on any failure.
archive_succeeded() {
  a_tok=$(docker exec "$CONTAINER" cat /storage/server.dev-token 2>/dev/null | tr -d '[:space:]')
  [ -n "$a_tok" ] || { echo "archive: cannot read the dev token" >&2; return 1; }
  a_ids="$(mktemp)" || { echo "archive: cannot create a temporary file" >&2; return 1; }
  a_off=0
  while :; do
    a_page=$(curl -fsSg -m 20 -H "Authorization: Bearer $a_tok" "$API/runs?page[limit]=100&page[offset]=$a_off") \
      || { echo "archive: list failed" >&2; rm -f "$a_ids"; return 1; }
    a_n=$(printf '%s' "$a_page" | jq -e '.data | length' 2>/dev/null) \
      || { echo "archive: unreadable run listing" >&2; rm -f "$a_ids"; return 1; }
    [ "$a_n" -gt 0 ] || break
    printf '%s' "$a_page" | jq -r '.data[] | select(.workflow.slug == "merge-queue")
      | select(.lifecycle.status.kind == "succeeded") | .id' >> "$a_ids"
    [ "$(printf '%s' "$a_page" | jq -r '.meta.has_more // false')" = true ] || break
    a_off=$((a_off + 100))
  done
  a_total=$(wc -l < "$a_ids" | tr -d ' ')
  if [ "$DRY_RUN" = 1 ]; then
    echo "archive: would archive $a_total succeeded merge-queue run(s)"
    rm -f "$a_ids"; return 0
  fi
  a_rc=0
  xargs -n "$ARCHIVE_BATCH" sh -c 'docker compose exec -T fabro fabro archive --quiet "$@" </dev/null >/dev/null' _ < "$a_ids" \
    || { echo "archive: a fabro archive call failed" >&2; a_rc=1; }
  echo "archive: archived $a_total succeeded merge-queue run(s)"
  rm -f "$a_ids"
  return "$a_rc"
}

archive_rc=0
archive_succeeded || archive_rc=1

help=$(docker compose exec -T fabro fabro system prune --help 2>&1) || {
  echo "prune: fabro system prune --help failed: $(printf '%s' "$help" | head -1)" >&2; exit 1; }

if printf '%s' "$help" | grep -q -- '--workflow'; then
  if [ "$DRY_RUN" = 1 ]; then
    docker compose exec -T fabro fabro system prune --workflow merge-queue --older-than "$OLDER_THAN"
  else
    docker compose exec -T fabro fabro system prune --workflow merge-queue --older-than "$OLDER_THAN" --yes
  fi
  prune_rc=$?
  [ "$prune_rc" -eq 0 ] || exit "$prune_rc"
  exit "$archive_rc"
fi

# Fallback: no --workflow flag.
case "$OLDER_THAN" in
  *d) secs=$(( ${OLDER_THAN%d} * 86400 )) ;;
  *h) secs=$(( ${OLDER_THAN%h} * 3600 )) ;;
  *) echo "prune: OLDER_THAN must end in d or h" >&2; exit 1 ;;
esac
cutoff=$(( $(date +%s) - secs ))
tok=$(docker exec "$CONTAINER" cat /storage/server.dev-token 2>/dev/null | tr -d '[:space:]')
[ -n "$tok" ] || { echo "prune: cannot read the dev token" >&2; exit 1; }
deleted=0; off=0
while [ "$deleted" -lt "$MAX_DELETE" ]; do
  page=$(curl -fsSg -m 20 -H "Authorization: Bearer $tok" "$API/runs?page[limit]=100&page[offset]=$off") || { echo "prune: list failed" >&2; exit 1; }
  n=$(printf '%s' "$page" | jq '.data | length')
  [ "$n" -gt 0 ] || break
  ids=$(printf '%s' "$page" | jq -r --argjson c "$cutoff" '
    .data[] | select(.workflow.slug == "merge-queue")
    | select(.lifecycle.status.kind | IN("succeeded","failed","cancelled","errored"))
    | select((.timestamps.created_at | sub("\\.[0-9]+Z$";"Z") | fromdateiso8601) < $c) | .id')
  for id in $ids; do
    [ "$deleted" -lt "$MAX_DELETE" ] || break
    if [ "$DRY_RUN" = 1 ]; then echo "would delete $id"
    else curl -fsSg -m 20 -X DELETE -H "Authorization: Bearer $tok" "$API/runs/$id" >/dev/null || echo "prune: DELETE $id failed" >&2
    fi
    deleted=$((deleted + 1))
  done
  off=$((off + 100))
  [ "$off" -lt 5000 ] || break
done
echo "prune: fallback pass handled $deleted run(s) (DRY_RUN=$DRY_RUN)"
exit "$archive_rc"
