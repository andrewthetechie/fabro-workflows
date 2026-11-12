#!/bin/sh
# fabro-sandbox-sweep.sh — remove exited fabro run sandbox containers.
#
# Fabro creates one `fabro-run-<run_id>` container per run and stops it when the run
# reaches a terminal state. It never REMOVES it, and nothing else does either, so the
# host accumulates one dead container per run forever. Measured 2026-09-16: 165
# containers, 16.8 GB of writable layers, oldest three days old. This reaps them, the
# same job `fabro-branch-sweep.sh` does for the `fabro/run/*` branches.
#
# `stop_on_terminal` is NOT broken — that was the earlier reading, and the evidence
# contradicts it. 164 of 165 were `Exited (137)` with `FinishedAt` matching their run's
# `completed_at` to the millisecond. The one container still `Up` had already stopped
# correctly at 15:19:11 and was started again by hand at 16:51:35 the same day, during
# a debugging session on the run that exposed the pr-review reporting bug. Fabro's stop
# is fine; what it does not do is REMOVE the container, and for six weeks nothing else
# did either. What this header did not know on 2026-09-16, and rule 2 below now does, is
# that a container could also come back up on its own — because a file read does that.
#
# A fixed grace alone cannot protect this disk (issue #50). On 2026-10-19 the host
# filled: 383 stopped sandboxes held 724 GB reclaimable, 778 GB of it in `backlog`
# sandboxes — writers-app's cold cargo builds, 22-62 GB each. The 04:43Z sweep removed
# 0 and kept 152 within the 24h grace. At ~24 GB a run, about 38 writers-app runs in
# any one grace window fill the disk, whatever the window is. So the grace stays the
# normal rule, and disk pressure overrides it:
#
#   normal     a stopped sandbox is kept for GRACE_HOURS, or BIG_GRACE_HOURS when its
#              writable layer exceeds BIG_GB (a writer's scratch is not worth 24h).
#   pressure   when `df -P /` is at or above PRESSURE_PCT, remove stopped sandboxes
#              oldest FinishedAt first, ignoring the grace, until use drops below
#              TARGET_PCT. `df` is re-read after every removal. Even under pressure a
#              sandbox stopped less than MIN_GRACE_MINUTES ago is kept, so the sandbox
#              of a run that just failed is still there to open. If pressure cannot get
#              below the target without crossing that floor, the pass logs it and exits
#              nonzero so the pass is not read as success; the FAIL line is the signal.
#
# Rule 2 was a permanent hole in that, and it is the rule that catches the leak now.
# A sandbox that is RUNNING is not necessarily a run in flight: fabro's docker provider
# answers `GET /runs/{id}/sandbox/file` on a STOPPED sandbox by starting the container
# again, and a run that has reached a terminal state has no worker left to stop it a
# second time. Measured 2026-11-11: 34 succeeded `ralph` runs were sitting on live
# containers 0.3-41 h old, each restarted about 2m15s after fabro stopped it, and this
# script reported every one of them as `keep ... (still running)` and moved on — 112
# such containers and 82% of the root filesystem before a human reaped them by hand.
# So a running sandbox older than MAX_RUNNING_HOURS is reaped too, and loudly:
#
#   running    a container started within MAX_RUNNING_HOURS is a live run and is never
#              touched — killing one would kill the run. Past the limit it is the leak,
#              so it is stopped, removed and reported with a WARN naming the rule.
#              Under pressure the limit tightens to PRESSURE_RUNNING_HOURS.
#
# Both limits sit above the longest run ever measured here (17.2 h, a `ralph` run, from
# 800 recent runs; p99 14.5 h; `backlog` peaks at 6.2 h), so a real run is never cut
# off. They are deliberately blunt — an age, not a run lookup — because the script
# still reads nothing from the fabro API and needs no token. The authoritative check
# ("this run is terminal, so its live container is garbage") is the obvious upgrade and
# is noted in ops/README.md.
#
# Deterministic: every skip is a mechanical test, there is no judgment and no model
# involved. It reads nothing from the fabro API and needs no token — an exited
# container older than the grace has nothing live in it, whatever the run record says,
# and a running one older than MAX_RUNNING_HOURS has outrun every run this host has ever
# finished.
#
# Skip rules, in order:
#   1. the name is not exactly fabro-run-<26-char ULID>  -> never touch
#      This is what keeps `fabro-fabro-1` and the unrelated `sandcastle-*` containers
#      on this host out of reach. A substring filter would not.
#   2. the container is RUNNING and started within MAX_RUNNING_HOURS (24 under
#      pressure: PRESSURE_RUNNING_HOURS)  -> never touch, report it
#      It is either an in-flight run or a sandbox someone deliberately restarted to
#      look inside. Killing the second one silently is worse than leaving it, so this
#      reports and moves on, the same report-do-not-correct discipline
#      provision-server-state.sh uses for drift. Past the limit it cannot be a run —
#      the longest run ever measured here is 17.2h — so it is the leak below, and it is
#      stopped, removed and `WARN`ed about.
#   3. it never ran (zero FinishedAt)                    -> not a finished sandbox
#   4. it stopped more recently than MIN_GRACE_MINUTES   -> not yet, even under pressure
#   5. normal mode: stopped more recently than its grace -> not yet
#
# The sizes come from one `docker inspect --size -f '{{.SizeRw}}'` per candidate.
# `docker ps -a --size` took over a minute for 390 containers (2026-10-19).
#
# Env:
#   DRY_RUN=1            print what would be removed, remove nothing (default)
#   GRACE_HOURS=24       how long a stopped sandbox is kept for post-mortems
#   BIG_GB=5             an oversized sandbox gets the short grace (0 disables the cap)
#   BIG_GRACE_HOURS=2    its grace
#   PRESSURE_PCT=70      df use at or above this starts pressure mode
#   TARGET_PCT=60        pressure mode removes until df use is below this
#   MIN_GRACE_MINUTES=30 never remove one stopped more recently than this, even hot
#   MAX_RUNNING_HOURS=36 never remove one RUNNING for less than this: the longest run
#                        ever measured here is 17.2h, so this cannot cut one off
#   PRESSURE_RUNNING_HOURS=24 the same limit while the disk is in pressure mode
#   STOP_TIMEOUT_SECONDS=5 docker stop's grace before it kills, on a stale sandbox
#
# `fabro-monitor.sh` alerts (C6) at DISK_PCT=85, deliberately higher than the
# pressure threshold: 70 is where the sweep acts to stop the fill, 85 is where a human
# is told the sweep could not. They are separate knobs because one is a control and
# the other an alert; do not lower C6 to 70 or it will page on normal headroom.
set -u

