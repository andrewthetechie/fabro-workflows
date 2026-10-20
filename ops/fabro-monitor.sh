#!/bin/sh
# fabro-monitor.sh — out-of-band health monitor for the fabro host.
#
# This runs ON THE HOST (cron), unlike the in-band hook notifications
# (discord-notify.sh): those fire from inside a run, so anything that prevents
# a run from starting or finishing never reaches them. This script watches
# exactly that gap — dead container, dead API, dead scheduler, a scheduler that
# answers but never dispatches, stuck run, empty work queue, full disk. It
# never alerts on a run FAILURE: that stays hook-owned, because double pings
# train the operator to ignore the channel (ADR 0004). See
# docs/turn-it-on/00-overview-and-contracts.md.
#
# Conditions (thresholds are env knobs so a shakedown can force each one):
#   C1 container-down   container not running, or healthcheck not healthy   🔴 4h
#   C2 api-unreachable  system/info fails, times out (10s), or 401s         🔴 4h
#   C3 dead-scheduler   any schedule enabled AND newest run > IDLE_HOURS old 🔴 4h
#                       (arch-review schedules excluded, ADR 0012; merge-queue runs
#                       are skipped when finding the newest run, ADR 0024; quiet
#                       while every undrained coder pool holds a live run)
#   C4 stuck-run        non-terminal run older than STUCK_HOURS             🔴 4h
#                       (MQ_STUCK_MINUTES for a merge-queue run)
#                       (ARCH_STUCK_HOURS for an arch-review run)
#   C5 starvation       zero open agent-labeled issues across the repos     🟡 7d
#   C6 disk-pressure    df / use >= DISK_PCT                                🟠 24h
#   C7 scheduler-down   GET SCHEDULER_URL/health does not answer 200 in 5s   🔴 4h
#   C8 scheduler-wedged queue non-empty AND every undrained coder pool idle
#                       (at least one undrained), held for > WEDGE_MINUTES   🔴 4h
#
# C1 suppresses C2's connection failures (report the cause, not the symptom)
# but never its 401 — a rejected token is its own problem. When the container
# is down, C3/C4 are unevaluable (no API, no token): their stamps are left
# untouched so a resolved message is not fabricated. C3 self-activates: it
# gates on "any automation has an enabled schedule", read live, so the alarm
# exists the moment task 06 turns schedules on, with no config change here.
#
# C7 suppresses C8 — an unreachable scheduler cannot answer "queued work, idle
# boxes", so C8's stamp is left untouched and the cause (C7) is reported alone.
# Both are independent of C1: the scheduler is a SEPARATE container, so a fabro
# outage does not clear a wedged scheduler and a scheduler outage does not clear
# a dead fabro. They are evaluated even while C1 fires or the fabro API is
# unreachable. C8 is the post-cutover dead-scheduler alarm: once draft 13
# disables the four backlog schedules, C3 goes inert ("no enabled schedules")
# and a scheduler that answers but never dispatches is caught here instead.
#
# Probed against the live server 2026-09-16 (fabro 0.354.0-nightly.0); the
# parser depends only on what the probes proved:
#   GET /api/v1/runs      -> {data: [...], meta: {has_more, total}}. The bare
#                            default page is 20 rows and `?limit=` is ignored;
#                            `page[limit]` (clamped at 100) and `page[offset]` page
#                            it (curl needs -g), and `status=` filters by board
#                            column. Rows are newest-first by timestamps.created_at.
#                            The merge queue (ADR 0024) adds 288 runs a day, so no
#                            fixed newest page is safe: C3 pages up to 5x100 rows and
#                            skips `merge-queue` rows, and C4 reads every non-terminal
#                            run by status filter (the columns list_active_runs uses),
#                            up to 10x100, with its own MQ_STUCK_MINUTES limit. The old
#                            accepted gap (a stuck run falling off page 1) is closed.
#   run rows              -> carry timestamps.created_at (RFC3339, fractional
#                            seconds) and lifecycle.status.kind. Statuses
#                            observed: succeeded, failed. Terminal set is
#                            assumed {succeeded, failed, cancelled, errored};
#                            anything else — including a missing kind, which
#                            is what a never-executing `submitted` run looks
#                            like — counts as non-terminal for C4.
#   timestamp source      -> timestamps.created_at via `date -d` (GNU date on
#                            the Ubuntu host). Fallback: the run id is a ULID
#                            whose first 10 Crockford-base32 chars ARE the
#                            epoch milliseconds (verified: decode matched
#                            date -d to the second on three live rows):
#                              v=0; for ch in id[:10]: v = v*32 + index(ch);
#                              epoch = v // 1000
#   GET /api/v1/system/info -> BARE json (no data envelope), .runs.total.
#                            A garbage token returns 401 with body
#                            {"errors":[{"code":"access_token_invalid",...}]}
#                            — distinguishable from a connection refusal by
#                            both the status and the body.
#   GET /api/v1/automations -> {data: [{triggers: [{type,id,enabled,...}]}]}.
#                            An enabled schedule is a trigger with
#                            type=="schedule" and enabled==true.
#   docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' fabro-fabro-1
#                            -> "running healthy". A container with no
#                            healthcheck prints "<no value>" for health; that
#                            is not a C1 trigger on its own (status-only).
#
# Secret material is READ, never stored: the dev token and the Discord webhook
# are pulled out of the container with docker exec, exactly the sources
# discord-notify.sh uses from inside. Nothing is written to disk.
#
# Failure discipline: a failed Discord POST is logged (cron appends stderr to
# the log) and never fatal — alerting must not wedge cron. The script exits 0
# when every evaluable condition was evaluated, non-zero only when evaluation
# itself broke (docker/gh missing, token unreadable while the container is up,
# API body unparseable). That non-zero exit is what keys the heartbeat /fail.
# Every run also prints one `ok`/`skip` line per condition to stdout — the cron
# log shows a healthy evaluation at a glance, and a silent log means a dead
# monitor, not a healthy host.
#
# Env:
#   DRY_RUN=1                     print what would send, send nothing, write
#                                 no state (default, like the sweepers)
#   IDLE_HOURS=2                  C3: newest run older than this with a
#                                 schedule enabled  -> dead scheduler, unless
#                                 every undrained coder pool is busy
#   STUCK_HOURS=3                 C4: non-terminal run older than this
#   MQ_STUCK_MINUTES=30           C4: the limit for a merge-queue sweep, which ends in
#                                 seconds (ADR 0024 D8)
#   ARCH_STUCK_HOURS=9            C4: the same limit for an arch-review run, which
#                                 triages up to 18 issues and stops starting new
#                                 ones at 6h (ADR 0012)
#   DISK_PCT=85                   C6: df / use at or above this
#   FABRO_HOST=10.10.0.32
#   FABRO_PORT=32276
#   FABRO_CONTAINER=fabro-fabro-1
#   SCHEDULER_URL=http://127.0.0.1:32280  C7/C8: the scheduler's HTTP surface
#                                 (LAN-only, unauthenticated) — /health,
#                                 /api/queue, /api/pools
#   WEDGE_MINUTES=20              C8: fire only after queued work with every
#                                 undrained coder pool idle for this long; 0 fires on
#                                 the first qualifying sample (the forced test)
#   FABRO_MONITOR_REPOS="a/b c/d"  space-separated; an EMPTY value falls back
#                                 to the four factory repos (`:-`), so pass a
#                                 real value to narrow a run
#   FABRO_HEARTBEAT_URL=          dead-man's ping, GETed at the end of every
#                                 run; evaluation failure GETs <url>/fail
#                                 instead (task 03). Unset: no ping, no error.
#   FABRO_MONITOR_STATE=          state file override (tests); default
#                                 ~/.local/state/fabro-monitor.state
set -u

