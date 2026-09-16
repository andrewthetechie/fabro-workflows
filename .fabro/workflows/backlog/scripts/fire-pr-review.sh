#!/bin/sh
# fire-pr-review.sh — fire a `pr-review` run against a named pull request.
#
# Usage: fire-pr-review.sh <owner/repo> <pr_number>
#
# Two jobs in one file:
#   * the operator's manual-fire tool, replacing the AGENTS.md curl recipe that
#     finding 1 disproved (POST /automations/{id}/runs ignores its request body);
#   * the reference implementation the backlog `trigger_review` node runs, and the
#     test harness for proving the API sequence against a real PR.
#
# It makes three POSTs, in this order, and the third is not optional:
#   1. /workflow-versions   register the pr-review package (idempotent)
#   2. /runs                create the run from the version (NOT started)
#   3. /runs/<id>/start     actually launch it
#
# Why it lives here and not in ops/: task 04's node executes it out of the clone it
# already makes, so a live automation reads this file. ops/ is defined as the tree no
# automation reads, which is what makes editing ops/ safe. Deploy it to the host as
# ~/bin/fabro-fire-pr-review.sh for manual use — same split as discord-notify.sh.
#
# Output contract (relied on by the graph node, do not break):
#   * the LAST line on stdout is the created run id, and nothing else;
#   * the LAST line on stderr is a one-line reason on every failure path.
# The node does `tail -1` on each and turns the stderr line into a Discord alert.
#
# The per-repo auto-merge kill switch is read out of the `pr-review-<repo>`
# automation's `description`, and sent as `auto_merge` in `args.inputs`. It is stored
# there because fabro 0.354.0-nightly.0 has no `labels` field on an automation
# (`POST` with one returns 422 `unknown field 'labels'`) and no `PATCH
# /automations/{id}` (405); `description` is the only free-form writable field on the
# row. `ops/fabro-auto-merge-switch.sh <repo> on|off` performs the GET+PUT.
#
# Reading it: an absent token means ON, which is operator decision 10 (auto-merge is
# the default and the switches turn it off); a token that is present and not exactly
# `true` means OFF. Nothing here guesses: unparseable fails closed.
#
# Env:
#   FABRO_API_URL    http://10.10.0.32:32276/api/v1   includes the /api/v1 prefix
#   FABRO_API_TOKEN  —                                required; never echoed
#   WORKFLOWS_REPO   andrewthetechie/fabro-workflows  source of the pr-review package
#   WORKFLOWS_REF    main                             ref to register
#   PARENT_RUN_ID    empty                            set by the graph node only
#   DRY_RUN          1                                print payloads, send nothing
#   ISSUE_NUMBER     empty                            optional; adds an `issue` label
#   CHECK_PR         1                                pre-flight the PR exists
#   GITHUB_TOKEN     —                                optional; raises the rate limit
#                                                     on the PR pre-flight
#
# DRY_RUN defaults to 1, following fabro-branch-sweep.sh: this script creates real
# runs that push to real repositories. DRY_RUN=1 still reads the automation row and
# lists the package files, because those results are what the printed payloads
# contain. It sends no POST, so it creates no workflow version and no run.
# Consequently the printed RunIntent carries a placeholder `workflow_version_id`.
#
# Checks run cheapest-and-most-specific first — argument shape, then per-repo
# automation config, then the pull request, then the package — so a repo with no
# pr-review automation says so instead of reporting a 404 from GitHub.
#
# POSIX sh, not bash: task 04's node invokes this as `sh fire-pr-review.sh ...` inside
# the sandbox, where the interpreter is whatever /bin/sh is — a `#!/bin/bash` shebang
# would be ignored. No arrays, no `[[ ]]`, no `pipefail`. `set -e` is deliberately
# absent: every failure needs a readable one-line reason, and a bare `set -e` exit
# would produce a silent non-zero.
#
# No secret, host token or webhook URL belongs in this file. It is tracked in a public
# repo. Never add `set -x`.
set -u