DRY_RUN="${DRY_RUN:-1}"
GRACE_HOURS="${GRACE_HOURS:-24}"
BIG_GB="${BIG_GB:-5}"
BIG_GRACE_HOURS="${BIG_GRACE_HOURS:-2}"
PRESSURE_PCT="${PRESSURE_PCT:-70}"
TARGET_PCT="${TARGET_PCT:-60}"
MIN_GRACE_MINUTES="${MIN_GRACE_MINUTES:-30}"
MAX_RUNNING_HOURS="${MAX_RUNNING_HOURS:-36}"
PRESSURE_RUNNING_HOURS="${PRESSURE_RUNNING_HOURS:-24}"
STOP_TIMEOUT_SECONDS="${STOP_TIMEOUT_SECONDS:-5}"

command -v docker >/dev/null 2>&1 || { echo "docker is required but not on PATH" >&2; exit 1; }

now=$(date +%s)
grace_secs=$(( GRACE_HOURS * 3600 ))
big_grace_secs=$(( BIG_GRACE_HOURS * 3600 ))
floor_secs=$(( MIN_GRACE_MINUTES * 60 ))
running_secs=$(( MAX_RUNNING_HOURS * 3600 ))
if [ "$BIG_GB" -gt 0 ]; then
  big_bytes=$(( BIG_GB * 1073741824 ))
  # The smallest grace any candidate can have: below it, no rule can remove it, so its
  # size does not need an inspect.
  min_grace_secs=$grace_secs
  [ "$big_grace_secs" -lt "$min_grace_secs" ] && min_grace_secs=$big_grace_secs
