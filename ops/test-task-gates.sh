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
SHARED_TRIAGE="$REPO_ROOT/.fabro/workflows/_shared/triage/triage.fabro"
[ -f "$SHARED_TRIAGE" ] || { echo "ERROR: $SHARED_TRIAGE not found" >&2; exit 1; }

command -v jq >/dev/null || { echo "ERROR: jq is required" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# The PATH as it was before any section prepended a stub bin. `next_task` installs
# an `exit 0` git and never restores it, so every later `SAVED_PATH="$PATH"`
# captures that stub too -- which silently turns a section that wants the REAL git
# into one whose repo setup does nothing and whose checks then fail for the wrong
# reason. A section that needs real tools restores this.
ORIG_PATH="$PATH"

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
check "names the race, not perms"  "1" "$(grep -c 'head-push race' "$T/merge_block_reason")"
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
check "non-race is not the race"   "0" "$(grep -c 'head-push race' "$T/merge_block_reason")"
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
check "names the race not conflict" "1" "$(grep -c 'head-push race' "$T/merge_block_reason")"
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
check "not reported as the race"    "0" "$(grep -c 'head-push race' "$T/merge_block_reason")"

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
# open_pr_prep — the two floors in front of the PR (empty diff, workflow files)
# ---------------------------------------------------------------------------
# Every other gate here stubs `git`. This one must not: what is being checked is
# whether `git diff --quiet origin/main HEAD` tells the truth about a real branch
# with real fabro checkpoint commits on it, and a stub would only test the stub.
# So each case builds an actual repo and clones it over file://, which needs no
# network. `.fabro/setup.sh` and `.fabro/ci.sh` are repo files rather than PATH
# commands, so they are written into the clone and log their own invocations --
# that log is how the "refuses before spending two CI runs" check reads.
#
# The hole this covers: `open_pr_prep` derives the PR title and body entirely
# from issue.json and never looks at the tree, so on a branch where no task
# landed it produced a well-formed PR asserting the issue was implemented, and
# its own three `grep -q` assertions passed. womens-fantasy-sports#1236 and
# writers-app#949, both 0 files changed, both on 2026-09-21.
echo ""
echo "open_pr_prep"
SAVED_PATH="$PATH"
PATH="$ORIG_PATH"
T="$WORK/prep"; mkdir -p "$T"; stage open_pr_prep

# A fresh upstream + clone per case. $1 is the run branch's content disposition:
# "empty" leaves the tree equal to main, "work" puts a real change on it.
opp_repo() {
    rm -rf "$T/up" "$T/wt"
    mkdir -p "$T/up"
    git -C "$T/up" init -q -b main .
    git -C "$T/up" config user.email t@t
    git -C "$T/up" config user.name t
    mkdir -p "$T/up/.fabro"
    printf '%s\n' '#!/bin/sh' 'echo setup >> "$OPP_LOG"' > "$T/up/.fabro/setup.sh"
    printf '%s\n' '#!/bin/sh' 'echo ci >> "$OPP_LOG"' > "$T/up/.fabro/ci.sh"
    chmod +x "$T/up/.fabro/setup.sh" "$T/up/.fabro/ci.sh"
    echo base > "$T/up/a.txt"
    git -C "$T/up" add -A
    git -C "$T/up" commit -qm init
    git clone -q "$T/up" "$T/wt"
    git -C "$T/wt" config user.email t@t
    git -C "$T/wt" config user.name t
    git -C "$T/wt" checkout -qb fabro/run/01TEST
    # What fabro leaves on the branch when nothing lands: one checkpoint commit
    # per stage, none of them touching a repository file.
    git -C "$T/wt" commit -q --allow-empty -m 'fabro(01TEST): claim (succeeded)'
    git -C "$T/wt" commit -q --allow-empty -m 'fabro(01TEST): prep (failed)'
    case "$1" in
    work|workflow|wfrevert)
        echo implemented >> "$T/wt/a.txt"
        git -C "$T/wt" commit -qam 'fabro(01TEST): integrate (succeeded)'
        ;;
    esac
    # A coder that edited a CI workflow file. Real work landed first, so this is
    # the poisoned-branch case and not the empty-tree one.
    case "$1" in
    workflow|wfrevert)
        mkdir -p "$T/wt/.github/workflows"
        printf '%s\n' 'name: t' 'on: push' 'jobs: {}' > "$T/wt/.github/workflows/test-backend.yml"
        git -C "$T/wt" add -A
        git -C "$T/wt" commit -qm 'fabro(01TEST): coder (succeeded)'
        ;;
    esac
    # ...and then took it back out again. The net diff against main is now clean
    # while the COMMITS still carry it, which is the whole reason the gate reads
    # `git log` and not `git diff`.
    if [ "$1" = wfrevert ]; then
        git -C "$T/wt" rm -q .github/workflows/test-backend.yml
        git -C "$T/wt" commit -qm 'fabro(01TEST): rework_t1 (succeeded)'
    fi
    : > "$T/opp.log"
    rm -f "$T/completed.md" "$T/pr_body.md" "$T/pr_title.txt" "$T/commit_subject.txt"
    printf '%s' '{"number":1205,"title":"month_played_and_crowned","body":"","labels":[]}' > "$T/issue.json"
}

opp_run() { ( cd "$T/wt" && OPP_LOG="$T/opp.log" sh "$T/open_pr_prep.sh" 2>&1 ); }

