#!/bin/sh
# fabro-fire-backlog.sh — fire an explicit issue through the `backlog` workflow.
#
# Usage: fabro-fire-backlog.sh <owner/repo> <issue_number>
#
# The manual escape hatch for the coder scheduler (docs/scheduler everywhere).
# Since draft 10, a `backlog` run stops choosing its own work: the run's
# `issue_number` input is the only way to decide what it works, and there is no
# automation path left that supplies it — the backlog automations either enqueue
# through the scheduler (which passes `issue_number`) or are turned off. When the
# scheduler is down and an issue has to move, this is how one gets fired by hand.
#
# It makes three POSTs, in this order, and the third is not optional:
#   1. /workflow-versions   register the .fabro package (idempotent)
#   2. /runs                create the run from the version (NOT started)
#   3. /runs/<id>/start     actually launch it
#
# It does NOT enqueue or take a box: there is no lease. The run is fired with
# `coder_pool: "coders-a"` -- a pinned box. The both-boxes `coders` group and the
# LiteLLM provider behind it are retired (ADR 0007, task 04), so a hand fire has
# no untethered pool to aim at; it pins coders-a and must not run while the
# scheduler could also be dispatching to that box.
#
# This file lives in ops/ — the tree no automation reads — and is deployed to
# ~/bin/fabro-fire-backlog.sh on the host (AGENTS.md, *Deploying*). It is the
# reference implementation draft 09/10 were written against, alongside
# `.fabro/workflows/backlog/scripts/fire-pr-review.sh`, whose three-POST shape it
# shares.
#
# Output contract (do not break):
#   * the LAST line on stdout is the created run id, and nothing else;
#   * the LAST line on stderr is a one-line reason on every failure path.
#
# Env:
#   FABRO_API_URL    http://10.10.0.32:32276/api/v1   includes the /api/v1 prefix
#   FABRO_API_TOKEN  —                                required; never echoed
#   WORKFLOWS_REPO   andrewthetechie/fabro-workflows  source of the backlog package
#   WORKFLOWS_REF    main                             ref to register
#   DRY_RUN          1                                print payloads, send nothing
#   CHECK_ISSUE      1                                pre-flight the issue is open
#   GITHUB_TOKEN     —                                optional; raises the rate limit
#                                                     on the issue pre-flight
#
# DRY_RUN defaults to 1, following fire-pr-review.sh and fabro-branch-sweep.sh:
# this script creates real runs that push to real repositories. DRY_RUN=1 still
# reads the automation row and lists the package files, because those results are
# what the printed payloads contain. It sends no POST, so it creates no workflow
# version and no run. Consequently the printed RunIntent carries a placeholder
# `workflow_version_id`.
#
# Checks run cheapest-and-most-specific first — argument shape, then per-repo
# automation config, then the issue — so a repo with no backlog automation says so
# instead of reporting a 404 from GitHub.
#
# POSIX sh, not bash: no arrays, no `[[ ]]`, no `pipefail`. `set -e` is
# deliberately absent: every failure needs a readable one-line reason, and a bare
# `set -e` exit would produce a silent non-zero.
#
# No secret, host token or webhook URL belongs in this file. It is tracked in a
# public repo. Never add `set -x`.
set -u

FABRO_API_URL="${FABRO_API_URL:-http://10.10.0.32:32276/api/v1}"
WORKFLOWS_REPO="${WORKFLOWS_REPO:-andrewthetechie/fabro-workflows}"
WORKFLOWS_REF="${WORKFLOWS_REF:-main}"
DRY_RUN="${DRY_RUN:-1}"
CHECK_ISSUE="${CHECK_ISSUE:-1}"

# The version is rooted at `.fabro/`, not at the backlog package, and the entrypoint
# is package-qualified. Both are forced, not stylistic — see fire-pr-review.sh for
# the two failure modes they avoid (an unresolved `import="../_shared/..."` and a
# `workflow.toml` wrongly named as the entrypoint).
ENTRYPOINT="workflows/backlog/workflow.fabro"