else
  big_bytes=0
  min_grace_secs=$grace_secs
fi

kept_running=0
kept_never=0
kept_floor=0
kept_grace=0
removed=0
removed_grace=0
removed_big=0
removed_pressure=0
removed_stale=0
stale_found=0
failed=0
unknown_size=0
freed_bytes=0
sim_freed_kb=0

DF_PCT=""
DF_USED=""
DF_AVAIL=""
SIZE=""
SIZE_KNOWN=0

# Read the root filesystem's capacity line. df -P is the portable one-line form.
df_read() {
  DF_PCT=""; DF_USED=""; DF_AVAIL=""
  _line=$(df -P / 2>/dev/null | awk 'NR==2 {print $2, $3, $4, $5}')
  [ -n "$_line" ] || return 1
  # shellcheck disable=SC2086  # word splitting is the point: four df fields
  set -- $_line
  [ $# -eq 4 ] || return 1
  DF_USED=$2
  DF_AVAIL=$3
  _pct=${4%\%}
  case "$_pct" in
    ''|*[!0-9]*) return 1 ;;
  esac
  DF_PCT=$_pct
  return 0
}

# The use percentage after freeing this many 1K blocks. `df` capacity is used over
# used+available, and freeing moves bytes from the first to the second, so the
# denominator is stable; this is what a dry run reports instead of pretending df moved.
sim_pct() {
  _freed=${1:-0}
  [ -n "$DF_USED" ] && [ -n "$DF_AVAIL" ] || return 1
  _denom=$(( DF_USED + DF_AVAIL ))
  [ "$_denom" -gt 0 ] || return 1
  _used=$(( DF_USED - _freed ))
  [ "$_used" -lt 0 ] && _used=0
  printf '%s' $(( _used * 100 / _denom ))
}

# The writable layer in bytes, or nothing when inspect cannot tell us. Sets SIZE and
# SIZE_KNOWN (0 when the inspect output is not a byte count).
size_of() {
  SIZE=""
  SIZE_KNOWN=0
  _raw=$(docker inspect --size -f '{{.SizeRw}}' "$1" </dev/null 2>/dev/null) || return
  case "$_raw" in
    ''|*[!0-9]*) ;;
    *) SIZE=$_raw; SIZE_KNOWN=1 ;;
  esac
}

# remove_or_dry <name> <label> <rule> <size-known> <size> [stop-first]
# Counts a dry-run removal too: "would free" is the interesting number in a dry run.
# `label` is the timestamp line in the report, pre-worded by the caller: an exited
# sandbox is "stopped <FinishedAt>", a leaked running one "running since <StartedAt>".
# `stop-first` is 1 only for a running container: `docker rm` on one fails, so it has to
# be stopped first, and its PID 1 is `sleep infinity`, which takes SIGTERM immediately.
remove_or_dry() {
  _name=$1
  _label=$2
  _rule=$3
  _known=$4
  _size=$5
  _stop=${6:-0}

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY   $_name ($_label) would be removed ($_rule)"
  else
    if [ "$_stop" = "1" ]; then
      # Best effort. A container that exited between the inspect and here is not a
      # failure — the `docker rm` below is what the pass actually wants.
      docker stop -t "$STOP_TIMEOUT_SECONDS" "$_name" </dev/null >/dev/null 2>&1 || :
    fi
    if ! docker rm "$_name" </dev/null >/dev/null 2>&1; then
      echo "WARN  $_name: remove failed"
      failed=$(( failed + 1 ))
      return 1
    fi
    echo "rm    $_name ($_label) ($_rule)"
  fi

  removed=$(( removed + 1 ))
  case "$_rule" in
    pressure)          removed_pressure=$(( removed_pressure + 1 )) ;;
    big)               removed_big=$(( removed_big + 1 )) ;;
    stale-running)     removed_stale=$(( removed_stale + 1 )) ;;
    *)                 removed_grace=$(( removed_grace + 1 )) ;;
  esac
  if [ "$_known" = "1" ]; then
    freed_bytes=$(( freed_bytes + _size ))
    # A dry run's "after" df is simulated from every removal, not just pressure ones.
    [ "$DRY_RUN" = "1" ] && sim_freed_kb=$(( sim_freed_kb + _size / 1024 ))
  else
    unknown_size=$(( unknown_size + 1 ))
  fi
  return 0
}

