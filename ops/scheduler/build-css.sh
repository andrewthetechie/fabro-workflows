#!/usr/bin/env bash
# build-css.sh — regenerate the scheduler's committed Tailwind stylesheet.
#
# The pages link /static/app.css, a prebuilt (minified) Tailwind build committed
# to the repo so the runtime image needs no node and the LAN serves itself. This
# script is the only thing that edits it. Run it after changing any template or
# adding/removing a class, then commit BOTH this script's output
# (src/fabro_scheduler/static/app.css) and any change to assets/tailwind.css or
# package.json/package-lock.json.
#
# Requires node/npm on the machine you run it from (the Mac); deps are pinned in
# the committed package-lock.json. `npm ci` first so a fresh checkout builds
# reproducibly.
set -euo pipefail
cd "$(dirname "$0")"

[ -d node_modules ] || npm ci

npx tailwindcss \
  -i assets/tailwind.css \
  -o src/fabro_scheduler/static/app.css \
  --minify

echo "wrote src/fabro_scheduler/static/app.css ($(wc -c < src/fabro_scheduler/static/app.css) bytes)"
