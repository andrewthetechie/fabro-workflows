#!/usr/bin/env bash
# test-task-gates.sh — offline tests for the backlog task-queue gates.
#
# Usage:
#   ./ops/test-task-gates.sh
#
# Why this exists
# ---------------
# `decompose_gate`, `improve_gate` and `next_task` are shell scripts embedded as
# DOT attributes. `fabro validate` checks that the graph parses; it does not run
# them. Their bugs are exactly the class AGENTS.md warns about -- they pass
# validation and fail at runtime, with a PR already open and hours already spent.
#
# So this extracts each `script` attribute from workflow.fabro verbatim,
# unescapes it the way the DOT parser does, rebases `/tmp/fabro` onto a scratch
# directory, and runs it against fixtures. It needs no host, no container, no
# sandbox and no network, which makes it cheap enough for a pre-push hook
# alongside `check-routing-schemas.py`.
#
# It tests the QUEUE mechanics -- selection, splicing, cursor, guards. It says
# nothing about whether an agent fills the contract correctly; that is what the
# gates' own validation is for.
#
# Exit: 0 all passed, 1 any failure.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GRAPH="$REPO_ROOT/.fabro/workflows/backlog/workflow.fabro"
SHARED="$REPO_ROOT/.fabro/workflows/_shared/review-merge/review-merge.fabro"
[ -f "$GRAPH" ] || { echo "ERROR: $GRAPH not found" >&2; exit 1; }
[ -f "$SHARED" ] || { echo "ERROR: $SHARED not found" >&2; exit 1; }