tmpbase=${TMPDIR:-/tmp}
names=$(mktemp "$tmpbase/fabro-sandbox-sweep.names.XXXXXX") || {
  echo "FAILED to create a temporary file; cannot sweep." >&2
  exit 1
}
cands=$(mktemp "$tmpbase/fabro-sandbox-sweep.cands.XXXXXX") || {
  rm -f "$names"
  echo "FAILED to create a temporary file; cannot sweep." >&2
  exit 1
}
stale=$(mktemp "$tmpbase/fabro-sandbox-sweep.stale.XXXXXX") || {
  rm -f "$names" "$cands"
  echo "FAILED to create a temporary file; cannot sweep." >&2
  exit 1
}
# shellcheck disable=SC2329  # invoked by the traps below
cleanup() { rm -f "$names" "$cands" "$stale"; }
trap cleanup 0
trap 'cleanup; exit 1' 1 2 3 15

df_before="?"
pressure_active=0
if df_read; then
  df_before=$DF_PCT
  if [ "$DF_PCT" -ge "$PRESSURE_PCT" ]; then
    pressure_active=1
    running_secs=$(( PRESSURE_RUNNING_HOURS * 3600 ))
    echo "note  / is at ${DF_PCT}% (pressure ${PRESSURE_PCT}%, target ${TARGET_PCT}%): reaping oldest first, ignoring grace, and a running sandbox past ${PRESSURE_RUNNING_HOURS}h"
  fi
else
  echo "WARN  df -P / gave no usable percentage; pressure mode is off"
fi

# Names first, then one inspect per container: piping `docker ps` straight into a
# filter would take the filter's exit status, so a docker outage would come back as
# empty output and be indistinguishable from "nothing to sweep".
if ! docker ps -a --format '{{.Names}}' > "$names"; then
  echo "FAILED to list containers; cannot tell what exists." >&2
  exit 1
fi

