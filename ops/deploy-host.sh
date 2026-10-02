#!/usr/bin/env bash
# Every quoted `~` in this file is a HOST path: it must reach ssh/rsync unexpanded
# and expand in the remote shell, never to the Mac's $HOME.
# shellcheck disable=SC2088
#
# Deploy this checkout's host-side trees to the fabro host: the steps of AGENTS.md
# "Deploying to the server after a merge to `main`", in one command. The Makefile at
# the repository root is the front end (`make deploy`, `make deploy-scheduler`, ...).
#
# Workflow changes need none of this: every automation reads .fabro/workflows from
# `main` at fire time. This deploys what nothing syncs -- ops/ and the notify script.
#
#   ops/deploy-host.sh all                  # everything below, then verify
#   ops/deploy-host.sh scheduler verify     # just these steps, in this order
#
# Steps:
#   notify     discord-notify.sh into the fabro container (the backlog hooks call it)
#   scripts    the operator scripts into ~/bin (sweepers, monitor, switches, fire)
#   compose    docker-compose.yaml into ~/fabro. Copying it does NOT apply it: a
#              `docker compose up -d` restarts fabro and fails every in-flight run,
#              so that is `compose-up`, which is never part of `all`.
#   scheduler  rsync ops/scheduler, then `docker compose up -d --build --no-deps
#              scheduler` (never touches fabro) and wait for /health. Skipped when the host tree already matches and
#              the last build of this tree succeeded, because every rebuild re-dates
#              the draft-14 shakedown window.
#   images     rsync profile-images, fabro-io and the output schemas, then
#              build-images.sh. Skipped on the same rule as `scheduler`: the build
#              clones four repos and takes many minutes. A failed build leaves the
#              previous images tagged and in use.
#   provision  ops/provision-server-state.sh against the API with the server's own
#              dev token. Create-if-absent: it never rewrites a row, and drift is
#              reported (exit 2), which this treats as a warning.
#   verify     every host copy diffed against this checkout, and scheduler /health.
#   compose-up `docker compose up -d` for the whole project. Explicit only, and it
#              needs CONFIRM=1.
#
# Environment:
#   HOST=andrew@10.10.0.32   ssh target
#   FORCE=1                  rebuild the scheduler and images even when unchanged
#   ALLOW_DIRTY=1            deploy a checkout that is dirty or not at origin/main
#   SKIP_IMAGES=1            leave `images` out of `all`
#   CONFIRM=1                required by compose-up
#
# A step is "unchanged" when rsync itemizes no change AND a stamp on the host
# (~/.fabro-deploy-stamps/<step>) holds this checkout's git tree hash for its inputs.
# The stamp is written only after a successful build, so a build that failed after
# its rsync succeeded is retried on the next deploy instead of looking done.

set -euo pipefail