DRY_RUN="${DRY_RUN:-1}"
IDLE_HOURS="${IDLE_HOURS:-2}"
STUCK_HOURS="${STUCK_HOURS:-3}"
ARCH_STUCK_HOURS="${ARCH_STUCK_HOURS:-9}"
MQ_STUCK_MINUTES="${MQ_STUCK_MINUTES:-30}"
DISK_PCT="${DISK_PCT:-85}"
FABRO_HOST="${FABRO_HOST:-10.10.0.32}"
FABRO_PORT="${FABRO_PORT:-32276}"
CONTAINER="${FABRO_CONTAINER:-fabro-fabro-1}"
SCHEDULER_URL="${SCHEDULER_URL:-http://127.0.0.1:32280}"
WEDGE_MINUTES="${WEDGE_MINUTES:-20}"
REPOS="${FABRO_MONITOR_REPOS:-andrewthetechie/jelly-swipe andrewthetechie/womens-fantasy-sports andrewthetechie/lawncare-saas andrewthetechie/writers-app}"
STATE_FILE="${FABRO_MONITOR_STATE:-$HOME/.local/state/fabro-monitor.state}"
API="http://$FABRO_HOST:$FABRO_PORT/api/v1"

# Re-alert intervals (seconds) per the condition table.
ALERT_SECONDS="${FABRO_MONITOR_RE_ALERT_SECONDS:-14400}"            # 4h — C1..C4
STARVE_SECONDS="${FABRO_MONITOR_STARVE_RE_ALERT_SECONDS:-604800}"   # 7d — C5
DISK_RE_SECONDS="${FABRO_MONITOR_DISK_RE_ALERT_SECONDS:-86400}"     # 24h — C6

# Lifecycle.status.kind values that mean "this run is done". Anything else —
# running, submitted, queued, a missing kind — is non-terminal for C4.
TERMINAL=" succeeded failed cancelled errored "

now=$(date +%s)

have() { command -v "$1" >/dev/null 2>&1; }
warn() { printf 'WARN  %s\n' "$1" >&2; }

tmp="$(mktemp -d)" || { printf '%s\n' "could not create a temporary directory" >&2; exit 1; }
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