FABRO_API_URL="${FABRO_API_URL:-http://10.10.0.32:32276/api/v1}"
WORKFLOWS_REPO="${WORKFLOWS_REPO:-andrewthetechie/fabro-workflows}"
WORKFLOWS_REF="${WORKFLOWS_REF:-main}"
PARENT_RUN_ID="${PARENT_RUN_ID:-}"
DRY_RUN="${DRY_RUN:-1}"
ISSUE_NUMBER="${ISSUE_NUMBER:-}"
CHECK_PR="${CHECK_PR:-1}"

# The pr-review package's entrypoint, relative to the package root, which is also the
# key it must appear under in `files`. Verified against the live server: a
# package-relative key returns 201, while `pr-review/workflow.fabro` and the
# repo-relative path both return 422 `entrypoint ... is not present in workflow files`.
ENTRYPOINT="workflow.fabro"

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

[ "$#" -eq 2 ] || die "usage: fire-pr-review.sh <owner/repo> <pr_number>"

REPO="$1"
PR_NUM="$2"

case "$REPO" in
  */*) ;;
  *) die "repo must be owner/name, got '$REPO'" ;;
esac
case "$REPO" in
  *[!A-Za-z0-9._/-]*) die "repo '$REPO' is not a valid owner/name" ;;
esac
case "$PR_NUM" in
  '' | *[!0-9]*) die "pr_number must be a positive integer, got '$PR_NUM'" ;;
esac

[ -n "${FABRO_API_TOKEN:-}" ] || die "FABRO_API_TOKEN is not set"

for tool in jq curl git; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required but not on PATH"
done

case "$DRY_RUN" in 1) ;; 0) ;; *) die "DRY_RUN must be 0 or 1, got '$DRY_RUN'" ;; esac
case "$CHECK_PR" in 1) ;; 0) ;; *) die "CHECK_PR must be 0 or 1, got '$CHECK_PR'" ;; esac
case "$WORKFLOWS_REPO" in
  *[!A-Za-z0-9._/-]*) die "WORKFLOWS_REPO '$WORKFLOWS_REPO' is not a valid owner/name" ;;
esac

# An optional issue label. The overview asks for the originating issue number in
# `args.labels` so lineage degrades to something greppable when parent_id has to be
# omitted. Ignored rather than fatal when it is not numeric, because a manual fire has
# no issue.
case "$ISSUE_NUMBER" in
  '' | *[!0-9]*) ISSUE_NUMBER="" ;;
esac

# The parent id is derived from backlog's fabro/run/<run_id> branch rather than
# declared, so its shape is validated and it is omitted rather than rejected: sending a
# malformed parent_id fails the whole create. Crockford base32 — no I, L, O or U.
VALID_PARENT=""
if [ -n "$PARENT_RUN_ID" ]; then
  if [ "${#PARENT_RUN_ID}" -eq 26 ]; then
    case "$PARENT_RUN_ID" in
      *[!0-9A-HJKMNP-TV-Z]*) warn "PARENT_RUN_ID '$PARENT_RUN_ID' is not a ULID; omitting parent_id" ;;
      *) VALID_PARENT="$PARENT_RUN_ID" ;;
    esac
  else
    warn "PARENT_RUN_ID '$PARENT_RUN_ID' is not 26 characters; omitting parent_id"
  fi
fi

tmp="$(mktemp -d)" || die "could not create a temporary directory"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

# ------------------------------------------------------ 1. resolve per-repo config ----

# Read per-repo configuration from the pr-review-<repo> automation rather than
# hardcoding it: a fifth repo must need no change here, and environment_id is not
# optional in practice — omitting it selects `default` (buildpack-deps:noble), which
# cannot build two of the four repos. The pr-review automations are config-only: they
# are read here and never fired.
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
   '[.data[] | select(.workflow == "pr-review" and .target.repo == $repo)]' \
   "$tmp/autorows.json" > "$tmp/automatch.json" \
  || die "could not parse the automations response"

match_count="$(jq 'length' "$tmp/automatch.json")"
case "$match_count" in
  0) die "no pr-review automation targets $REPO (create one with ops/provision-server-state.sh)" ;;
  1) ;;
  *) die "$match_count pr-review automations target $REPO; exactly one is required" ;;
esac

env_id="$(jq -r '.[0].environment_id // empty' "$tmp/automatch.json")"
target="$(jq -c '.[0].target' "$tmp/automatch.json")"
auto_id="$(jq -r '.[0].id' "$tmp/automatch.json")"

[ -n "$env_id" ] || die "the $auto_id automation has no environment_id; refusing to fall back to default"
case "$target" in
  "" | "null") die "the $auto_id automation has no target" ;;
esac

# The per-repo kill switch. Three outcomes, not two:
#   * no mention of `auto_merge` anywhere -> ON   (operator decision 10: an absent
#     token means nobody has touched this switch, and auto-merge is the default)
#   * `auto_merge=true`                   -> ON
#   * anything else that mentions it      -> OFF  (malformed, empty, `0`, `TRUE`: a
#     token that does not parse is a switch nobody can trust, so it fails closed)
# `tail -1` takes the last token so a human editing the description cannot resurrect an
# earlier value; `cut -d= -f2` on an empty grep output is empty.
desc="$(jq -r '.[0].description // ""' "$tmp/automatch.json")"
am=1
switch_tok=""
case "$desc" in
  *auto_merge*)
    switch_tok="$(printf '%s' "$desc" | grep -o 'auto_merge=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)"
    case "$switch_tok" in
      true) am=1 ;;
      *) am=0 ;;
    esac
    ;;
esac

# ---------------------------------------------------- 2. pre-flight: PR exists ----

# A PR number that does not exist otherwise produces a run that fails minutes later
# after burning model time. Only a definitive 404 is fatal: a 403 rate limit, a 5xx, or
# no network must never be reported as "the PR does not exist".
#
# `gh` first: it is authenticated on the host and in every sandbox (fabro mints
# GITHUB_TOKEN, which gh picks up), and it keeps the credential out of argv. Its
# `--include` flag prints the status line first and still exits 0 on a 404, so the
# status is read off that line rather than from the exit code. Bare curl is the
# fallback, and unauthenticated curl rate-limits at 60/hr from a shared address —
# which is exactly the 403 that must not be mistaken for a missing PR.
if [ "$CHECK_PR" = "1" ]; then
  pr_code=""
  if command -v gh >/dev/null 2>&1; then
    pr_code="$(gh api --include "repos/$REPO/pulls/$PR_NUM" 2>/dev/null \
      | head -1 | awk '{print $2}')" || pr_code=""
  fi
  if [ -z "$pr_code" ]; then
    pr_url="https://api.github.com/repos/$REPO/pulls/$PR_NUM"
    if [ -n "${GITHUB_TOKEN:-}" ]; then
      pr_code="$(curl -sS -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H 'Accept: application/vnd.github+json' "$pr_url" 2>/dev/null)" || pr_code=000
    else
      pr_code="$(curl -sS -o /dev/null -w '%{http_code}' \
        -H 'Accept: application/vnd.github+json' "$pr_url" 2>/dev/null)" || pr_code=000
    fi
  fi
  case "$pr_code" in
    200) ;;
    404) die "no pull request $PR_NUM in $REPO (the repo may also be private or unreadable)" ;;
    000) warn "could not reach the GitHub API to confirm $REPO#$PR_NUM exists; continuing" ;;
    *) warn "could not confirm $REPO#$PR_NUM exists (HTTP $pr_code); continuing" ;;
  esac
else
  warn "CHECK_PR=0: not confirming that $REPO#$PR_NUM exists"
fi

# ------------------------------------------------------- 3. fetch the package ----

# The repo is public, so no credential is involved — which also means this works
# inside a sandbox regardless of the run's GITHUB_TOKEN scope.
git clone --depth 1 --branch "$WORKFLOWS_REF" \
    "https://github.com/$WORKFLOWS_REPO" "$tmp/wf" >/dev/null 2>&1 \
  || die "could not clone $WORKFLOWS_REPO at $WORKFLOWS_REF"

pkg="$tmp/wf/.fabro/workflows/pr-review"
[ -d "$pkg" ] || die "$WORKFLOWS_REPO at $WORKFLOWS_REF has no .fabro/workflows/pr-review package"

# Enumerate from the directory, never a hardcoded list: a prompt added to pr-review
# later would silently fail to register, and the run would then die at admission on an
# unresolved @prompts reference.
( cd "$pkg" && find . -type f | sed 's|^\./||' | sort ) > "$tmp/paths" \
  || die "could not enumerate the pr-review package"

[ -s "$tmp/paths" ] || die "the pr-review package at $WORKFLOWS_REF contains no files"
grep -qx "$ENTRYPOINT" "$tmp/paths" \
  || die "the pr-review package at $WORKFLOWS_REF has no $ENTRYPOINT"

# jq -Rs does the JSON string encoding. Hand-rolled escaping would not survive this
# package: it is full of \" and embedded jq programs.
: > "$tmp/entries.jsonl"
while IFS= read -r path; do
  jq -Rs --arg k "$path" '{key:$k, value:.}' "$pkg/$path" >> "$tmp/entries.jsonl" \
    || die "could not read $path from the pr-review package"
done < "$tmp/paths"

# workflow_dependencies is {}: pr-review declares no child workflow.
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
# pr-review's validate_input runs `case "$PR" in *[!0-9]*)` and `case "$AM" in 1)`.
# A number is the shape that cannot carry shell syntax. args.labels values are strings.
jq -n \
  --arg vvid "$version_id" \
  --argjson target "$target" \
  --argjson pr "$PR_NUM" \
  --argjson am "$am" \
  --arg prs "$PR_NUM" \
  --arg env_id "$env_id" \
  --arg issue "$ISSUE_NUMBER" \
  --arg parent "$VALID_PARENT" '
  {
    workflow_version_id: $vvid,
    target: $target,
    args: {
      inputs: { pr_number: $pr, auto_merge: $am },
      labels: ({ source: "backlog", pr: $prs }
               + (if $issue == "" then {} else { issue: $issue } end))
    },
    environment_id: $env_id
  }
  + (if $parent == "" then {} else { parent_id: $parent } end)
' > "$tmp/intent.json" || die "could not build the RunIntent payload"

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
  # indefinitely — verified against 0.354.0-nightly.0 by leaving one alone for 90s.
  # REFERENCE.md describes the same two-step shape for the CLI (`fabro create` stops
  # at submitted; `fabro start <run>` launches it). Without this call the bridge
  # creates review runs that never execute, and nothing anywhere reports an error.
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
  # Read back from the start response: that is the first point the scheduler has seen
  # the run, and queue_position is the evidence for whether fabro queues or rejects at
  # max_concurrent_runs = 3. It has never been exercised in this deployment.
  queue_position="$(jq -r 'if .lifecycle.queue_position == null then "null"
                           else (.lifecycle.queue_position | tostring) end' "$tmp/start_resp.json")"
else
  run_status="not started"
  queue_position="not sent"
fi

# ---------------------------------------------------------------- 6. report ----

repo_branch="$(printf '%s' "$target" | jq -r '.branch // "?"')"

if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY_RUN=1: nothing will be sent. Re-run with DRY_RUN=0 to fire.\n'
  printf '\n== resolved ==\n'
  printf 'package  %s @ %s\n' "$WORKFLOWS_REPO" "$WORKFLOWS_REF"
  printf 'target   %s#%s  (%s)\n' "$REPO" "$PR_NUM" "$repo_branch"
  printf 'env      %s   (from automation %s)\n' "$env_id" "$auto_id"
  # A kill switch you cannot observe without firing is not a kill switch: print the
  # per-repo switch this fire resolved. The host switch is read at run launch, so it
  # is shown as unresolved rather than guessed at here.
  printf 'auto_merge  %s   (per-repo switch; %s description token is %s)\n' \
    "$am" "$auto_id" "${switch_tok:-<absent, default on>}"
  printf 'parent   %s\n' "${VALID_PARENT:-<omitted>}"
  # entrypoint and the files keys are the part most likely to need a round trip, so
  # they are listed up front. The full payloads follow, but this package is ~58 KB of
  # prompt bodies, which would otherwise bury the RunIntent below it.
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

printf 'verified  %s#%s exists\n' "$REPO" "$PR_NUM"
printf 'version   %s (registered)\n' "$version_id"
printf 'config    %s  environment=%s target=%s@%s\n' "$auto_id" "$env_id" "$REPO" "$repo_branch"
printf 'run       HTTP 201  status=%s  queue_position=%s\n' "$run_status" "$queue_position"

# The graph node reads this with `tail -1`, so the run id is the last line and nothing
# may follow it.
printf '%s\n' "$run_id"