HOST=${HOST:-andrew@10.10.0.32}
HOST_IP=${HOST#*@}
FORCE=${FORCE:-0}
ALLOW_DIRTY=${ALLOW_DIRTY:-0}
SKIP_IMAGES=${SKIP_IMAGES:-0}
CONFIRM=${CONFIRM:-0}
SCHEDULER_URL=${SCHEDULER_URL:-http://$HOST_IP:32280}
FABRO_API_URL=${FABRO_API_URL:-http://$HOST_IP:32276/api/v1}
STAMPS='~/.fabro-deploy-stamps'

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

BIN_SCRIPTS="fabro-branch-sweep.sh fabro-sandbox-sweep.sh fabro-monitor.sh fabro-auto-merge-switch.sh fabro-fire-backlog.sh fabro-automation-schedule.sh"
# node_modules is the Mac's untracked Tailwind toolchain (build-css.sh); the image never
# reads it (.dockerignore). Synced, its .bin symlinks never match: macOS gives a symlink
# mode 0755 and Linux always 0777, so rsync reported a change on every run, which rebuilt
# the scheduler (re-dating its shakedown window) and failed verify's tree check.
SCHED_EXCLUDES=(--exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude node_modules)
COMPOSE_CHANGED=0

say()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

# The git tree hash of one or more paths at HEAD, joined: identifies exactly what a
# step deploys, and changes only when one of those paths changes.
tree_id() {
  local p out=""
  for p in "$@"; do out="$out$(git rev-parse "HEAD:$p")"; done
  printf '%s' "$out"
}
stamp_get() { remote "cat $STAMPS/$1 2>/dev/null || true"; }
stamp_set() { remote "mkdir -p $STAMPS && printf '%s' '$2' > $STAMPS/$1"; }

# rsync that reports whether it changed anything. Prints the itemized changes.
# Called as an `if` condition, where `set -e` does not apply, so a failed rsync
# dies explicitly rather than read as "nothing changed".
sync_tree() { # sync_tree <src/> <host dest/> [rsync args...]
  local src=$1 dest=$2 out
  shift 2
  out=$(rsync -ai --delete "$@" "$src" "$HOST:$dest") || die "rsync $src -> $HOST:$dest failed"
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/    /'; return 0; fi
  return 1
}

preflight() {
  say "preflight"
  git fetch -q origin main
  local head origin
  head=$(git rev-parse HEAD)
  origin=$(git rev-parse origin/main)
  if [ "$head" != "$origin" ]; then
    [ "$ALLOW_DIRTY" = 1 ] || die "HEAD ($head) is not origin/main ($origin). Pull or push first, or ALLOW_DIRTY=1."
    warn "HEAD is not origin/main; deploying anyway (ALLOW_DIRTY=1)"
  fi
  if [ -n "$(git status --porcelain -- ops .fabro/workflows/backlog/scripts .fabro/workflows/_io/schemas)" ]; then
    [ "$ALLOW_DIRTY" = 1 ] || die "uncommitted changes under ops/ or the deployed .fabro paths. Commit them, or ALLOW_DIRTY=1."
    warn "deploying uncommitted changes (ALLOW_DIRTY=1)"
  fi
  remote true || die "cannot ssh to $HOST"
  echo "    $HOST reachable; deploying $(git rev-parse --short HEAD)"
}

step_notify() {
  say "notify: discord-notify.sh into fabro-fabro-1"
  scp -q .fabro/workflows/backlog/scripts/discord-notify.sh "$HOST:/tmp/discord-notify.sh"
  remote 'docker cp /tmp/discord-notify.sh fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'
}

step_scripts() {
  say "scripts: ~/bin"
  local s
  for s in $BIN_SCRIPTS; do scp -q "ops/$s" "$HOST:bin/$s"; done
  remote "cd ~/bin && chmod +x $BIN_SCRIPTS"
  echo "    $BIN_SCRIPTS"
}

step_compose() {
  say "compose: ~/fabro/docker-compose.yaml"
  if remote 'cat ~/fabro/docker-compose.yaml' | diff -q - ops/docker-compose.yaml >/dev/null; then
    echo "    unchanged"
    return
  fi
  scp -q ops/docker-compose.yaml "$HOST:fabro/docker-compose.yaml"
  COMPOSE_CHANGED=1
  warn "docker-compose.yaml changed and is NOT applied to fabro. The scheduler step, when it runs, recreates the scheduler service from it. To apply it to fabro, which fails every in-flight run: make compose-up CONFIRM=1"
}

step_scheduler() {
  say "scheduler: rsync ops/scheduler, rebuild if changed"
  remote 'test -f ~/fabro/scheduler.env' || die "~/fabro/scheduler.env is missing on the host; ops/README.md shows how to create it"
  local changed=0 want got
  if sync_tree ops/scheduler/ '~/fabro/scheduler/' "${SCHED_EXCLUDES[@]}"; then changed=1; fi
  want=$(tree_id ops/scheduler)
  got=$(stamp_get scheduler)
  [ "$want" = "$got" ] || changed=1
  [ "$COMPOSE_CHANGED" = 0 ] || changed=1
  if [ "$changed" = 0 ] && [ "$FORCE" != 1 ]; then
    echo "    unchanged since the last successful build; not rebuilt (FORCE=1 to rebuild)"
    return
  fi
  local before after
  before=$(remote "docker inspect -f '{{.State.StartedAt}}' fabro-scheduler 2>/dev/null || true")
  remote 'cd ~/fabro && docker compose up -d --build --no-deps scheduler'
  after=$(remote "docker inspect -f '{{.State.StartedAt}}' fabro-scheduler")
  [ "$after" != "$before" ] || warn "fabro-scheduler StartedAt did not change ($after); compose may have found nothing to recreate"
  for _ in $(seq 1 30); do
    if curl -fsS -m 5 -o /dev/null "$SCHEDULER_URL/health"; then
      stamp_set scheduler "$want"
      echo "    healthy; started $after (the draft-14 shakedown window restarts here)"
      return
    fi
    sleep 2
  done
  die "scheduler did not answer $SCHEDULER_URL/health within 60s; check: ssh $HOST 'docker logs --since 5m fabro-scheduler'"
}

step_images() {
  say "images: rsync profile-images, fabro-io, schemas; rebuild if changed"
  local changed=0 want got
  # Order is load-bearing: build-images.sh reads the crate to name the expected
  # fabro-io version, and the crate's tests compile the schemas.
  if sync_tree ops/profile-images/ '~/profile-images-build/'; then changed=1; fi
  if sync_tree ops/fabro-io/ '~/fabro-io/' --exclude target; then changed=1; fi
  if sync_tree .fabro/workflows/_io/schemas/ '~/fabro-io-schemas/'; then changed=1; fi
  want=$(tree_id ops/profile-images ops/fabro-io .fabro/workflows/_io/schemas)
  got=$(stamp_get images)
  [ "$want" = "$got" ] || changed=1
  if [ "$changed" = 0 ] && [ "$FORCE" != 1 ]; then
    echo "    unchanged since the last successful build; not rebuilt (FORCE=1 to rebuild)"
    return
  fi
  echo "    building; this clones four repositories and takes a while"
  remote 'cd ~/profile-images-build && ./build-images.sh'
  stamp_set images "$want"
}

step_provision() {
  say "provision: automations and server variables (create-if-absent)"
  local token rc=0
  token=$(remote 'docker exec fabro-fabro-1 cat /storage/server.dev-token')
  [ -n "$token" ] || die "could not read the server dev token"
  FABRO_API_URL=$FABRO_API_URL FABRO_DEV_TOKEN=$token ./ops/provision-server-state.sh || rc=$?
  case $rc in
    0) ;;
    2) warn "provisioning reported drift (above); nothing was rewritten. Reconcile by hand." ;;
    *) die "provisioning failed (exit $rc)" ;;
  esac
}