: > "$tmp/firing"     # lines: <cond>\t<re-alert secs>\t<emoji>\t<detail>
: > "$tmp/evaluated"  # condition ids that produced a definitive answer this run
eval_failed=0

# ------------------------------------------------------------------ plumbing ----

cond_name() {
  case "$1" in
    C1) printf '%s' "container-down" ;;
    C2) printf '%s' "api-unreachable" ;;
    C3) printf '%s' "dead-scheduler" ;;
    C4) printf '%s' "stuck-run" ;;
    C5) printf '%s' "starvation" ;;
    C6) printf '%s' "disk-pressure" ;;
    C7) printf '%s' "scheduler-down" ;;
    C8) printf '%s' "scheduler-wedged" ;;
    *) printf '%s' "$1" ;;
  esac
}

fire() { # $1=cond $2=re-alert secs $3=emoji $4=detail
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$tmp/firing"
}

mark_evaluated() {
  printf '%s\n' "$1" >> "$tmp/evaluated"
}

stamp_of() { # $1=cond -> epoch or empty
  [ -f "$STATE_FILE" ] || return 0
  grep -E "^$1=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2
}

ulid_epoch() { # decode a ULID run id to epoch seconds (see header for the math)
  python3 - "$1" <<'PY'
import sys
alpha = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
try:
    v = 0
    for ch in sys.argv[1].strip().upper()[:10]:
        v = v * 32 + alpha.index(ch)
    print(v // 1000)
except Exception:
    sys.exit(1)
PY
}

run_epoch() { # $1=created_at $2=run id -> epoch, or empty when neither parses
  if [ -n "$1" ]; then
    e="$(date -d "$1" +%s 2>/dev/null)" && [ -n "$e" ] && { printf '%s' "$e"; return 0; }
  fi
  ulid_epoch "$2" 2>/dev/null || printf ''
}

# Discord. The webhook is read lazily and cached for the run; a read or POST
# failure is logged and never fatal — alerting must not wedge cron.
WEBHOOK=""
webhook_tried=0
load_webhook() {
  [ "$webhook_tried" = 1 ] && return 0
  webhook_tried=1
  WEBHOOK="$(docker exec "$CONTAINER" cat /storage/secrets/discord_webhook_url 2>/dev/null | tr -d '[:space:]')"
  [ -n "$WEBHOOK" ]
}

send() { # $1 = full message text
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY   would send: %s\n' "$1"
    return 0
  fi
  load_webhook || { warn "cannot read the Discord webhook from $CONTAINER; alert dropped: $1"; return 1; }
  jq -n --arg content "$1" '{content: $content}' \
    | curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d @- "$WEBHOOK" \
      >/dev/null 2>&1 \
    || { warn "Discord POST failed; alert dropped: $1"; return 1; }
  return 0
}

# ------------------------------------------------------------ C1 container ----

mark_evaluated C1
container_status=""
container_health=""
if have docker; then
  info="$(docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' "$CONTAINER" 2>/dev/null)" || info=""
  if [ -n "$info" ]; then
    container_status="${info%% *}"
    container_health="${info##* }"
  fi
else
  warn "docker is not on PATH — C1 unevaluable (this script runs on the host)"
  eval_failed=1
fi

container_suppresses=0
if have docker && [ "$eval_failed" = 0 ]; then
  if [ -z "$container_status" ]; then
    if docker info >/dev/null 2>&1; then
      # daemon answers and the container is simply gone
      container_suppresses=1
      fire C1 "$ALERT_SECONDS" "🔴" "container-down — $CONTAINER does not exist"
    else
      warn "the docker daemon is unreachable — C1 unevaluable"
      eval_failed=1
    fi
  elif [ "$container_status" != "running" ]; then
    container_suppresses=1
    fire C1 "$ALERT_SECONDS" "🔴" \
      "container-down — $CONTAINER status is '$container_status' (health: $container_health); expected running/healthy"
  elif [ "$container_health" != "healthy" ] && [ "$container_health" != "<no value>" ]; then
    # Unhealthy while still running: C1 fires, but the API may well answer —
    # the suppression rationale ("the API is down by construction") holds only
    # for a container that is not running, so C2-C4 are still evaluated below.
    fire C1 "$ALERT_SECONDS" "🔴" \
      "container-down — $CONTAINER is running but health is '$container_health'; expected healthy"
  else
    printf 'ok    C1 container: %s/%s\n' "$container_status" "$container_health"
  fi
fi

# ------------------------------------------- C2/C3/C4 (need the API + token) ----
# A container that is NOT RUNNING makes the API unreachable BY CONSTRUCTION,
# so C2's connection failure would be the symptom of C1's cause: skip all
# three and leave their stamps untouched. (Unhealthy-but-running does NOT
# suppress — the API may well answer; see C1 above.) A 401, when it does
# happen with the container up, is C2 firing on its own — the token rotated
# and the monitor's read of it is stale.

token=""
api_ok=0
if [ "$container_suppresses" = 0 ] && [ "$eval_failed" = 0 ]; then
  token="$(docker exec "$CONTAINER" cat /storage/server.dev-token 2>/dev/null | tr -d '[:space:]')"
  if [ -z "$token" ]; then
    # Container is up but the token read failed: the monitor itself is broken.
    # This is evaluation failure (see header), not an alert condition.
    warn "could not read the dev token from $CONTAINER — C2/C3/C4 unevaluable"
    eval_failed=1
  else
    api_ok=1
  fi
elif [ "$container_suppresses" = 1 ]; then
  printf 'skip  C2-C4: C1 container-down is firing (API unreachable by construction)\n'
fi

if [ "$api_ok" = 1 ]; then
  # ---- C2: api-unreachable ------------------------------------------------
  mark_evaluated C2
  http="$(curl -sS -o "$tmp/info.json" -w '%{http_code}' -m 10 \
    -H "Authorization: Bearer $token" "$API/system/info" 2>"$tmp/curl.err")" || http=000
  case "$http" in
    200)
      if jq -e '.runs' "$tmp/info.json" >/dev/null 2>&1; then
        printf 'ok    C2 api: system/info 200\n'
      else
        warn "system/info returned an unparseable body — C2/C3/C4 unevaluable"
        eval_failed=1
        api_ok=0
      fi
      ;;
    401)
      code="$(jq -r '.errors[0].code // "?"' "$tmp/info.json" 2>/dev/null)"
      fire C2 "$ALERT_SECONDS" "🔴" \
        "api-unauthorized — the dev token was rejected (HTTP 401, $code); the server token rotated, update what the monitor reads"
      api_ok=0
      ;;
    *)
      err="$(tr '\n' ' ' < "$tmp/curl.err" | cut -c1-120)"
      if [ -n "$err" ]; then
        fire C2 "$ALERT_SECONDS" "🔴" \
          "api-unreachable — GET $API/system/info failed (HTTP $http, $err)"
      else
        fire C2 "$ALERT_SECONDS" "🔴" \
          "api-unreachable — GET $API/system/info failed (HTTP $http)"
      fi
      api_ok=0
      ;;
  esac
