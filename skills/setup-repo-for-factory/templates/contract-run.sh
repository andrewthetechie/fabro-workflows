#!/usr/bin/env bash
# Runs on the fabro host, fed over ssh by `onboard.py contract-run`.
# Usage: bash -s -- <owner/repo> <ref> <image> <cpus> <memory>
#
# Proves what `build-images.sh` proves only for the one repository each image is warmed from: that
# this repository's contract passes from a fresh clone in its profile image, at the sandbox's size.
set -euo pipefail

repo=$1 ref=$2 image=$3 cpus=$4 memory=$5

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

gh repo clone "$repo" "$work/src" -- -q --depth 1 --branch "$ref"
echo "commit $(git -C "$work/src" rev-parse HEAD)"

docker image inspect "$image" >/dev/null

# Mounted read-only and copied inside, as build-images.sh does, so nothing writes to the clone.
docker run --rm --cpus="$cpus" -m "$memory" -v "$work/src":/src:ro --entrypoint bash "$image" -c '
  set -e
  cp -a /src /verify
  cd /verify
  git config --global --add safe.directory "*"
  echo "== fabro-io test-env --check"
  fabro-io test-env --check --root /verify
  echo "== ./.fabro/setup.sh"
  ./.fabro/setup.sh
  echo "== ./.fabro/ci.sh"
  ./.fabro/ci.sh
  echo "== contract passed"
'