: > "$cands"
while read -r name; do
  [ -n "$name" ] || continue

  # Rule 1: exact shape only. 10 characters of prefix plus a 26-character ULID.
  case "$name" in
    fabro-run-*) ;;
    *) continue ;;
  esac
  [ "${#name}" -eq 36 ] || { echo "skip  $name (not a run sandbox name)"; continue; }
  id=${name#fabro-run-}
  case "$id" in
    *[!0-9A-Z]*) echo "skip  $name (not a run sandbox name)"; continue ;;
  esac

  # Every docker call in this loop takes </dev/null: the loop reads the names on stdin, and
  # a docker call that reads it too ends the pass early, silently (2026-10-15: 504 of ~1,950).
  state=$(docker inspect "$name" --format '{{.State.Running}} {{.State.FinishedAt}} {{.State.StartedAt}}' </dev/null 2>/dev/null) \
    || { echo "WARN  $name: could not inspect, skipping"; continue; }
  running=${state%% *}
  _rest=${state#* }
  finished=${_rest%% *}
  # An inspect that answered fewer fields than this expects has no StartedAt in it. Take
  # the empty string rather than the whole tail, which is what a naive split would do.
  case "$_rest" in
    *" "*) started=${_rest#* } ;;
    *) started="" ;;
  esac

  # Rule 2.
  if [ "$running" = "true" ]; then
    # Still running past the limit is the resurrection this script exists to reap: the
    # run reached a terminal state, fabro stopped the sandbox, and a later file read
    # started it again with no worker left to stop it. Under 36h it could be a real run
    # or a human looking inside a sandbox, and either way this leaves it alone.
    started_ts=""
    case "$started" in
      ''|0001-01-01*) : ;;
      *) started_ts=$(date -d "$started" +%s 2>/dev/null) || started_ts="" ;;
    esac
    if [ -n "$started_ts" ] && [ $(( now - started_ts )) -ge "$running_secs" ]; then
      printf '%s\t%s\t%s\n' "$started_ts" "$started" "$name" >> "$stale"
      stale_found=$(( stale_found + 1 ))
      continue
    fi
    if [ -z "$started_ts" ]; then
      echo "keep  $name (still running; no readable StartedAt)"
    else
      echo "keep  $name (still running since $started)"
    fi
    kept_running=$(( kept_running + 1 ))
    continue
  fi

  # Rule 3. A container that was created but never ran carries the zero time, which is
  # not a timestamp worth doing arithmetic on; treat it as ungraded and leave it.
  case "$finished" in
    0001-01-01*) echo "keep  $name (never ran)"; kept_never=$(( kept_never + 1 )); continue ;;
  esac
  ts=$(date -d "$finished" +%s 2>/dev/null) || ts=""
  if [ -z "$ts" ]; then
    echo "WARN  $name: unreadable FinishedAt '$finished', skipping"
    continue
  fi
  printf '%s\t%s\t%s\n' "$ts" "$finished" "$name" >> "$cands"
done < "$names"

tab=$(printf '\t')

# A stale running sandbox is both the largest thing here (it is a whole run's scratch,
# kept alive for days) and the one that never stops growing, so it goes first: under
# pressure, `df` is re-read after each removal exactly as the exited pass does.
# Pressure mode is the one set when this list was built, so a container between 24h and
# 36h old is a candidate only on a hot disk.
if [ "$stale_found" -gt 0 ]; then
  sort -k1,1n -o "$stale" "$stale"
  echo "WARN  $stale_found running sandbox(es) older than ${MAX_RUNNING_HOURS}h: a run has not lasted this long since 2026-11-11, so something is restarting stopped sandboxes"
  while IFS="$tab" read -r ts started name; do
    [ -n "$name" ] || continue
    echo "note  $name: started $started, $(( (now - ts) / 3600 ))h ago"
    size_of "$name"
    if remove_or_dry "$name" "running since $started" stale-running "$SIZE_KNOWN" "$SIZE" 1; then
      if [ "$DRY_RUN" = "1" ]; then
        sim=$(sim_pct "$sim_freed_kb")
        if [ -n "$sim" ] && [ "$sim" -lt "$TARGET_PCT" ]; then
          pressure_active=0
          echo "note  a dry run would reach ${sim}% after this removal: pressure off"
        fi
      elif df_read && [ "$DF_PCT" -lt "$TARGET_PCT" ]; then
        pressure_active=0
        echo "note  / is now at ${DF_PCT}%: pressure off"
      fi
    fi
  done < "$stale"
fi

# Pressure removes oldest FinishedAt first; sort once so both modes walk that order.
sort -k1,1n -o "$cands" "$cands"