fi

enabled_schedules=""
if [ "$api_ok" = 1 ]; then
  # ---- C3 gate: does ANY automation have an enabled schedule? -------------
  http="$(curl -sS -o "$tmp/autos.json" -w '%{http_code}' -m 10 \
    -H "Authorization: Bearer $token" "$API/automations" 2>/dev/null)" || http=000
  if [ "$http" != 200 ]; then
    warn "GET automations returned HTTP $http — C3 unevaluable"
    eval_failed=1
  else
    # arch-review runs twice a week per repo (ADR 0012), so its enabled schedules
    # must not arm a 2-hour idle alarm meant for the backlog cadence.
    enabled_schedules="$(jq '[.data[] | select(.workflow != "arch-review" and .workflow != "merge-queue") | .triggers[]? | select(.type == "schedule" and .enabled == true)] | length' "$tmp/autos.json" 2>/dev/null)"
    if ! printf '%s' "$enabled_schedules" | grep -Eq '^[0-9]+$'; then
      warn "automations response unparseable — C3 unevaluable"
      eval_failed=1
      enabled_schedules=""
    fi
  fi
fi

# ---- the scheduler's coder pools, read once for C3 and C8 -------------------
# The operator drains pools to use those boxes outside fabro, so a drained pool is
# normal. C3 asks "is every undrained pool busy?" and C8 "has every undrained pool
# sat idle?". Both read this one /api/pools sample. When it is unreadable, pools_ok
# stays 0: C3 then judges by run age alone, and C8 skips.
pools_ok=0
phttp="$(curl -sS -o "$tmp/pools.json" -w '%{http_code}' -m 5 \
  "$SCHEDULER_URL/api/pools" 2>/dev/null)" || phttp=000
if [ "$phttp" = 200 ] && jq -e 'type == "array"' "$tmp/pools.json" >/dev/null 2>&1; then
  pools_ok=1
fi