# 1. The regression itself. A branch carrying only checkpoint commits must not
#    become a PR, and must say so rather than failing obscurely.
opp_repo empty
OUT=$(opp_run); RC=$?
check "empty tree exits nonzero"   "1" "$RC"
check "empty tree names the cause" "1" \
    "$(grep -c 'identical to origin/main' <<<"$OUT")"
check "empty tree says no task landed" "1" \
    "$(grep -c 'no task ever reached integrate' <<<"$OUT")"
check "empty tree points at [X]"   "1" "$(grep -c 'X. Abandon' <<<"$OUT")"

# 2. It refuses BEFORE the setup/ci loop -- two full CI runs on a 20m budget
#    against a tree that equals main is the cost of checking in the wrong order.
check "empty tree runs no CI"      "0" "$(wc -l < "$T/opp.log" | tr -d ' ')"

# 3. And it publishes nothing a later node could read as a real PR.
check "empty tree writes no body"  "absent" \
    "$([ -f "$T/pr_body.md" ] && echo present || echo absent)"

# 4. The happy path is untouched: real work still validates and derives.
opp_repo work
OUT=$(opp_run); RC=$?
check "real work exits 0"          "0" "$RC"
check "real work runs CI"          "2" "$(wc -l < "$T/opp.log" | tr -d ' ')"
check "real work writes a body"    "1" "$(grep -c 'fabro:commit-body:start' "$T/pr_body.md")"
check "real work derives a CC subject" "1" \
    "$(grep -cE '^(feat|fix|docs|chore|refactor|test|ci|build|perf|revert)(\(.*\))?!?: ' "$T/commit_subject.txt")"
check "real work resolves the issue" "1" "$(grep -c '^Resolves #1205' "$T/pr_body.md")"

# 5. The case no reviewer would think of: the run's work was already on main, so
#    after `git merge origin/main` the branch adds nothing. That is still a PR
#    that would claim to resolve the issue, so the floor has to hold there too --
#    and it is the case a three-dot diff against the ORIGINAL merge base misses.
opp_repo empty
git -C "$T/up" commit -q --allow-empty -m 'someone else shipped it'
OUT=$(opp_run); RC=$?
check "no-op after merge exits nonzero" "1" "$RC"
check "no-op after merge runs no CI"    "0" "$(wc -l < "$T/opp.log" | tr -d ' ')"

# 6. Main moving underneath a branch that DID work is not an empty diff. This is
#    the false positive that would strand every run whose merge brought in a
#    change from main, which is most of them.
opp_repo work
git -C "$T/up" commit -q --allow-empty -m 'main moves on'
OUT=$(opp_run); RC=$?
check "main moved, work kept, exits 0" "0" "$RC"
check "main moved, work kept, runs CI" "2" "$(wc -l < "$T/opp.log" | tr -d ' ')"

# 7. The `.github/workflows/` floor. The branch is pushed with an OAuth App token
#    that has no `workflow` scope, so one commit touching a workflow file makes the
#    whole branch unpushable -- 17 rejected checkpoint pushes and a 1s `unknown
#    error` at `open_pr`, on run 01M33SDMZF55NAEV8JV29A8EA4 (2026-09-22) for a
#    seven-line YAML COMMENT edit. Refusing here turns that into one readable
#    message before the CI loop instead of after every task has been thrown away.
opp_repo workflow
OUT=$(opp_run); RC=$?
check "workflow file exits nonzero" "1" "$RC"
check "workflow file names the path" "1" \
    "$(grep -c 'changes a file under .github/workflows/' <<<"$OUT")"
check "workflow file names the scope" "1" "$(grep -c 'workflow. scope' <<<"$OUT")"
check "workflow file lists the commit" "1" \
    "$(grep -c 'coder (succeeded)' <<<"$OUT")"
check "workflow file lists the file" "1" \
    "$(grep -c '  .github/workflows/test-backend.yml' <<<"$OUT")"
check "workflow file says [P] will not help" "1" \
    "$(grep -c 'NOT recoverable by answering' <<<"$OUT")"
check "workflow file runs no CI" "0" "$(wc -l < "$T/opp.log" | tr -d ' ')"
check "workflow file writes no body" "absent" \
    "$([ -f "$T/pr_body.md" ] && echo present || echo absent)"

# 8. Edited and then reverted. `git diff origin/main HEAD` is clean for that path,
#    so a net-diff test would pass this branch -- and the push would still be
#    rejected, because GitHub judges the commits in the push, not the end state.
opp_repo wfrevert
OUT=$(opp_run); RC=$?
check "reverted workflow edit still refused" "1" "$RC"
check "reverted workflow edit names the commit" "1" \
    "$(grep -c 'coder (succeeded)' <<<"$OUT")"

# 9. The false positive that would strand every run merging a main that touched
#    its own CI. The change is reachable from origin/main, so it is not in the
#    push and must not trip the floor.
opp_repo work
mkdir -p "$T/up/.github/workflows"
printf '%s\n' 'name: t' 'on: push' 'jobs: {}' > "$T/up/.github/workflows/test-backend.yml"
git -C "$T/up" add -A
git -C "$T/up" commit -qm 'main changes its own CI'
OUT=$(opp_run); RC=$?
check "workflow change from main is not ours" "0" "$RC"
check "workflow change from main still runs CI" "2" "$(wc -l < "$T/opp.log" | tr -d ' ')"

