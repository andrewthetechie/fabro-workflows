#!/usr/bin/env bash
#
# Build all four sandbox profile images, with their dependency caches warmed
# from each target repository's real lockfiles.
#
# Run it on the fabro host (10.10.0.32). The images are referenced by tag from
# the server-managed environment records and are never pushed to a registry:
# sandbox-driver's `ensure_image` checks `image_present()` and returns before it
# would pull, so a locally built tag is used as-is.
#
#   ./build-images.sh              # all four
#   ./build-images.sh python ts    # just these
#   DRY_RUN=1 ./build-images.sh    # assemble contexts, print the builds, build nothing
#
# Cloning uses whatever credentials the host's git already has. Set GH_TOKEN if
# it has none. The token is used for the clone only -- it never enters a build
# context and never reaches an image layer, because the context holds nothing
# but manifest and lock files.
#
# Nightly, out of the way of the schedules:
#   23 3 * * *  /home/andrew/bin/build-profile-images.sh >> ~/.local/state/profile-images.log 2>&1
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-$HOME/.cache/fabro-profile-images}
DRY_RUN=${DRY_RUN:-0}

# profile : repo : image tag
PROFILES="
python:jelly-swipe:fabro-python:local
python-node:lawncare-saas:fabro-python-node:local
ts:womens-fantasy-sports:fabro-ts:local
rust-node:writers-app:fabro-rust-node:local
"

# Every manifest and lock file a warm step can need, matched against the
# repository's tracked files and copied at its repository-relative path. Nothing
# else is copied: no source, no history, no .env, no secrets.
MANIFESTS='(^|/)(pyproject\.toml|uv\.lock|\.python-version|package\.json|package-lock\.json|bun\.lock|bun\.lockb|Cargo\.toml|Cargo\.lock|rust-toolchain(\.toml)?)$'

log() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

sync_repo() {
  repo=$1
  dir="$WORK/src/$repo"
  if [ -d "$dir/.git" ]; then
    log "fetch $repo"
    git -C "$dir" fetch -q --depth 1 origin main
    git -C "$dir" reset -q --hard FETCH_HEAD
    git -C "$dir" clean -qfdx
  else
    log "clone $repo"
    mkdir -p "$(dirname "$dir")"
    git clone -q --depth 1 --branch main \
      "https://${GH_TOKEN:+$GH_TOKEN@}github.com/andrewthetechie/$repo.git" "$dir"
  fi
  git -C "$dir" rev-parse --short HEAD
}

assemble_context() {
  repo=$1 profile=$2
  src="$WORK/src/$repo"
  ctx="$WORK/ctx/$profile"
  rm -rf "$ctx"
  mkdir -p "$ctx/warm/repo"
  n=0
  # `git ls-files` rather than `find`: an untracked file is not part of the
  # repository's dependency contract and must not shape the cache.
  for f in $(git -C "$src" ls-files | grep -E "$MANIFESTS" || true); do
    mkdir -p "$ctx/warm/repo/$(dirname "$f")"
    cp "$src/$f" "$ctx/warm/repo/$f"
    n=$((n + 1))
  done
  [ "$n" -gt 0 ] || die "$repo: no manifest files matched; the cache would be empty"
  log "context $profile: $n manifest file(s)"
  printf '%s' "$ctx"
}

want() {
  [ $# -eq 0 ] && return 0
  for a in "$@"; do [ "$a" = "$PROFILE" ] && return 0; done
  return 1
}

mkdir -p "$WORK/src" "$WORK/ctx"
FAILED=''
for entry in $PROFILES; do
  PROFILE=${entry%%:*}
  rest=${entry#*:}
  REPO=${rest%%:*}
  TAG=${rest#*:}
  want "$@" || continue

  SHA=$(sync_repo "$REPO")
  CTX=$(assemble_context "$REPO" "$PROFILE")
  log "build $TAG  (from $REPO@$SHA)"
  if [ "$DRY_RUN" = "1" ]; then
    echo "  would run: docker build -f $HERE/Dockerfile.$PROFILE -t $TAG $CTX"
    continue
  fi
  # --label records what the cache was warmed from, so a stale image is
  # identifiable with `docker inspect` instead of guessed at.
  if docker build \
      -f "$HERE/Dockerfile.$PROFILE" \
      -t "$TAG" \
      --label "fabro.warmed-from=$REPO@$SHA" \
      --label "fabro.warmed-at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      "$CTX"; then
    log "ok $TAG"
  else
    log "FAILED $TAG"
    FAILED="$FAILED $TAG"
  fi
done

# A failed build leaves the PREVIOUS image in place under the same tag, so runs
# keep working on yesterday's cache rather than losing their environment. Report
# it loudly; do not let the exit status be mistaken for success.
if [ -n "$FAILED" ]; then
  printf '\nBuilds failed:%s\n' "$FAILED" >&2
  printf 'The previously built images are still tagged and still in use.\n' >&2
  exit 1
fi
log "all requested images built"