if [ "$api_ok" = 1 ]; then
  # ---- C3 and C4 read different run sets (ADR 0024 D8, rule 6) ------------
  # merge-queue sweeps (288 a day) would fill any fixed-size newest page, so C3 skips them and
  # pages past them, and C4 reads the non-terminal runs by status filter instead of the newest page.
  # C3: newest non-merge-queue run, pages of 100 newest-first, up to 5 pages.
  c3_ok=1
  : > "$tmp/c3.tsv"
  c3_floor=0
  off=0
  page_n=0
  while [ "$page_n" -lt 5 ]; do
    http="$(curl -sSg -o "$tmp/runs.json" -w '%{http_code}' -m 10 \
      -H "Authorization: Bearer $token" "$API/runs?page[limit]=100&page[offset]=$off" 2>/dev/null)" || http=000
    if [ "$http" != 200 ] || ! jq -e '.data | type == "array"' "$tmp/runs.json" >/dev/null 2>&1; then
      warn "GET runs failed or was unparseable (HTTP $http) — C3 unevaluable"
      c3_ok=0; eval_failed=1; break
    fi
    jq -r '.data[] | select((.workflow.slug // "") != "merge-queue") | [(.id // ""), (.timestamps.created_at // ""), (.lifecycle.status.kind // ""), (.workflow.slug // "")] | @tsv' \
      "$tmp/runs.json" >> "$tmp/c3.tsv" 2>/dev/null || { warn "could not extract run rows — C3 unevaluable"; c3_ok=0; eval_failed=1; break; }
    [ -s "$tmp/c3.tsv" ] && break
    n="$(jq '.data | length' "$tmp/runs.json")"
    c3_floor="$(jq -r '.data[-1].timestamps.created_at // ""' "$tmp/runs.json")"
    [ "${n:-0}" -gt 0 ] && [ "$(jq '.meta.has_more // false' "$tmp/runs.json")" = true ] || break
    off=$(( off + n )); page_n=$(( page_n + 1 ))
  done

  # C4: every non-terminal run, by the status filters list_active_runs uses (a stuck run cannot
  # fall off a page). Up to 10 pages of 100.
  c4_ok=1
  : > "$tmp/c4.tsv"
  off=0
  page_n=0
  while [ "$page_n" -lt 10 ]; do
    http="$(curl -sSg -o "$tmp/active.json" -w '%{http_code}' -m 10 \
      -H "Authorization: Bearer $token" "$API/runs?status=pending&status=runnable&status=initializing&status=running&status=blocked&status=removing&page[limit]=100&page[offset]=$off" 2>/dev/null)" || http=000
    if [ "$http" != 200 ] || ! jq -e '.data | type == "array"' "$tmp/active.json" >/dev/null 2>&1; then
      warn "GET active runs failed or was unparseable (HTTP $http) — C4 unevaluable"
      c4_ok=0; eval_failed=1; break
    fi
    jq -r '.data[] | [(.id // ""), (.timestamps.created_at // ""), (.lifecycle.status.kind // ""), (.workflow.slug // "")] | @tsv' \
      "$tmp/active.json" >> "$tmp/c4.tsv" 2>/dev/null || { warn "could not extract run rows — C4 unevaluable"; c4_ok=0; eval_failed=1; break; }
    n="$(jq '.data | length' "$tmp/active.json")"
    [ "${n:-0}" -gt 0 ] && [ "$(jq '.meta.has_more // false' "$tmp/active.json")" = true ] || break
    off=$(( off + n )); page_n=$(( page_n + 1 ))
  done

  if [ "$c3_ok" = 1 ]; then
    # C3: newest run vs IDLE_HOURS, only when the gate is known (a missing gate means
    # "unevaluable", never "resolved").
    if [ -n "$enabled_schedules" ]; then
      mark_evaluated C3
      newest=0
      newest_id=""
      while IFS="$(printf '\t')" read -r rid created kind slug; do
        [ -n "$rid" ] || continue
        e="$(run_epoch "$created" "$rid")"
        [ -n "$e" ] || { warn "run $rid has no usable timestamp; row skipped"; continue; }
        if [ "$e" -gt "$newest" ]; then newest="$e"; newest_id="$rid"; fi
      done < "$tmp/c3.tsv"
      # 500 merge-queue rows and nothing else: the newest other run is at least as old as the
      # oldest row read, so that row's age is a lower bound.
      if [ "$newest" = 0 ] && [ -n "$c3_floor" ]; then
        e="$(run_epoch "$c3_floor" "")"
        if [ -n "$e" ]; then newest="$e"; newest_id="(none in the newest 500 runs)"; fi
      fi

      # A full scheduler is not a dead one (2026-10-20). With pools drained, the
      # undrained ones can each hold a run for over IDLE_HOURS, so no new run starts.
      # Saturated means every undrained pool's lease names a run that C4 read as
      # non-terminal. A lease on a finished or unknown run is a wedge, so it does not
      # count. Every pool drained counts as saturated: the operator stopped dispatch.
      # Without both reads, C3 judges by run age alone, as before.
      saturated=""
      if [ "$pools_ok" = 1 ] && [ "$c4_ok" = 1 ]; then
        live_ids="$(cut -f1 "$tmp/c4.tsv" | jq -Rsc 'split("\n") | map(select(. != ""))')"
        saturated="$(jq -r --argjson live "${live_ids:-[]}" '
          [.[] | select(.drained == false)] as $open
          | if length > 0 and ($open | all(.lease.run_id as $r | $r != null and ($live | any(.[]; . == $r))))
            then "\($open | length) undrained pool(s) leased, \(length - ($open | length)) drained"
            else "" end' "$tmp/pools.json" 2>/dev/null)"
      fi

      if [ "$enabled_schedules" -gt 0 ] && [ "$newest" -gt 0 ] \
        && [ $(( now - newest )) -gt $(( IDLE_HOURS * 3600 )) ] && [ -n "$saturated" ]; then
        printf 'ok    C3 dead-scheduler: newest run %smin ago, over the %sh limit, but the scheduler is full (%s)\n' \
          $(( (now - newest) / 60 )) "$IDLE_HOURS" "$saturated"
      elif [ "$enabled_schedules" -gt 0 ] && [ "$newest" -gt 0 ] \
        && [ $(( now - newest )) -gt $(( IDLE_HOURS * 3600 )) ]; then
        fire C3 "$ALERT_SECONDS" "🔴" \
          "dead-scheduler — $enabled_schedules schedule(s) enabled but no run for $(( (now - newest) / 3600 ))h (newest run $newest_id, limit ${IDLE_HOURS}h)"
      elif [ "$enabled_schedules" -eq 0 ]; then
        printf 'ok    C3 dead-scheduler: inert, no enabled schedules\n'
      else
        printf 'ok    C3 dead-scheduler: newest run %smin ago within %sh limit (%s enabled)\n' \
          $(( (now - newest) / 60 )) "$IDLE_HOURS" "$enabled_schedules"
      fi
    else
      printf 'skip  C3 dead-scheduler: schedule gate unreadable\n'
    fi
  fi

  if [ "$c4_ok" = 1 ]; then
    # C4's answer comes from the active-run list alone; it is definitive even when the C3 gate
    # could not be read.
    mark_evaluated C4
    # Any non-terminal run older than its limit. Multiple stuck runs coalesce into ONE fire (the
    # oldest is named) so the per-condition dedup stamp cannot multiply messages.
    stuck_n=0
    stuck_oldest_e=0
    stuck_rid=""
    stuck_kind=""
    stuck_age=0
    stuck_lim_s=$(( STUCK_HOURS * 3600 ))
    while IFS="$(printf '\t')" read -r rid created kind slug; do
      [ -n "$rid" ] || continue
      case "$TERMINAL" in
        *" $kind "*) continue ;;
      esac
      e="$(run_epoch "$created" "$rid")"
      [ -n "$e" ] || continue
      age=$(( now - e ))
      lim_s=$(( STUCK_HOURS * 3600 ))
      [ "$slug" = arch-review ] && lim_s=$(( ARCH_STUCK_HOURS * 3600 ))
      [ "$slug" = merge-queue ] && lim_s=$(( MQ_STUCK_MINUTES * 60 ))
      if [ "$age" -gt "$lim_s" ]; then
        stuck_n=$(( stuck_n + 1 ))
        if [ "$stuck_oldest_e" = 0 ] || [ "$e" -lt "$stuck_oldest_e" ]; then
          stuck_oldest_e="$e"; stuck_rid="$rid"; stuck_kind="$kind"; stuck_age="$age"; stuck_lim_s="$lim_s"
        fi
      fi
    done < "$tmp/c4.tsv"
    if [ "$stuck_n" -gt 0 ]; then
      if [ "$stuck_n" -eq 1 ]; then
        fire C4 "$ALERT_SECONDS" "🔴" \
          "stuck-run — run $stuck_rid status='${stuck_kind:-unknown}' age $(( stuck_age / 60 ))min (limit $(( stuck_lim_s / 60 ))min)"
      else
        fire C4 "$ALERT_SECONDS" "🔴" \
          "stuck-run — $stuck_n runs over their limit, oldest $stuck_rid status='${stuck_kind:-unknown}' age $(( stuck_age / 60 ))min"
      fi
    else
      printf 'ok    C4 stuck-run: no non-terminal runs over their limit\n'
    fi
  fi