die() { printf '%s\n' "$1" >&2; exit 1; }
warn() { printf 'warning: %s\n' "$1" >&2; }

# Collapse a server error body to a single readable line. Fabro returns either an
# RFC-7807-shaped {"errors":[{detail}]} or a flat {"detail"|"code"}.
body_reason() {
  jq -r 'if .errors and (.errors | length) > 0 then
           (.errors[0].detail // .errors[0].title // "error")
         elif .detail then .detail
         elif .error then (.error.detail // .error)
         elif .message then .message
         else "no detail in response" end' "$1" 2>/dev/null \
    | tr '\n\r\t' '   ' | cut -c1-300
}

# ---------------------------------------------------------------- arguments ----

[ "$#" -eq 2 ] || die "usage: fabro-fire-backlog.sh <owner/repo> <issue_number>"

REPO="$1"
ISSUE_NUM="$2"

case "$REPO" in
  */*) ;;
  *) die "repo must be owner/name, got '$REPO'" ;;
esac
case "$REPO" in
  *[!A-Za-z0-9._/-]*) die "repo '$REPO' is not a valid owner/name" ;;
esac
case "$ISSUE_NUM" in
  '' | *[!0-9]*) die "issue_number must be a positive integer, got '$ISSUE_NUM'" ;;
esac

[ -n "${FABRO_API_TOKEN:-}" ] || die "FABRO_API_TOKEN is not set"

for tool in jq curl git; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required but not on PATH"
done

case "$DRY_RUN" in 1) ;; 0) ;; *) die "DRY_RUN must be 0 or 1, got '$DRY_RUN'" ;; esac
case "$CHECK_ISSUE" in 1) ;; 0) ;; *) die "CHECK_ISSUE must be 0 or 1, got '$CHECK_ISSUE'" ;; esac
case "$WORKFLOWS_REPO" in
  *[!A-Za-z0-9._/-]*) die "WORKFLOWS_REPO '$WORKFLOWS_REPO' is not a valid owner/name" ;;
esac

tmp="$(mktemp -d)" || die "could not create a temporary directory"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

# ------------------------------------------------------ 1. resolve per-repo config ----

# Read per-repo configuration from the backlog-<repo> automation rather than
# hardcoding it: a fifth repo must need no change here, and environment_id is not
# optional in practice — omitting it selects `default` (buildpack-deps:noble),
# which cannot build two of the four repos.
auto_code="$(curl -sS -o "$tmp/autorows.json" -w '%{http_code}' \
  -H "Authorization: Bearer $FABRO_API_TOKEN" \
  "$FABRO_API_URL/automations" 2>/dev/null)" || auto_code=000
case "$auto_code" in
  200) ;;
  000) die "could not reach $FABRO_API_URL to list automations" ;;
  401) die "the API rejected FABRO_API_TOKEN (HTTP 401)" ;;
  *) die "could not list automations (HTTP $auto_code): $(body_reason "$tmp/autorows.json")" ;;
esac

jq --arg repo "$REPO" \
   '[.data[] | select(.workflow == "backlog" and .target.repo == $repo)]' \
   "$tmp/autorows.json" > "$tmp/automatch.json" \
  || die "could not parse the automations response"

match_count="$(jq 'length' "$tmp/automatch.json")"
case "$match_count" in
  0) die "no backlog automation targets $REPO (create one with ops/provision-server-state.sh)" ;;
  1) ;;
  *) die "$match_count backlog automations target $REPO; exactly one is required" ;;
esac

env_id="$(jq -r '.[0].environment_id // empty' "$tmp/automatch.json")"
target="$(jq -c '.[0].target' "$tmp/automatch.json")"
auto_id="$(jq -r '.[0].id' "$tmp/automatch.json")"

[ -n "$env_id" ] || die "the $auto_id automation has no environment_id; refusing to fall back to default"
case "$target" in
  "" | "null") die "the $auto_id automation has no target" ;;
esac

# ---------------------------------------------------- 2. pre-flight: issue exists ----

# A closed or missing issue otherwise produces a run that fails at `claim` (the
# graph fails closed, by design, rather than working a different issue) — but that
# wastes a run and a coder slot just to say so. Failing here instead is cheaper,
# and it is the same one-line reason either way. Only a definitive 404 is fatal: a
# 403 rate limit, a 5xx, or no network must never be reported as "the issue does
# not exist". A closed issue is a definitive, non-transient answer, so it is fatal.
if [ "$CHECK_ISSUE" = "1" ]; then
  issue_state=""
  issue_code=""
  # gh first: it is authenticated on the host, and its `state` field cleanly
  # distinguishes CLOSED (a definitive answer) from a transient read failure.
  # A missing issue makes gh fail, so that case falls through to curl below.
  if command -v gh >/dev/null 2>&1; then
    issue_state="$(gh issue view "$ISSUE_NUM" --repo "$REPO" --json state --jq .state 2>/dev/null)" \
      || issue_state=""
  fi
  case "$issue_state" in
    OPEN) ;;
    CLOSED) die "issue $ISSUE_NUM in $REPO is CLOSED; refusing to fire a run at a closed issue" ;;
  esac
  # Only a definitive 404 is fatal: a 403 rate limit, a 5xx, or no network must
  # never be reported as "the issue does not exist".
  if [ -z "$issue_state" ]; then
    issue_url="https://api.github.com/repos/$REPO/issues/$ISSUE_NUM"
    issue_code="$(curl -sS -o "$tmp/issue_resp.json" -w '%{http_code}' \
      -H 'Accept: application/vnd.github+json' "$issue_url" 2>/dev/null)" || issue_code=000
    case "$issue_code" in
      200) issue_state="$(jq -r '.state // empty' "$tmp/issue_resp.json" 2>/dev/null)"
           case "$issue_state" in
             OPEN) ;;
             CLOSED) die "issue $ISSUE_NUM in $REPO is CLOSED; refusing to fire a run at a closed issue" ;;
             "") warn "could not read the state of $REPO#$ISSUE_NUM; continuing" ;;
             *) warn "issue $REPO#$ISSUE_NUM is in an unexpected state '$issue_state'; continuing" ;;
           esac ;;
      404) die "no issue $ISSUE_NUM in $REPO (the repo may also be private or unreadable)" ;;
      000) warn "could not reach the GitHub API to confirm $REPO#$ISSUE_NUM; continuing" ;;
      *) warn "could not confirm $REPO#$ISSUE_NUM exists (HTTP $issue_code); continuing" ;;
    esac
  fi
else
  warn "CHECK_ISSUE=0: not confirming that $REPO#$ISSUE_NUM exists and is open"
fi

# -------------------------------------------------------- 3. fetch the tree ----

# The repo is public, so no credential is involved — which also means this works
# inside a sandbox regardless of the run's GITHUB_TOKEN scope.
git clone --depth 1 --branch "$WORKFLOWS_REF" \
    "https://github.com/$WORKFLOWS_REPO" "$tmp/wf" >/dev/null 2>&1 \
  || die "could not clone $WORKFLOWS_REPO at $WORKFLOWS_REF"

pkg="$tmp/wf/.fabro"
[ -f "$pkg/$ENTRYPOINT" ] \
  || die "$WORKFLOWS_REPO at $WORKFLOWS_REF has no $ENTRYPOINT"

# Enumerate from the directory, never a hardcoded list: a prompt added to backlog
# later would silently fail to register, and the run would then die at admission on
# an unresolved @prompts reference. Rooting the version at `.fabro/` is also what
# picks up `workflows/_shared/review-merge/review-merge.fabro`.
( cd "$pkg" && find . -type f | sed 's|^\./||' | sort ) > "$tmp/paths" \
  || die "could not enumerate the .fabro tree"

[ -s "$tmp/paths" ] || die "the .fabro tree at $WORKFLOWS_REF contains no files"
grep -qx "$ENTRYPOINT" "$tmp/paths" \
  || die "the .fabro tree at $WORKFLOWS_REF has no $ENTRYPOINT"

# jq -Rs does the JSON string encoding. Hand-rolled escaping would not survive this
# tree: it is full of \" and embedded jq programs.
: > "$tmp/entries.jsonl"
while IFS= read -r path; do
  jq -Rs --arg k "$path" '{key:$k, value:.}' "$pkg/$path" >> "$tmp/entries.jsonl" \
    || die "could not read $path from the .fabro tree"
done < "$tmp/paths"

# workflow_dependencies is {}: backlog declares no child workflow (the review and
# merge phase is spliced in at parse time, not a separate run).
jq -s --arg ep "$ENTRYPOINT" \
   '{entrypoint:$ep, files:(from_entries), workflow_dependencies:{}}' \
   "$tmp/entries.jsonl" > "$tmp/version.json" \
  || die "could not build the WorkflowVersion payload"

# ------------------------------------------------------ 4. register the version ----

# POST /workflow-versions is content-addressed and idempotent: unchanged package
# content returns the same identifier, so re-running costs one round trip and creates
# no duplicate. In DRY_RUN it is skipped, which is why the printed RunIntent carries a
# placeholder version id.
version_id=""
if [ "$DRY_RUN" = "1" ]; then
  version_id="<workflow_version_id from POST /workflow-versions>"
else
  vv_code="$(curl -sS -o "$tmp/vv_resp.json" -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $FABRO_API_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "@$tmp/version.json" \
    "$FABRO_API_URL/workflow-versions" 2>/dev/null)" || vv_code=000
  case "$vv_code" in
    201) ;;
    000) die "could not reach $FABRO_API_URL to register the workflow version" ;;
    401) die "the API rejected FABRO_API_TOKEN (HTTP 401)" ;;
    *) die "registering the workflow version failed (HTTP $vv_code): $(body_reason "$tmp/vv_resp.json")" ;;
  esac
  version_id="$(jq -r '.workflow_version_id // empty' "$tmp/vv_resp.json")"
  [ -n "$version_id" ] || die "the workflow-versions response carried no workflow_version_id"
fi

# ------------------------------------------------------------ 5. create the run ----

# args.inputs values that reach a POSIX `case` guard are JSON numbers, not strings:
# backlog's `claim` runs `case "$ISSUE" in *[!0-9]*)`. A number is the shape that
# cannot carry shell syntax. `coder_pool` is a string and pins `coders-a` (the
# retired both-boxes `coders` group left no untethered pool; ADR 0007, task 04).
# args.labels values are strings.
jq -n \
  --arg vvid "$version_id" \
  --argjson target "$target" \
  --argjson issue "$ISSUE_NUM" \
  --arg issue_s "$ISSUE_NUM" \
  --arg env_id "$env_id" \
  '{
    workflow_version_id: $vvid,
    target: $target,
    args: {
      inputs: { issue_number: $issue, coder_pool: "coders-a" },
      labels: { source: "manual", issue: $issue_s }
    },
    environment_id: $env_id
  }' > "$tmp/intent.json" || die "could not build the RunIntent payload"

if [ "$DRY_RUN" = "0" ]; then
  run_code="$(curl -sS -o "$tmp/run_resp.json" -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $FABRO_API_TOKEN" \
    -H 'Content-Type: application/json' \
    --data-binary "@$tmp/intent.json" \
    "$FABRO_API_URL/runs" 2>/dev/null)" || run_code=000
  case "$run_code" in
    201) ;;
    000) die "could not reach $FABRO_API_URL to create the run" ;;
    401) die "the API rejected FABRO_API_TOKEN (HTTP 401)" ;;
    *) die "creating the run failed (HTTP $run_code): $(body_reason "$tmp/run_resp.json")" ;;
  esac
  run_id="$(jq -r '.id // empty' "$tmp/run_resp.json")"
  [ -n "$run_id" ] || die "the runs response carried no run id"

  # POST /runs creates but does NOT start. RunIntent is documented as "a request to
  # create, but not start", and a created run sits in `submitted` with no stages
  # indefinitely. Without this call the fire creates runs that never execute, and
  # nothing anywhere reports an error.
  start_code="$(curl -sS -o "$tmp/start_resp.json" -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $FABRO_API_TOKEN" \
    "$FABRO_API_URL/runs/$run_id/start" 2>/dev/null)" || start_code=000
  case "$start_code" in
    200) ;;
    000) die "run $run_id was created but $FABRO_API_URL could not be reached to start it" ;;
    401) die "run $run_id was created but the API rejected FABRO_API_TOKEN (HTTP 401)" ;;
    404) die "run $run_id was created but the start call found no such run (HTTP 404)" ;;
    409) die "run $run_id was created but was not in submitted status, so it could not be started" ;;
    *) die "run $run_id was created but could not be started (HTTP $start_code): $(body_reason "$tmp/start_resp.json")" ;;
  esac
  run_status="$(jq -r '.lifecycle.status.kind // "unknown"' "$tmp/start_resp.json")"
else
  run_status="not started"
fi

# ---------------------------------------------------------------- 6. report ----

repo_branch="$(printf '%s' "$target" | jq -r '.branch // "?"')"

if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY_RUN=1: nothing will be sent. Re-run with DRY_RUN=0 to fire.\n'
  printf '\n== resolved ==\n'
  printf 'package  %s @ %s\n' "$WORKFLOWS_REPO" "$WORKFLOWS_REF"
  printf 'target   %s#%s  (%s)\n' "$REPO" "$ISSUE_NUM" "$repo_branch"
  printf 'env      %s   (from automation %s)\n' "$env_id" "$auto_id"
  printf 'entrypoint  %s\n' "$ENTRYPOINT"
  printf 'files       %s file(s):\n' "$(wc -l < "$tmp/paths" | tr -d ' ')"
  while IFS= read -r p; do
    printf '              %8s  %s\n' "$(wc -c < "$pkg/$p" | tr -d ' ')" "$p"
  done < "$tmp/paths"
  printf '\n== POST %s/workflow-versions ==\n' "$FABRO_API_URL"
  jq . "$tmp/version.json"
  printf '\n== POST %s/runs ==\n' "$FABRO_API_URL"
  jq . "$tmp/intent.json"
  # SC2016 is suppressed deliberately below: printing the literal variable name is the
  # point. The token value must never appear in this output.
  # shellcheck disable=SC2016
  printf '\n== calls that would be made ==\n'
  # shellcheck disable=SC2016
  printf 'register  curl -X POST -H "Authorization: Bearer $FABRO_API_TOKEN" \\\n'
  printf '            -H "Content-Type: application/json" \\\n'
  printf '            --data-binary @version.json %s/workflow-versions\n' "$FABRO_API_URL"
  # shellcheck disable=SC2016
  printf 'create    curl -X POST -H "Authorization: Bearer $FABRO_API_TOKEN" \\\n'
  printf '            -H "Content-Type: application/json" \\\n'
  printf '            --data-binary @intent.json %s/runs\n' "$FABRO_API_URL"
  # shellcheck disable=SC2016
  printf 'then      curl -X POST -H "Authorization: Bearer $FABRO_API_TOKEN" \\\n'
  printf '            %s/runs/<run id>/start     (create does not start)\n' "$FABRO_API_URL"
  printf '\nDRY_RUN=1: nothing was sent\n'
  exit 0
fi

printf 'verified  %s#%s is OPEN\n' "$REPO" "$ISSUE_NUM"
printf 'version   %s (registered)\n' "$version_id"
printf 'config    %s  environment=%s target=%s@%s\n' "$auto_id" "$env_id" "$REPO" "$repo_branch"
printf 'run       HTTP 201  status=%s\n' "$run_status"

# The operator reads this with `tail -1`, so the run id is the last line and nothing
# may follow it.
printf '%s\n' "$run_id"