PATH="$SAVED_PATH"

# ---------------------------------------------------------------------------
# open_pr + deliver — the lease push once checkpoints stop pushing (ADR 0011 D1)
#
# REAL git, against a real bare remote, because what is under test is git's own
# --force-with-lease rule: with no remote-tracking ref for the branch it refuses
# as `stale info`. The sandbox clone is single-branch, so that ref only exists
# if open_pr creates it. Case 3 is the control: it proves this section can see
# the failure, so a green case 1 means something.
# ---------------------------------------------------------------------------
echo ""
echo "open_pr + deliver (real git)"
SAVED_PATH="$PATH"
T="$WORK/lease"; mkdir -p "$T/bin"; stage open_pr
extract_from "$SHARED" deliver | sed "s#/tmp/fabro#$T#g" > "$T/deliver.sh"
if ! sh -n "$T/deliver.sh" 2>"$T/deliver.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL deliver is not valid POSIX sh\n'; sed 's/^/       /' "$T/deliver.syntax"
fi
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view") echo "https://github.com/o/r/pull/7"; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/gh"
PATH="$T/bin:$ORIG_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

BR="fabro/run/01TEST"
lease_repo() {
    rm -rf "$T/up.git" "$T/seed" "$T/wt"
    git init -q --bare -b main "$T/up.git"
    git clone -q "$T/up.git" "$T/seed" 2>/dev/null
    git -C "$T/seed" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
    git -C "$T/seed" push -q origin HEAD:main
    git clone -q --single-branch --branch main "$T/up.git" "$T/wt"
    git -C "$T/wt" config user.email t@t
    git -C "$T/wt" config user.name t
    git -C "$T/wt" checkout -qb "$BR"
    echo work > "$T/wt/a.txt"
    git -C "$T/wt" add -A
    git -C "$T/wt" commit -qm 'fabro(01TEST): coder (succeeded)'
    printf '%s' '{"number":350,"title":"t"}' > "$T/issue.json"
    printf '%s' 'fix: a title' > "$T/pr_title.txt"
    : > "$T/pr_body.md"
    echo "$BR" > "$T/head_ref"
    echo main > "$T/base_ref"
    : > "$T/gh.log"
}
REFSPEC="+refs/heads/$BR:refs/remotes/origin/$BR"

# 1. open_pr pushes and creates the tracking ref; deliver then pushes a new commit.
lease_repo
OUT=$( cd "$T/wt" && sh "$T/open_pr.sh" 2>&1 ); RC=$?
check "open_pr exits 0 on a single-branch clone" "0" "$RC"
check "open_pr creates the tracking ref" \
    "$(git -C "$T/wt" rev-parse HEAD)" \
    "$(git -C "$T/wt" rev-parse -q --verify "refs/remotes/origin/$BR")"
echo more >> "$T/wt/a.txt"
git -C "$T/wt" commit -qam 'fabro(01TEST): review_merge.review_fix (succeeded)'
OUT=$( cd "$T/wt" && sh "$T/deliver.sh" 2>&1 )
check "deliver reports delivered=true" "true" \
    "$(jq -r '.context_updates.delivered' <<<"$(lastjson "$OUT")")"
check "deliver moved the remote branch" \
    "$(git -C "$T/wt" rev-parse HEAD)" "$(git -C "$T/up.git" rev-parse "$BR")"

# 2. A second open_pr (human_rescue -> [P]) adds no duplicate refspec.
( cd "$T/wt" && sh "$T/open_pr.sh" >/dev/null 2>&1 )
check "second open_pr keeps one refspec line" "1" \
    "$(git -C "$T/wt" config --get-all remote.origin.fetch | grep -cxF "$REFSPEC")"

# 3. Control: push WITHOUT the widening, as the old open_pr did; deliver is refused.
lease_repo
( cd "$T/wt" && git push -q -u origin HEAD 2>/dev/null )
echo more >> "$T/wt/a.txt"
git -C "$T/wt" commit -qam 'more'
OUT=$( cd "$T/wt" && sh "$T/deliver.sh" 2>&1 )
check "control: no tracking ref, deliver refused" "false" \
    "$(jq -r '.context_updates.delivered' <<<"$(lastjson "$OUT")")"
check "control: git says stale info" "1" "$(grep -c 'stale info' <<<"$OUT")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# review-merge `watch_checks` — one rerun of the failed jobs before ci_fix (ADR 0011 D2)
#
# Four of the seven "CI-fix could not fix" blocks in the 2026-09-24 review were
# flakes or CI infrastructure faults that passed on a plain rerun. The node now
# reruns the failed Actions jobs once per merge phase, and only while at least
# 30 minutes of merge_deadline remain and the node has run under 20 minutes.
# `date` is stubbed with a controllable clock so the 20-minute guard can be
# reached without waiting; with no clock file it defers to the real `date`.
# ---------------------------------------------------------------------------
echo ""
echo "review-merge watch_checks"
SAVED_PATH="$PATH"
T="$WORK/wchecks"; mkdir -p "$T/bin" "$T/review" "$T/feedback"
extract_from "$SHARED" watch_checks | sed "s#/tmp/fabro#$T#g" > "$T/watch_checks.sh"
if ! sh -n "$T/watch_checks.sh" 2>"$T/watch_checks.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL watch_checks is not valid POSIX sh\n'; sed 's/^/       /' "$T/watch_checks.syntax"
fi
REAL_DATE=$(command -v date)
cat > "$T/bin/sleep" <<'STUB'
#!/bin/sh
exit 0
STUB
cat > "$T/bin/date" <<STUB
#!/bin/sh
if [ "\$1" = "+%s" ] && [ -f "\$GH_STATE/clock" ]; then
    now=\$(cat "\$GH_STATE/clock"); echo "\$now"
    echo \$((now + \$(cat "\$GH_STATE/clock_step"))) > "\$GH_STATE/clock"
    exit 0