fi

# ------------------------------------------------------------- C5 starvation ----

mark_evaluated C5
if ! have gh; then
  warn "gh is not on PATH — C5 unevaluable"
  printf 'skip  C5 starvation: gh not on PATH\n'
  eval_failed=1
else
  open_agent_issues=0
  nrepos=0
  c5_ok=1
  for repo in $REPOS; do
    nrepos=$(( nrepos + 1 ))
    n="$(gh issue list -R "$repo" --label agent --state open --limit 200 \
      --json number --jq 'length' 2>/dev/null)"
    if ! printf '%s' "$n" | grep -Eq '^[0-9]+$'; then
      warn "gh issue list failed for $repo — C5 unevaluable"
      eval_failed=1
      c5_ok=0
      break
    fi
    open_agent_issues=$(( open_agent_issues + n ))
  done
  if [ "$c5_ok" = 1 ]; then
    if [ "$open_agent_issues" -eq 0 ]; then
      fire C5 "$STARVE_SECONDS" "🟡" \
        "starvation — zero open agent-labeled issues across all four repos; file issues labeled needs-triage (or agent) on any of the four repos"
    else
      printf 'ok    C5 starvation: %s open agent issues across %s repos\n' "$open_agent_issues" "$nrepos"
    fi
  fi
fi

# ---------------------------------------------------------- C6 disk pressure ----

mark_evaluated C6
use_pct="$(df -P / 2>/dev/null | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
if ! printf '%s' "$use_pct" | grep -Eq '^[0-9]+$'; then
  warn "df -P / gave no usable percentage — C6 unevaluable"
  printf 'skip  C6 disk-pressure: df gave no percentage\n'
  eval_failed=1