while IFS="$tab" read -r ts finished name; do
  [ -n "$name" ] || continue
  age=$(( now - ts ))

  # Rule 4, even under pressure: the sandbox of a run that just failed must be openable.
  if [ "$age" -lt "$floor_secs" ]; then
    echo "keep  $name (stopped $finished, within ${MIN_GRACE_MINUTES}m floor)"
    kept_floor=$(( kept_floor + 1 ))
    continue
  fi

  if [ "$pressure_active" = "1" ]; then
    size_of "$name"
    if remove_or_dry "$name" "stopped $finished" pressure "$SIZE_KNOWN" "$SIZE"; then
      if [ "$DRY_RUN" = "1" ]; then
        sim=$(sim_pct "$sim_freed_kb")
        if [ -n "$sim" ] && [ "$sim" -lt "$TARGET_PCT" ]; then
          pressure_active=0
          echo "note  a dry run would reach ${sim}% after this removal: pressure off"
        fi
      elif df_read && [ "$DF_PCT" -lt "$TARGET_PCT" ]; then
        pressure_active=0
        echo "note  / is now at ${DF_PCT}%: pressure off"
      fi
    fi
    continue
  fi

  # Rule 5, the normal rule. Skip the size inspect when no grace can have expired.
  if [ "$age" -lt "$min_grace_secs" ]; then
    echo "keep  $name (within grace)"
    kept_grace=$(( kept_grace + 1 ))
    continue
  fi
  size_of "$name"
  if [ "$big_bytes" -gt 0 ] && [ "$SIZE_KNOWN" = "1" ] && [ "$SIZE" -gt "$big_bytes" ]; then
    eff_grace=$big_grace_secs
    rule=big
  else
    eff_grace=$grace_secs
    rule=grace
  fi
  if [ "$age" -lt "$eff_grace" ]; then
    echo "keep  $name (within grace)"
    kept_grace=$(( kept_grace + 1 ))
    continue
  fi
  remove_or_dry "$name" "stopped $finished" "$rule" "$SIZE_KNOWN" "$SIZE" || :
done < "$cands"

df_after=$df_before
if [ "$DRY_RUN" = "1" ]; then
  sim=$(sim_pct "$sim_freed_kb")
  [ -n "$sim" ] && df_after=$sim
  echo "$(date '+%Y-%m-%dT%H:%M:%S%z') sweep finished (DRY_RUN=1): would remove $removed (grace $removed_grace, big $removed_big, pressure $removed_pressure, stale $removed_stale), kept $kept_grace within grace, $kept_floor within ${MIN_GRACE_MINUTES}m floor, $kept_running running, $kept_never never ran, would free $freed_bytes bytes; df ${df_before}% -> ${df_after}% (simulated)"
else
  if df_read; then df_after=$DF_PCT; else df_after="?"; fi
  echo "$(date '+%Y-%m-%dT%H:%M:%S%z') sweep finished: removed $removed (grace $removed_grace, big $removed_big, pressure $removed_pressure, stale $removed_stale), kept $kept_grace within grace, $kept_floor within ${MIN_GRACE_MINUTES}m floor, $kept_running running, $kept_never never ran, freed $freed_bytes bytes; df ${df_before}% -> ${df_after}%"
  [ "$failed" -gt 0 ] && echo "WARN  $failed remove(s) failed"
  # Not a janitor note: a live sandbox this old is a run that was resurrected by a file
  # read after it ended. The reap is the symptom going away, not the cause being fixed.
  [ "$removed_stale" -gt 0 ] \
    && echo "WARN  $removed_stale stale running sandbox(es) removed: something is starting stopped sandboxes again (header, rule 2)"
  [ "$unknown_size" -gt 0 ] && echo "WARN  $unknown_size removed container(s) had no readable size; freed bytes undercount"
fi

if [ "$pressure_active" = "1" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    echo "FAIL  disk pressure (dry run): would still be at ${df_after}% (target ${TARGET_PCT}%); remaining candidates are within the ${MIN_GRACE_MINUTES}m floor or have no readable size"
  else
    echo "FAIL  disk pressure: still at ${df_after}% (target ${TARGET_PCT}%) after the pass; remaining candidates are within the ${MIN_GRACE_MINUTES}m floor or could not be removed"
  fi
  exit 1
fi
exit 0