fi
exec $REAL_DATE "\$@"
STUB
# `gh pr checks` answers from checks_<n>.json for the n-th poll, repeating the last
# file once the sequence runs out.
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr checks")
      n=0; [ -f "$GH_STATE/polls" ] && n=$(cat "$GH_STATE/polls")
      n=$((n + 1)); echo "$n" > "$GH_STATE/polls"
      while [ "$n" -gt 1 ] && [ ! -f "$GH_STATE/checks_$n.json" ]; do n=$((n - 1)); done
      cat "$GH_STATE/checks_$n.json"; exit 0 ;;
  "run rerun") exit 0 ;;
  "run view")  echo "the failing log"; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/sleep" "$T/bin/date" "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

FAILING='[{"name":"test","state":"FAILURE","bucket":"fail","link":"https://github.com/o/r/actions/runs/111/job/9"}]'
PASSING='[{"name":"test","state":"SUCCESS","bucket":"pass","link":"https://github.com/o/r/actions/runs/111/job/10"}]'
wc_setup() {
    rm -f "$T"/gh.log "$T"/polls "$T"/checks_*.json "$T"/ci_rerun_done "$T"/clock "$T"/clock_step \
          "$T"/gh_fix_attempts "$T"/merge_block_reason "$T"/feedback/ci_fix.md
    : > "$T/gh.log"
    echo 7 > "$T/pr_number"
    echo $(( $("$REAL_DATE" +%s) + 3600 )) > "$T/merge_deadline"
}
wc_run() { sh "$T/watch_checks.sh" 2>&1; }

# 1. A flake: fails, is rerun once, passes. Merges without ci_fix.
wc_setup
printf '%s' "$FAILING" > "$T/checks_1.json"; printf '%s' "$PASSING" > "$T/checks_2.json"
OUT=$(wc_run)
check "flake: checks_ok after the rerun" "true" "$(jq -r '.context_updates.checks_ok' <<<"$(lastjson "$OUT")")"
check "flake: exactly one rerun, of run 111" "1" "$(grep -c '^run rerun 111 --failed$' "$T/gh.log")"
check "flake: no ci_fix attempt spent" "absent" "$([ -f "$T/gh_fix_attempts" ] && echo present || echo absent)"

# 2. A real failure: fails, is rerun once, fails again. Goes to ci_fix tier 1.
wc_setup
printf '%s' "$FAILING" > "$T/checks_1.json"
OUT=$(wc_run)
check "real failure: routes to ci_fix" "false:1" \
    "$(jq -r '"\(.context_updates.checks_ok):\(.context_updates.gh_fix_attempts)"' <<<"$(lastjson "$OUT")")"
check "real failure: still only one rerun" "1" "$(grep -c '^run rerun' "$T/gh.log")"
check "real failure: ci_fix.md carries the log" "1" "$(grep -c 'the failing log' "$T/feedback/ci_fix.md")"

# 3. Second visit in the same merge phase (after a ci_fix push): no second rerun.
wc_setup
: > "$T/ci_rerun_done"; echo 1 > "$T/gh_fix_attempts"
printf '%s' "$FAILING" > "$T/checks_1.json"
OUT=$(wc_run)
check "second visit: no rerun" "0" "$(grep -c '^run rerun' "$T/gh.log")"
check "second visit: ci_fix tier 2" "2" "$(jq -r '.context_updates.gh_fix_attempts' <<<"$(lastjson "$OUT")")"

# 4. Under 30 minutes of budget left: no rerun, straight to ci_fix.
wc_setup
echo $(( $("$REAL_DATE" +%s) + 1000 )) > "$T/merge_deadline"
printf '%s' "$FAILING" > "$T/checks_1.json"
OUT=$(wc_run)
check "low budget: no rerun" "0" "$(grep -c '^run rerun' "$T/gh.log")"
check "low budget: says why" "1" "$(grep -c 'under 30 minutes' <<<"$OUT")"

# 5. The node has already waited 20 minutes: no rerun. The clock advances 700s per
#    `date +%s` call, so by the post-poll check 2100s have passed since T0.
wc_setup
echo 1000000 > "$T/clock"; echo 700 > "$T/clock_step"
echo 1010000 > "$T/merge_deadline"
printf '%s' "$FAILING" > "$T/checks_1.json"
OUT=$(wc_run)
check "20 minutes in node: no rerun" "0" "$(grep -c '^run rerun' "$T/gh.log")"
check "20 minutes in node: says why" "1" "$(grep -c 'already waited 20 minutes' <<<"$OUT")"

# 6. A failing check that is not an Actions run (no /actions/runs/ link): nothing to
#    rerun, so it goes to ci_fix at once instead of waiting for nothing.
wc_setup
printf '%s' '[{"name":"codeql","state":"FAILURE","bucket":"fail","link":"https://github.com/o/r/runs/5"}]' > "$T/checks_1.json"
OUT=$(wc_run)
check "non-Actions failure: no rerun" "0" "$(grep -c '^run rerun' "$T/gh.log")"
check "non-Actions failure: routes to ci_fix" "1" "$(jq -r '.context_updates.gh_fix_attempts' <<<"$(lastjson "$OUT")")"

