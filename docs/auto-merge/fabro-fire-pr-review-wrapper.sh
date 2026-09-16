#!/bin/sh
# ~/bin/fabro-fire-pr-review.sh
#
# Do not edit, and do not replace this with a copy of the real script.
#
# This wrapper exists so there is exactly one copy of fire-pr-review.sh: the tracked
# one at .fabro/workflows/backlog/scripts/fire-pr-review.sh. It runs whatever is on
# origin/main, which is the same thing backlog's trigger_review node clones and runs,
# so the operator's manual fire and the automated one can never diverge.
#
# `git show origin/main:<path>` reads the blob without touching the working tree, so
# this is safe against uncommitted work in the deploy checkout — which AGENTS.md names
# as a live hazard between the two checkouts of this repository.
#
# Env:
#   FABRO_WF_CHECKOUT   ~/.fabro-deploy/fabro-workflows   the deploy checkout to read
# Everything else (FABRO_API_TOKEN, DRY_RUN, CHECK_PR, ...) belongs to the real script
# and is passed through untouched.
set -eu

R="${FABRO_WF_CHECKOUT:-$HOME/.fabro-deploy/fabro-workflows}"
P=.fabro/workflows/backlog/scripts/fire-pr-review.sh

[ -d "$R/.git" ] || { echo "no fabro-workflows checkout at $R" >&2; exit 1; }

# A failed fetch warns and continues: firing a review is not worth blocking on a
# network blip, and the last-known origin/main is still closer to correct than a copy.
git -C "$R" fetch --quiet origin main \
  || echo "warning: could not fetch origin/main; using the last-known copy" >&2

T="$(mktemp)"
trap 'rm -f "$T"' EXIT HUP INT TERM
git -C "$R" show "origin/main:$P" > "$T" \
  || { echo "could not read $P from origin/main in $R" >&2; exit 1; }

# exec, not a pipe: the real script reads $1 and $2, and piping would take its stdin.
exec sh "$T" "$@"
