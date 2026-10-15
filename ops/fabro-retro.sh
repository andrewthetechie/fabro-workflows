#!/bin/sh
# fabro-retro.sh — the weekly factory retro, posted to Discord (issue #33, ADR 0026).
#
# This runs ON THE HOST (cron, every Monday at 12:00 UTC) and does two things: it runs
# the retro subcommand inside the scheduler container, and it posts the subcommand's
# stdout to the Discord webhook that only the fabro container holds. It never alerts
# on a single run FAILURE: that stays hook-owned (ADR 0004). It reports **classes** of
# failure, with counts across many runs, once a week — the scorecard's numbers are
# what a human would otherwise have to read by hand.
#
# A dry run still POSTS. That is the point: the operator reads the first message in
# Discord, then adds `DRY_RUN=0` to the cron line to let it file issues. Only the
# subcommand's GitHub writes are skipped on a dry run, never the Discord message.
#
# Env:
#   DRY_RUN=1                     anything but 0 passes --dry-run to the subcommand
#   NO_POST=0                     1 prints the message and posts nothing (tests, by hand)
#   SCHEDULER_CONTAINER=fabro-scheduler   where the subcommand runs
#   CONTAINER=fabro-fabro-1       where the Discord webhook is read
#   RETRO_ARGS=""                 extra subcommand arguments, word-split, by-hand runs
#
# Crontab (installed by the operator, task 07; `make deploy` never touches crontab):
#   0 12 * * 1 /home/andrew/bin/fabro-retro.sh >> /home/andrew/.local/state/fabro-retro.log 2>&1
# Going live adds `DRY_RUN=0 ` before the script:
#   0 12 * * 1 DRY_RUN=0 /home/andrew/bin/fabro-retro.sh >> /home/andrew/.local/state/fabro-retro.log 2>&1
#
# The subcommand's stderr passes through to this script's stderr, which cron appends
# to the log. stdout is captured: it is the Discord message, and nothing else.

DRY_RUN="${DRY_RUN:-1}"
NO_POST="${NO_POST:-0}"
SCHEDULER_CONTAINER="${SCHEDULER_CONTAINER:-fabro-scheduler}"
CONTAINER="${CONTAINER:-fabro-fabro-1}"
RETRO_ARGS="${RETRO_ARGS:-}"

warn() { printf 'WARNING: %s\n' "$*" >&2; }

# Build the subcommand's arguments. Anything but 0 means a dry run.
set -- retro --last-week
[ "$DRY_RUN" = "0" ] || set -- "$@" --dry-run
# shellcheck disable=SC2086  # word-split on purpose: RETRO_ARGS is for by-hand runs
set -- "$@" $RETRO_ARGS

out="$(docker exec "$SCHEDULER_CONTAINER" python -m fabro_scheduler "$@")"
rc=$?

if [ "$rc" -eq 0 ]; then
  content="$out"
else
  content="retro failed: exit $rc; see ~/.local/state/fabro-retro.log"
  [ -n "$out" ] && content="$content
$out"
fi

if [ "$NO_POST" = "1" ]; then
  printf '%s\n' "$content"
  exit "$rc"
fi

WEBHOOK="$(docker exec "$CONTAINER" cat /storage/secrets/discord_webhook_url 2>/dev/null \
  | tr -d '[:space:]')"
if [ -z "$WEBHOOK" ]; then
  warn "cannot read the Discord webhook from $CONTAINER; retro dropped: $content"
  exit 1
fi

# Truncate in jq, which counts code points: `head -c` counts bytes and would cut the
# message's `·` and `…` in half.
if ! jq -n --arg content "$content" '{content: ($content[:2000])}' \
  | curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d @- "$WEBHOOK" \
    >/dev/null 2>&1; then
  warn "Discord POST failed; retro dropped: $content"
  exit 1
fi

printf '%s\n' "$content"
exit "$rc"