# 7. The rerun never settles: the node reports blocked at its own 47-minute cap
#    instead of running into its 50m timeout (which routes to mark_needs_human with a
#    generic reason). 300s per `date +%s` call: the rerun starts 900s in, and the
#    pending polls after it reach T0 + 2820 well before merge_deadline.
wc_setup
echo 1000000 > "$T/clock"; echo 300 > "$T/clock_step"
echo 1010000 > "$T/merge_deadline"
printf '%s' "$FAILING" > "$T/checks_1.json"
printf '%s' '[{"name":"test","state":"QUEUED","bucket":"pending","link":"https://github.com/o/r/actions/runs/111/job/11"}]' > "$T/checks_2.json"
OUT=$(wc_run)
check "rerun never settles: one rerun" "1" "$(grep -c '^run rerun' "$T/gh.log")"
check "rerun never settles: blocked" "true" "$(jq -r '.context_updates.checks_blocked' <<<"$(lastjson "$OUT")")"
check "rerun never settles: names the cap" "1" "$(grep -c 'after 47 minutes' "$T/merge_block_reason")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# autofix — the optional per-repository .fabro/fix.sh, always fail-open (ADR 0011 D5)
# ---------------------------------------------------------------------------
echo ""
echo "autofix"
SAVED_PATH="$PATH"
PATH="$ORIG_PATH"
T="$WORK/autofix"; mkdir -p "$T"; stage autofix
af_repo() {
    rm -rf "$T/wt"; mkdir -p "$T/wt"
    git -C "$T/wt" init -q -b main .
    printf 'unformatted\n' > "$T/wt/a.txt"
}
af_run() { ( cd "$T/wt" && sh "$T/autofix.sh" 2>&1 ); }

# 1. No fix.sh: nothing happens, and the stage still succeeds.
af_repo
OUT=$(af_run); RC=$?
check "no fix.sh: exit 0"          "0" "$RC"
check "no fix.sh: says so"         "1" "$(grep -c 'no .fabro/fix.sh' <<<"$OUT")"

# 2. An executable fix.sh that reformats a file: the edit is left in the tree.
af_repo
mkdir -p "$T/wt/.fabro"
printf '%s\n' '#!/bin/sh' 'printf "formatted\n" > a.txt' > "$T/wt/.fabro/fix.sh"
chmod +x "$T/wt/.fabro/fix.sh"
OUT=$(af_run); RC=$?
check "fix.sh ran: exit 0"         "0" "$RC"
check "fix.sh ran: file changed"   "formatted" "$(cat "$T/wt/a.txt")"

# 3. A fix.sh that fails: output kept, stage still succeeds.
af_repo
mkdir -p "$T/wt/.fabro"
printf '%s\n' '#!/bin/sh' 'echo clippy blew up' 'exit 3' > "$T/wt/.fabro/fix.sh"
chmod +x "$T/wt/.fabro/fix.sh"
OUT=$(af_run); RC=$?
check "failing fix.sh: exit 0"     "0" "$RC"
check "failing fix.sh: rc reported" "1" "$(grep -c 'fix.sh exited 3' <<<"$OUT")"
check "failing fix.sh: output kept" "1" "$(grep -c 'clippy blew up' <<<"$OUT")"

# 4. A fix.sh without the executable bit still runs, under bash.
af_repo
mkdir -p "$T/wt/.fabro"
printf '%s\n' 'printf "formatted\n" > a.txt' > "$T/wt/.fabro/fix.sh"
OUT=$(af_run); RC=$?
check "non-executable fix.sh: exit 0" "0" "$RC"
check "non-executable fix.sh: ran"  "formatted" "$(cat "$T/wt/a.txt")"

PATH="$SAVED_PATH"

# ---------------------------------------------------------------------------
# task budget and remainder (ADR 0011 D6)
#
# A run implements at most 8 DECOMPOSED tasks -- counted as tasks improve_gate
# routes to the coder, decompose tasks and their split slices -- and next_task moves
# the decomposed tasks past that to remainder.json. file_remainder then files them
# as ONE issue the scheduler holds until the PR merges. Extra-review follow-ups, and
# slices split from one (`from_extra`), are exempt: they never count and are never
# moved, so they are worked in this run even with the budget spent.
# ---------------------------------------------------------------------------
echo ""
echo "task budget and remainder"
SAVED_PATH="$PATH"
T="$WORK/budget"; mkdir -p "$T/bin" "$T/feedback" "$T/extra"
stage next_task; stage improve_gate; stage extra_prep; stage file_remainder
cat > "$T/bin/git" <<'STUB'
#!/bin/sh
case "$1 $2" in
  "rev-parse --abbrev-ref") echo "fabro/run/01TESTRUN" ;;
