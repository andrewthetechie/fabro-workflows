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

# Profiles whose warm step needs the repository's tracked SOURCE as well, copied
# to warm/src/. Only cargo does: `cargo fetch` parses every target a Cargo.toml
# declares and refuses the manifest when their default paths are missing --
# writers-app's produced "can't find `summarize` test at `tests/summarize.rs`"
# and 19 more like it, and a stubbed tree is a losing game against 20 declared
# targets. The Dockerfiles that use warm/src/ do so in a BUILDER stage and copy
# only the warmed registry forward, so no source reaches the shipped image.
NEEDS_SOURCE='rust-node'

# fabro-io (ADR 0016, docs/stage-io): built ONCE in a container from the crate at
# ../fabro-io, then copied to every context root beside fabro-code. The binary is
# x86_64-unknown-linux-musl, statically linked, no runtime dependencies.
CRATE=$(cd "$HERE/.." && pwd)/fabro-io
FABRO_IO_VERSION=$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$CRATE/Cargo.toml" | head -1)
FABRO_IO_BIN="$WORK/fabro-io-bin"
# The Stage-manifest output schemas, which `cargo test` compiles (schema_tests.rs). On
# the host they are synced beside the crate to ~/fabro-io-schemas; in a repository
# checkout they are .fabro/workflows/_io/schemas. build_fabro_io fails when neither
# exists: a skipped schema test would let a schema that does not compile ship.
SCHEMAS=$(cd "$HERE/.." && pwd)/fabro-io-schemas
[ -d "$SCHEMAS" ] || SCHEMAS=$(cd "$HERE/../.." && pwd)/.fabro/workflows/_io/schemas

# To stderr, not stdout: sync_repo and assemble_context are read with $(...)
# and anything they print on stdout becomes part of the sha or the context path.
log() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
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
  for want_src in $NEEDS_SOURCE; do
    [ "$want_src" = "$profile" ] || continue
    mkdir -p "$ctx/warm/src"
    git -C "$src" archive --format=tar HEAD | tar -x -C "$ctx/warm/src"
    log "context $profile: + tracked source (cargo needs it to parse the manifest)"
  done
  cp "$HERE/warm-build-backend.sh" "$ctx/warm/warm-build-backend.sh"
  # Sits at the context root, not under warm/, because the Dockerfiles COPY it to
  # /usr/local/bin rather than running it during the warm step.
  cp "$HERE/fabro-pg-ensure.sh" "$ctx/fabro-pg-ensure.sh"
  # The fabro-code wrapper (docs/code-context C2) also lands at the context root
  # so every Dockerfile can `COPY fabro-code /usr/local/bin/fabro-code`.
  cp "$HERE/fabro-code" "$ctx/fabro-code"
  # fabro-io (ADR 0016) lands at the context root like fabro-code, so every
  # Dockerfile can `COPY fabro-io /usr/local/bin/fabro-io`.
  cp "$FABRO_IO_BIN" "$ctx/fabro-io"
  log "context $profile: $n manifest file(s)"
  printf '%s' "$ctx"
}

