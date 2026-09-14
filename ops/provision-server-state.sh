#!/bin/sh
# provision-server-state.sh — recreate the fabro server's ENVIRONMENTS and
# AUTOMATIONS on a fresh host.
#
# These are server state, not config: they live in fabro's own store, NOT in
# settings.toml, so restoring settings.toml alone leaves a host that cannot run
# anything. This script closes that gap.
#
# Prerequisites:
#   - the fabro server is up and healthy
#   - the profile images are built (see profile-images/README.md)
#   - FABRO_API and FABRO_TOKEN are exported
#
#   FABRO_API=http://<HOST>:32276 FABRO_TOKEN=<dev token> ./provision-server-state.sh
#
# Idempotent-ish: creating an id that already exists returns a 409; that is
# reported and skipped rather than treated as a failure.
set -u

API="${FABRO_API:?set FABRO_API, e.g. http://<HOST>:32276}"
TOK="${FABRO_TOKEN:?set FABRO_TOKEN (never commit it)}"

post() { # $1=path  $2=json
  code=$(printf '%s' "$2" | curl -sS -o /tmp/_prov.out -w '%{http_code}' \
    -X POST "$API$1" -H "Authorization: Bearer $TOK" \
    -H 'Content-Type: application/json' --data-binary @-)
  case "$code" in
    2*) echo "  created  $1" ;;
    409) echo "  exists   $1 (skipped)" ;;
    *)   echo "  FAILED   $1 -> HTTP $code"; head -c 300 /tmp/_prov.out; echo ;;
  esac
  rm -f /tmp/_prov.out
}

echo "Environments"
# id | image | cpu | memory | repo label
for row in \
  'default|buildpack-deps:noble|2|4GB|' \
  'python|fabro-python:local|2|4GB|jelly-swipe' \
  'python-node|fabro-python-node:local|2|4GB|lawncare-saas' \
  'ts|fabro-ts:local|2|4GB|womens-fantasy-sports' \
  'rust-node|fabro-rust-node:local|4|8GB|writers-app'
do
  id=$(echo "$row" | cut -d'|' -f1)
  img=$(echo "$row" | cut -d'|' -f2)
  cpu=$(echo "$row" | cut -d'|' -f3)
  mem=$(echo "$row" | cut -d'|' -f4)
  repo=$(echo "$row" | cut -d'|' -f5)
  if [ -n "$repo" ]; then labels="{\"repo\":\"$repo\"}"; else labels="{}"; fi
  post /api/v1/environments "{
    \"id\": \"$id\",
    \"provider\": \"docker\",
    \"image\": {\"docker\": \"$img\"},
    \"resources\": {\"cpu\": $cpu, \"memory\": \"$mem\"},
    \"network\": {\"mode\": \"allow_all\", \"allow\": []},
    \"lifecycle\": {\"preserve\": false, \"stop_on_terminal\": true},
    \"labels\": $labels
  }"
done

echo "Automations"
# id | target repo | environment id
for row in \
  'backlog-jelly-swipe|andrewthetechie/jelly-swipe|python' \
  'backlog-lawncare-saas|andrewthetechie/lawncare-saas|python-node' \
  'backlog-womens-fantasy-sports|andrewthetechie/womens-fantasy-sports|ts' \
  'backlog-writers-app|andrewthetechie/writers-app|rust-node'
do
  id=$(echo "$row" | cut -d'|' -f1)
  repo=$(echo "$row" | cut -d'|' -f2)
  env=$(echo "$row" | cut -d'|' -f3)
  # The schedule trigger is created DISABLED on purpose: the operator enables it
  # when the loop should run unattended. jelly-swipe additionally keeps an
  # always-enabled manual/api trigger for test fires.
  extra=''
  if [ "$id" = "backlog-jelly-swipe" ]; then
    extra='{"type":"api","id":"manual","enabled":true},'
  fi
  post /api/v1/automations "{
    \"id\": \"$id\",
    \"name\": \"$id\",
    \"environment_id\": \"$env\",
    \"target\": {\"kind\": \"git\", \"repo\": \"$repo\", \"branch\": \"main\"},
    \"workflow\": \"backlog\",
    \"workflow_source\": {\"repo\": \"andrewthetechie/fabro-workflows\", \"branch\": \"main\"},
    \"triggers\": [ $extra
      {\"type\":\"schedule\",\"id\":\"every-15m\",\"enabled\":false,\"expression\":\"*/15 * * * *\"}
    ]
  }"
done

echo
echo "Verify:"
echo "  curl -sS -H \"Authorization: Bearer \$FABRO_TOKEN\" \$FABRO_API/api/v1/environments"
echo "  curl -sS -H \"Authorization: Bearer \$FABRO_TOKEN\" \$FABRO_API/api/v1/automations"