esac
exit 0
STUB
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue create")
      [ -f "$GH_STATE/create_fails" ] && { echo "HTTP 422" >&2; exit 1; }
      echo "https://github.com/o/r/issues/77"; exit 0 ;;
  "pr comment")
      # keep the body so the checks can read it
      shift 2; while [ $# -gt 0 ]; do [ "$1" = "--body-file" ] && cp "$2" "$GH_STATE/pr_comment.txt"; shift; done
      exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/git" "$T/bin/gh"
PATH="$T/bin:$ORIG_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

tasks_json() { # tasks_json 1 10 -> ids t1..t10
    i=$1; out=""
    while [ "$i" -le "$2" ]; do
        out="$out{\"id\":\"t$i\",\"title\":\"T$i\",\"body\":\"do t$i\",\"files\":[],\"covers\":[],\"source\":\"decompose\"},"
        i=$((i + 1))
    done
    printf '[%s]' "${out%,}"
}
bd_setup() { # bd_setup <tasks_coded> <task_index> <last task id>
    rm -f "$T"/remainder*.json "$T"/remainder_issue "$T"/remainder_body.md "$T"/pr_comment.txt \
          "$T"/create_fails "$T"/improve_result.json "$T"/extra_round
    : > "$T/gh.log"
    tasks_json 1 "$3" > "$T/tasks.json"
    echo "$1" > "$T/tasks_coded"
    echo "$2" > "$T/task_index"
}

# 1. improve_gate counts a task routed to the coder, and only that.
bd_setup 3 1 2
printf '%s' '{"id":"t1","title":"T1","body":"b","files":[],"covers":[],"source":"decompose"}' > "$T/current_task.json"
printf '%s' '{"disposition":"ready","task":{"title":"T1","body":"sharpened"}}' > "$T/improve_result.json"
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "ready counts toward the budget" "4" "$(cat "$T/tasks_coded")"
printf '%s' '{"disposition":"redundant","reason":"already done"}' > "$T/improve_result.json"
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "redundant does not count"       "4" "$(cat "$T/tasks_coded")"
printf '%s' '{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"}' > "$T/current_task.json"
printf '%s' '{"disposition":"ready","task":{"title":"X1","body":"sharpened"}}' > "$T/improve_result.json"
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "extra-review ready does not count" "4" "$(cat "$T/tasks_coded")"

# 1b. A split of an extra-review follow-up stamps its slices from_extra, and a slice
#     that is then routed to the coder does not count either.
bd_setup 4 9 8
jq '. + [{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"}]' \
    "$T/tasks.json" > "$T/tasks.tmp" && mv "$T/tasks.tmp" "$T/tasks.json"
jq '.[8]' "$T/tasks.json" > "$T/current_task.json"
rm -f "$T/split_rounds"
printf '%s' '{"disposition":"split","tasks":[{"id":"x1a","title":"X1a","body":"b"},{"id":"x1b","title":"X1b","body":"b"}]}' > "$T/improve_result.json"
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "extra split: slices carry from_extra" "true true" "$(jq -r '[.[8:][] | .from_extra | tostring] | join(" ")' "$T/tasks.json")"
check "extra split: nothing counted"   "4" "$(cat "$T/tasks_coded")"
jq '.[8]' "$T/tasks.json" > "$T/current_task.json"
printf '%s' '{"disposition":"ready","task":{"title":"X1a","body":"sharpened"}}' > "$T/improve_result.json"
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "extra slice ready does not count" "4" "$(cat "$T/tasks_coded")"
bd_setup 4 1 2
printf '%s' '{"disposition":"split","tasks":[{"id":"t1a","title":"T1a","body":"b"},{"id":"t1b","title":"T1b","body":"b"}]}' > "$T/improve_result.json"
jq '.[0]' "$T/tasks.json" > "$T/current_task.json"
sh "$T/improve_gate.sh" >/dev/null 2>&1
check "decompose split: slices not exempt" "false false" "$(jq -r '[.[0:2][] | .from_extra | tostring] | join(" ")' "$T/tasks.json")"
rm -f "$T/split_rounds"

# 2. Under budget, next_task selects as before.
bd_setup 7 7 10
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "under budget: selects t8"       "t8" "$(jq -r .id "$T/current_task.json")"
check "under budget: no remainder"     "absent" "$([ -e "$T/remainder.json" ] && echo present || echo absent)"

# 3. Budget spent with two tasks left: both go to the remainder, the run moves on.
bd_setup 8 8 10
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "spent: tasks_done"              "true" "$(jq -r '.context_updates.tasks_done' <<<"$(lastjson "$OUT")")"
check "spent: remainder holds t9 t10"  "t9 t10" "$(jq -r '[.[].id]|join(" ")' "$T/remainder.json")"
check "spent: queue trimmed to 8"      "8" "$(jq length "$T/tasks.json")"
check "spent: says so"                 "1" "$(grep -c 'task budget of 8 decomposed tasks spent' <<<"$OUT")"

# 4. Extra-review follow-ups appended after the budget is spent (as extra_gate does,
#    including a slice split from one) are worked in this run, not moved.
jq '. + [{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"},
         {"id":"x2a","title":"X2a","body":"b","files":[],"covers":[],"source":"split","from_extra":true}]' \
    "$T/tasks.json" > "$T/tasks.tmp" && mv "$T/tasks.tmp" "$T/tasks.json"
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "extras: not done"               "false" "$(jq -r '.context_updates.tasks_done' <<<"$(lastjson "$OUT")")"
check "extras: selects x1"             "x1" "$(jq -r .id "$T/current_task.json")"
check "extras: remainder unchanged"    "t9 t10" "$(jq -r '[.[].id]|join(" ")' "$T/remainder.json")"
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "extras: then selects x2a"       "x2a" "$(jq -r .id "$T/current_task.json")"
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "extras: then done"              "true" "$(jq -r '.context_updates.tasks_done' <<<"$(lastjson "$OUT")")"

