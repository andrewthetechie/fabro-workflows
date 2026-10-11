#!/bin/sh
# fabro-run-prune.sh — delete old merge-queue runs (ADR 0024 D8, P9).
#
# The merge queue creates one run per repository every 10 minutes, 576 a day. Nothing else
# deletes them, so the run store would grow without limit. This prunes only `merge-queue`
# runs, older than a week, through `fabro system prune`. It never names another workflow.
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
set -u

DRY_RUN="${DRY_RUN:-1}"
OLDER_THAN="${OLDER_THAN:-7d}"
FABRO_DIR="${FABRO_DIR:-$HOME/fabro}"
MAX_DELETE="${MAX_DELETE:-2000}"
API="${API:-http://127.0.0.1:32276/api/v1}"
CONTAINER="${CONTAINER:-fabro-fabro-1}"

cd "$FABRO_DIR" || { echo "prune: cannot cd to $FABRO_DIR" >&2; exit 1; }

help=$(docker compose exec -T fabro fabro system prune --help 2>&1) || {
  echo "prune: fabro system prune --help failed: $(printf '%s' "$help" | head -1)" >&2; exit 1; }

if printf '%s' "$help" | grep -q -- '--workflow'; then
  if [ "$DRY_RUN" = 1 ]; then
    docker compose exec -T fabro fabro system prune --workflow merge-queue --older-than "$OLDER_THAN"
  else
    docker compose exec -T fabro fabro system prune --workflow merge-queue --older-than "$OLDER_THAN" --yes
  fi
  exit $?
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