# Two checks, because they answer two different questions and only one of them
# can be asked with the network down.
#
# CACHE (offline): does the baked cache cover this repository's dependency
# closure? Run per manifest the repository actually has. This is the check that
# fails when a lockfile has moved past what was warmed.
#
# It deliberately stops at the dependency closure. `uv sync --frozen` also builds
# the project, and uv re-resolves build-system.requires every time it does --
# resolution reads the package index, so it reaches for PyPI's simple page or a
# PEP 658 `.whl.metadata` sidecar even when every wheel is already local. Nothing
# short of UV_NO_INDEX changes that, and UV_NO_INDEX would break the delta fetch
# that makes a stale cache merely slow instead of fatal. Sandbox runs have a
# network; what the cache owes them is speed, not independence.
#
# CONTRACT (network up): does the repository's own .fabro/setup.sh actually run
# in this image? This is the check that would have caught jelly-swipe's ci.sh
# calling `npm ci` in an image with no npm -- the failure that matters, because
# it turns every task into a rework-ladder loop against something no code change
# can fix.
#
# SKIP_VERIFY=1 disables both.
verify_image() {
  repo=$1 tag=$2
  [ "${SKIP_VERIFY:-0}" = "1" ] && { log "verify $tag: skipped"; return 0; }
  src="$WORK/src/$repo"
  ok=0

  log "verify $tag: cache check, offline"
  # Mounted read-only and copied inside, so a check can never write to the clone
  # the next build reuses.
  if docker run --rm --network=none -v "$src":/src:ro --entrypoint bash "$tag" -c '
        set -eu
        cp -a /src /verify && cd /verify
        ran=0
        for d in . backend site frontend; do
          [ -d "$d" ] || continue
          ( cd "$d"
            if [ -f uv.lock ];          then echo "-- uv  $d";   uv sync --frozen --no-install-workspace >/dev/null; fi
            # Both files, not just the lockfile: jelly-swipe carries a root
            # package-lock.json with no package.json beside it, and `npm ci`
            # there fails ENOENT on a repository that is perfectly healthy.
            if [ -f package-lock.json ] && [ -f package.json ]; then echo "-- npm $d"; npm ci --ignore-scripts >/dev/null; rm -rf node_modules; fi
            if [ -f bun.lock ] && [ -f package.json ]; then echo "-- bun $d"; bun install --frozen-lockfile --ignore-scripts >/dev/null; rm -rf node_modules; fi
            if [ -f Cargo.lock ];       then echo "-- cargo $d"; cargo fetch >/dev/null; fi
          )
          ran=1
        done
        [ "$ran" = 1 ] || { echo "no install root found"; exit 1; }
      ' >"$WORK/verify-cache-$repo.log" 2>&1; then
    log "verify $tag: cache OK ($(grep -c "^-- " "$WORK/verify-cache-$repo.log" || echo 0) install root(s), no network)"
  else
    log "verify $tag: CACHE CHECK FAILED -- the baked cache does not cover this repo"
    tail -20 "$WORK/verify-cache-$repo.log" >&2
    ok=1
  fi

  # LOCALE (offline): the sandbox runs in UTF-8, and a baked cluster is UTF8.
  # Without it psycopg 3 reads an SQL_ASCII cluster's text as bytes, and every
  # task's validate fails on a fault no code change can fix (see common.env.md).
  log "verify $tag: locale check, offline"
  if docker run --rm --network=none --entrypoint bash "$tag" -c '
        set -eu
        [ "${LANG:-}" = C.UTF-8 ] && [ "${LC_ALL:-}" = C.UTF-8 ] || { echo "LANG=${LANG:-} LC_ALL=${LC_ALL:-}"; exit 1; }
        [ "${PGCLIENTENCODING:-}" = UTF8 ] || { echo "PGCLIENTENCODING=${PGCLIENTENCODING:-}"; exit 1; }
        if command -v fabro-pg-ensure >/dev/null 2>&1; then
          fabro-pg-ensure >/dev/null
          enc=$(su postgres -s /bin/sh -c "psql -p ${PGPORT:-5432} -d test -tAc \"SHOW server_encoding\"")
          echo "server_encoding=$enc"
          [ "$enc" = UTF8 ]
        else
          echo "no postgres in this image"
        fi
      ' >"$WORK/verify-locale-$repo.log" 2>&1; then
    log "verify $tag: locale OK ($(tail -1 "$WORK/verify-locale-$repo.log"))"
  else
    log "verify $tag: LOCALE CHECK FAILED"
    tail -20 "$WORK/verify-locale-$repo.log" >&2
    ok=1
  fi

  # FABRO-IO (offline): the Stage-I/O binary every agent stage depends on. A broken
  # binary blocks every stage in every run (ADR 0016, Consequences), so `fabro-io
  # version` must print the expected version and `fabro-io stage` must exit 0 under
  # the io-stage probe manifest (min_binary below the crate, empty stages).
  log "verify $tag: fabro-io gate, offline"
  if docker run --rm --network=none --entrypoint bash "$tag" -c '
        set -eu
        [ "$(fabro-io version)" = "fabro-io '"$FABRO_IO_VERSION"'" ] \
          || { echo "version mismatch: $(fabro-io version)"; exit 1; }
        FABRO_NODE_ID=probe \
          FABRO_IO_MANIFEST="{\"version\":1,\"min_binary\":\"0.0.0\",\"stages\":{}}" \
          fabro-io stage
      ' >"$WORK/verify-fabroio-$repo.log" 2>&1; then
    log "verify $tag: fabro-io OK ($FABRO_IO_VERSION)"
  else
    log "verify $tag: FABRO-IO CHECK FAILED"
    tail -20 "$WORK/verify-fabroio-$repo.log" >&2
    ok=1
  fi

  # TOOLS (offline): `rg`, which pebble's prompt tells every agent to prefer, and
  # real Node wherever a `node` is on PATH. fabro-ts once had no Node, so vitest ran
  # on Bun's runtime and every womens-fantasy-sports validate failed in worker
  # setup (docs/coder-tweaks result-2026-10-03.txt). fabro-ts must have Node.
  log "verify $tag: tools check, offline"
  if docker run --rm --network=none --entrypoint bash "$tag" -c '
        set -eu
        rg --version | head -1
        if [ "'"$tag"'" = fabro-ts:local ]; then command -v node >/dev/null || { echo "no node"; exit 1; }; fi
        if command -v node >/dev/null; then
          node -e "process.exit(process.versions.bun ? 1 : 0)" || { echo "node is a bun shim"; exit 1; }
          echo "node $(node -v)"
        fi
      ' >"$WORK/verify-tools-$repo.log" 2>&1; then
    log "verify $tag: tools OK ($(tr '\n' ' ' <"$WORK/verify-tools-$repo.log"))"
  else
    log "verify $tag: TOOLS CHECK FAILED"
    tail -20 "$WORK/verify-tools-$repo.log" >&2
    ok=1
  fi

  if [ -x "$src/.fabro/setup.sh" ]; then
    log "verify $tag: contract check, running $repo/.fabro/setup.sh"
    t0=$(date +%s)
    if docker run --rm -v "$src":/src:ro --entrypoint bash "$tag" -c '
          set -e
          cp -a /src /verify && cd /verify
          ./.fabro/setup.sh
        ' >"$WORK/verify-setup-$repo.log" 2>&1; then
      log "verify $tag: .fabro/setup.sh OK in $(( $(date +%s) - t0 ))s"
    else
      log "verify $tag: .fabro/setup.sh FAILED -- this image cannot run the repo contract"
      tail -20 "$WORK/verify-setup-$repo.log" >&2
      ok=1
    fi
  else
    log "verify $tag: $repo has no .fabro/setup.sh to check"
  fi

  # INDEX (offline): does codegraph build an index of this repository in a 2 CPU
  # / 4 GB sandbox, and does the build stay out of git (ADR 0014 D5)? This is the
  # check that would catch a codegraph version or schema regression the moment it
  # hit the image, before a run's prep builds an empty or broken index. It runs
  # against the REAL repository (not the manifest-only context), in a throwaway
  # copy, with the same resource cap the bench used.
  #
  # The last test proves the only new path is `.codegraph/` -- git status sees
  # exactly one porcelain line naming codegraph. Nothing may write into AGENTS.md
  # or anywhere else.
  log "verify $tag: index check, offline"
  t0=$(date +%s)
  if docker run --rm --network=none --cpus=2 -m 4g -v "$src":/src:ro \
      --entrypoint bash "$tag" -c '
        set -eu
        cp -a /src /verify && cd /verify
        # The verify container runs as root and /src is an andrew-owned clone, so
        # after `cp -a` the copy is also uid 1000 -- git refuses it as "dubious
        # ownership" unless told it is safe. The real sandbox runs the repo owned
        # by its own user, so this is a build-time artifact only.
        git config --global --add safe.directory "*"
        codegraph init --yes </dev/null \
          && codegraph status | grep -q "up to date" \
          && [ "$(git status --porcelain | grep -c codegraph)" = 1 ]
      ' >"$WORK/verify-index-$repo.log" 2>&1; then
    log "verify $tag: index OK in $(( $(date +%s) - t0 ))s (only .codegraph/ is new)"
  else
    log "verify $tag: INDEX CHECK FAILED -- codegraph cannot index this tree cleanly"
    tail -20 "$WORK/verify-index-$repo.log" >&2
    ok=1
  fi

  return $ok
}