elif [ "$use_pct" -ge "$DISK_PCT" ]; then
  fire C6 "$DISK_RE_SECONDS" "🟠" \
    "disk-pressure — / is at ${use_pct}% (threshold ${DISK_PCT}%)"
else
  printf 'ok    C6 disk-pressure: / at %s%% (threshold %s%%)\n' "$use_pct" "$DISK_PCT"
fi

# --------------------------------------------------------- C7 scheduler -------
# Evaluated unconditionally — the scheduler is a separate container and its
# liveness does not depend on fabro's, so C1 firing (or the fabro API being
# unreachable) must not silence this. /health is unauthenticated (decision 16),
# so no token is read for either condition. A curl failure IS the condition:
# it is never `eval_failed`.

mark_evaluated C7
c7_firing=0
http="$(curl -sS -o "$tmp/sched.json" -w '%{http_code}' -m 5 \
  "$SCHEDULER_URL/health" 2>"$tmp/sched.err")" || http=000
if [ "$http" = 200 ]; then
  printf 'ok    C7 scheduler: %s/health 200\n' "$SCHEDULER_URL"
else
  c7_firing=1
  err="$(tr '\n' ' ' < "$tmp/sched.err" | cut -c1-120)"
  if [ -n "$err" ]; then
    fire C7 "$ALERT_SECONDS" "🔴" \
      "scheduler-down — GET $SCHEDULER_URL/health failed (HTTP $http, $err)"
  else
    fire C7 "$ALERT_SECONDS" "🔴" \
      "scheduler-down — GET $SCHEDULER_URL/health failed (HTTP $http)"
  fi
fi

# ----------------------------------------------- C8 scheduler-wedged --------
# Queued work with every undrained coder pool idle (no lease), held for
# WEDGE_MINUTES. A box is momentarily idle between runs — the design keeps that
# gap near 15s — so a single sample is never enough; the window is what stops
# this from crying wolf. A drained pool is intentionally idle, so it is left out
# of the count: two idle pools with two drained is a wedge (before 2026-10-20 any
# drained pool silenced C8). With every pool drained there is nothing that should
# dispatch, so C8 does not fire. The first qualifying sample's epoch lives in the state file under its
# own key `C8_since`, seeded into `c8_since`; it is cleared the moment a sample
# stops qualifying. WEDGE_MINUTES=0 fires on the FIRST qualifying sample — that
# zero-length window is the documented forced test. C7 suppresses C8 (an
# unreachable scheduler makes the question unanswerable) and leaves the C8 stamp
# and C8_since untouched, exactly as C1 leaves C3/C4 alone.

c8_manage=0        # 1 once C8 was evaluated -> the state rewrite owns C8_since
c8_since_write=""  # the epoch to persist under C8_since when c8_manage=1
if [ "$c7_firing" = 1 ]; then
  printf 'skip  C8 scheduler-wedged: C7 scheduler-down is firing (queue unreadable by construction)\n'
else
  qhttp="$(curl -sS -o "$tmp/queue.json" -w '%{http_code}' -m 5 \
    "$SCHEDULER_URL/api/queue" 2>/dev/null)" || qhttp=000
  if [ "$qhttp" != 200 ] || [ "$pools_ok" != 1 ] \
    || ! jq -e 'type == "array"' "$tmp/queue.json" >/dev/null 2>&1; then
    # /health answered but the data is unreadable: not definitive, so C8 is NOT
    # marked evaluated and its stamp is carried over rather than resolved.
    printf 'skip  C8 scheduler-wedged: /api/queue or /api/pools unreadable (HTTP %s/%s)\n' \
      "$qhttp" "$phttp"
  else
    mark_evaluated C8
    c8_manage=1
    qlen="$(jq 'length' "$tmp/queue.json" 2>/dev/null)"
    n_pools="$(jq 'length' "$tmp/pools.json" 2>/dev/null)"
    n_open="$(jq '[.[] | select(.drained == false)] | length' "$tmp/pools.json" 2>/dev/null)"
    n_idle="$(jq '[.[] | select((.lease == null) and (.drained == false))] | length' \
      "$tmp/pools.json" 2>/dev/null)"
    n_drained=$(( ${n_pools:-0} - ${n_open:-0} ))
    if printf '%s' "$qlen$n_pools$n_open$n_idle" | grep -Eq '^[0-9]+$' \
      && [ "$qlen" -gt 0 ] && [ "$n_open" -gt 0 ] && [ "$n_idle" = "$n_open" ]; then
      c8_since="$(stamp_of C8_since)"
      printf '%s' "$c8_since" | grep -Eq '^[0-9]+$' || c8_since="$now"
      c8_since_write="$c8_since"
      if [ $(( now - c8_since )) -ge $(( WEDGE_MINUTES * 60 )) ]; then
        fire C8 "$ALERT_SECONDS" "🔴" \
          "scheduler-wedged — $qlen item(s) queued and all $n_open undrained coder pool(s) idle for $(( (now - c8_since) / 60 ))min (limit ${WEDGE_MINUTES}min); $n_drained drained"
      else
        printf 'ok    C8 scheduler-wedged: %s queued, all %s undrained pools idle for %smin/%smin (%s drained)\n' \
          "$qlen" "$n_open" $(( (now - c8_since) / 60 )) "$WEDGE_MINUTES" "$n_drained"
      fi
    else
      printf 'ok    C8 scheduler-wedged: %s queued, %s of %s undrained pools idle (%s drained)\n' \
        "$qlen" "$n_idle" "$n_open" "$n_drained"
    fi
  fi
