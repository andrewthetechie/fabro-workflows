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
# a debugging session on the run that exposed the pr-review reporting bug. There is no
# leak to fix in fabro; there is a janitor missing from this tree.
#
# Deterministic: every skip is a mechanical test, there is no judgment and no model
# involved. It reads nothing from the fabro API and needs no token — an exited
# container older than the grace has nothing live in it, whatever the run record says.
#
# Skip rules, in order:
#   1. the name is not exactly fabro-run-<26-char ULID>  -> never touch
#      This is what keeps `fabro-fabro-1` and the unrelated `sandcastle-*` containers
#      on this host out of reach. A substring filter would not.
#   2. the container is RUNNING                          -> never touch, report it
#      It is either an in-flight run or a sandbox someone deliberately restarted to
#      look inside. Killing the second one silently is worse than leaving it, so this
#      reports and moves on, the same report-do-not-correct discipline
#      provision-server-state.sh uses for drift.
#   3. it stopped more recently than the grace period    -> not yet
#      The sandbox of the run that just failed is the one you want to open.
#
# Env:
#   DRY_RUN=1        print what would be removed, remove nothing (default)
#   GRACE_HOURS=48   how long a stopped sandbox is kept for post-mortems
set -u

DRY_RUN="${DRY_RUN:-1}"
GRACE_HOURS="${GRACE_HOURS:-48}"

command -v docker >/dev/null 2>&1 || { echo "docker is required but not on PATH" >&2; exit 1; }

now=$(date +%s)
cutoff=$(( now - GRACE_HOURS * 3600 ))

kept_running=0
kept_grace=0
removed=0
failed=0

# Names first, then one inspect per container: piping `docker ps` straight into a
# filter would take the filter's exit status, so a docker outage would come back as
# empty output and be indistinguishable from "nothing to sweep".
if ! docker ps -a --format '{{.Names}}' > /tmp/fabro-sandbox-sweep.names; then
  echo "FAILED to list containers; cannot tell what exists." >&2
  exit 1
fi

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

  state=$(docker inspect "$name" --format '{{.State.Running}} {{.State.FinishedAt}}' 2>/dev/null) \
    || { echo "WARN  $name: could not inspect, skipping"; continue; }
  running=${state%% *}
  finished=${state#* }

  # Rule 2.
  if [ "$running" = "true" ]; then
    echo "keep  $name (still running)"
    kept_running=$(( kept_running + 1 ))
    continue
  fi

  # Rule 3. A container that was created but never ran carries the zero time, which is
  # not a timestamp worth doing arithmetic on; treat it as ungraded and leave it.
  case "$finished" in
    0001-01-01*) echo "keep  $name (never ran)"; kept_grace=$(( kept_grace + 1 )); continue ;;
  esac
  ts=$(date -d "$finished" +%s 2>/dev/null) || ts=""
  if [ -z "$ts" ]; then
    echo "WARN  $name: unreadable FinishedAt '$finished', skipping"
    continue
  fi
  if [ "$ts" -ge "$cutoff" ]; then
    echo "keep  $name (within grace)"
    kept_grace=$(( kept_grace + 1 ))
    continue
  fi

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY   $name (stopped $finished) would be removed"
    removed=$(( removed + 1 ))
    continue
  fi

  if docker rm "$name" >/dev/null 2>&1; then
    echo "rm    $name (stopped $finished)"
    removed=$(( removed + 1 ))
  else
    echo "WARN  $name: remove failed"
    failed=$(( failed + 1 ))
  fi
done < /tmp/fabro-sandbox-sweep.names

rm -f /tmp/fabro-sandbox-sweep.names

if [ "$DRY_RUN" = "1" ]; then
  echo "$(date '+%Y-%m-%dT%H:%M:%S%z') sweep finished (DRY_RUN=1): would remove $removed, kept $kept_grace within grace, $kept_running running"
else
  echo "$(date '+%Y-%m-%dT%H:%M:%S%z') sweep finished: removed $removed, kept $kept_grace within grace, $kept_running running, $failed failed"
fi
exit 0