# 4b. A decomposed task behind the budget still moves, and joins the remainder
#     without duplicating an id already there; an extra-review task beside it stays.
bd_setup 8 8 10
tasks_json 9 9 > "$T/remainder.json"
jq '. + [{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"}]' \
    "$T/tasks.json" > "$T/tasks.tmp" && mv "$T/tasks.tmp" "$T/tasks.json"
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "mixed: remainder t9 t10"        "t9 t10" "$(jq -r '[.[].id]|join(" ")' "$T/remainder.json")"
check "mixed: queue keeps x1 at slot 9" "x1" "$(jq -r '.[8].id' "$T/tasks.json")"
check "mixed: selects x1"              "x1" "$(jq -r .id "$T/current_task.json")"

# 5. extra_prep: a remainder does not stop the second extra-review round; the
#    round cap of 2 still does.
echo 0 > "$T/extra_round"
OUT=$(sh "$T/extra_prep.sh" 2>/dev/null)
check "first extra round runs"         "false" "$(jq -r '.context_updates.extra_done' <<<"$(lastjson "$OUT")")"
OUT=$(sh "$T/extra_prep.sh" 2>/dev/null)
check "second round runs with a remainder" "false" "$(jq -r '.context_updates.extra_done' <<<"$(lastjson "$OUT")")"
OUT=$(sh "$T/extra_prep.sh" 2>/dev/null)
check "round cap still ends it"        "true" "$(jq -r '.context_updates.extra_done' <<<"$(lastjson "$OUT")")"

# 6. file_remainder with nothing to file: no gh call at all.
bd_setup 3 3 3
printf '%s' '{"number":350,"title":"Big issue"}' > "$T/issue.json"; echo 7 > "$T/pr_number"
OUT=$(sh "$T/file_remainder.sh" 2>&1); RC=$?
check "no remainder: exit 0"           "0" "$RC"
check "no remainder: no gh call"       "0" "$(wc -l < "$T/gh.log" | tr -d ' ')"

# 7. file_remainder files one held issue, with the marker first, and says so on the PR.
tasks_json 9 10 > "$T/remainder.json"
OUT=$(sh "$T/file_remainder.sh" 2>&1); RC=$?
check "files: exit 0"                  "0" "$RC"
check "files: one issue create"        "1" "$(grep -c '^issue create' "$T/gh.log")"
check "files: held, not queued"        "1" "$(grep '^issue create' "$T/gh.log" | grep -c -- '--label agent-remainder --label ai-generated')"
check "files: never labels agent"      "0" "$(grep '^issue create' "$T/gh.log" | grep -c -- '--label agent ')"
check "files: marker is the first line" "<!-- fabro:remainder parent=350 pr=7 -->" "$(head -1 "$T/remainder_body.md")"
check "files: lists the tasks"         "2" "$(grep -c '^### T' "$T/remainder_body.md")"
check "files: records the number"      "77" "$(cat "$T/remainder_issue")"
check "files: PR comment names it"     "yes" "$(grep -q '#77' "$T/pr_comment.txt" && echo yes || echo no)"

# 8. A second visit (human_rescue -> [P] -> open_pr) files nothing new.
: > "$T/gh.log"
sh "$T/file_remainder.sh" >/dev/null 2>&1
check "second visit: no new issue"     "0" "$(grep -c '^issue create' "$T/gh.log")"

# 9. If the issue cannot be filed, the tasks are listed on the PR instead.
rm -f "$T/remainder_issue" "$T/pr_comment.txt"; : > "$T/create_fails"; : > "$T/gh.log"
OUT=$(sh "$T/file_remainder.sh" 2>&1); RC=$?
check "create fails: still exit 0"     "0" "$RC"
check "create fails: tasks on the PR"  "1" "$(grep -c '^- T10$' "$T/pr_comment.txt")"
check "create fails: nothing recorded" "absent" "$([ -e "$T/remainder_issue" ] && echo present || echo absent)"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# triage phase (_shared/triage) — claim, triage_gate, terminal outcome (ADR 0012)
# ---------------------------------------------------------------------------
echo ""
echo "triage phase"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/triage"; mkdir -p "$T/bin"
for n in claim triage_gate post_questions release done; do
    extract_from "$SHARED_TRIAGE" "$n" | sed "s#/tmp/fabro#$T#g" > "$T/$n.sh"
    if ! sh -n "$T/$n.sh" 2>"$T/$n.syntax"; then
        FAIL=$((FAIL + 1)); printf '  FAIL %s is not valid POSIX sh\n' "$n"
    fi
done
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue view") cat "$GH_STATE/issue_fixture.json"; exit 0 ;;
esac
if [ "$1" = api ]; then
  [ -f "$GH_STATE/events_fail" ] && exit 1
  cat "$GH_STATE/events.json"; exit 0
fi
exit 0
STUB
chmod +x "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