fi

# ------------------------------------------------------------- dispatch ----
# A firing condition with no stamp alerts; with a stamp older than its re-alert
# interval it re-alerts; otherwise it stays quiet and keeps its stamp. A stamp
# whose condition was evaluated and is NOT firing sends exactly one resolved
# message and loses the stamp. Stamps of conditions that were not evaluated
# this run (suppressed by C1, or evaluation broke) are carried over untouched.
# DRY_RUN prints the would-send lines and writes nothing.

TAB="$(printf '\t')"
firing_ids=" "
while IFS="$TAB" read -r cond _rest; do
  [ -n "$cond" ] || continue
  firing_ids="$firing_ids$cond "
done < "$tmp/firing"

remove=""
restamp=""
while IFS="$TAB" read -r cond interval emoji detail; do
  [ -n "$cond" ] || continue
  s="$(stamp_of "$cond")"
  if [ -z "$s" ]; then
    send "$emoji fabro monitor: $detail"
    restamp="$restamp $cond=$now"
  elif [ $(( now - s )) -ge "$interval" ]; then
    send "$emoji fabro monitor: $detail (still firing)"
    restamp="$restamp $cond=$now"
  else
    restamp="$restamp $cond=$s"
  fi
done < "$tmp/firing"

if [ -f "$STATE_FILE" ]; then
  for cond in C1 C2 C3 C4 C5 C6 C7 C8; do
    s="$(stamp_of "$cond")"
    [ -n "$s" ] || continue
    case "$firing_ids" in *" $cond "*) continue ;; esac
    grep -qx "$cond" "$tmp/evaluated" || continue
    send "✅ fabro monitor: $(cond_name "$cond") resolved"
    remove="$remove $cond "
  done
fi

if [ "$DRY_RUN" != "1" ]; then
  : > "$tmp/state.new"
  if [ -f "$STATE_FILE" ]; then
    while IFS='=' read -r cond val; do
      [ -n "$cond" ] || continue
      case "$remove" in *" $cond "*) continue ;; esac
      case "$restamp" in *" $cond="*) continue ;; esac
      # C8_since is C8's wedge window, not a dedup stamp: it is re-emitted from
      # c8_since_write below whenever C8 was evaluated this run, and dropped
      # when the sample stopped qualifying. When C8 was skipped (C7 firing),
      # c8_manage stays 0 and the existing key is carried over untouched.
      if [ "$cond" = "C8_since" ] && [ "$c8_manage" = 1 ]; then continue; fi
      printf '%s=%s\n' "$cond" "$val" >> "$tmp/state.new"
    done < "$STATE_FILE"
  fi
  # shellcheck disable=SC2086
  for kv in $restamp; do printf '%s\n' "$kv" >> "$tmp/state.new"; done
  if [ "$c8_manage" = 1 ] && [ -n "$c8_since_write" ]; then
    printf 'C8_since=%s\n' "$c8_since_write" >> "$tmp/state.new"
  fi
  # Nothing firing and no prior stamps: write no file at all, so a clean tick
  # leaves no state behind (acceptance: no state file when nothing fires).
  if [ -s "$tmp/state.new" ] || [ -f "$STATE_FILE" ]; then
    mkdir -p "$(dirname "$STATE_FILE")" || exit 1
    mv "$tmp/state.new" "$STATE_FILE" || exit 1
  fi
fi

# ------------------------------------------------------------- heartbeat ----
# A dead host is silent by construction, so an external dead-man's ping is the
# only way to notice host death. Pinged at the end of EVERY run; evaluation
# failure pings <url>/fail instead. A failed ping is logged, never fatal.
hb_url="${FABRO_HEARTBEAT_URL:-}"
hb_url="${hb_url%/}"
if [ -n "$hb_url" ]; then
  if [ "$eval_failed" = 1 ]; then
    curl -fsS -m 10 -o /dev/null "$hb_url/fail" 2>/dev/null \
      || warn "heartbeat /fail ping to $hb_url failed"
  else
    curl -fsS -m 10 -o /dev/null "$hb_url" 2>/dev/null \
      || warn "heartbeat ping to $hb_url failed"
  fi
fi

if [ "$eval_failed" = 1 ]; then
  printf '%s\n' "fabro-monitor: evaluation failed (see warnings above)" >&2
  exit 1
fi
exit 0