step_verify() {
  say "verify: host copies match this checkout"
  local bad=0 s
  check_copy() { # check_copy <label> <remote cat command> <local file>
    if remote "$2" | diff -q - "$3" >/dev/null 2>&1; then
      echo "    ok    $1"
    else
      echo "    DIFF  $1"; bad=1
    fi
  }
  for s in $BIN_SCRIPTS; do check_copy "~/bin/$s" "cat ~/bin/$s" "ops/$s"; done
  check_copy "~/fabro/docker-compose.yaml" 'cat ~/fabro/docker-compose.yaml' ops/docker-compose.yaml
  check_copy "~/fabro/scheduler/repos.toml" 'cat ~/fabro/scheduler/repos.toml' ops/scheduler/repos.toml
  check_copy "container discord-notify.sh" 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' .fabro/workflows/backlog/scripts/discord-notify.sh
  if [ -n "$(rsync -ain --delete "${SCHED_EXCLUDES[@]}" ops/scheduler/ "$HOST:~/fabro/scheduler/")" ]; then
    echo "    DIFF  ~/fabro/scheduler/ tree"; bad=1
  else
    echo "    ok    ~/fabro/scheduler/ tree"
  fi
  if [ "$(stamp_get scheduler)" = "$(tree_id ops/scheduler)" ]; then
    echo "    ok    running scheduler was built from this tree"
  else
    echo "    STALE running scheduler was not built from this tree (make deploy-scheduler)"; bad=1
  fi
  if curl -fsS -m 5 -o /dev/null "$SCHEDULER_URL/health"; then
    echo "    ok    scheduler /health"
  else
    echo "    FAIL  scheduler /health"; bad=1
  fi
  [ "$bad" = 0 ] || die "the host does not match this checkout (above)"
}

step_compose_up() {
  say "compose-up: docker compose up -d (all services)"
  [ "$CONFIRM" = 1 ] || die "this restarts fabro if its service changed, which fails every in-flight run. Re-run with CONFIRM=1."
  remote 'cd ~/fabro && docker compose up -d'
}

[ $# -gt 0 ] || { sed -n '6,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

steps=()
for a in "$@"; do
  case $a in
    all)
      steps+=(notify scripts compose scheduler)
      [ "$SKIP_IMAGES" = 1 ] || steps+=(images)
      steps+=(provision verify) ;;
    notify|scripts|compose|scheduler|images|provision|verify|compose-up) steps+=("$a") ;;
    *) die "unknown step: $a" ;;
  esac
done

preflight
for s in "${steps[@]}"; do
  "step_${s//-/_}"
done
say "done"