want() {
  [ $# -eq 0 ] && return 0
  for a in "$@"; do [ "$a" = "$PROFILE" ] && return 0; done
  return 1
}

# Build fabro-io once, for all four images, before any profile builds. `cargo test`
# runs first -- a failing test stops the build and leaves yesterday's images in use.
# Named volumes for the registry and the target dir make the nightly build incremental.
build_fabro_io() {
  log "build fabro-io $FABRO_IO_VERSION (crate $CRATE, schemas $SCHEMAS)"
  if [ ! -d "$SCHEMAS" ]; then
    log "fatal: no output schemas at $SCHEMAS; sync .fabro/workflows/_io/schemas/ to ~/fabro-io-schemas/"
    exit 1
  fi
  docker volume create fabro-io-cargo >/dev/null 2>&1 || true
  docker volume create fabro-io-target >/dev/null 2>&1 || true
  if ! docker run --rm \
      -v "$CRATE":/src:ro \
      -v "$SCHEMAS":/schemas:ro \
      -e FABRO_IO_SCHEMAS_DIR=/schemas \
      -v fabro-io-cargo:/cargo \
      -v fabro-io-target:/target \
      -e CARGO_HOME=/cargo -e CARGO_TARGET_DIR=/target \
      -w /src \
      rust:1.98.1-trixie sh -c '
        set -eu
        rustup target add x86_64-unknown-linux-musl >/dev/null
        apt-get update >/dev/null
        apt-get install -y musl-tools >/dev/null
        cargo test --locked
        cargo build --release --locked --target x86_64-unknown-linux-musl
      ' >"$WORK/fabro-io-build.log" 2>&1; then
    log "fatal: fabro-io tests/build failed (see $WORK/fabro-io-build.log); aborting before any image build"
    tail -20 "$WORK/fabro-io-build.log" >&2
    exit 1
  fi
  docker run --rm -v fabro-io-target:/target:ro -v "$WORK":/out alpine sh -c \
    'cp /target/x86_64-unknown-linux-musl/release/fabro-io /out/fabro-io-bin && chmod +x /out/fabro-io-bin'
  log "fabro-io $FABRO_IO_VERSION built: $FABRO_IO_BIN"
}

mkdir -p "$WORK/src" "$WORK/ctx"
build_fabro_io
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
      "$CTX" && verify_image "$REPO" "$TAG"; then
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