tp_setup() { # tp_setup <labels json array> <labeled created_at or empty>
    rm -f "$T"/gh.log "$T"/triage_outcome "$T"/events_fail "$T"/issue.json
    echo 7 > "$T/issue_number"
    printf '{"number":7,"title":"t","body":"b","labels":%s,"comments":[],"url":"https://github.com/o/r/issues/7"}' "$1" > "$T/issue_fixture.json"
    if [ -n "${2:-}" ]; then
        printf '[{"event":"labeled","label":{"name":"triage-in-progress"},"created_at":"%s"}]' "$2" > "$T/events.json"
    else
        echo '[]' > "$T/events.json"
    fi
}

# 1. No claim on the issue: claim it.
tp_setup '[]'
OUT=$(sh "$T/claim.sh" 2>&1); RC=$?
check "claim: exit 0"                 "0" "$RC"
check "claim: claimed"                "claimed" "$(jq -r '.context_updates.claim_state' <<<"$(lastjson "$OUT")")"
check "claim: issue_url published"    "https://github.com/o/r/issues/7" "$(jq -r '.context_updates.issue_url' <<<"$(lastjson "$OUT")")"
check "claim: adds the claim label"   "1" "$(grep -c '^issue edit 7 --add-label triage-in-progress' "$T/gh.log")"

# 2. Another run claimed it less than 24h ago (a future date is always fresh).
tp_setup '[{"name":"triage-in-progress"}]' '2099-01-01T00:00:00Z'
OUT=$(sh "$T/claim.sh" 2>&1)
check "fresh claim: skipped"          "skipped" "$(jq -r '.context_updates.claim_state' <<<"$(lastjson "$OUT")")"
check "fresh claim: outcome file"     "skipped" "$(cat "$T/triage_outcome")"
check "fresh claim: no edit"          "0" "$(grep -c '^issue edit' "$T/gh.log")"

# 3. The claim is older than 24h: take it over.
tp_setup '[{"name":"triage-in-progress"}]' '2020-01-01T00:00:00Z'
OUT=$(sh "$T/claim.sh" 2>&1)
check "stale claim: claimed"          "claimed" "$(jq -r '.context_updates.claim_state' <<<"$(lastjson "$OUT")")"
check "stale claim: edits"            "1" "$(grep -c '^issue edit 7 --add-label triage-in-progress' "$T/gh.log")"

# 4. The event list cannot be read: fail closed to skipped.
tp_setup '[{"name":"triage-in-progress"}]' '2020-01-01T00:00:00Z'; : > "$T/events_fail"
OUT=$(sh "$T/claim.sh" 2>&1)
check "events unreadable: skipped"    "skipped" "$(jq -r '.context_updates.claim_state' <<<"$(lastjson "$OUT")")"

# 5. done publishes the file, and anything else as released.
echo skipped > "$T/triage_outcome"
check "done: publishes the word"      "skipped"  "$(jq -r '.context_updates.triage_outcome' <<<"$(lastjson "$(sh "$T/done.sh" 2>&1)")")"
rm -f "$T/triage_outcome"
check "done: no file is released"     "released" "$(jq -r '.context_updates.triage_outcome' <<<"$(lastjson "$(sh "$T/done.sh" 2>&1)")")"
echo garbage > "$T/triage_outcome"
check "done: garbage is released"     "released" "$(jq -r '.context_updates.triage_outcome' <<<"$(lastjson "$(sh "$T/done.sh" 2>&1)")")"

# 6. triage_gate: needs_info with one question.
tp_setup '[]'
echo 0 > "$T/triage_attempts"
echo '{"readiness":"needs_info","title":"feat: x","labels":["bug"],"questions":[{"id":"Q1","question":"Which endpoint?","why":"w","recommended":"/v2"}]}' > "$T/triage.json"
echo 'report' > "$T/triage.md"
OUT=$(sh "$T/triage_gate.sh" 2>&1); RC=$?
check "gate needs_info: exit 0"       "0" "$RC"
check "gate needs_info: readiness"    "needs_info" "$(jq -r '.context_updates.triage_readiness' <<<"$(lastjson "$OUT")")"
check "gate: no answered key"         "false" "$(jq -r '.context_updates | has("answered")' <<<"$(lastjson "$OUT")")"

# 7. triage_gate: ready with a question is invalid; the first attempt fails.
echo 0 > "$T/triage_attempts"
echo '{"readiness":"ready","title":"feat: x","labels":[],"questions":[{"id":"Q1","question":"q"}]}' > "$T/triage.json"
sh "$T/triage_gate.sh" >/dev/null 2>&1; RC=$?
check "gate ready+question: retries"  "1" "$RC"

# 8. release removes the claim and records released.
printf '{"number":7}' > "$T/issue.json"; : > "$T/gh.log"
sh "$T/release.sh" >/dev/null 2>&1
check "release: outcome file"         "released" "$(cat "$T/triage_outcome")"
check "release: removes the claim"    "1" "$(grep -c '^issue edit 7 --remove-label triage-in-progress' "$T/gh.log")"

# 9. post_questions labels needs-info and records needs_info.
printf '{"number":7,"labels":[{"name":"needs-triage"},{"name":"triage-in-progress"}]}' > "$T/issue.json"
echo '{"readiness":"needs_info","title":"feat: x","labels":["bug"],"questions":[{"id":"Q1","question":"q"}]}' > "$T/triage.json"
: > "$T/gh.log"
sh "$T/post_questions.sh" >/dev/null 2>&1
check "post_questions: outcome file"  "needs_info" "$(cat "$T/triage_outcome")"
check "post_questions: needs-info"    "1" "$(grep -c -- '--add-label needs-info' "$T/gh.log")"

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