command -v jq >/dev/null || { echo "ERROR: jq is required" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Extract a node's `script` attribute exactly as the DOT parser sees it.
# ---------------------------------------------------------------------------
extract() { extract_from "$GRAPH" "$1"; }

# `extract`, against a named graph. The shared review-merge graph is spliced into
# both backlog and pr-review at parse time, so its nodes are not in either package's
# own .fabro file and have to be read from the shared one.
extract_from() {
    python3 - "$1" "$2" <<'PY'
import sys
src = open(sys.argv[1]).read()
i = src.index("\n    %s [" % sys.argv[2])
k = src.index('script="', i) + len('script="')
out = []
while True:
    c = src[k]
    if c == '\\' and src[k + 1] == '"':
        out.append('"'); k += 2; continue
    if c == '"':
        break
    out.append(c); k += 1
sys.stdout.write(''.join(out))
PY
}

# Stage a gate with /tmp/fabro rebased onto $T, and hold it to AGENTS.md's
# "Inline scripts are POSIX `sh`" invariant. Everything below runs the staged
# file with `sh`, not `bash`, for the same reason: a bashism that works on this
# Mac fails in the sandbox, and running the tests under bash would hide it.
# `sh -n` is blind inside `jq '...'`, as AGENTS.md notes -- that is what the
# behavioural checks are for.
stage() { stage_into "$1" "$T"; }

# `stage`, with the sandbox root given explicitly. Every gate below rebases
# /tmp/fabro onto its own $T, which already exists -- so none of them can observe
# whether a node CREATES that directory. `claim` is the one node that has to, and
# it passes a path that does not exist yet. That is the whole 2026-09-19
# regression: `acquire` was deleted, it was the only node running
# `mkdir -p /tmp/fabro`, and every gate stayed green.
stage_into() {
    extract "$1" | sed "s#/tmp/fabro#$2#g" > "$T/$1.sh"
    if ! sh -n "$T/$1.sh" 2>"$T/$1.syntax"; then
        FAIL=$((FAIL + 1))
        printf '  FAIL %s is not valid POSIX sh\n' "$1"
        sed 's/^/       /' "$T/$1.syntax"
    fi
}

# Fabro selects the LAST JSON object from merged stdout+stderr.
lastjson() { grep -o '{.*}' <<<"$1" | tail -1; }

check() {
    if [ "$2" = "$3" ]; then
        PASS=$((PASS + 1)); printf '  ok   %s\n' "$1"
    else
        FAIL=$((FAIL + 1))
        printf '  FAIL %s\n       expected: %s\n       actual:   %s\n' "$1" "$2" "$3"
    fi
}

# ---------------------------------------------------------------------------
# decompose_gate — `covers` normalisation and the oversize warning
# ---------------------------------------------------------------------------
echo "decompose_gate"
T="$WORK/dg"; mkdir -p "$T"; stage decompose_gate

dg_setup() {
    rm -f "$T"/*.json "$T"/decompose_attempts "$T"/task_index
    printf '%s' "$1" > "$T/decomposition.json"
}

dg_setup '{"status":"issues","summary":"s","issues":[
  {"id":"a","title":"A","body":"b","files":[],"covers":["c1"]},
  {"id":"b","title":"B","body":"b","files":[],"covers":["c2","c3"]}]}'
OUT=$(sh "$T/decompose_gate.sh" 2>&1); RC=$?; J=$(lastjson "$OUT")
check "well-sized exits 0"          "0"         "$RC"
check "well-sized oversized=0"      "0"         "$(jq -r '.context_updates.oversized_tasks' <<<"$J")"
check "well-sized task_count"       "2"         "$(jq -r '.context_updates.task_count' <<<"$J")"
check "covers preserved"            "c1"        "$(jq -r '.[0].covers[0]' "$T/tasks.json")"
check "source stamped"              "decompose" "$(jq -r '.[0].source' "$T/tasks.json")"
check "no spurious warning"         "0"         "$(grep -c 'WARNING' <<<"$OUT")"

dg_setup '{"status":"issues","summary":"s","issues":[
  {"id":"a","title":"A","body":"b","files":[],"covers":["c2"]},
  {"id":"big","title":"Big","body":"b","files":[],"covers":["c1","c3","c4","c5","c6"]}]}'
OUT=$(sh "$T/decompose_gate.sh" 2>&1); RC=$?; J=$(lastjson "$OUT")
check "oversize does not fail"      "0" "$RC"
check "oversize counted"            "1" "$(jq -r '.context_updates.oversized_tasks' <<<"$J")"
check "oversize warns"              "1" "$(grep -c 'WARNING' <<<"$OUT")"
check "oversize names the task"     "1" "$(grep -c -- '- big: 5 requirements' <<<"$OUT")"
check "routing JSON is last object" "issues" "$(jq -r '.context_updates.decomp_status' <<<"$J")"

# Boundary: three is the documented limit and must not trip.
dg_setup '{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[],"covers":["1","2","3"]}]}'
check "three covers not flagged" "0" \
    "$(jq -r '.context_updates.oversized_tasks' <<<"$(lastjson "$(sh "$T/decompose_gate.sh" 2>&1)")")"

# A model that forgets `covers`, or sends the wrong type, must not break the run
# or be counted by string length.
dg_setup '{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[]}]}'
OUT=$(sh "$T/decompose_gate.sh" 2>&1); RC=$?
check "missing covers exits 0"      "0" "$RC"
check "missing covers becomes []"   "0" "$(jq -r '.[0].covers | length' "$T/tasks.json")"

dg_setup '{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[],"covers":"a long string over three chars"}]}'
OUT=$(sh "$T/decompose_gate.sh" 2>&1); RC=$?
check "string covers exits 0"       "0" "$RC"
check "string covers coerced to []" "0" "$(jq -r '.[0].covers | length' "$T/tasks.json")"
check "string covers not flagged"   "0" "$(jq -r '.context_updates.oversized_tasks' <<<"$(lastjson "$OUT")")"

dg_setup '{"status":"no_work","summary":"nothing","issues":[]}'
OUT=$(sh "$T/decompose_gate.sh" 2>&1); J=$(lastjson "$OUT")
check "no_work preserved"           "no_work" "$(jq -r '.context_updates.decomp_status' <<<"$J")"
check "no_work oversized=0"         "0"       "$(jq -r '.context_updates.oversized_tasks' <<<"$J")"

dg_setup '{"status":"issues","summary":"s","issues":[{"id":"","title":"A","body":"b","files":[]}]}'
sh "$T/decompose_gate.sh" >/dev/null 2>&1
check "invalid entry still retries" "1" "$?"

# ---------------------------------------------------------------------------
# improve_gate — the split splice and its guards
# ---------------------------------------------------------------------------
echo ""
echo "improve_gate"
T="$WORK/ig"; mkdir -p "$T"; stage improve_gate

TASKS='[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"],"source":"decompose"},
        {"id":"big","title":"Big","body":"b","files":[],"covers":["c1","c2","c3","c4","c5"],"source":"decompose"},
        {"id":"z","title":"Z","body":"b","files":[],"covers":["c6"],"source":"decompose"}]'
CUR='{"id":"big","title":"Big","body":"b","files":[],"covers":["c1","c2","c3","c4","c5"],"source":"decompose"}'
SPLIT2='{"disposition":"split","reason":"too big","tasks":[
    {"id":"big-module","title":"Add module","body":"x","files":[],"covers":["c1"]},
    {"id":"big-migrate","title":"Migrate callers","body":"y","files":[],"covers":["c2","c3"]}]}'

ig_setup() { # tasks current result task_index split_rounds
    rm -f "$T"/*.json "$T"/task_index "$T"/split_rounds "$T"/improve_attempts
    printf '%s' "$1" > "$T/tasks.json"
    printf '%s' "$2" > "$T/current_task.json"
    printf '%s' "$3" > "$T/improve_result.json"
    echo "$4" > "$T/task_index"
    echo "$5" > "$T/split_rounds"
}

ig_setup "$TASKS" "$CUR" "$SPLIT2" 2 0
OUT=$(sh "$T/improve_gate.sh" 2>/dev/null); RC=$?; J=$(lastjson "$OUT")
check "split exits 0"               "0"     "$RC"
check "split disposition"           "split" "$(jq -r '.context_updates.task_disposition' <<<"$J")"
check "spliced in place, in order"  "a big-module big-migrate z" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "slices stamped source"       "decompose split split decompose" "$(jq -r '[.[].source]|join(" ")' "$T/tasks.json")"
check "cursor rewound to the slot"  "1"     "$(cat "$T/task_index")"
check "split_rounds incremented"    "1"     "$(cat "$T/split_rounds")"
check "task_count republished"      "4"     "$(jq -r '.context_updates.task_count' <<<"$J")"

# Guard 1: a slice can never be split again.
ig_setup "$TASKS" '{"id":"big-1","title":"S","body":"b","files":[],"source":"split"}' "$SPLIT2" 2 0
OUT=$(sh "$T/improve_gate.sh" 2>/dev/null)
check "slice refuses re-split"      "ready"   "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "refusal leaves queue alone"  "a big z" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "refusal keeps current_task"  "big-1"   "$(jq -r .id "$T/current_task.json")"

# Guard 2: the per-run budget.
ig_setup "$TASKS" "$CUR" "$SPLIT2" 2 2
OUT=$(sh "$T/improve_gate.sh" 2>/dev/null)
check "spent budget coerces ready"  "ready" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "spent budget keeps current"  "big"   "$(jq -r .id "$T/current_task.json")"

ig_setup "$TASKS" "$CUR" '{"disposition":"split","tasks":[{"id":"x","title":"X","body":"b","files":[]}]}' 2 0
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "one slice is invalid"        "1" "$?"

ig_setup "$TASKS" "$CUR" '{"disposition":"split","tasks":[{"id":"z","title":"X","body":"b","files":[]},{"id":"q","title":"Q","body":"b","files":[]}]}' 2 0
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "id colliding with queue"     "1" "$?"

ig_setup "$TASKS" "$CUR" '{"disposition":"split","tasks":[{"id":"dup","title":"X","body":"b","files":[]},{"id":"dup","title":"Q","body":"b","files":[]}]}' 2 0
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "duplicate ids within split"  "1" "$?"

# The replaced task's own id is free to reuse -- it is leaving the queue.
ig_setup "$TASKS" "$CUR" '{"disposition":"split","tasks":[{"id":"big","title":"X","body":"b","files":[]},{"id":"q","title":"Q","body":"b","files":[]}]}' 2 0
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "slice may reuse replaced id" "0" "$?"

# Splice at both ends of the queue.
ig_setup "$TASKS" '{"id":"a","source":"decompose"}' "$SPLIT2" 1 0
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "splice at head"              "big-module big-migrate big z" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "splice at head rewinds to 0" "0" "$(cat "$T/task_index")"

ig_setup "$TASKS" '{"id":"z","source":"decompose"}' "$SPLIT2" 3 0
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "splice at tail"              "a big big-module big-migrate" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"

# The pre-existing dispositions must be untouched by all of the above.
ig_setup "$TASKS" "$CUR" '{"disposition":"ready","task":{"id":"big","title":"Sharpened","body":"nb","files":[],"covers":["c1"]}}' 2 0
OUT=$(sh "$T/improve_gate.sh" 2>/dev/null)
check "ready disposition"           "ready"     "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "ready rewrites current_task" "Sharpened" "$(jq -r .title "$T/current_task.json")"
check "ready leaves queue alone"    "a big z"   "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"

ig_setup "$TASKS" "$CUR" '{"disposition":"redundant","reason":"done"}' 2 0
OUT=$(sh "$T/improve_gate.sh" 2>/dev/null)
check "redundant disposition"       "redundant" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"

ig_setup "$TASKS" "$CUR" '{"disposition":"nonsense"}' 2 0
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "unknown disposition retries" "1" "$?"

# A truncated counter file must not read as "budget unspent". Unquoted `[ $SR
# -ge 2 ]` on an empty value raises "unary operator expected", which short-
# circuits the && and silently PERMITS the split.
ig_setup "$TASKS" "$CUR" "$SPLIT2" 2 ""
: > "$T/split_rounds"
OUT=$(sh "$T/improve_gate.sh" 2>/dev/null)
check "empty split_rounds is not a crash" "split" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "empty split_rounds counts as 0"    "1"     "$(cat "$T/split_rounds")"

# Same for a garbage counter: treat as spent-from-zero, never as an error.
ig_setup "$TASKS" "$CUR" "$SPLIT2" 2 "garbage"
OUT=$(sh "$T/improve_gate.sh" 2>/dev/null)
check "garbage split_rounds normalised"   "split" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"

# `oversized_tasks` and `task_count` are interpolated bare into the routing
# JSON, so an empty value emits `"oversized_tasks":}` -- unparseable, which makes
# fabro's routing scan go inert with no error anywhere (AGENTS.md invariant 1).
T="$WORK/dg2"; mkdir -p "$T"; stage decompose_gate
printf '%s' '{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"]}]}' > "$T/decomposition.json"
OUT=$(sh "$T/decompose_gate.sh" 2>&1)
check "routing JSON always parses"  "0" "$(jq -e . >/dev/null 2>&1 <<<"$(lastjson "$OUT")"; echo $?)"
check "oversized_tasks is a number" "number" "$(jq -r '.context_updates.oversized_tasks | type' <<<"$(lastjson "$OUT")")"

# ---------------------------------------------------------------------------
# next_task + improve_gate — the split must resume ON the first slice
# ---------------------------------------------------------------------------
echo ""
echo "next_task <-> improve_gate"
T="$WORK/loop"; mkdir -p "$T/bin" "$T/feedback"; stage next_task; stage improve_gate
# next_task shells out to git for the mainline refresh; only selection is under test.
printf '#!/bin/sh\nexit 0\n' > "$T/bin/git"; chmod +x "$T/bin/git"
PATH="$T/bin:$PATH"

cat > "$T/tasks.json" <<'EOF'
[{"id":"small","title":"Small","body":"b","files":[],"covers":["c1"],"source":"decompose"},
 {"id":"big","title":"Big","body":"b","files":[],"covers":["c1","c2","c3","c4","c5"],"source":"decompose"}]
EOF
echo 0 > "$T/task_index"; echo 0 > "$T/split_rounds"

OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "selects first task"          "small" "$(jq -r .id "$T/current_task.json")"
check "not done yet"                "false" "$(jq -r '.context_updates.tasks_done' <<<"$(lastjson "$OUT")")"
sh "$T/next_task.sh" >/dev/null 2>&1
check "selects oversized task"      "big"   "$(jq -r .id "$T/current_task.json")"

cat > "$T/improve_result.json" <<'EOF'
{"disposition":"split","reason":"five criteria","tasks":[
 {"id":"big-module","title":"Add module","body":"x","files":[],"covers":["c1"]},
 {"id":"big-migrate","title":"Migrate callers","body":"y","files":[],"covers":["c2","c3"]},
 {"id":"big-adr","title":"Record ADR","body":"z","files":[],"covers":["c4","c5"]}]}
EOF
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "queue spliced"               "small big-module big-migrate big-adr" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"

# The regression this guards: an off-by-one here silently SKIPS the first slice.
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "resumes on the first slice"  "big-module" "$(jq -r .id "$T/current_task.json")"
check "disposition reset by next"   "none"       "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"

sh "$T/improve_gate.sh" >/dev/null 2>&1
check "slice cannot re-split"       "small big-module big-migrate big-adr" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"

sh "$T/next_task.sh" >/dev/null 2>&1
check "drains to second slice"      "big-migrate" "$(jq -r .id "$T/current_task.json")"
sh "$T/next_task.sh" >/dev/null 2>&1
check "drains to third slice"       "big-adr"     "$(jq -r .id "$T/current_task.json")"
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "queue reports done"          "true" "$(jq -r '.context_updates.tasks_done' <<<"$(lastjson "$OUT")")"

# AGENTS.md: "Delete a contract file before the agent that writes it runs — an
# agent that exits succeeded without writing hands the gate its predecessor's
# result." With `split` in play a stale improve_result.json no longer just
# mis-improves one task, it can splice the previous task's slices into the queue.
echo 0 > "$T/task_index"
printf '%s' '{"disposition":"split","tasks":[]}' > "$T/improve_result.json"
sh "$T/next_task.sh" >/dev/null 2>&1
check "next_task clears improve_result" "absent" \
    "$([ -e "$T/improve_result.json" ] && echo present || echo absent)"

# ---------------------------------------------------------------------------
# extra_gate — follow-up tasks join the same queue under the same size budget
# ---------------------------------------------------------------------------
echo ""
echo "extra_gate"
T="$WORK/eg"; mkdir -p "$T/extra"; stage extra_gate

eg_setup() { # tasks followups
    rm -f "$T"/*.json "$T"/extra/*.json "$T"/extra_decompose_attempts
    printf '%s' "$1" > "$T/tasks.json"
    printf '%s' "$2" > "$T/extra/followups.json"
}

eg_setup '[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"],"source":"decompose"}]' \
         '{"status":"issues","summary":"s","issues":[{"id":"f1","title":"F","body":"b","files":[],"covers":["finding 1"]}]}'
OUT=$(sh "$T/extra_gate.sh" 2>&1); RC=$?
check "follow-up appends"           "0"             "$RC"
check "follow-up joins the queue"   "a f1"          "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "follow-up source stamped"    "extra-review"  "$(jq -r '.[1].source' "$T/tasks.json")"
check "follow-up covers preserved"  "finding 1"     "$(jq -r '.[1].covers[0]' "$T/tasks.json")"
check "new_tasks counted"           "1"             "$(jq -r '.context_updates.new_tasks' <<<"$(lastjson "$OUT")")"

# The queue must stay uniform: `improve` reads `covers` off every task it is
# handed, whatever wrote it.
eg_setup '[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"],"source":"decompose"}]' \
         '{"status":"issues","summary":"s","issues":[{"id":"f1","title":"F","body":"b","files":[]}]}'
sh "$T/extra_gate.sh" >/dev/null 2>&1
check "missing covers normalised"   "0" "$(jq -r '.[1].covers | length' "$T/tasks.json")"

eg_setup '[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"],"source":"decompose"}]' \
         '{"status":"issues","summary":"s","issues":[{"id":"big","title":"F","body":"b","files":[],"covers":["1","2","3","4"]}]}'
OUT=$(sh "$T/extra_gate.sh" 2>&1)
check "oversized follow-up warns"   "1" "$(grep -c 'WARNING' <<<"$OUT")"
check "oversized follow-up still merges" "a big" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "extra_gate JSON parses"      "0" "$(jq -e . >/dev/null 2>&1 <<<"$(lastjson "$OUT")"; echo $?)"

# Dedup by id is what stops a follow-up round re-adding what is already queued.
eg_setup '[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"],"source":"decompose"}]' \
         '{"status":"issues","summary":"s","issues":[{"id":"a","title":"dup","body":"b","files":[],"covers":["x"]}]}'
OUT=$(sh "$T/extra_gate.sh" 2>&1)
check "duplicate follow-up dropped" "a" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "dropped duplicate counts 0"  "0" "$(jq -r '.context_updates.new_tasks' <<<"$(lastjson "$OUT")")"

# ---------------------------------------------------------------------------
# open_pr — gh must never be left to infer the branch
# ---------------------------------------------------------------------------
echo ""
echo "open_pr"
SAVED_PATH="$PATH"
T="$WORK/openpr"; mkdir -p "$T/bin"; stage open_pr
BR="fabro/run/01TEST"

# The sandbox clone is single-branch, so `refs/remotes/origin/<run branch>` never
# exists and gh cannot resolve the head remote from it. These stubs therefore do
# NOT emulate that lookup -- they assert the script never depends on it, by
# logging every gh invocation so the checks below can read the arguments back.
cat > "$T/bin/git" <<'STUB'
#!/bin/sh
case "$1 $2" in
  "rev-parse --abbrev-ref") echo "fabro/run/01TEST" ;;
  "push -u")                echo "Everything up-to-date" ;;
esac
exit 0
STUB
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view")
      if [ -f "$GH_STATE/pr_exists" ] || [ -f "$GH_STATE/created" ]; then
          echo "https://github.com/andrewthetechie/jelly-swipe/pull/123"; exit 0
      fi
      echo "no pull requests found for branch" >&2; exit 1 ;;
  "pr create")
      if [ -f "$GH_STATE/create_fails" ]; then
          echo "aborted: you must first push the current branch to a remote, or use the --head flag" >&2
          exit 1
      fi
      : > "$GH_STATE/created"; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/git" "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

op_setup() {
    rm -f "$T"/gh.log "$T"/pr_exists "$T"/created "$T"/create_fails "$T"/pr_number
    printf '%s' '{"number":350,"title":"t"}' > "$T/issue.json"
    printf '%s' 'fix: a title' > "$T/pr_title.txt"
    : > "$T/pr_body.md"
    [ -n "${1:-}" ] && : > "$T/$1"
    return 0
}

# 1. No PR yet: create it, then read the URL back.
op_setup
OUT=$(sh "$T/open_pr.sh" 2>&1); RC=$?
check "opens a PR, exit 0"        "0"   "$RC"
check "pr_url reported"           "https://github.com/andrewthetechie/jelly-swipe/pull/123" \
    "$(jq -r '.context_updates.pr_url' <<<"$(lastjson "$OUT")")"
check "pr_number extracted"       "123" "$(cat "$T/pr_number")"

# THE REGRESSION. A bare `gh pr create` resolves the head branch through
# refs/remotes/origin/<branch>, which a single-branch clone cannot store.
check "create passes --head"      "1" "$(grep -c -- "pr create --head $BR" "$T/gh.log")"
# Same for `gh pr view`: with no argument it resolves the CURRENT branch the same
# way. Every view call must name the branch.
check "every pr view names branch" "0" \
    "$(grep '^pr view' "$T/gh.log" | grep -vc "^pr view $BR" || true)"

# 2. A PR already exists (retry after a partial failure): reuse, do not create.
op_setup pr_exists
OUT=$(sh "$T/open_pr.sh" 2>&1); RC=$?
check "existing PR, exit 0"       "0"   "$RC"
check "existing PR is reused"     "0"   "$(grep -c 'pr create' "$T/gh.log")"
check "existing PR url reported"  "https://github.com/andrewthetechie/jelly-swipe/pull/123" \
    "$(jq -r '.context_updates.pr_url' <<<"$(lastjson "$OUT")")"

# 3. A real create failure must surface, not be swallowed by a fallback.
op_setup create_fails
OUT=$(sh "$T/open_pr.sh" 2>&1); RC=$?
check "create failure fails stage" "1" "$RC"
check "gh's own error survives"    "1" "$(grep -c 'you must first push the current branch' <<<"$OUT")"
check "no pr_url on failure"       "" "$(lastjson "$OUT" | jq -r '.context_updates.pr_url // ""' 2>/dev/null)"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# claim — the run's first node, and the only one that creates /tmp/fabro
#
# This section exists because of a live incident. Draft 10 deleted `acquire`,
# `acquire` was the only node that ran `mkdir -p /tmp/fabro`, and `claim` --
# now the first node -- still redirected into that directory. Every backlog run
# died at its second node and parked on human_rescue for 4h holding a coder box,
# and `fabro-fire-backlog.sh` failed identically. `fabro validate` and
# check-routing-schemas.py were both green: neither runs a node's shell, and this
# script covered the task-queue gates and open_pr but not claim.
# ---------------------------------------------------------------------------
echo ""
echo "claim"
SAVED_PATH="$PATH"
T="$WORK/claim"; mkdir -p "$T/bin"
# NOT created: the point of the first check below is that `claim` creates it.
FAB="$T/fabro"

# `sleep` is stubbed so the retry path costs no wall-clock time. `gh` logs every
# invocation and reads its behaviour out of marker files, exactly like open_pr's.
cat > "$T/bin/sleep" <<'STUB'
#!/bin/sh
exit 0
STUB
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "label create") exit 0 ;;
  "issue edit")   exit 0 ;;
  "issue view")
      n=0
      [ -f "$GH_STATE/view_attempts" ] && n=$(cat "$GH_STATE/view_attempts")
      n=$((n + 1)); echo "$n" > "$GH_STATE/view_attempts"
      fail_until=0
      [ -f "$GH_STATE/view_fails_until" ] && fail_until=$(cat "$GH_STATE/view_fails_until")
      if [ "$n" -le "$fail_until" ]; then
          echo "could not connect to api.github.com" >&2; exit 1
      fi
      cat "$GH_STATE/issue_payload.json"; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/sleep" "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

# Re-extracted per case because the issue number is a MiniJinja input, not a
# variable: the graph carries `{{ inputs.issue_number }}` literally.
claim_stage() {
    extract claim \
        | sed "s#/tmp/fabro#$FAB#g" \
        | sed "s#{{ inputs.issue_number }}#$1#g" > "$T/claim.sh"
}
claim_setup() {
    rm -rf "$FAB"; rm -f "$T/view_attempts" "$T/view_fails_until"
    # Created empty, not deleted: a case that makes no gh call at all still has to
    # be countable, and `wc -l` on a missing file is an error, not a zero.
    : > "$T/gh.log"
    printf '%s' '{"state":"OPEN","number":356,"title":"t","body":"b","labels":[{"name":"agent-in-progress"}]}' \
        > "$T/issue_payload.json"
    # `${1-356}`, NOT `${1:-356}`: the empty string is a case under test -- it is
    # what an unset MiniJinja input renders to -- and `:-` would substitute the
    # default for it and quietly test 356 four times.
    claim_stage "${1-356}"
}

claim_setup 356
sh -n "$T/claim.sh" 2>"$T/claim.syntax" \
    || { FAIL=$((FAIL + 1)); printf '  FAIL claim is not valid POSIX sh\n'; sed 's/^/       /' "$T/claim.syntax"; }

# 1. THE REGRESSION. /tmp/fabro does not exist when claim starts, and claim is
#    the only node that can make it: prep's mkdir is one node too late.
OUT=$(sh "$T/claim.sh" 2>&1); RC=$?
check "happy path exits 0"            "0"   "$RC"
check "claim creates /tmp/fabro"      "yes" "$([ -d "$FAB" ] && echo yes || echo no)"
check "issue.json is written"         "356" "$(jq -r .number "$FAB/issue.json")"

# 2. The number is written before any network call, so mark_stuck can always name
#    the issue whose receipt it has to remove.
check "issue_number is written"       "356" "$(cat "$FAB/issue_number")"

# 3. The label swap is idempotent and never left to a default.
check "swaps both labels"             "1" \
    "$(grep -c -- 'issue edit 356 --add-label agent-in-progress --remove-label agent' "$T/gh.log")"

# 4. Input validation fails closed, before anything is written to GitHub.
for bad in "not-a-number" "" "0" "12x"; do
    claim_setup "$bad"
    OUT=$(sh "$T/claim.sh" 2>&1); RC=$?
    check "rejects issue_number '$bad'"     "1" "$RC"
    check "no gh call for '$bad'"           "0" "$(wc -l < "$T/gh.log" | tr -d ' ')"
done

# 5. A closed issue is a definitive answer: refuse, never work it.
claim_setup 356
printf '%s' '{"state":"CLOSED","number":356,"title":"t","body":"b","labels":[{"name":"agent-in-progress"}]}' \
    > "$T/issue_payload.json"
OUT=$(sh "$T/claim.sh" 2>&1); RC=$?
check "refuses a CLOSED issue"        "1" "$RC"
check "says why it refused"           "1" "$(grep -c 'not OPEN' <<<"$OUT")"

# 6. The receipt must actually be on the issue after the swap, or this run is
#    about to work an issue the scheduler did not lease.
claim_setup 356
printf '%s' '{"state":"OPEN","number":356,"title":"t","body":"b","labels":[{"name":"agent"}]}' \
    > "$T/issue_payload.json"
OUT=$(sh "$T/claim.sh" 2>&1); RC=$?
check "refuses without the receipt"   "1" "$RC"
check "names the wrong-issue risk"    "1" "$(grep -c 'refusing to work the wrong issue' <<<"$OUT")"

# 7. A transient gh failure must not cost a coder box for four hours. Two
#    failures then a success is a working run, not a human gate.
claim_setup 356
echo 2 > "$T/view_fails_until"
OUT=$(sh "$T/claim.sh" 2>&1); RC=$?
check "retries a transient failure"   "0" "$RC"
check "took three view attempts"      "3" "$(cat "$T/view_attempts")"

# 8. A permanent failure still fails closed, bounded, with gh's own reason.
claim_setup 356
echo 99 > "$T/view_fails_until"
OUT=$(sh "$T/claim.sh" 2>&1); RC=$?
check "gives up on a hard failure"    "1" "$RC"
check "bounded at three attempts"     "3" "$(cat "$T/view_attempts")"
check "surfaces gh's own error"       "1" "$(grep -c 'could not connect' <<<"$OUT")"
check "issue_number survives failure" "356" "$(cat "$FAB/issue_number")"

# ---------------------------------------------------------------------------
# mark_stuck — the receipt must come off, especially when claim failed
#
# An issue left wearing `agent-in-progress` is in no collection the scheduler
# reads, so it silently leaves the queue. Before 2026-09-19 this node read the
# number only from issue.json and skipped all label work when that file was
# absent -- which is precisely the run that reaches it.
# ---------------------------------------------------------------------------
echo ""
echo "mark_stuck"
T="$WORK/markstuck"; mkdir -p "$T/bin"
FAB="$T/fabro"; mkdir -p "$FAB"
cp "$WORK/claim/bin/gh" "$T/bin/gh"; chmod +x "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"
stage_into mark_stuck "$FAB"

ms_setup() { : > "$T/gh.log"; rm -f "$FAB/issue.json" "$FAB/issue_number"; }

ms_setup
printf '%s' '{"number":356}' > "$FAB/issue.json"
OUT=$(sh "$T/mark_stuck.sh" 2>&1); RC=$?
check "exits 0 with issue.json"       "0" "$RC"
check "swaps the receipt for stuck"   "1" \
    "$(grep -c -- 'issue edit 356 --remove-label agent-in-progress --add-label agent-stuck' "$T/gh.log")"

# THE REGRESSION: claim died before writing issue.json, so the only record of the
# issue is the number claim wrote first.
ms_setup
echo 353 > "$FAB/issue_number"
OUT=$(sh "$T/mark_stuck.sh" 2>&1); RC=$?
check "falls back to issue_number"    "1" \
    "$(grep -c -- 'issue edit 353 --remove-label agent-in-progress --add-label agent-stuck' "$T/gh.log")"
check "fallback exits 0"              "0" "$RC"

# A truncated or half-written issue.json reads back as null, not as a number.
ms_setup
printf '%s' '{"number":null}' > "$FAB/issue.json"
echo 2277 > "$FAB/issue_number"
OUT=$(sh "$T/mark_stuck.sh" 2>&1)
check "null number falls back too"    "1" \
    "$(grep -c -- 'issue edit 2277 --remove-label agent-in-progress' "$T/gh.log")"

# Nothing to name at all: say nothing to GitHub rather than edit issue 0.
ms_setup
OUT=$(sh "$T/mark_stuck.sh" 2>&1); RC=$?
check "no number, no gh call"         "0" "$(wc -l < "$T/gh.log" | tr -d ' ')"
check "no number still exits 0"       "0" "$RC"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE


# ---------------------------------------------------------------------------
# review-merge `merge` — the checkpoint-push race
#
# Fabro commits and pushes the run branch after EVERY stage, and `merge` is the
# one node whose branch is the thing being merged: the push lands ~15ms before the
# node starts, GitHub invalidates the computed merge ref, and the mutation is
# rejected with "Base branch was modified". The base is innocent -- jelly-swipe#386
# lost its merge to this on 2026-09-19 with `main` unmoved for 35h -- so the node
# waits for mergeability to settle, retries the race, and only then gives up.
# ---------------------------------------------------------------------------
echo ""
echo "review-merge merge"
SAVED_PATH="$PATH"
T="$WORK/rmmerge"; mkdir -p "$T/bin"

extract_from "$SHARED" merge | sed "s#/tmp/fabro#$T#g" > "$T/merge.sh"
if ! sh -n "$T/merge.sh" 2>"$T/merge.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL merge is not valid POSIX sh\n'; sed 's/^/       /' "$T/merge.syntax"
fi

cat > "$T/bin/sleep" <<'STUB'
#!/bin/sh
exit 0
STUB
cat > "$T/bin/git" <<'STUB'
#!/bin/sh
case "$1 $2" in
  "merge-base --is-ancestor")
      # exit 0 = base IS an ancestor of HEAD = we are NOT behind it
      [ -f "$GH_STATE/behind_base" ] && exit 1
      exit 0 ;;
esac
exit 0
STUB
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view")
      case "$*" in
        *"--json state"*)
            cat "$GH_STATE/pr_state" 2>/dev/null || echo OPEN ;;
        *"--json mergeable"*)
            n=0; [ -f "$GH_STATE/m_calls" ] && n=$(cat "$GH_STATE/m_calls")
            n=$((n + 1)); echo "$n" > "$GH_STATE/m_calls"
            u=0; [ -f "$GH_STATE/unknown_until" ] && u=$(cat "$GH_STATE/unknown_until")
            if [ "$n" -le "$u" ]; then echo UNKNOWN; else echo MERGEABLE; fi ;;
      esac
      exit 0 ;;
  "pr checks")
      # Real `gh pr checks` exits 8 while anything is pending and still prints the
      # rows, so the node must read the JSON and ignore the exit code. The stub
      # reproduces that, because a `|| echo []` fallback would silently turn every
      # pending poll into "no checks at all".
      n=0; [ -f "$GH_STATE/check_polls" ] && n=$(cat "$GH_STATE/check_polls")
      n=$((n + 1)); echo "$n" > "$GH_STATE/check_polls"
      p=0; [ -f "$GH_STATE/checks_pending_until" ] && p=$(cat "$GH_STATE/checks_pending_until")
      if [ "$n" -le "$p" ]; then
          echo '[{"name":"test","bucket":"pending"}]'; exit 8
      fi
      echo '[{"name":"test","bucket":"pass"}]'; exit 0 ;;
  "pr merge")
      n=0; [ -f "$GH_STATE/merge_calls" ] && n=$(cat "$GH_STATE/merge_calls")
      n=$((n + 1)); echo "$n" > "$GH_STATE/merge_calls"
      f=0; [ -f "$GH_STATE/merge_fails_until" ] && f=$(cat "$GH_STATE/merge_fails_until")
      if [ "$n" -le "$f" ]; then
          cat "$GH_STATE/merge_error" >&2
          exit 1
      fi
      echo "Squashed and merged pull request #386"; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/sleep" "$T/bin/git" "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

rm_setup() {
    : > "$T/gh.log"
    rm -f "$T/m_calls" "$T/merge_calls" "$T/unknown_until" "$T/merge_fails_until" \
          "$T/behind_base" "$T/pr_state" "$T/strict_retry" \
          "$T/merge_block_reason" "$T/needs_human_reason" "$T/merge_attempt.log" \
          "$T/check_polls" "$T/checks_pending_until" "$T/merge_checks.json"
    echo 386 > "$T/pr_number"
    echo main > "$T/base_ref"
    # A real budget, or `settle` sees DL=0, returns on its first line, and every
    # assertion below about waiting for CI would pass without running anything.
    echo $(( $(date +%s) + 3600 )) > "$T/merge_deadline"
    printf 'fix: a subject (#356)' > "$T/commit_subject.txt"
    : > "$T/commit_body.md"
    printf 'GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)\n' \
        > "$T/merge_error"
}

# 1. Clean merge.
rm_setup
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "clean merge exits 0"        "0"      "$RC"
check "clean merge reports merged" "merged" "$(jq -r '.context_updates.merge_state' <<<"$(lastjson "$OUT")")"
check "clean merge tries once"     "1"      "$(cat "$T/merge_calls")"

# 2. THE RACE, survived. First attempt is rejected, the retry wins.
rm_setup
echo 1 > "$T/merge_fails_until"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "race retried, exits 0"      "0"      "$RC"
check "race retried, merged"       "merged" "$(jq -r '.context_updates.merge_state' <<<"$(lastjson "$OUT")")"
check "race took two attempts"     "2"      "$(cat "$T/merge_calls")"

# 3. THE RACE, unsurvivable. Bounded at three, and the reason must NOT blame
#    permissions -- that wording is what sent a human looking for a token problem.
rm_setup
echo 9 > "$T/merge_fails_until"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "persistent race fails"      "1" "$RC"
check "bounded at three attempts"  "3" "$(cat "$T/merge_calls")"
check "names the race, not perms"  "1" "$(grep -c 'checkpoint-push race' "$T/merge_block_reason")"
check "says the PR is mergeable"   "1" "$(grep -c 'mergeable as it stands' "$T/merge_block_reason")"

# THE OTHER HALF OF #386: mark_needs_human renders `needs-human`, which reads
# needs_human_reason, NOT merge_block_reason. Both must be written or the operator
# is shown a stale placeholder from a stage that succeeded.
check "writes merge_block_reason"  "1" "$([ -s "$T/merge_block_reason" ] && echo 1 || echo 0)"
check "writes needs_human_reason"  "1" "$([ -s "$T/needs_human_reason" ] && echo 1 || echo 0)"
check "both reasons agree"         "" "$(diff "$T/merge_block_reason" "$T/needs_human_reason")"

# 4. A genuine failure is NOT retried, and it QUOTES GitHub instead of guessing.
#    The old wording named "permissions, conflict since review, or API error" for
#    every unrecognised rejection, which is what sent a human looking in three
#    wrong places when jelly-swipe#389 was refused by branch protection.
rm_setup
echo 9 > "$T/merge_fails_until"
printf 'GraphQL: Resource not accessible by integration (mergePullRequest)\n' > "$T/merge_error"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "non-race fails"             "1" "$RC"
check "non-race is not retried"    "1" "$(cat "$T/merge_calls")"
check "non-race quotes GitHub"     "1" "$(grep -c 'Resource not accessible' "$T/merge_block_reason")"
check "non-race is not the race"   "0" "$(grep -c 'checkpoint-push race' "$T/merge_block_reason")"
check "non-race sets both files"   "1" "$([ -s "$T/needs_human_reason" ] && echo 1 || echo 0)"

# 4b. THE #389 FAILURE: branch protection refuses because the checkpoint push reset
#     the required checks. The node must wait for CI on the NEW head and retry --
#     and the wait has to be in this node, because any node boundary is another
#     checkpoint and another new head.
rm_setup
echo 1 > "$T/merge_fails_until"
echo 2 > "$T/checks_pending_until"
printf 'X Pull request andrewthetechie/jelly-swipe#389 is not mergeable: the base branch policy prohibits the merge.\n' \
    > "$T/merge_error"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "protection retried, exits 0" "0"      "$RC"
check "protection retried, merged"  "merged" "$(jq -r '.context_updates.merge_state' <<<"$(lastjson "$OUT")")"
check "protection took two tries"   "2"      "$(cat "$T/merge_calls")"
check "waited out pending CI"       "1"      "$([ "$(cat "$T/check_polls")" -ge 3 ] && echo 1 || echo 0)"

# 4c. Protection refuses for good. Bounded, and the reason names the real cause
#     rather than the three-guess wording.
rm_setup
echo 9 > "$T/merge_fails_until"
printf 'X Pull request andrewthetechie/jelly-swipe#389 is not mergeable: the base branch policy prohibits the merge.\n' \
    > "$T/merge_error"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "protection persists, fails"  "1" "$RC"
check "protection bounded at three" "3" "$(cat "$T/merge_calls")"
check "names branch protection"     "1" "$(grep -c 'Branch protection on the base' "$T/merge_block_reason")"
check "names the required check"    "1" "$(grep -c 'required status check' "$T/merge_block_reason")"
check "protection sets both files"  ""  "$(diff "$T/merge_block_reason" "$T/needs_human_reason")"

# 4d. THE #1234 FAILURE: the same checkpoint push, but GitHub serves a STALE
#     `MERGEABLE` instead of UNKNOWN, so the wait loop never engages and the
#     mutation refuses. Not a conflict -- the branch is not behind -- so it must
#     be retried, not reported as one.
rm_setup
echo 1 > "$T/merge_fails_until"
printf 'GraphQL: Pull Request is not mergeable (mergePullRequest)\n' > "$T/merge_error"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "stale-mergeable retried"     "0"      "$RC"
check "stale-mergeable merged"      "merged" "$(jq -r '.context_updates.merge_state' <<<"$(lastjson "$OUT")")"
check "stale-mergeable two tries"   "2"      "$(cat "$T/merge_calls")"

# 4e. It persists: bounded, and reported as the race rather than as a conflict.
rm_setup
echo 9 > "$T/merge_fails_until"
printf 'GraphQL: Pull Request is not mergeable (mergePullRequest)\n' > "$T/merge_error"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "stale-mergeable fails"       "1" "$RC"
check "stale-mergeable bounded"     "3" "$(cat "$T/merge_calls")"
check "names the race not conflict" "1" "$(grep -c 'checkpoint-push race' "$T/merge_block_reason")"
check "rules out a conflict"        "1" "$(grep -c 'cannot be a conflict' "$T/merge_block_reason")"
check "no stray doubled quote"      "0" "$(grep -c 'GitHubs' "$T/merge_block_reason")"

# 4f. ORDERING TRAP: the protection rejection also contains "not mergeable", so a
#     general match must not swallow it. It has to still be reported as protection.
rm_setup
echo 9 > "$T/merge_fails_until"
printf 'X Pull request andrewthetechie/jelly-swipe#389 is not mergeable: the base branch policy prohibits the merge.\n' \
    > "$T/merge_error"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "protection wins the match"   "1" "$(grep -c 'Branch protection on the base' "$T/merge_block_reason")"
check "not reported as the race"    "0" "$(grep -c 'checkpoint-push race' "$T/merge_block_reason")"

# 5. Mergeability is UNKNOWN right after the checkpoint push; wait for it.
rm_setup
echo 2 > "$T/unknown_until"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "waits out UNKNOWN"          "0"      "$RC"
check "polled mergeability"        "3"      "$(cat "$T/m_calls")"
check "then merged"                "merged" "$(jq -r '.context_updates.merge_state' <<<"$(lastjson "$OUT")")"

# 6. A PR someone else already handled is not a failure.
rm_setup
echo CLOSED > "$T/pr_state"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "closed PR exits 0"          "0"      "$RC"
check "closed PR reports closed"   "closed" "$(jq -r '.context_updates.merge_state' <<<"$(lastjson "$OUT")")"
check "closed PR never merges"     "0"      "$(grep -c 'pr merge' "$T/gh.log")"

# 7. Genuinely behind the base: hand off to remerge_base, do not call it a failure.
rm_setup
echo 9 > "$T/merge_fails_until"
: > "$T/behind_base"
OUT=$(sh "$T/merge.sh" 2>&1); RC=$?
check "behind base exits 0"        "0"     "$RC"
check "behind base is stale"       "stale" "$(jq -r '.context_updates.merge_state' <<<"$(lastjson "$OUT")")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "PASS: $PASS checks"
    exit 0
fi
echo "FAIL: $FAIL of $((PASS + FAIL)) checks"
exit 1
