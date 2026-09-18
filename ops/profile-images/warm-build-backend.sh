#!/bin/sh
# Pull every pyproject's `build-system.requires` into UV_CACHE_DIR.
#
# `uv sync --frozen --no-install-project` warms the dependency closure but not
# the build backend, because it never builds the project. `.fabro/setup.sh` runs
# plain `uv sync --frozen`, which does -- and then reaches for hatchling (or
# setuptools, or maturin) over the network on a machine whose whole point is that
# it should not need to.
#
# Reading the requirement out of the file beats hard-coding hatchling: the four
# repositories do not all use it, and a repository that switches backends would
# otherwise silently lose its warm cache.
set -eu
ROOT=${1:?usage: warm-build-backend.sh <context root>}

# Not every image has a system python3 -- the node-based profiles get theirs from
# uv's managed downloads -- so resolve an interpreter rather than assuming one.
# `uv run --no-project` ignores the pyproject we are standing next to, which would
# otherwise make uv try to resolve the project before running anything.
if command -v python3 >/dev/null 2>&1; then
  PY="python3"
elif command -v uv >/dev/null 2>&1; then
  PY="uv run --no-project --quiet python"
else
  echo "warm-build-backend: no python interpreter available" >&2
  exit 1
fi

REQS=$(find "$ROOT" -name pyproject.toml -print0 | xargs -0 -r $PY -c '
import sys, tomllib
seen = []
for path in sys.argv[1:]:
    with open(path, "rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError:
            continue
    for req in data.get("build-system", {}).get("requires", []):
        if req not in seen:
            seen.append(req)
print(" ".join(seen))
')

# PEP 517 backends ask for more at build time than they declare in
# `build-system.requires`. hatchling requests `editables` from
# get_requires_for_build_editable, and `uv sync` installs the project editable by
# default, so a cache warmed from the declared requires alone still reaches for
# PyPI -- proved offline in this image:
#
#   cause: No solution found when resolving: `hatchling`, `editables~=0.3`
#
# setuptools asks for `wheel` the same way. These three are cheap, and
# build-images.sh verifies the result offline rather than trusting the list.
DYNAMIC="editables setuptools wheel"

if [ -z "$REQS" ]; then
  echo "warm-build-backend: no build-system.requires found under $ROOT; warming dynamic hooks only"
  uv pip install --target /tmp/buildreqs $DYNAMIC
  exit 0
fi

REQS="$REQS $DYNAMIC"
echo "warm-build-backend: $REQS"

# --target keeps the install out of any real environment; the point is the side
# effect on UV_CACHE_DIR, and the caller throws the target directory away.
#
# This warms the WHEELS, not the resolution. `uv sync --frozen` re-resolves
# build-system.requires on every project build, and resolution reads the package
# index -- so with no network it still fails, on PyPI's simple page or on a PEP
# 658 `.whl.metadata` sidecar, even with every wheel already local. A
# --find-links directory does not change that: uv keeps preferring the configured
# index, and the only switch that would is UV_NO_INDEX, which would turn a
# lockfile that has drifted past the cache from "fetch the delta" into "fail".
#
# That is why build-images.sh checks the dependency closure offline and the
# repository's real setup.sh with the network up, rather than demanding both of
# one run. Sandbox runs have network; what the cache owes them is speed.
uv pip install --target /tmp/buildreqs $REQS
