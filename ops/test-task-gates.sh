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
# alongside `check-routing-schemas.py`. It needs only jq, awk and git -- the
# extractor is awk, not python3, so the suite also runs inside every sandbox
# profile image, against the mawk and jq the nodes really run under.
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
ARCH="$REPO_ROOT/.fabro/workflows/arch-review/workflow.fabro"
[ -f "$ARCH" ] || { echo "ERROR: $ARCH not found" >&2; exit 1; }
PR="$REPO_ROOT/.fabro/workflows/pr-review/workflow.fabro"
[ -f "$PR" ] || { echo "ERROR: $PR not found" >&2; exit 1; }
INTRIAGE="$REPO_ROOT/.fabro/workflows/issue-triage/workflow.fabro"
[ -f "$INTRIAGE" ] || { echo "ERROR: $INTRIAGE not found" >&2; exit 1; }

command -v jq >/dev/null || { echo "ERROR: jq is required" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# A gate that runs git (improve_gate's backstop reverts HEAD) must run in a scratch
# directory, never here. 2026-10-01: two unwrapped runs reverted the repo's own HEAD.
# The last check of the suite compares this snapshot.
REPO_STATE_BEFORE="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)|$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null | cksum)"

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
    awk -v pfx="    $2 [" '
function scan(s,  i, c) {
    for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\\" && substr(s, i + 1, 1) == "\"") { printf "\""; i++; continue }
        if (c == "\"") return 1
        printf "%s", c
    }
    return 0
}
st == 0 && substr($0, 1, length(pfx)) == pfx { st = 1 }
st == 1 && (k = index($0, "script=\"")) { st = 2; if (scan(substr($0, k + 8))) { done = 1; exit } next }
st == 2 { printf "\n"; if (scan($0)) { done = 1; exit } }
END { if (!done) exit 1 }
' "$1"
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

# A valid fabro-io inputs receipt for the current stage (ADR 0016 C7): stage.json
# carries visit $1 and served.json records every named input as fully served at
# that visit. Gates that now check the receipt read $T/.io/stage.json + served.json.
mkreadok() {
    local v="$1"; shift
    mkdir -p "$T/.io"
    printf '%s' "{\"workflow\":\"backlog\",\"node\":\"test\",\"visit\":\"$v\",\"started\":\"2026-01-01T00:00:00Z\"}" > "$T/.io/stage.json"
    local req
    req=$(printf '%s\n' "$@" | jq -Rn '[inputs]')
    jq -n --arg v "$v" --argjson req "$req" \
      '{visit:$v, inputs:($req | map(. as $n | {key:$n, value:{path:("/tmp/fabro/"+$n),parts:1,served:[1],status:"ok"}}) | from_entries)}' > "$T/.io/served.json"
}

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
    printf '%s' "$1" > "$T/decomposition.json" && jq --arg v ok1 '._io.visit=$v' "$T/decomposition.json" > /tmp/x && mv /tmp/x "$T/decomposition.json"
}

mkreadok ok1 issue.json

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

# needs_human_review routes straight to mark_stuck, which posts the summary on the
# issue. The gate is the only writer of stuck_reason.md; prep deletes it.
rm -f "$T/stuck_reason.md"
dg_setup '{"status":"needs_human_review","summary":"blocked by #1278, which is not merged","issues":[]}'
OUT=$(sh "$T/decompose_gate.sh" 2>&1); RC=$?; J=$(lastjson "$OUT")
check "needs_human_review exits 0"     "0" "$RC"
check "needs_human_review routes"      "needs_human_review" "$(jq -r '.context_updates.decomp_status' <<<"$J")"
check "needs_human_review writes reason" "blocked by #1278, which is not merged" "$(cat "$T/stuck_reason.md" 2>/dev/null)"
check "needs_human_review one routing object" "1" "$(grep -c 'context_updates' <<<"$OUT")"
rm -f "$T/stuck_reason.md"
dg_setup '{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[]}]}'
sh "$T/decompose_gate.sh" >/dev/null 2>&1
check "issues writes no stuck reason"  "absent" "$([ -e "$T/stuck_reason.md" ] && echo present || echo absent)"

# ---------------------------------------------------------------------------
# improve_gate — the split splice and its guards
# ---------------------------------------------------------------------------
echo ""
echo "improve_gate"
T="$WORK/ig"; mkdir -p "$T"; stage improve_gate
mkreadok ok1 current_task.json issue.json

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
printf '%s' "$3" > "$T/improve_result.json" && jq --arg v ok1 '._io.visit=$v' "$T/improve_result.json" > /tmp/x && mv /tmp/x "$T/improve_result.json"
    echo "$4" > "$T/task_index"
    echo "$5" > "$T/split_rounds"
}

ig_setup "$TASKS" "$CUR" "$SPLIT2" 2 0
OUT=$( (cd "$T" && sh "$T/improve_gate.sh") 2>/dev/null); RC=$?; J=$(lastjson "$OUT")
check "split exits 0"               "0"     "$RC"
check "split disposition"           "split" "$(jq -r '.context_updates.task_disposition' <<<"$J")"
check "spliced in place, in order"  "a big-module big-migrate z" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "slices stamped source"       "decompose split split decompose" "$(jq -r '[.[].source]|join(" ")' "$T/tasks.json")"
check "cursor rewound to the slot"  "1"     "$(cat "$T/task_index")"
check "split_rounds incremented"    "1"     "$(cat "$T/split_rounds")"
check "task_count republished"      "4"     "$(jq -r '.context_updates.task_count' <<<"$J")"

# Guard 1: a slice can never be split again.
ig_setup "$TASKS" '{"id":"big-1","title":"S","body":"b","files":[],"source":"split"}' "$SPLIT2" 2 0
OUT=$( (cd "$T" && sh "$T/improve_gate.sh") 2>/dev/null)
check "slice refuses re-split"      "ready"   "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "refusal leaves queue alone"  "a big z" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "refusal keeps current_task"  "big-1"   "$(jq -r .id "$T/current_task.json")"

# Guard 2: the per-run budget.
ig_setup "$TASKS" "$CUR" "$SPLIT2" 2 2
OUT=$( (cd "$T" && sh "$T/improve_gate.sh") 2>/dev/null)
check "spent budget coerces ready"  "ready" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "spent budget keeps current"  "big"   "$(jq -r .id "$T/current_task.json")"

ig_setup "$TASKS" "$CUR" '{"disposition":"split","tasks":[{"id":"x","title":"X","body":"b","files":[]}]}' 2 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "one slice is invalid"        "1" "$?"

ig_setup "$TASKS" "$CUR" '{"disposition":"split","tasks":[{"id":"z","title":"X","body":"b","files":[]},{"id":"q","title":"Q","body":"b","files":[]}]}' 2 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "id colliding with queue"     "1" "$?"

ig_setup "$TASKS" "$CUR" '{"disposition":"split","tasks":[{"id":"dup","title":"X","body":"b","files":[]},{"id":"dup","title":"Q","body":"b","files":[]}]}' 2 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "duplicate ids within split"  "1" "$?"

# The replaced task's own id is free to reuse -- it is leaving the queue.
ig_setup "$TASKS" "$CUR" '{"disposition":"split","tasks":[{"id":"big","title":"X","body":"b","files":[]},{"id":"q","title":"Q","body":"b","files":[]}]}' 2 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "slice may reuse replaced id" "0" "$?"

# Splice at both ends of the queue.
ig_setup "$TASKS" '{"id":"a","source":"decompose"}' "$SPLIT2" 1 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "splice at head"              "big-module big-migrate big z" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"
check "splice at head rewinds to 0" "0" "$(cat "$T/task_index")"

ig_setup "$TASKS" '{"id":"z","source":"decompose"}' "$SPLIT2" 3 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "splice at tail"              "a big big-module big-migrate" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"

# The pre-existing dispositions must be untouched by all of the above.
ig_setup "$TASKS" "$CUR" '{"disposition":"ready","task_id":"big","task_title":"Sharpened","task_body":"nb","task_files":[],"task_covers":["c1"]}' 2 0
OUT=$( (cd "$T" && sh "$T/improve_gate.sh") 2>/dev/null)
check "ready disposition"           "ready"     "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "ready rewrites current_task" "Sharpened" "$(jq -r .title "$T/current_task.json")"
check "ready flat: body"            "nb"        "$(jq -r .body "$T/current_task.json")"
check "ready flat: explicit covers" "c1"        "$(jq -r '.covers|join(" ")' "$T/current_task.json")"

# Flat ready: id, files, covers and priority default from the current task, and an
# id the agent supplies cannot replace it with another (the gate keeps byte-for-byte).
CURP='{"id":"big","title":"Big","body":"b","files":["f.py"],"covers":["c1","c2"],"priority":"high","source":"decompose"}'
ig_setup "$TASKS" "$CURP" '{"disposition":"ready","task_id":"other","task_title":"S","task_body":"nb"}' 2 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "flat ready: id from current"       "big"   "$(jq -r .id "$T/current_task.json")"
check "flat ready: files from current"    "f.py"  "$(jq -r '.files|join(" ")' "$T/current_task.json")"
check "flat ready: covers from current"   "c1 c2" "$(jq -r '.covers|join(" ")' "$T/current_task.json")"
check "flat ready: priority from current" "high"  "$(jq -r .priority "$T/current_task.json")"
check "flat ready: no null keys"          "0"     "$(jq '[.[]|select(.==null)]|length' "$T/current_task.json")"

# The nested shape that corrupted in run 01M3SFYASZQ7MEKVKR1A38C4J0 is no longer valid
# input, and an empty body is not a body.
ig_setup "$TASKS" "$CUR" '{"disposition":"ready","task":{"id":"big","title":"S","body":"nb"}}' 2 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "nested task is refused"            "1"     "$?"
ig_setup "$TASKS" "$CUR" '{"disposition":"ready","task_title":"S","task_body":""}' 2 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "empty task_body is refused"        "1"     "$?"
check "ready leaves queue alone"    "a big z"   "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"

ig_setup "$TASKS" "$CUR" '{"disposition":"redundant","reason":"done"}' 2 0
OUT=$( (cd "$T" && sh "$T/improve_gate.sh") 2>/dev/null)
check "redundant disposition"       "redundant" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"

ig_setup "$TASKS" "$CUR" '{"disposition":"nonsense"}' 2 0
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "unknown disposition retries" "1" "$?"

# A truncated counter file must not read as "budget unspent". Unquoted `[ $SR
# -ge 2 ]` on an empty value raises "unary operator expected", which short-
# circuits the && and silently PERMITS the split.
ig_setup "$TASKS" "$CUR" "$SPLIT2" 2 ""
: > "$T/split_rounds"
OUT=$( (cd "$T" && sh "$T/improve_gate.sh") 2>/dev/null)
check "empty split_rounds is not a crash" "split" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "empty split_rounds counts as 0"    "1"     "$(cat "$T/split_rounds")"

# Same for a garbage counter: treat as spent-from-zero, never as an error.
ig_setup "$TASKS" "$CUR" "$SPLIT2" 2 "garbage"
OUT=$( (cd "$T" && sh "$T/improve_gate.sh") 2>/dev/null)
check "garbage split_rounds normalised"   "split" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"

# `oversized_tasks` and `task_count` are interpolated bare into the routing
# JSON, so an empty value emits `"oversized_tasks":}` -- unparseable, which makes
# fabro's routing scan go inert with no error anywhere (AGENTS.md invariant 1).
T="$WORK/dg2"; mkdir -p "$T"; stage decompose_gate
mkreadok ok1 issue.json
printf '%s' '{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"]}],"_io":{"visit":"ok1"}}' > "$T/decomposition.json"
OUT=$(sh "$T/decompose_gate.sh" 2>&1)
check "routing JSON always parses"  "0" "$(jq -e . >/dev/null 2>&1 <<<"$(lastjson "$OUT")"; echo $?)"
check "oversized_tasks is a number" "number" "$(jq -r '.context_updates.oversized_tasks | type' <<<"$(lastjson "$OUT")")"

# ---------------------------------------------------------------------------
# improve_gate backstop (docs/coder-tweaks/06): REAL git. An improve checkpoint
# that changed the tree is reverted (staged, not committed) and counted; a clean
# one is left alone. Only HEAD is reverted, never the stage before it.
# ---------------------------------------------------------------------------
echo ""
echo "improve_gate backstop"
export ORIG_PATH_SAVE="$PATH"; PATH="$ORIG_PATH"
T="$WORK/igb"; mkdir -p "$T/repo"; stage improve_gate
mkreadok ok1 current_task.json issue.json
ig_git_setup() {
    rm -rf "$T/repo" "$T"/improve_touched_tree; mkdir -p "$T/repo"
    ( cd "$T/repo" && git init -q . && git config user.email t@t && git config user.name t \
      && echo one > mod.txt && echo two > del.txt && git add -A && git commit -qm base \
      && echo coder > coder.txt && git add -A && git commit -qm coder-checkpoint )
    printf '%s' "$TASKS" > "$T/tasks.json"; printf '%s' "$CUR" > "$T/current_task.json"
    printf '%s' '{"disposition":"redundant","reason":"r","_io":{"visit":"ok1"}}' > "$T/improve_result.json"
    echo 2 > "$T/task_index"; echo 0 > "$T/split_rounds"
}
ig_git_setup
( cd "$T/repo" && echo changed > mod.txt && git rm -q del.txt && echo new > add.txt \
  && git add -A && git commit -qm improve-checkpoint )
OUT=$( (cd "$T/repo" && sh "$T/improve_gate.sh") 2>/dev/null); RC=$?
check "touched improve: exits 0"           "0" "$RC"
check "touched improve: routing unchanged" "redundant" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
check "touched improve: file count"        "3" "$(cat "$T/improve_touched_tree" 2>/dev/null)"
check "touched improve: names the files"   "3" "$(grep -c '^improve changed (reverted): ' <<<"$OUT")"
check "touched improve: tree is the coder's" "" "$(cd "$T/repo" && git diff --cached --name-only HEAD^ -- coder.txt)"
check "touched improve: mod.txt restored"  "one" "$(cat "$T/repo/mod.txt")"
check "touched improve: del.txt restored"  "two" "$(cat "$T/repo/del.txt")"
check "touched improve: add.txt removed"   "1" "$([ -e "$T/repo/add.txt" ]; echo $?)"
check "touched improve: coder work kept"   "coder" "$(cat "$T/repo/coder.txt")"
check "touched improve: revert is staged, not committed" "improve-checkpoint" "$(cd "$T/repo" && git log -1 --format=%s)"
ig_git_setup
( cd "$T/repo" && git commit -q --allow-empty -m improve-checkpoint )
OUT=$( (cd "$T/repo" && sh "$T/improve_gate.sh") 2>/dev/null)
check "clean improve: nothing written"     "1" "$([ -e "$T/improve_touched_tree" ]; echo $?)"
check "clean improve: nothing reverted"    "" "$(cd "$T/repo" && git status --porcelain)"
check "clean improve: routing unchanged"   "redundant" "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
PATH="$ORIG_PATH_SAVE"

# ---------------------------------------------------------------------------
# rework_router — `task_exempt` is looked up in tasks.json by id, because
# improve_gate rewrites current_task.json without `source`/`from_extra`. Only an
# exempt task takes the round-6 edge to drop_followup.
# ---------------------------------------------------------------------------
echo ""
echo "rework_router"
T="$WORK/rr"; mkdir -p "$T"; stage rework_router
printf '%s' '[{"id":"t1","source":"decompose"},{"id":"f1","source":"extra-review"},{"id":"s1","source":"split","from_extra":true},{"id":"s2","source":"split","from_extra":false}]' > "$T/tasks.json"
rr_run() { printf '%s' "{\"id\":\"$1\",\"title\":\"T\"}" > "$T/current_task.json"; echo "$2" > "$T/round"; lastjson "$(sh "$T/rework_router.sh" 2>&1)"; }
J=$(rr_run f1 5)
check "round increments"               "6"     "$(jq -r '.context_updates.round' <<<"$J")"
check "extra-review follow-up exempt"  "true"  "$(jq -r '.context_updates.task_exempt' <<<"$J")"
check "round persisted"                "6"     "$(cat "$T/round")"
check "slice of a follow-up exempt"    "true"  "$(jq -r '.context_updates.task_exempt' <<<"$(rr_run s1 0)")"
check "decomposed task not exempt"     "false" "$(jq -r '.context_updates.task_exempt' <<<"$(rr_run t1 5)")"
check "slice of a decomposed task not exempt" "false" "$(jq -r '.context_updates.task_exempt' <<<"$(rr_run s2 5)")"
check "unknown id not exempt"          "false" "$(jq -r '.context_updates.task_exempt' <<<"$(rr_run nope 5)")"
rm -f "$T/tasks.json"
J=$(rr_run f1 0)
check "no tasks.json not exempt"       "false" "$(jq -r '.context_updates.task_exempt' <<<"$J")"
check "no tasks.json still counts"     "1"     "$(jq -r '.context_updates.round' <<<"$J")"
check "no round file starts at 1"      "1"     "$(rm -f "$T/round"; printf '%s' '{"id":"x"}' > "$T/current_task.json"; jq -r '.context_updates.round' <<<"$(lastjson "$(sh "$T/rework_router.sh" 2>&1)")")"

# ---------------------------------------------------------------------------
# drop_followup — REAL git. The follow-up's commits are undone back to the task
# base (added files deleted, modified and deleted files restored), work from
# earlier tasks stays, nothing is committed (the checkpoint does that), and the
# drop is recorded in completed.md for the PR body.
# ---------------------------------------------------------------------------
echo ""
echo "drop_followup"
export ORIG_PATH_SAVE="$PATH"; PATH="$ORIG_PATH"
T="$WORK/df"; mkdir -p "$T/repo" "$T/review"; stage drop_followup
( cd "$T/repo" && git init -q . && git config user.email t@t && git config user.name t \
  && echo one > mod.txt && echo two > del.txt && echo earlier > earlier.txt && git add -A && git commit -qm task-base )
git -C "$T/repo" rev-parse HEAD > "$T/task_base_sha"
( cd "$T/repo" && echo changed > mod.txt && git rm -q del.txt && echo new > add.txt && mkdir -p d && echo x > d/f.txt \
  && git add -A && git commit -qm followup-rework )
printf '%s' '{"id":"update-vitest-docs","title":"Fix the CI comment"}' > "$T/current_task.json"
printf '%s' '{"decision":"changes_requested","summary":"the workflow comment is still stale"}' > "$T/review/verdict.json"
echo '### Task: earlier' > "$T/completed.md"
OUT=$( (cd "$T/repo" && sh "$T/drop_followup.sh") 2>&1); RC=$?
check "drop: exits 0"                  "0"     "$RC"
check "drop: tree equals the task base" "0"    "$(cd "$T/repo" && git diff --quiet "$(cat "$T/task_base_sha")" --; echo $?)"
check "drop: mod.txt restored"         "one"   "$(cat "$T/repo/mod.txt")"
check "drop: del.txt restored"         "two"   "$(cat "$T/repo/del.txt" 2>/dev/null)"
check "drop: add.txt removed"          "absent" "$([ -e "$T/repo/add.txt" ] && echo present || echo absent)"
check "drop: nested added file removed" "absent" "$([ -e "$T/repo/d/f.txt" ] && echo present || echo absent)"
check "drop: earlier work kept"        "earlier" "$(cat "$T/repo/earlier.txt")"
check "drop: not committed"            "followup-rework" "$(cd "$T/repo" && git log -1 --format=%s)"
check "drop: earlier entries kept"     "1"     "$(grep -c '^### Task: earlier$' "$T/completed.md")"
check "drop: recorded for the PR body" "1"     "$(grep -c '^### Dropped follow-up: Fix the CI comment$' "$T/completed.md")"
check "drop: names the task id"        "1"     "$(grep -c 'update-vitest-docs' "$T/completed.md")"
check "drop: carries the last review"  "1"     "$(grep -c 'Last review: the workflow comment is still stale' "$T/completed.md")"
rm -f "$T/task_base_sha"
( cd "$T/repo" && sh "$T/drop_followup.sh" >/dev/null 2>&1 ); RC=$?
check "drop: no task base fails"       "1"     "$([ $RC -ne 0 ] && echo 1 || echo 0)"
echo 0000000000000000000000000000000000000000 > "$T/task_base_sha"
( cd "$T/repo" && sh "$T/drop_followup.sh" >/dev/null 2>&1 ); RC=$?
check "drop: unknown task base fails"  "1"     "$([ $RC -ne 0 ] && echo 1 || echo 0)"
PATH="$ORIG_PATH_SAVE"

# ---------------------------------------------------------------------------
# next_task drops the baseline_check worktree (docs/coder-tweaks 04): REAL git, so the
# stale `base-tree` of the last task is gone before the next one starts, and the node
# still prints exactly one routing object.
# ---------------------------------------------------------------------------
echo ""
echo "next_task worktree cleanup"
PATH="$ORIG_PATH"
T="$WORK/ntwt"; mkdir -p "$T/repo"; stage next_task
mkreadok ok1 current_task.json issue.json
( cd "$T/repo" && git init -q -b main . && git config user.email t@t && git config user.name t \
  && echo a > a.txt && git add -A && git commit -qm base && echo b > b.txt && git add -A && git commit -qm second \
  && git worktree add -q --detach "$T/base-tree" HEAD~1 && mkdir -p "$T/base-tree/node_modules" && ln -s "$T/repo" "$T/base-tree/linked" )
echo '[]' > "$T/tasks.json"; echo 0 > "$T/task_index"
check "worktree exists before"        "2" "$(git -C "$T/repo" worktree list | wc -l | tr -d ' ')"
OUT=$( (cd "$T/repo" && sh "$T/next_task.sh") 2>/dev/null)
check "stale worktree removed"        "1" "$(git -C "$T/repo" worktree list | wc -l | tr -d ' ')"
check "stale worktree dir removed"    "absent" "$([ -e "$T/base-tree" ] && echo present || echo absent)"
check "link target survives"          "1" "$([ -f "$T/repo/a.txt" ] && echo 1 || echo 0)"
check "one routing object"            "1" "$(grep -c 'context_updates' <<<"$OUT")"
check "routing says done"             "true" "$(jq -r '.context_updates.tasks_done' <<<"$(lastjson "$OUT")")"
( cd "$T/repo" && sh "$T/next_task.sh" >/dev/null 2>&1 ); RC=$?
check "no worktree at all is fine"    "0" "$RC"

# ---------------------------------------------------------------------------
# next_task + improve_gate — the split must resume ON the first slice
# ---------------------------------------------------------------------------
echo ""
echo "next_task <-> improve_gate"
T="$WORK/loop"; mkdir -p "$T/bin" "$T/feedback"; stage next_task; stage improve_gate
mkreadok ok1 current_task.json issue.json
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
 {"id":"big-adr","title":"Record ADR","body":"z","files":[],"covers":["c4","c5"]}],"_io":{"visit":"ok1"}}
EOF
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "queue spliced"               "small big-module big-migrate big-adr" "$(jq -r '[.[].id]|join(" ")' "$T/tasks.json")"

# The regression this guards: an off-by-one here silently SKIPS the first slice.
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "resumes on the first slice"  "big-module" "$(jq -r .id "$T/current_task.json")"
check "disposition reset by next"   "none"       "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"

(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
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
printf '%s' '{"disposition":"split","tasks":[],"_io":{"visit":"ok1"}}'> "$T/improve_result.json"
sh "$T/next_task.sh" >/dev/null 2>&1
check "next_task clears improve_result" "absent" \
    "$([ -e "$T/improve_result.json" ] && echo present || echo absent)"

# Task 04 / 07: next_task also deletes the task dossier (delete-before-write) and
# re-renders the map after the mainline refresh, discarding fabro-code's stdout --
# this node's stdout is scanned for `context_updates`, so a leaked map would risk a
# wrong route. A stub fabro-code that prints to stdout proves the discard.
cat > "$T/bin/fabro-code" <<'STUB'
#!/bin/sh
echo 'MAP LEAKED TO STDOUT'
exit 0
STUB
chmod +x "$T/bin/fabro-code"
printf '%s' '# Task dossier: stale' > "$T/task-context.md"
printf '%s' '# Code the task dossier cites: stale' > "$T/task-code.md"
cat > "$T/tasks.json" <<'EOF'
[{"id":"t1","title":"T1","body":"b","files":[],"covers":["c1"],"source":"decompose"}]
EOF
echo 0 > "$T/task_index"
OUT=$(sh "$T/next_task.sh" 2>&1)
check "next_task deletes task-context" "absent" \
    "$([ -e "$T/task-context.md" ] && echo present || echo absent)"
check "next_task deletes task-code" "absent" \
    "$([ -e "$T/task-code.md" ] && echo present || echo absent)"
check "next_task discards fabro-code stdout" "0" "$(grep -c 'MAP LEAKED' <<<"$OUT")"
check "next_task still one routing object" "1" "$(grep -c '^{.*}$' <<<"$OUT")"

# ---------------------------------------------------------------------------
# Code-index entry fragments (docs/code-context C4, tasks 04 and 05)
# ---------------------------------------------------------------------------
# backlog `prep` cannot run whole here (it runs the target repo's setup.sh), so its
# two index lines are pulled out of the graph's own `prep` script -- never retyped --
# and run under `set -e`, which is how prep runs them. The other three entry nodes
# run WHOLE, from their own graphs, in a real git repo with `gh` stubbed:
# pr-review `claim` (a routing node, so stdout must stay exactly one routing
# object), arch-review `prep` (set -e, stdout must stay `ready`) and issue-triage
# `acquire` (no set -e, no stdout). Each runs twice with a `fabro-code` that
# prints to stdout, and once with no `fabro-code` at all -- an image built before
# task 02.
# ---------------------------------------------------------------------------
echo ""
echo "code-index entry fragments"
PATH="$ORIG_PATH"   # real git; the task-queue section left an `exit 0` git stub on PATH

# The tools a node needs, minus fabro-code, for the "old image" runs. On the Mac jq
# is under /opt/homebrew, and in the images fabro-code sits next to gh in
# /usr/local/bin, so neither PATH can simply be trimmed.
NOFC_BIN="$WORK/nofc-bin"; mkdir -p "$NOFC_BIN"
for tool in jq git; do ln -sf "$(command -v "$tool")" "$NOFC_BIN/$tool"; done
NOFC_PATH="$NOFC_BIN:/usr/bin:/bin"

entry_repo() {   # $1 = dir: a real repo with one commit and a stub bin/
    rm -rf "$1"; mkdir -p "$1/repo" "$1/bin" "$1/sb"
    git -C "$1/repo" init -q -b main .
    git -C "$1/repo" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
    cat > "$1/bin/gh" <<'GH'
#!/bin/sh
case "$1 $2" in
  "pr view")     echo '{"number":7,"title":"t","body":"b","url":"https://x/pull/7","state":"OPEN","isCrossRepository":false,"headRefName":"feat","baseRefName":"main","headRefOid":"abc","labels":[],"closingIssuesReferences":[]}' ;;
  "issue list")  case "$*" in *needs-triage*) echo '[{"number":5,"title":"x"}]' ;; *) echo '[]' ;; esac ;;
esac
exit 0
GH
    cat > "$1/bin/fabro-code" <<'FC'
#!/bin/sh
echo 'FABRO-CODE LEAKED TO STDOUT'
case "$1" in index) printf 'ok\n' > "$FABRO_TEST_STATE" ;; esac
exit 0
FC
    chmod +x "$1/bin/gh" "$1/bin/fabro-code"
    ln -sf "$1/bin/gh" "$NOFC_BIN/gh"
}

# backlog prep: the two lines, from the graph.
ET="$WORK/entry-backlog-prep"; entry_repo "$ET"
extract prep | sed "s#/tmp/fabro#$ET/sb#g" \
    | grep -e "grep -qx '.codegraph/'" -e 'command -v fabro-code' > "$ET/frag.sh"
check "backlog prep carries both index lines" "2" "$(grep -c . "$ET/frag.sh")"
for i in 1 2; do
    ( cd "$ET/repo" && PATH="$ET/bin:$PATH" FABRO_TEST_STATE="$ET/sb/code_index" sh -c "set -e; $(cat "$ET/frag.sh")" ) >/dev/null
done
check "backlog prep exclude line added once" "1" "$(grep -cx '.codegraph/' "$ET/repo/.git/info/exclude")"
check "backlog prep index ran" "ok" "$(cat "$ET/sb/code_index" 2>/dev/null || echo missing)"
rm -f "$ET/sb/code_index"
( cd "$ET/repo" && PATH="$NOFC_PATH" sh -c "set -e; $(cat "$ET/frag.sh")" ); EC=$?
check "backlog prep with no fabro-code exits 0" "0" "$EC"
check "backlog prep records not-in-image" "failed: fabro-code not in image" "$(cat "$ET/sb/code_index" 2>/dev/null)"

# The three whole nodes. Expected stdout: claim one routing object, prep `ready`,
# acquire nothing.
for entry in "claim|$PR|{\"context_updates\":{\"base_ref\":\"main\"}}" \
             "prep|$ARCH|ready" \
             "acquire|$INTRIAGE|"; do
    NODE=${entry%%|*}; rest=${entry#*|}; EGRAPH=${rest%%|*}; WANT=${rest#*|}
    ET="$WORK/entry-$NODE"; entry_repo "$ET"
    echo 7 > "$ET/sb/pr_number"
    extract_from "$EGRAPH" "$NODE" | sed "s#/tmp/fabro#$ET/sb#g" > "$ET/$NODE.sh"
    if ! sh -n "$ET/$NODE.sh" 2>"$ET/syntax"; then
        FAIL=$((FAIL + 1)); printf '  FAIL %s entry script not valid POSIX sh\n' "$NODE"
        sed 's/^/       /' "$ET/syntax"
    fi
    for i in 1 2; do
        OUT=$( cd "$ET/repo" && PATH="$ET/bin:$PATH" FABRO_TEST_STATE="$ET/sb/code_index" sh "$ET/$NODE.sh" ); EC=$?
    done
    check "$NODE exits 0 with fabro-code" "0" "$EC"
    check "$NODE stdout unchanged with fabro-code" "$WANT" "$OUT"
    check "$NODE index ran" "ok" "$(cat "$ET/sb/code_index" 2>/dev/null || echo missing)"
    check "$NODE exclude line added once" "1" "$(grep -cx '.codegraph/' "$ET/repo/.git/info/exclude")"
    rm -f "$ET/sb/code_index"
    OUT=$( cd "$ET/repo" && PATH="$NOFC_PATH" sh "$ET/$NODE.sh" ); EC=$?
    check "$NODE exits 0 with no fabro-code" "0" "$EC"
    check "$NODE stdout unchanged with no fabro-code" "$WANT" "$OUT"
    check "$NODE records not-in-image" "failed: fabro-code not in image" "$(cat "$ET/sb/code_index" 2>/dev/null)"
done
check "acquire still picks its issue" "5" "$(cat "$WORK/entry-acquire/sb/issue_number" 2>/dev/null)"

# The agent guide install (docs/coder-tweaks 02, C1), in the same four entry nodes. The
# guide is written to .codex/instructions.md and excluded once; an empty variable writes
# nothing; a repository that TRACKS the file keeps it byte-identical; stdout is unchanged
# and the node still succeeds. The backlog fragment is cut out of `prep` by its first and
# last line.
GUIDE_TEXT=$(printf '# guide\nline two\n')
for entry in "prep|$GRAPH|" "claim|$PR|{\"context_updates\":{\"base_ref\":\"main\"}}" \
             "prep|$ARCH|ready" "acquire|$INTRIAGE|"; do
    NODE=${entry%%|*}; rest=${entry#*|}; EGRAPH=${rest%%|*}; WANT=${rest#*|}
    case "$EGRAPH" in "$GRAPH") LABEL=backlog-prep ;; "$ARCH") LABEL=arch-prep ;; *) LABEL=$NODE ;; esac
    ET="$WORK/guide-$LABEL"; entry_repo "$ET"; echo 7 > "$ET/sb/pr_number"
    if [ "$EGRAPH" = "$GRAPH" ]; then
        extract_from "$EGRAPH" "$NODE" | sed "s#/tmp/fabro#$ET/sb#g" \
            | awk '/^if \[ -z "\$FABRO_AGENT_GUIDE"/ {p = 1} p {print} /^else echo .agent guide/ {p = 0}' > "$ET/$NODE.sh"
        check "$LABEL: guide lines found" "4" "$(grep -c . "$ET/$NODE.sh")"
        RUN="set -e; . $ET/$NODE.sh"
    else
        extract_from "$EGRAPH" "$NODE" | sed "s#/tmp/fabro#$ET/sb#g" > "$ET/$NODE.sh"
        RUN=". $ET/$NODE.sh"
    fi
    for i in 1 2; do
        OUT=$( cd "$ET/repo" && PATH="$ET/bin:$PATH" FABRO_TEST_STATE="$ET/sb/code_index" FABRO_AGENT_GUIDE="$GUIDE_TEXT" sh -c "$RUN" ); EC=$?
    done
    check "$LABEL: guide install exits 0"        "0" "$EC"
    check "$LABEL: guide file is the guide"      "$GUIDE_TEXT" "$(cat "$ET/repo/.codex/instructions.md" 2>/dev/null)"
    check "$LABEL: guide excluded once"          "1" "$(grep -cx '.codex/instructions.md' "$ET/repo/.git/info/exclude")"
    check "$LABEL: guide file not in porcelain"  "0" "$(git -C "$ET/repo" status --porcelain | grep -c codex)"
    if [ "$EGRAPH" != "$GRAPH" ]; then
        check "$LABEL: stdout unchanged by the guide" "$WANT" "$(grep -v 'FABRO-CODE LEAKED' <<<"$OUT")"
    fi
    # an empty variable: nothing is written, the node still succeeds
    ET2="$WORK/guide0-$LABEL"; entry_repo "$ET2"; echo 7 > "$ET2/sb/pr_number"
    sed "s#$ET/sb#$ET2/sb#g" "$ET/$NODE.sh" > "$ET2/$NODE.sh"
    if [ "$EGRAPH" = "$GRAPH" ]; then RUN2="set -e; . $ET2/$NODE.sh"; else RUN2=". $ET2/$NODE.sh"; fi
    ( cd "$ET2/repo" && PATH="$ET2/bin:$PATH" FABRO_TEST_STATE="$ET2/sb/code_index" FABRO_AGENT_GUIDE="" sh -c "$RUN2" ) >/dev/null 2>&1; EC=$?
    check "$LABEL: empty guide exits 0"          "0" "$EC"
    check "$LABEL: empty guide writes nothing"   "absent" "$([ -e "$ET2/repo/.codex" ] && echo present || echo absent)"
    # a tracked file is left alone
    ET3="$WORK/guide1-$LABEL"; entry_repo "$ET3"; echo 7 > "$ET3/sb/pr_number"
    sed "s#$ET/sb#$ET3/sb#g" "$ET/$NODE.sh" > "$ET3/$NODE.sh"
    mkdir -p "$ET3/repo/.codex"; printf 'mine\n' > "$ET3/repo/.codex/instructions.md"
    git -C "$ET3/repo" add -A; git -C "$ET3/repo" -c user.email=t@t -c user.name=t commit -qm codex
    if [ "$EGRAPH" = "$GRAPH" ]; then RUN3="set -e; . $ET3/$NODE.sh"; else RUN3=". $ET3/$NODE.sh"; fi
    ( cd "$ET3/repo" && PATH="$ET3/bin:$PATH" FABRO_TEST_STATE="$ET3/sb/code_index" FABRO_AGENT_GUIDE="$GUIDE_TEXT" sh -c "$RUN3" ) >/dev/null 2>&1; EC=$?
    check "$LABEL: tracked file, exits 0"        "0" "$EC"
    check "$LABEL: tracked file left alone"      "mine" "$(cat "$ET3/repo/.codex/instructions.md")"
    check "$LABEL: tracked file not excluded"    "0" "$(grep -cx '.codex/instructions.md' "$ET3/repo/.git/info/exclude")"
done

# ---------------------------------------------------------------------------
# extra_gate — follow-up tasks join the same queue under the same size budget
# ---------------------------------------------------------------------------
echo ""
echo "extra_gate"
T="$WORK/eg"; mkdir -p "$T/extra"; stage extra_gate
mkreadok ok1 standards.json spec.json quality.json changed_files.txt diffstat.txt issue.json completed.md

eg_setup() { # tasks followups
    rm -f "$T"/*.json "$T"/extra/*.json "$T"/extra_decompose_attempts
    printf '%s' "$1" > "$T/tasks.json"
    printf '%s' "$2" | jq --arg v ok1 '._io.visit = $v' > "$T/extra/followups.json"
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

# 4. An Architecture issue's PR gets the label (ADR 0012 D8); a plain one does not.
op_setup
printf '%s' '{"number":350,"title":"t","labels":[{"name":"architecture"}]}' > "$T/issue.json"
sh "$T/open_pr.sh" >/dev/null 2>&1
check "architecture PR: labelled"  "1" "$(grep -c "^pr edit $BR --add-label architecture" "$T/gh.log")"
op_setup
sh "$T/open_pr.sh" >/dev/null 2>&1
check "plain PR: not labelled"     "0" "$(grep -c 'add-label architecture' "$T/gh.log")"

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

ms_setup() { : > "$T/gh.log"; rm -f "$FAB/issue.json" "$FAB/issue_number" "$FAB/stuck_reason.md" "$FAB/stuck_comment.md"; }

ms_setup
printf '%s' '{"number":356}' > "$FAB/issue.json"
OUT=$(sh "$T/mark_stuck.sh" 2>&1); RC=$?
check "exits 0 with issue.json"       "0" "$RC"
check "swaps the receipt for stuck"   "1" \
    "$(grep -c -- 'issue edit 356 --remove-label agent-in-progress --add-label agent-stuck' "$T/gh.log")"
check "comments from a file"          "1" "$(grep -c -- "issue comment 356 --body-file $FAB/stuck_comment.md" "$T/gh.log")"
check "generic comment without a reason" "1" "$(grep -c 'could not complete this issue and gave up' "$FAB/stuck_comment.md")"

# decompose_gate's needs_human_review route: the decomposer's summary is the comment.
ms_setup
printf '%s' '{"number":1279}' > "$FAB/issue.json"
echo 'blocker #1278 is not merged' > "$FAB/stuck_reason.md"
OUT=$(sh "$T/mark_stuck.sh" 2>&1); RC=$?
check "reason: exits 0"               "0" "$RC"
check "reason: still swaps the label" "1" "$(grep -c -- 'issue edit 1279 --remove-label agent-in-progress --add-label agent-stuck' "$T/gh.log")"
check "reason: comment carries it"    "1" "$(grep -c '^blocker #1278 is not merged$' "$FAB/stuck_comment.md")"
check "reason: names the decomposer"  "1" "$(grep -c 'The decomposer reported' "$FAB/stuck_comment.md")"

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

# 10. The regression from run 01M4DSYPAWDMJB68CCX5HM3M56 (womens-fantasy-sports#1391,
#     2026-10-08): a long/stale run's `git merge origin/main` inside this node created a
#     merge commit {64240f8} whose workflow delta came entirely from main. Under the
#     plain path-limited log that commit appeared in `origin/main..HEAD -- .github/workflows/`
#     with an EMPTY files list -- a false positive that tripped the guard, looped the
#     Accept-partial path four times, and died to the deterministic breaker. The guard
#     must exclude merge commits (--no-merges) so it only fires on agent-authored
#     workflow commits, which tests 7 and 8 still catch. Grepping the extracted script is
#     the binding contract test: a revert to plain `git log` re-opens this bug and fails
#     here even though no fixture can cheaply rebuild the historical 3-way merge state.
check "guard excludes merge commits" "1" "$(grep -c -- 'git log --no-merges' "$T/open_pr_prep.sh")"
check "guard still scopes to workflows path" "1" "$(grep -q -- '-- .github/workflows/' "$T/open_pr_prep.sh" && echo 1 || echo 0)"

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
# excerpts — the code the task dossier cites, copied for the coder, always exit 0
# ---------------------------------------------------------------------------
echo ""
echo "excerpts"
SAVED_PATH="$PATH"
PATH="$ORIG_PATH"
T="$WORK/excerpts"; mkdir -p "$T/bin" "$T/wt/src" "$T/wt/tests"; stage excerpts
ex_run() { ( cd "$T/wt" && PATH="$1" sh "$T/excerpts.sh" 2>&1 ); }
awk 'BEGIN {for (i = 1; i <= 60; i++) print "line " i " of a"}' > "$T/wt/src/a.py"
awk 'BEGIN {for (i = 1; i <= 30; i++) print "line " i " of t"}' > "$T/wt/tests/t.py"
# 2000 lines of 30 bytes: one 150-line range is 4500 bytes, so eight ranges fit the cap.
awk 'BEGIN {for (i = 1; i <= 2000; i++) printf "big %025d\n", i}' > "$T/wt/src/big.py"

# 1. No dossier: nothing is written, and a stale task-code.md is removed.
rm -f "$T/task-context.md"; echo stale > "$T/task-code.md"
OUT=$(ex_run "$NOFC_PATH"); RC=$?
check "no dossier: exit 0"              "0" "$RC"
check "no dossier: no task-code.md"     "absent" "$([ -e "$T/task-code.md" ] && echo present || echo absent)"
check "no dossier: says so"             "1" "$(grep -c 'no task dossier' <<<"$OUT")"

# 2. Ranges are copied verbatim, merged per file, and missing files are skipped. A
# single line with no fabro-code is the window N-3..N+12, clamped to the file.
cat > "$T/task-context.md" <<'DOSSIER'
# Task dossier: t1 Do the thing
## Files and symbols
- src/a.py:10-14 `f`: the seam; also src/a.py:16-20 `g`, merged with the first
- src/a.py:40-45 `h`; `src/a.py:2` the import line
- ./src/a.py:58-70 runs past the end of the file
- gone/x.py:1-5 does not exist; :30 a bare line number is skipped
## Tests
- tests/t.py:29
DOSSIER
OUT=$(ex_run "$NOFC_PATH"); RC=$?
check "ranges: exit 0"                  "0" "$RC"
check "ranges: one line of stdout"      "1" "$(grep -c . <<<"$OUT")"
check "ranges: no routing object"       "0" "$(grep -c 'context_updates' <<<"$OUT")"
check "ranges: headings" \
    "## src/a.py:1-20|## src/a.py:40-45|## src/a.py:58-60|## tests/t.py:26-30" \
    "$(grep '^## ' "$T/task-code.md" | paste -sd'|' -)"
check "ranges: block is the file text" "$(sed -n '40,45p' "$T/wt/src/a.py")" \
    "$(awk '/^## src[/]a[.]py:40-45$/ {getline; on = 1; next} on && /^````$/ {exit} on' "$T/task-code.md")"
check "ranges: missing file skipped"    "0" "$(grep -c 'gone/x.py' "$T/task-code.md")"
check "ranges: no scratch files left"   "0" "$(ls "$T"/excerpts.refs* "$T"/task-code.md.tmp 2>/dev/null | grep -c .)"

# 3. With fabro-code, a single line becomes the narrowest symbol that holds it.
cat > "$T/bin/fabro-code" <<'FC'
#!/bin/sh
[ "$1" = show ] || exit 64
case "$2" in
  src/a.py:2)    echo '# src/a.py:1-4  function  imports' ;;
  src/a.py:30)   echo '# src/a.py:30-30  variable  LIMIT' ;;
  tests/t.py:29) echo 'no symbol at tests/t.py:29'; exit 1 ;;
esac
echo '## calls'
exit 0
FC
chmod +x "$T/bin/fabro-code"
OUT=$(ex_run "$T/bin:$NOFC_PATH"); RC=$?
check "fabro-code: exit 0"              "0" "$RC"
check "fabro-code: symbol range used" \
    "## src/a.py:1-4|## src/a.py:10-20|## src/a.py:40-45|## src/a.py:58-60|## tests/t.py:26-30" \
    "$(grep '^## ' "$T/task-code.md" | paste -sd'|' -)"
printf '%s\n' '# Task dossier: const' '- src/a.py:30 `LIMIT` is one line' > "$T/task-context.md"
ex_run "$T/bin:$NOFC_PATH" >/dev/null
check "fabro-code: a one-line symbol keeps the window" "## src/a.py:27-42" \
    "$(grep '^## ' "$T/task-code.md")"

# 4. Past 40000 bytes a range is listed, not copied, and nothing is cut mid-range.
{ echo '# Task dossier: big'; for i in 0 1 2 3 4 5 6 7 8 9 10 11; do
    echo "- src/big.py:$((i * 160 + 1))-$((i * 160 + 150))"; done; } > "$T/task-context.md"
OUT=$(ex_run "$NOFC_PATH"); RC=$?
check "cap: exit 0"                     "0" "$RC"
check "cap: copied ranges"              "8" "$(grep -c '^## src/big.py' "$T/task-code.md")"
check "cap: listed ranges"              "4" "$(grep -c '^- src/big.py' "$T/task-code.md")"
check "cap: under the byte cap"         "1" "$([ "$(wc -c < "$T/task-code.md")" -lt 42000 ] && echo 1 || echo 0)"
check "cap: says how much"              "1" "$(grep -c 'copied 8 range(s), 36000 bytes' <<<"$OUT")"

# 4b. Call sites (docs/coder-tweaks/07). A stub fabro-code whose `callers` prints two
# rows and a footer: both rows appear, each followed by its call line, and no footer.
cat > "$T/bin/fabro-code" <<'FC'
#!/bin/sh
case "$1" in
  show) echo '## calls'; exit 0 ;;
  callers)
    case "$2" in
      f) echo 'src/a.py:30  function  g'; echo 'tests/t.py:5  function  test_f'; echo '[index synced]'; exit 0 ;;
      *) echo "no callers of $2 found"; exit 1 ;;
    esac ;;
esac
exit 64
FC
chmod +x "$T/bin/fabro-code"
cat > "$T/task-context.md" <<'DOSSIER'
# Task dossier: t1 Do the thing
## Files and symbols
- src/a.py:10-14 `f` and `nobody(x)`; `src/a.py:2` is not a symbol; `f` again
## Tests
- tests/t.py:29 `ignored_symbol`
DOSSIER
OUT=$(ex_run "$T/bin:$NOFC_PATH"); RC=$?
check "callers: exit 0"                 "0" "$RC"
check "callers: section after the code" "## src/a.py:1-14|## tests/t.py:26-30|## Where these are used" \
    "$(grep '^## ' "$T/task-code.md" | paste -sd'|' -)"
check "callers: both rows"              "2" "$(grep -c '^- .*:[0-9]*  function  ' "$T/task-code.md")"
check "callers: call line follows row"  "    line 30 of a" "$(grep -A1 '^- src/a.py:30  function  g' "$T/task-code.md" | tail -1)"
check "callers: second call line"       "    line 5 of t" "$(grep -A1 '^- tests/t.py:5  ' "$T/task-code.md" | tail -1)"
check "callers: no footer"              "0" "$(grep -c 'index synced' "$T/task-code.md")"
check "callers: one symbol listed"      "1" "$(grep -c '^### ' "$T/task-code.md")"
check "callers: scratch files removed"  "0" "$(ls "$T"/task-code.md.* 2>/dev/null | grep -c .)"
OUT=$(ex_run "$NOFC_PATH")
check "callers: no fabro-code, no section" "0" "$(grep -c 'Where these are used' "$T/task-code.md")"
# Near the cap the call sites go first: eight 4500-byte code blocks leave no room.
{ echo '# Task dossier: big'; echo '## Files and symbols'; echo '- `s1` `s2` `s3` `s4` `s5` `s6` `s7` `s8` `s9` `s10` `s11` `s12`'; for i in 0 1 2 3 4 5 6 7 8 9 10 11; do
    echo "- src/big.py:$((i * 160 + 1))-$((i * 160 + 150))"; done; } > "$T/task-context.md"
cat > "$T/bin/fabro-code" <<'FC'
#!/bin/sh
[ "$1" = callers ] || { echo '## calls'; exit 0; }
i=0; while [ $i -lt 15 ]; do echo "src/big.py:$((i + 1))  function  caller$i"; i=$((i + 1)); done
FC
OUT=$(ex_run "$T/bin:$NOFC_PATH"); RC=$?
check "callers cap: exit 0"             "0" "$RC"
check "callers cap: every code block kept" "8" "$(grep -c '^## src/big.py' "$T/task-code.md")"
check "callers cap: some call sites cut" "1" "$([ "$(grep -c '^### ' "$T/task-code.md")" -lt 12 ] && echo 1 || echo 0)"
check "callers cap: under the byte cap" "1" "$([ "$(wc -c < "$T/task-code.md")" -le 42000 ] && echo 1 || echo 0)"

# 5. A dossier with no usable range leaves no task-code.md behind.
echo stale > "$T/task-code.md"
printf '%s\n' '# Task dossier: none' '- src/a.py has no range' > "$T/task-context.md"
OUT=$(ex_run "$NOFC_PATH"); RC=$?
check "no ranges: exit 0"               "0" "$RC"
check "no ranges: no task-code.md"      "absent" "$([ -e "$T/task-code.md" ] && echo present || echo absent)"

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
mkreadok ok1 current_task.json issue.json
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
printf '%s' '{"disposition":"ready","task_title":"T1","task_body":"sharpened","_io":{"visit":"ok1"}}'> "$T/improve_result.json"
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "ready counts toward the budget" "4" "$(cat "$T/tasks_coded")"
printf '%s' '{"disposition":"redundant","reason":"already done","_io":{"visit":"ok1"}}'> "$T/improve_result.json"
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "redundant does not count"       "4" "$(cat "$T/tasks_coded")"
printf '%s' '{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"}' > "$T/current_task.json"
printf '%s' '{"disposition":"ready","task_title":"X1","task_body":"sharpened","_io":{"visit":"ok1"}}'> "$T/improve_result.json"
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "extra-review ready does not count" "4" "$(cat "$T/tasks_coded")"

# 1b. A split of an extra-review follow-up stamps its slices from_extra, and a slice
#     that is then routed to the coder does not count either.
bd_setup 4 9 8
jq '. + [{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"}]' \
    "$T/tasks.json" > "$T/tasks.tmp" && mv "$T/tasks.tmp" "$T/tasks.json"
jq '.[8]' "$T/tasks.json" > "$T/current_task.json"
rm -f "$T/split_rounds"
printf '%s' '{"disposition":"split","tasks":[{"id":"x1a","title":"X1a","body":"b"},{"id":"x1b","title":"X1b","body":"b"}],"_io":{"visit":"ok1"}}'> "$T/improve_result.json"
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "extra split: slices carry from_extra" "true true" "$(jq -r '[.[8:][] | .from_extra | tostring] | join(" ")' "$T/tasks.json")"
check "extra split: nothing counted"   "4" "$(cat "$T/tasks_coded")"
jq '.[8]' "$T/tasks.json" > "$T/current_task.json"
printf '%s' '{"disposition":"ready","task_title":"X1a","task_body":"sharpened","_io":{"visit":"ok1"}}'> "$T/improve_result.json"
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "extra slice ready does not count" "4" "$(cat "$T/tasks_coded")"
bd_setup 4 1 2
printf '%s' '{"disposition":"split","tasks":[{"id":"t1a","title":"T1a","body":"b"},{"id":"t1b","title":"T1b","body":"b"}],"_io":{"visit":"ok1"}}'> "$T/improve_result.json"
jq '.[0]' "$T/tasks.json" > "$T/current_task.json"
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
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

# 10. An Architecture issue's remainder keeps the label (ADR 0012 D8).
rm -f "$T/remainder_issue" "$T/create_fails"; : > "$T/gh.log"
printf '%s' '{"number":350,"title":"Big issue","labels":[{"name":"architecture"}]}' > "$T/issue.json"
sh "$T/file_remainder.sh" >/dev/null 2>&1
check "architecture remainder: labelled" "1" "$(grep '^issue create' "$T/gh.log" | grep -c -- '--label architecture')"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# rescue_brief and partial_remainder — the rescue gate explains itself
#
# Every edge into human_rescue passes through rescue_brief, which writes the gate's
# summary into `response.rescue_brief` and points `last_stage` at it. Its facts come
# from failure_signature (stdin) and the run's files. partial_remainder sits on the
# [P] edge and moves the unfinished and unstarted tasks to remainder.json, so
# file_remainder files them instead of dropping them. Fixtures are shaped on run
# 01M3Y86Z013N2V1QDNNG591MBQ (womens-fantasy-sports#1279): 7 tasks, 5 landed, task 6
# out of rework tiers on a .github/workflows/ finding, task 7 never started.
# ---------------------------------------------------------------------------
echo ""
echo "rescue_brief and partial_remainder"
SAVED_PATH="$PATH"
T="$WORK/rescue"; mkdir -p "$T/bin"
FAB="$T/fabro"
cat > "$T/bin/git" <<'STUB'
#!/bin/sh
case "$1 $2" in
  "diff --shortstat") echo " 40 files changed, 859 insertions(+), 983 deletions(-)" ;;
  "diff --quiet") [ -f "$GIT_STATE/empty_diff" ] && exit 0; exit 1 ;;
  "log --format=%H") [ -f "$GIT_STATE/wf_commit" ] && echo deadbeef ;;
  "log --no-merges") [ -f "$GIT_STATE/wf_commit" ] && echo deadbeef ;;
  "rev-parse --abbrev-ref") echo "fabro/run/01TESTRUN" ;;
esac
exit 0
STUB
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue create") echo "https://github.com/o/r/issues/88"; exit 0 ;;
  "pr comment") shift 2; while [ $# -gt 0 ]; do [ "$1" = "--body-file" ] && cp "$2" "$GH_STATE/pr_comment.txt"; shift; done; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/git" "$T/bin/gh"
PATH="$T/bin:$ORIG_PATH"
export GIT_STATE="$T" GH_LOG="$T/gh.log" GH_STATE="$T"
stage_into rescue_brief "$FAB"
stage_into partial_remainder "$FAB"
stage_into file_remainder "$FAB"
stage_into next_task "$FAB"

rs_tasks() { # rs_tasks N -> t1..tN
    i=1; out=""
    while [ "$i" -le "$1" ]; do
        out="$out{\"id\":\"t$i\",\"title\":\"T$i\",\"body\":\"do t$i\",\"files\":[],\"covers\":[],\"source\":\"decompose\"},"
        i=$((i + 1))
    done
    printf '[%s]' "${out%,}"
}
rs_setup() { # rs_setup <task_index> <landed> <current id>: 7 tasks
    rm -rf "$FAB" "$T/empty_diff" "$T/wf_commit" "$T/pr_comment.txt"; mkdir -p "$FAB/feedback" "$FAB/.io"; : > "$T/gh.log"
    printf '%s' '{"number":1279,"title":"Cut over to session auth","labels":[{"name":"agent-in-progress"}]}' > "$FAB/issue.json"
    rs_tasks 7 > "$FAB/tasks.json"
    echo "$1" > "$FAB/task_index"
    i=1; while [ "$i" -le "$2" ]; do printf '### Task: T%s\n- Review: ok\n\n' "$i" >> "$FAB/completed.md"; i=$((i + 1)); done
    jq ".[] | select(.id == \"$3\")" "$FAB/tasks.json" > "$FAB/current_task.json"
    echo 0 > "$FAB/round"
    printf '%s' '{"workflow":"backlog","node":"review","visit":"v1","started":"2026-10-02T00:00:00Z"}' > "$FAB/.io/stage.json"
}
rs_run() { # rs_run <stdin> -> sets OUT (merged) and J (the routing object), RC
    OUT=$(printf '%s' "$1" | sh "$T/rescue_brief.sh" 2>&1); RC=$?
    J=$(printf '%s' "$1" | sh "$T/rescue_brief.sh" 2>/dev/null)
}
rs_text() { jq -r '.context_updates["response.rescue_brief"]' <<<"$J"; }
rs_has() { rs_text | grep -qF -- "$1" && echo yes || echo no; }

# 1. The rework ladder ran out on a finding no agent may fix.
rs_setup 6 5 t6; echo 6 > "$FAB/round"
printf '%s\n' '## Review findings to fix' 'Criterion 4 is missing.' '- .github/workflows/build-publish.yml:73-77 still passes VITE_AUTH0_*' > "$FAB/feedback/rework.md"
FABRO_AUTO_MERGE=1 rs_run ''
check "ladder: exits 0"                    "0" "$RC"
check "ladder: routing object is last"     "rescue_brief" "$(jq -r '.context_updates.last_stage' <<<"$(lastjson "$OUT")")"
check "ladder: brief is the gate's text"   "rescue_brief" "$(jq -r '.context_updates.last_stage' <<<"$J")"
check "ladder: says why"                   "yes" "$(rs_has 'the rework ladder ran out. Task 6 failed validate or review at every tier (6 rounds).')"
check "ladder: names the task"             "yes" "$(rs_has 'Unfinished task 6 of 7: T6')"
check "ladder: counts landed and unstarted" "yes" "$(rs_has 'Tasks: 5 of 7 passed review, 1 not started.')"
check "ladder: branch size"                "yes" "$(rs_has 'Branch against main: 40 files changed')"
check "ladder: quotes the findings"        "yes" "$(rs_has 'Criterion 4 is missing.')"
check "ladder: flags the workflow file"    "yes" "$(rs_has 'No agent may edit that directory')"
check "ladder: guidance cannot help"       "yes" "$(rs_has '**Type guidance:** Cannot help')"
check "ladder: [P] says what ships"        "yes" "$(rs_has 'Opens a PR with the 5 task(s) that passed review and the unreviewed edits of task 6')"
check "ladder: [P] files the rest"         "yes" "$(rs_has 'The 2 task(s) not done (task 6 included) go to one held remainder issue')"
check "ladder: [P] can auto-merge"         "yes" "$(rs_has 'merges it with no human')"
check "ladder: [X] says what is lost"      "yes" "$(rs_has 'so the 5 task(s) that passed review are lost')"
check "ladder: [X] names the issue"        "yes" "$(rs_has 'Labels #1279 agent-stuck')"
check "ladder: prints only the object"   "1" "$(printf '%s\n' "$OUT" | grep -c .)"
check "ladder: brief kept in a file"      "1" "$(grep -c '^\*\*Why the run stopped:\*\*' "$FAB/rescue_brief.md")"

# 2. A failed node: the signature names it, and guidance retries the unfinished task.
rs_setup 3 2 t3; echo 1 > "$FAB/round"
printf '%s\n' '## Review findings to fix' 'The test asserts nothing.' > "$FAB/feedback/rework.md"
rs_run 'rework_t2|transient_infra|stage timed out after <n>ms'
check "failed node: exits 0"               "0" "$RC"
check "failed node: names it"              "yes" "$(rs_has 'rework_t2 failed (transient_infra): stage timed out after <n>ms')"
check "failed node: guidance retries it"   "yes" "$(rs_has 'tries task 3 again with your text')"
check "failed node: no workflow flag"      "no" "$(rs_has 'No agent may edit')"
check "failed node: auto-merge off host"   "yes" "$(rs_has 'Auto-merge is off on this host')"

# 3. A validate failure: header plus the tail, with ANSI colour stripped.
rs_setup 2 1 t2; echo 2 > "$FAB/round"
ESC=$(printf '\033')
{ echo '## Validation failed (exit 1): ./.fabro/setup.sh && ./.fabro/ci.sh'; i=1; while [ $i -le 30 ]; do echo "${ESC}[31mline $i${ESC}[39m"; i=$((i + 1)); done; } > "$FAB/feedback/rework.md"
rs_run ''
check "validate: keeps the header"         "yes" "$(rs_has '## Validation failed (exit 1)')"
check "validate: keeps the last line"      "yes" "$(rs_has 'line 30')"
check "validate: drops the middle"         "no" "$(rs_has 'line 5')"
check "validate: strips ANSI"              "0" "$(rs_text | grep -c "$ESC")"
check "validate: gate reason when round<6" "yes" "$(rs_has 'a gate rejected its agent')"
check "validate: names the last agent"     "yes" "$(rs_has 'The last agent stage was review.')"

# 4. Findings from an earlier task are not shown: round 0 means no rework yet.
rs_setup 4 3 t4
printf '%s\n' '## Review findings to fix' 'STALE from task 3' > "$FAB/feedback/rework.md"
rs_run 'improve|deterministic|script failed'
check "stale findings: not shown"          "no" "$(rs_has 'STALE from task 3')"

# 5. Every task landed: nothing unfinished, guidance has nothing to fix.
rs_setup 7 7 t7
rs_run 'standards|deterministic|unknown'
check "all landed: no unfinished task"     "no" "$(rs_has 'Unfinished task')"
check "all landed: counts"                 "yes" "$(rs_has 'Tasks: 7 of 7 passed review, 0 not started.')"
check "all landed: guidance is useless"    "yes" "$(rs_has 'No task is unfinished')"
check "all landed: nothing to file"        "no" "$(rs_has 'remainder issue')"

# 6. open_pr_prep's two refusals, and the PR-already-open case.
rs_setup 6 5 t6; : > "$T/empty_diff"; rs_run ''
check "empty diff: [P] refused"            "yes" "$(rs_has 'The branch changes no file')"
rs_setup 6 5 t6; : > "$T/wf_commit"; rs_run ''
check "workflow commit: [P] refused"       "yes" "$(rs_has 'which the sandbox token cannot push')"
rs_setup 7 7 t7; echo 1319 > "$FAB/pr_number"; rs_run 'review_merge|deterministic|unknown'
check "PR open: [P] reuses it"             "yes" "$(rs_has 'PR #1319 is already open')"
check "PR open: [X] keeps it"              "yes" "$(rs_has 'PR #1319 stays open')"

# 7. The auto-merge sentence follows the same switches the merge phase reads.
rs_setup 6 5 t6
printf '%s' '{"number":1279,"labels":[{"name":"architecture"}]}' > "$FAB/issue.json"
FABRO_AUTO_MERGE=1 rs_run ''
check "architecture: a human merges"       "yes" "$(rs_has 'labelled architecture')"
rs_setup 6 5 t6; echo 0 > "$FAB/auto_merge_repo"; FABRO_AUTO_MERGE=1 rs_run ''
check "repo switch off: a human merges"    "yes" "$(rs_has 'off for this repository')"

# 8. claim died before anything: the directory does not exist yet.
rm -rf "$FAB"
rs_run 'claim|deterministic|script failed with exit code: <n>'
check "nothing: exits 0"                   "0" "$RC"
check "nothing: creates the directory"     "yes" "$([ -d "$FAB" ] && echo yes || echo no)"
check "nothing: still a routing object"    "rescue_brief" "$(jq -r '.context_updates.last_stage' <<<"$J")"
check "nothing: [P] refused"               "yes" "$(rs_has 'never read the issue')"
check "nothing: no tasks"                  "yes" "$(rs_has 'Tasks: none')"
echo 353 > "$FAB/issue_number"; rs_run ''
check "nothing: issue from issue_number"   "yes" "$(rs_has 'Labels #353 agent-stuck')"

# 9. The brief is capped, however long the findings are.
rs_setup 6 5 t6; echo 6 > "$FAB/round"
{ echo '## Review findings to fix'; i=1; while [ $i -le 40 ]; do printf 'finding %s %0390d\n' "$i" 0; i=$((i + 1)); done; } > "$FAB/feedback/rework.md"
rs_run ''
check "cap: brief at most 3500 bytes"      "yes" "$([ "$(rs_text | wc -c)" -le 3501 ] && echo yes || echo no)"
check "cap: still valid JSON"              "rescue_brief" "$(jq -r '.context_updates.last_stage' <<<"$J")"

# partial_remainder
# 10. Mid-task: the unfinished task and the unstarted one move; tasks.json keeps the landed.
rs_setup 6 5 t6
OUT=$(sh "$T/partial_remainder.sh" 2>&1); RC=$?
check "partial: exits 0"                   "0" "$RC"
check "partial: moves task 6 and 7"        "t6,t7" "$(jq -r 'map(.id) | join(",")' "$FAB/remainder.json")"
check "partial: tasks.json keeps 5"        "5" "$(jq length "$FAB/tasks.json")"
check "partial: marks the kind"            "partial" "$(cat "$FAB/remainder_kind")"
check "partial: says so"                   "1" "$(grep -c '2 unfinished task(s) moved' <<<"$OUT")"
OUT=$(sh "$T/next_task.sh" 2>&1)
check "partial: next_task starts nothing"  "true" "$(jq -r '.context_updates.tasks_done' <<<"$(lastjson "$OUT")")"

# 11. The current task already landed: only the unstarted ones move.
rs_setup 6 6 t6
sh "$T/partial_remainder.sh" >/dev/null 2>&1
check "landed current: moves only t7"      "t7" "$(jq -r 'map(.id) | join(",")' "$FAB/remainder.json")"

# 12. A budget remainder already holds t7: no duplicate.
rs_setup 6 5 t6
printf '%s' '[{"id":"t7","title":"T7","body":"do t7"},{"id":"t9","title":"T9","body":"do t9"}]' > "$FAB/remainder.json"
sh "$T/partial_remainder.sh" >/dev/null 2>&1
check "dedupe: keeps one t7"               "t7,t9,t6" "$(jq -r 'map(.id) | join(",")' "$FAB/remainder.json")"

# 13. Every task finished: nothing moves, nothing is marked.
rs_setup 7 7 t7
OUT=$(sh "$T/partial_remainder.sh" 2>&1); RC=$?
check "all done: exits 0"                  "0" "$RC"
check "all done: no remainder"             "absent" "$([ -e "$FAB/remainder.json" ] && echo present || echo absent)"
check "all done: no kind marker"           "absent" "$([ -e "$FAB/remainder_kind" ] && echo present || echo absent)"

# 14. Before decompose: no tasks.json, still exits 0.
rm -rf "$FAB"; mkdir -p "$FAB"
OUT=$(sh "$T/partial_remainder.sh" 2>&1); RC=$?
check "no tasks: exits 0"                  "0" "$RC"
check "no tasks: nothing written"          "absent" "$([ -e "$FAB/remainder.json" ] && echo present || echo absent)"

# 15. file_remainder tells the next run why the tasks were not done.
rs_setup 6 5 t6; echo 1319 > "$FAB/pr_number"
sh "$T/partial_remainder.sh" >/dev/null 2>&1
OUT=$(sh "$T/file_remainder.sh" 2>&1); RC=$?
check "partial filing: exits 0"            "0" "$RC"
check "partial filing: held issue"         "1" "$(grep '^issue create' "$T/gh.log" | grep -c -- '--label agent-remainder')"
check "partial filing: marker first"       "<!-- fabro:remainder parent=1279 pr=1319 -->" "$(head -1 "$FAB/remainder_body.md")"
check "partial filing: body says why"      "1" "$(grep -c 'an operator chose Accept partial' "$FAB/remainder_body.md")"
check "partial filing: no budget claim"    "0" "$(grep -c 'task budget' "$FAB/remainder_body.md")"
check "partial filing: lists both tasks"   "2" "$(grep -c '^### T' "$FAB/remainder_body.md")"
check "partial filing: PR comment says why" "1" "$(grep -c 'chose Accept partial' "$T/pr_comment.txt")"

PATH="$SAVED_PATH"
unset GIT_STATE GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# triage phase (_shared/triage) — claim, triage_gate, terminal outcome (ADR 0012)
# ---------------------------------------------------------------------------
echo ""
echo "triage phase"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/triage"; mkdir -p "$T/bin"
for n in claim triage_gate post_questions release done apply_ready improve_gate plan_gate; do
    extract_from "$SHARED_TRIAGE" "$n" | sed "s#/tmp/fabro#$T#g" > "$T/$n.sh"
    if ! sh -n "$T/$n.sh" 2>"$T/$n.syntax"; then
        FAIL=$((FAIL + 1)); printf '  FAIL %s is not valid POSIX sh\n' "$n"
    fi
done
mkreadok ok1 issue.json
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
echo '{"readiness":"needs_info","title":"feat: x","labels":["bug"],"questions":[{"id":"Q1","question":"Which endpoint?","why":"w","recommended":"/v2"}],"_io":{"visit":"ok1"}}' > "$T/triage.json"
echo 'report' > "$T/triage.md"
OUT=$(sh "$T/triage_gate.sh" 2>&1); RC=$?
check "gate needs_info: exit 0"       "0" "$RC"
check "gate needs_info: readiness"    "needs_info" "$(jq -r '.context_updates.triage_readiness' <<<"$(lastjson "$OUT")")"
check "gate: no answered key"         "false" "$(jq -r '.context_updates | has("answered")' <<<"$(lastjson "$OUT")")"

# 7. triage_gate: ready with a question is invalid; the first attempt fails.
echo 0 > "$T/triage_attempts"
echo '{"readiness":"ready","title":"feat: x","labels":[],"questions":[{"id":"Q1","question":"q"}],"_io":{"visit":"ok1"}}' > "$T/triage.json"
sh "$T/triage_gate.sh" >/dev/null 2>&1; RC=$?
check "gate ready+question: retries"  "1" "$RC"

# 8. release removes the claim and records released.
printf '{"number":7}' > "$T/issue.json"; : > "$T/gh.log"
sh "$T/release.sh" >/dev/null 2>&1
check "release: outcome file"         "released" "$(cat "$T/triage_outcome")"
check "release: removes the claim"    "1" "$(grep -c '^issue edit 7 --remove-label triage-in-progress' "$T/gh.log")"

# 9. post_questions labels needs-info and records needs_info.
printf '{"number":7,"labels":[{"name":"needs-triage"},{"name":"triage-in-progress"}]}' > "$T/issue.json"
echo '{"readiness":"needs_info","title":"feat: x","labels":["bug"],"questions":[{"id":"Q1","question":"q"}],"_io":{"visit":"ok1"}}' > "$T/triage.json"
: > "$T/gh.log"
sh "$T/post_questions.sh" >/dev/null 2>&1
check "post_questions: outcome file"  "needs_info" "$(cat "$T/triage_outcome")"
check "post_questions: needs-info"    "1" "$(grep -c -- '--add-label needs-info' "$T/gh.log")"

# 10. An Architecture issue may decide without a basis.
printf '{"number":7,"body":"b","labels":[{"name":"architecture"}]}' > "$T/issue.json"
echo 0 > "$T/triage_attempts"
echo '{"readiness":"ready","title":"refactor: x","labels":[],"questions":[],"decisions":[{"question":"q","decision":"d"}],"_io":{"visit":"ok1"}}' > "$T/triage.json"
OUT=$(sh "$T/triage_gate.sh" 2>&1); RC=$?
check "decide arch: exit 0"            "0" "$RC"
check "decide arch: ready"             "ready" "$(jq -r '.context_updates.triage_readiness' <<<"$(lastjson "$OUT")")"

# 11. A human-filed issue needs a basis.
printf '{"number":7,"body":"b","labels":[]}' > "$T/issue.json"
echo 0 > "$T/triage_attempts"
sh "$T/triage_gate.sh" >/dev/null 2>&1; RC=$?
check "decide human, no basis: retry"  "1" "$RC"

# 12. decisions must be an array.
echo 0 > "$T/triage_attempts"
echo '{"readiness":"ready","title":"refactor: x","labels":[],"questions":[],"decisions":"x","_io":{"visit":"ok1"}}' > "$T/triage.json"
sh "$T/triage_gate.sh" >/dev/null 2>&1; RC=$?
check "decisions not array: retry"     "1" "$RC"

# 13. apply_ready appends one section, and replaces an old one.
printf '{"number":7,"body":"first line","labels":[]}' > "$T/issue.json"
echo '{"readiness":"ready","title":"feat: x","labels":[],"questions":[],"decisions":[{"question":"Where?","decision":"Here.","basis":"CONTEXT.md"}],"_io":{"visit":"ok1"}}' > "$T/triage.json"
echo 'report' > "$T/triage.md"; : > "$T/gh.log"; rm -f "$T/decided_body.md"
sh "$T/apply_ready.sh" >/dev/null 2>&1
check "decided: one heading"           "1" "$(grep -c '^## Decisions made during triage$' "$T/decided_body.md")"
check "decided: keeps the body"        "1" "$(grep -c '^first line$' "$T/decided_body.md")"
check "decided: lists the decision"    "1" "$(grep -c '^- \*\*Where?\*\* Here. (basis: CONTEXT.md)$' "$T/decided_body.md")"
jq -n --rawfile b "$T/decided_body.md" '{number:7,body:$b,labels:[]}' > "$T/issue.json"
sh "$T/apply_ready.sh" >/dev/null 2>&1
check "decided again: still one"       "1" "$(grep -c '^## Decisions made during triage$' "$T/decided_body.md")"

# 14. No decisions: the body is not edited.
printf '{"number":7,"body":"b","labels":[]}' > "$T/issue.json"
echo '{"readiness":"ready","title":"feat: x","labels":[],"questions":[],"_io":{"visit":"ok1"}}' > "$T/triage.json"
: > "$T/gh.log"
sh "$T/apply_ready.sh" >/dev/null 2>&1
check "no decisions: no body edit"     "0" "$(grep -c 'decided_body' "$T/gh.log")"

# 15. The decisions rewrite drops only the old section; text below it is kept.
jq -n '{number:7,labels:[],body:"intro\n## Decisions made during triage\n\n- old\n## Notes\nkeep me"}' > "$T/issue.json"
echo '{"readiness":"ready","title":"feat: x","labels":[],"questions":[],"decisions":[{"question":"Q","decision":"D","basis":"b"}],"_io":{"visit":"ok1"}}' > "$T/triage.json"
sh "$T/apply_ready.sh" >/dev/null 2>&1
check "decided: keeps text below"      "1" "$(grep -c '^keep me$' "$T/decided_body.md")"
check "decided: drops the old entry"   "0" "$(grep -c '^- old$' "$T/decided_body.md")"

# 16. improve_gate restores an Architecture issue's slug marker (contract C2).
MKR='<!-- fabro:arch-candidate slug=room-deck -->'
ig_run() { # ig_run <improved body>
    jq -n --arg m "$MKR" '{number:7,labels:[],body:($m + "\n\nold body")}' > "$T/issue.json"
    jq -n --arg b "$1" '{status:"improved",body:$b,_io:{visit:"ok1"}}' > "$T/improve.json"
    echo 0 > "$T/improve_attempts"; : > "$T/gh.log"; rm -f "$T/improved_body.md"
    (cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
}
printf '{"number":7,"title":"t","body":"b","labels":[],"comments":[],"url":"u"}' > "$T/issue_fixture.json"
ig_run 'new body, marker dropped'
check "marker dropped: restored"       "$MKR" "$(head -1 "$T/improved_body.md")"
check "marker dropped: body kept"      "1" "$(grep -c '^new body, marker dropped$' "$T/improved_body.md")"
ig_run "$(printf 'top\n%s\nrest' "$MKR")"
check "marker moved: back on line 1"   "$MKR" "$(head -1 "$T/improved_body.md")"
check "marker moved: only once"        "1" "$(grep -cF "$MKR" "$T/improved_body.md")"
jq -n '{number:7,labels:[],body:"a human report"}' > "$T/issue.json"
jq -n '{status:"improved",body:"better report",_io:{visit:"ok1"}}' > "$T/improve.json"
echo 0 > "$T/improve_attempts"; rm -f "$T/improved_body.md"
(cd "$T" && sh "$T/improve_gate.sh") >/dev/null 2>&1
check "no marker: body untouched"      "better report" "$(cat "$T/improved_body.md")"

# --- plan_gate (ADR 0015 C1/C4) ---
mkreadok ok1 issue.json
printf '{"number":7,"labels":[]}' > "$T/issue.json"

pg3='{"tasks":[{"id":"a","title":"A","intent":"i","files":["f"],"covers":["c"]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]}],"children":[],"summary":"s"}'
printf '%s' "$pg3" | jq -c '._io.visit="ok1"' > "$T/plan.json"; echo 0 > "$T/plan_attempts"
rm -f "$T/plan_ok" "$T/task_map.md" "$T/split_children.json"
OUT=$(sh "$T/plan_gate.sh" 2>&1); RC=$?
check "plan map: exit 0"             "0" "$RC"
check "plan map: status"             "map"  "$(jq -r '.context_updates.plan_status' <<<"$(lastjson "$OUT")")"
check "plan map: count"              "3"    "$(jq -r '.context_updates.task_count' <<<"$(lastjson "$OUT")")"
check "plan map: plan_ok"            "1"    "$([ -f "$T/plan_ok" ] && echo 1 || echo 0)"
check "plan map: 3 numbered tasks"   "3"    "$(grep -cE '^[0-9]+\. \*\*' "$T/task_map.md")"
check "plan map: header"             "1"    "$(grep -c 'Drafted by triage against `main` at `' "$T/task_map.md")"

pg7='{"tasks":[{"id":"a1","title":"A1","intent":"i","files":[],"covers":[]},{"id":"a2","title":"A2","intent":"i","files":[],"covers":[]},{"id":"a3","title":"A3","intent":"i","files":[],"covers":[]},{"id":"a4","title":"A4","intent":"i","files":[],"covers":[]},{"id":"b1","title":"B1","intent":"i","files":[],"covers":[]},{"id":"b2","title":"B2","intent":"i","files":[],"covers":[]},{"id":"b3","title":"B3","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): abc","criteria":["c"],"summary":"one","tasks":["a1","a2","a3","a4"],"after":null},{"title":"feat(b): def","criteria":["c"],"summary":"two","tasks":["b1","b2","b3"],"after":0}],"summary":"s"}'
printf '%s' "$pg7" | jq -c '._io.visit="ok1"' > "$T/plan.json"; echo 0 > "$T/plan_attempts"
rm -f "$T/plan_ok" "$T/task_map.md" "$T/split_children.json"
OUT=$(sh "$T/plan_gate.sh" 2>&1); RC=$?
check "plan split: status"           "split" "$(jq -r '.context_updates.plan_status' <<<"$(lastjson "$OUT")")"
check "plan split: count"            "7"     "$(jq -r '.context_updates.task_count' <<<"$(lastjson "$OUT")")"
check "plan split: 2 children"       "2"     "$(jq 'length' "$T/split_children.json")"
check "plan split: child maps"       "2"     "$(jq '[.[] | select(has("task_map"))] | length' "$T/split_children.json")"
check "plan split: tasks resolved"   "2"     "$(jq '[.[] | select((.tasks[0].id // "") != "")] | length' "$T/split_children.json")"
check "plan split: criteria carried" "c"     "$(jq -r '.[0].criteria[0]' "$T/split_children.json")"

# C1 rule 3 is a partition, not a contiguous one: children may interleave the
# tasks as long as each lists its own in `tasks` order.
pg5i='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","c","e"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["b","d"],"after":0}],"summary":"s"}'
printf '%s' "$pg5i" | jq -c '._io.visit="ok1"' > "$T/plan.json"; echo 0 > "$T/plan_attempts"
rm -f "$T/plan_ok" "$T/task_map.md" "$T/split_children.json"
OUT=$(sh "$T/plan_gate.sh" 2>&1); RC=$?
check "plan interleaved: split"      "split" "$(jq -r '.context_updates.plan_status' <<<"$(lastjson "$OUT")")"
check "plan interleaved: child tasks" "a c e|b d" "$(jq -r 'map([.tasks[].id]|join(" "))|join("|")' "$T/split_children.json")"

pg13=$(jq -nc '{tasks:[range(13) | {id:("t" + tostring),title:"T",intent:"i",files:[],covers:[]}],children:[],summary:"s"}')
printf '%s' "$pg13" | jq -c '._io.visit="ok1"' > "$T/plan.json"; echo 0 > "$T/plan_attempts"
rm -f "$T/plan_ok" "$T/task_map.md" "$T/split_children.json"
printf '{"readiness":"ready","classification":"bug","confidence":"high","title":"feat: x","labels":[],"questions":[],"decisions":[],"summary":"s"}' > "$T/triage.json"
printf 'old triage.md\n' > "$T/triage.md"
OUT=$(sh "$T/plan_gate.sh" 2>&1); RC=$?
check "plan too_large: status"       "too_large" "$(jq -r '.context_updates.plan_status' <<<"$(lastjson "$OUT")")"
check "plan too_large: readiness"    "needs_info" "$(jq -r .readiness "$T/triage.json")"
check "plan too_large: one question" "1"   "$(jq '.questions|length' "$T/triage.json")"
check "plan too_large: appends map"  "1" "$(grep -c '### Proposed task map (too large to split)' "$T/triage.md")"
check "plan too_large: no plan_ok"   "0"   "$([ -f "$T/plan_ok" ] && echo 1 || echo 0)"
check "plan too_large: question_count" "1" "$(jq -r '.context_updates.question_count' <<<"$(lastjson "$OUT")")"
check "plan too_large: hook question" "1" "$(jq -r '.context_updates.triage_questions' <<<"$(lastjson "$OUT")" | grep -c '^- This issue decomposes into more than 12')"

bad_dup='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["b","c","d","e"],"after":0}],"summary":"s"}'
bad_5tasks='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]},{"id":"f","title":"F","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b","c","d","e"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["f"],"after":0}],"summary":"s"}'
bad_after='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]},{"id":"f","title":"F","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["c","d","e","f"],"after":4}],"summary":"s"}'
bad_title='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]},{"id":"f","title":"F","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b","c"],"after":null},{"title":"feat! b","criteria":["c"],"summary":"2","tasks":["d","e","f"],"after":0}],"summary":"s"}'
bad_empty='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]},{"id":"f","title":"F","intent":"i","files":[],"covers":[]}],"children":[],"summary":"s"}'
bad_order='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["c","a"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["b","d","e"],"after":0}],"summary":"s"}'
bad_crit='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":[],"summary":"1","tasks":["a","b"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["c","d","e"],"after":0}],"summary":"s"}'
bad_missing='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["c","d"],"after":0}],"summary":"s"}'
bad_did='{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"a","title":"B","intent":"i","files":[],"covers":[]}],"children":[],"summary":"s"}'
for pg_bad in bad_dup bad_5tasks bad_after bad_title bad_empty bad_did bad_order bad_crit bad_missing; do
    printf '%s' "${!pg_bad}" | jq -c '._io.visit="ok1"' > "$T/plan.json"; echo 0 > "$T/plan_attempts"
    rm -f "$T/plan_ok" "$T/task_map.md" "$T/split_children.json"
    sh "$T/plan_gate.sh" >/dev/null 2>&1; RC=$?
    check "plan invalid $pg_bad: retries" "1" "$RC"
done

printf '%s' "$bad_empty" | jq -c '._io.visit="ok1"' > "$T/plan.json"; echo 1 > "$T/plan_attempts"
rm -f "$T/plan_ok" "$T/task_map.md" "$T/split_children.json"
OUT=$(sh "$T/plan_gate.sh" 2>&1); RC=$?
check "plan second invalid: exit 0"  "0"    "$RC"
check "plan second invalid: none"    "none" "$(jq -r '.context_updates.plan_status' <<<"$(lastjson "$OUT")")"

# claim deletes the C1 files, resets plan_attempts, and publishes plan_status
tp_setup '[]'
touch "$T/plan_ok" "$T/task_map.md"; echo x > "$T/plan.json"; echo y > "$T/split_children.json"; echo 5 > "$T/plan_attempts"
sh "$T/claim.sh" >/dev/null 2>&1
check "claim deletes plan.json"          "0"    "$([ -f "$T/plan.json" ] && echo 1 || echo 0)"
check "claim deletes plan_ok"            "0"    "$([ -f "$T/plan_ok" ] && echo 1 || echo 0)"
check "claim deletes task_map"           "0"    "$([ -f "$T/task_map.md" ] && echo 1 || echo 0)"
check "claim deletes split_children"     "0"    "$([ -f "$T/split_children.json" ] && echo 1 || echo 0)"
check "claim resets plan_attempts"       "0"    "$(cat "$T/plan_attempts")"
OUT=$(sh "$T/claim.sh" 2>&1)
check "claim routing plan_status"        "none" "$(jq -r '.context_updates.plan_status' <<<"$(lastjson "$OUT")")"

# done publishes split
echo split > "$T/triage_outcome"
check "done: publishes split"            "split" "$(jq -r '.context_updates.triage_outcome' <<<"$(lastjson "$(sh "$T/done.sh" 2>&1)")")"
rm -f "$T/triage_outcome"

# --- apply_split (ADR 0015 C2/C3/C4) ---
AS="$WORK/asplit"; mkdir -p "$AS/bin"
extract_from "$SHARED_TRIAGE" apply_split | sed "s#/tmp/fabro#$AS#g" > "$AS/apply_split.sh"
if ! sh -n "$AS/apply_split.sh" 2>"$AS/syn"; then FAIL=$((FAIL + 1)); printf '  FAIL apply_split is not valid POSIX sh\n'; fi
# The stub answers the sub-issues list in GitHub's real shape: a list of plain issue
# objects, no wrapper key. Fault switches (files in $GH_STATE): sub_fail makes the
# list read fail; link_fail makes the link POST fail; link_dup makes it fail the way
# a duplicate link does, with the child already in the list; create_blank makes
# `issue create` print no URL.
cat > "$AS/bin/gh" <<'ASTUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "api repos/{owner}/{repo}/issues/7/sub_issues" ]; then
  [ -f "$GH_STATE/sub_fail" ] && { echo 'HTTP 502' >&2; exit 1; }
  [ -f "$GH_STATE/sub_seed.json" ] && cat "$GH_STATE/sub_seed.json" || echo '[]'
  exit 0
fi
if [ "$1" = api ] && [ "$2" = "-X" ]; then
  [ -f "$GH_STATE/link_fail" ] && { echo 'HTTP 500' >&2; exit 1; }
  if [ -f "$GH_STATE/link_dup" ]; then
    C=$(cat "$GH_STATE/counter")
    S=$(cat "$GH_STATE/sub_seed.json" 2>/dev/null || echo '[]')
    printf '%s' "$S" | jq -c --argjson c "$C" '. + [{number:$c,title:"linked"}]' > "$GH_STATE/sub_seed.json"
    echo 'HTTP 422: Issue may not contain duplicate sub-issues' >&2; exit 1
  fi
  echo '{}'; exit 0
fi
if [ "$1" = api ]; then
  echo '123456'; exit 0
fi
if [ "$1 $2" = "issue create" ]; then
  [ -f "$GH_STATE/create_blank" ] && exit 0
  C=$(cat "$GH_STATE/counter" 2>/dev/null || echo 100); C=$((C+1)); echo $C > "$GH_STATE/counter"
  BF=""; p=""
  for a in "$@"; do [ "$p" = "--body-file" ] && BF="$a"; p="$a"; done
  [ -n "$BF" ] && cp "$BF" "$GH_STATE/child_${C}.md"
  echo "https://github.com/o/r/issues/$C"
  exit 0
fi
exit 0
ASTUB
chmod +x "$AS/bin/gh"
PATH="$AS/bin:$SAVED_PATH"
export GH_LOG="$AS/gh.log" GH_STATE="$AS"

printf '{"number":7,"title":"Parent","labels":[{"name":"needs-triage"},{"name":"architecture"},{"name":"agent"}],"body":"parent body"}' > "$AS/issue.json"
printf '{"readiness":"ready","title":"feat: parent","labels":["area:web"],"questions":[],"decisions":[{"question":"Where?","decision":"Here.","basis":"CONTEXT.md"}],"summary":"s"}' > "$AS/triage.json"
printf 'report body\n' > "$AS/triage.md"
printf '## Proposed task map\n\nDrafted by triage against `main`.\n' > "$AS/task_map.md"
printf '%s\n' '[{"title":"feat(one): one","summary":"does one","criteria":["Users can do one."],"after":null,"task_map":"## Proposed task map\n\n1. **A1** (`a1`)\n","tasks":[{"id":"a1","title":"A1","intent":"i","files":[],"covers":["c1"]}]},{"title":"feat(two): two","summary":"does two","criteria":["Users can do two."],"after":0,"task_map":"## Proposed task map\n\n1. **B1** (`b1`)\n","tasks":[{"id":"b1","title":"B1","intent":"i","files":[],"covers":[]}]},{"title":"feat(three): three","summary":"does three","criteria":["Users can do three."],"after":1,"task_map":"## Proposed task map\n\n1. **C1** (`c1`)\n","tasks":[{"id":"c1","title":"C1","intent":"i","files":[],"covers":[]}]}]' > "$AS/split_children.json"
as_reset() { rm -f "$AS/counter" "$AS/triage_outcome" "$AS/sub_seed.json" "$AS/sub_fail" "$AS/link_fail" "$AS/link_dup" "$AS/create_blank" "$AS"/child_*.md; : > "$AS/gh.log"; }
as_reset

OUT=$(sh "$AS/apply_split.sh" 2>&1); RC=$?
check "asplit: exit 0"                 "0" "$RC"
check "asplit: outcome"                "split" "$(cat "$AS/triage_outcome")"
check "asplit: 3 creates"              "3" "$(grep -c '^issue create' "$AS/gh.log")"
check "asplit: first agent"          "1" "$(grep '^issue create' "$AS/gh.log" | sed -n '1p' | grep -c -- '--label agent ')"
check "asplit: later children held"    "2" "$(grep '^issue create' "$AS/gh.log" | sed -n '2,3p' | grep -c -- '--label agent-held')"
check "asplit: architecture inherited" "3" "$(grep '^issue create' "$AS/gh.log" | grep -c -- '--label=architecture')"
check "asplit: triage labels inherited" "3" "$(grep '^issue create' "$AS/gh.log" | grep -c -- '--label=area:web')"
check "asplit: triage label created"   "1" "$(grep -c '^label create area:web' "$AS/gh.log")"
check "asplit: agent never inherited"  "0" "$(grep '^issue create' "$AS/gh.log" | grep -c -- '--label=agent')"
check "asplit: needs-triage dropped"   "0" "$(grep '^issue create' "$AS/gh.log" | grep -c -- 'needs-triage')"
check "asplit: 3 sub-issue links"      "3" "$(grep -c -- 'sub_issues -F sub_issue_id' "$AS/gh.log")"
check "asplit: parent agent-split"     "1" "$(grep -c -- 'issue edit 7 --title feat: parent --add-label agent-split' "$AS/gh.log")"
check "asplit: parent never agent"     "0" "$(grep 'issue edit 7' "$AS/gh.log" | grep -c -- 'add-label agent ')"
check "asplit: parent agent removed"   "1" "$(grep 'issue edit 7 --title' "$AS/gh.log" | grep -c -- '--remove-label=agent')"

check "asplit: marker child1 after=0"  "<!-- fabro:split-child parent=7 after=0 -->" "$(head -1 "$AS/child_101.md")"
check "asplit: marker child2"          "<!-- fabro:split-child parent=7 after=101 -->" "$(head -1 "$AS/child_102.md")"
check "asplit: marker child3"          "<!-- fabro:split-child parent=7 after=102 -->" "$(head -1 "$AS/child_103.md")"
check "asplit: child summary 1st prose" "does two" "$(sed -n '3p' "$AS/child_102.md")"
check "asplit: child criteria"         "1" "$(grep -c '^- Users can do two[.]$' "$AS/child_102.md")"
check "asplit: child criteria heading" "1" "$(grep -c '^## Acceptance criteria$' "$AS/child_102.md")"
check "asplit: split into checklist"   "1" "$(grep -c '^## Split into' "$AS/split_parent_body.md")"
check "asplit: split decision line"    "1" "$(grep -c '\*\*Split this issue?\*\* Into #101, #102, #103 (basis: task map of 3 tasks' "$AS/split_parent_body.md")"

# Idempotent re-run: the sub-issues list (real shape) already holds two children.
as_reset
printf '%s\n' '[{"number":101,"title":"feat(one): one","state":"open"},{"number":102,"title":"feat(two): two","state":"open"}]' > "$AS/sub_seed.json"
echo 102 > "$AS/counter"
sh "$AS/apply_split.sh" >/dev/null 2>&1
check "asplit idempotent: 1 create"    "1" "$(grep -c '^issue create' "$AS/gh.log")"
check "asplit idempotent: 1 link"      "1" "$(grep -c -- 'sub_issues -F sub_issue_id' "$AS/gh.log")"
check "asplit idempotent: real pred"   "<!-- fabro:split-child parent=7 after=102 -->" "$(head -1 "$AS/child_103.md")"

# An unreadable sub-issues list fails closed: it is never read as "no children".
as_reset; : > "$AS/sub_fail"
sh "$AS/apply_split.sh" >/dev/null 2>&1; RC=$?
check "asplit read fails: nonzero"     "1" "$([ $RC -ne 0 ] && echo 1 || echo 0)"
check "asplit read fails: no create"   "0" "$(grep -c '^issue create' "$AS/gh.log")"
check "asplit read fails: no outcome"  "0" "$([ -f "$AS/triage_outcome" ] && echo 1 || echo 0)"

# A link that fails and is not in the list stops the node after that child.
as_reset; : > "$AS/link_fail"
sh "$AS/apply_split.sh" >/dev/null 2>&1; RC=$?
check "asplit link fails: nonzero"     "1" "$([ $RC -ne 0 ] && echo 1 || echo 0)"
check "asplit link fails: 1 create"    "1" "$(grep -c '^issue create' "$AS/gh.log")"
check "asplit link fails: no outcome"  "0" "$([ -f "$AS/triage_outcome" ] && echo 1 || echo 0)"

# A link rejected as a duplicate, with the child already in the list, is success.
as_reset; : > "$AS/link_dup"
sh "$AS/apply_split.sh" >/dev/null 2>&1; RC=$?
check "asplit link dup: exit 0"        "0" "$RC"
check "asplit link dup: outcome"       "split" "$(cat "$AS/triage_outcome" 2>/dev/null)"

# A create that yields no issue number stops the node; no child is held behind after=0.
as_reset; : > "$AS/create_blank"
sh "$AS/apply_split.sh" >/dev/null 2>&1; RC=$?
check "asplit no number: nonzero"      "1" "$([ $RC -ne 0 ] && echo 1 || echo 0)"
check "asplit no number: 1 create"     "1" "$(grep -c '^issue create' "$AS/gh.log")"
check "asplit no number: no link"      "0" "$(grep -c -- 'sub_issues -F sub_issue_id' "$AS/gh.log")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# arch-review — scan_gate, file_issues, summarize (ADR 0012)
# ---------------------------------------------------------------------------
echo ""
echo "arch-review scan and file"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/arch"; mkdir -p "$T/bin" "$T/arch"
for n in scan_gate file_issues summarize; do
    extract_from "$ARCH" "$n" | sed "s#/tmp/fabro#$T#g" > "$T/$n.sh"
    if ! sh -n "$T/$n.sh" 2>"$T/$n.syntax"; then
        FAIL=$((FAIL + 1)); printf '  FAIL %s is not valid POSIX sh\n' "$n"
    fi
done
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue list")   cat "$GH_STATE/live.json"; exit 0 ;;
  "issue create") C=$(cat "$GH_STATE/counter" 2>/dev/null || echo 400); C=$((C+1)); echo $C > "$GH_STATE/counter"
                  echo "https://github.com/o/r/issues/$C"; exit 0 ;;
  "repo view")    echo "jelly-swipe"; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

cand() { # cand <slug> <strength>
    printf '{"slug":"%s","title":"refactor: %s","strength":"%s","files":["a.py"],"problem":"p","solution":"s","benefits":"b","diagram":""}' "$1" "$1" "$2"
}

mkreadok ok1 hotspots.txt existing.json
scandump() { jq --arg v ok1 '._io.visit = $v' > "$T/arch/candidates.json"; }

# 1. A valid file.
echo 0 > "$T/arch/scan_attempts"
printf '{"candidates":[%s,%s]}' "$(cand one-a Strong)" "$(cand two-b 'Worth exploring')" | scandump
OUT=$(sh "$T/scan_gate.sh" 2>&1); RC=$?
check "scan_gate valid: exit 0"        "0"  "$RC"
check "scan_gate valid: ok"            "ok" "$(jq -r '.context_updates.scan_status' <<<"$(lastjson "$OUT")")"
check "scan_gate valid: count"         "2"  "$(jq -r '.context_updates.candidate_count' <<<"$(lastjson "$OUT")")"

# 2. A duplicate slug: one repair turn, then failed.
echo 0 > "$T/arch/scan_attempts"
printf '{"candidates":[%s,%s]}' "$(cand one-a Strong)" "$(cand one-a Strong)" | scandump
sh "$T/scan_gate.sh" >/dev/null 2>&1; RC=$?
check "scan_gate dup: first retries"   "1" "$RC"
OUT=$(sh "$T/scan_gate.sh" 2>&1); RC=$?
check "scan_gate dup: second exit 0"   "0" "$RC"
check "scan_gate dup: failed"          "failed" "$(cat "$T/arch/scan_status")"

# 3. An unknown strength.
echo 0 > "$T/arch/scan_attempts"
printf '{"candidates":[%s]}' "$(cand one-a Maybe)" | scandump
sh "$T/scan_gate.sh" >/dev/null 2>&1; RC=$?
check "scan_gate bad strength: retry"  "1" "$RC"

# 3b. Fields file_issues concatenates must be strings: a number passes an emptiness
#     test and then stops file_issues partway through the list.
for bad in '.files = [1]' '.problem = 5' '.slug = 123' '.diagram = 7'; do
    echo 0 > "$T/arch/scan_attempts"
    jq -c "{candidates: [($(cand one-a Strong)) | $bad]}" <<<'null' | scandump
    sh "$T/scan_gate.sh" >/dev/null 2>&1; RC=$?
    check "scan_gate $bad: retry"      "1" "$RC"
done

# 4. file_issues: 5 Strong, 5 Worth exploring, 1 Speculative; c0 already filed.
{
  printf '{"candidates":['
  for i in 0 1 2 3 4; do printf '%s,' "$(cand c$i Strong)"; done
  for i in 5 6 7 8 9; do printf '%s,' "$(cand c$i 'Worth exploring')"; done
  printf '%s]}' "$(cand spec-one Speculative)"
} | scandump
printf '[{"body":"<!-- fabro:arch-candidate slug=c0 -->\\nold"}]' > "$T/live.json"
rm -f "$T/counter"; : > "$T/gh.log"
OUT=$(sh "$T/file_issues.sh" 2>&1)
check "file: caps at 8"                "8" "$(grep -c '^issue create' "$T/gh.log")"
check "file: skips an existing slug"   "0" "$(grep -c 'refactor: c0 --body-file' "$T/gh.log")"
check "file: never Speculative"        "0" "$(grep -c 'refactor: spec-one --body-file' "$T/gh.log")"
check "file: stops after the cap"      "0" "$(grep -c 'refactor: c9 --body-file' "$T/gh.log")"
check "file: all three labels"         "8" "$(grep -c -- '--label architecture --label needs-triage --label ai-generated' "$T/gh.log")"
check "file: marker is line one"       "<!-- fabro:arch-candidate slug=c8 -->" "$(head -1 "$T/arch/body.md")"
check "file: filed_count"              "8" "$(jq -r '.context_updates.filed_count' <<<"$(lastjson "$OUT")")"

# 5. summarize: the two shapes of the line.
echo ok > "$T/arch/scan_status"; echo '[401,402]' > "$T/arch/filed.json"
check "summary: filed"                 "jelly-swipe: 2 filed (#401 #402); 0 triaged: 0 agent, 0 split, 0 needs-info, 0 not actionable" \
    "$(jq -r '.context_updates.arch_summary' <<<"$(lastjson "$(sh "$T/summarize.sh" 2>&1)")")"
echo failed > "$T/arch/scan_status"; echo '[]' > "$T/arch/filed.json"
check "summary: scan failed"           "jelly-swipe: scan failed, 0 filed; 0 triaged: 0 agent, 0 split, 0 needs-info, 0 not actionable" \
    "$(jq -r '.context_updates.arch_summary' <<<"$(lastjson "$(sh "$T/summarize.sh" 2>&1)")")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# arch-review — the triage queue: build_queue, next_issue, summarize (ADR 0012)
# ---------------------------------------------------------------------------
echo ""
echo "arch-review triage queue"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/archq"; mkdir -p "$T/bin" "$T/arch"
for n in build_queue next_issue summarize; do
    extract_from "$ARCH" "$n" | sed "s#/tmp/fabro#$T#g" > "$T/$n.sh"
    if ! sh -n "$T/$n.sh" 2>"$T/$n.syntax"; then
        FAIL=$((FAIL + 1)); printf '  FAIL %s is not valid POSIX sh\n' "$n"
    fi
done
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$*" in
  "issue list --label needs-info"*)   cat "$GH_STATE/info.json"; exit 0 ;;
  "issue list --label needs-triage"*) cat "$GH_STATE/triage.json"; exit 0 ;;
  "repo view"*)                       echo "jelly-swipe"; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

# 1. build_queue: this run's issues first, answered needs-info next, then
#    needs-triage oldest first, at most 10 others, no duplicates.
echo '[401,402]' > "$T/arch/filed.json"
echo '[{"number":6,"comments":[{"body":"<!-- fabro:triage-questions -->"}]},{"number":5,"comments":[{"body":"the answer is yes"}]}]' > "$T/info.json"
echo '[{"number":401},{"number":19},{"number":3},{"number":7},{"number":8},{"number":11},{"number":12},{"number":13},{"number":14},{"number":15},{"number":16},{"number":17},{"number":18}]' > "$T/triage.json"
OUT=$(sh "$T/build_queue.sh" 2>&1); RC=$?
check "queue: exit 0"                  "0" "$RC"
check "queue: order and cap"           "401 402 5 3 7 8 11 12 13 14 15 16" "$(jq -r 'map(tostring) | join(" ")' "$T/queue.json")"
check "queue: unanswered not queued"   "0" "$(jq '[.[] | select(. == 6)] | length' "$T/queue.json")"
check "queue: length published"        "12" "$(jq -r '.context_updates.queue_length' <<<"$(lastjson "$OUT")")"
check "queue: lists past 100 issues"   "2" "$(grep -c -- '--limit 1000' "$T/gh.log")"
check "graph: signature limit covers the loop" "1" \
    "$(grep -c '^        loop_restart_signature_limit=20,$' "$ARCH")"

# 2. next_issue walks the queue and counts each outcome.
echo '[11,12]' > "$T/queue.json"; echo 0 > "$T/queue_index"; date +%s > "$T/run_started"
echo '{"ready":0,"needs_info":0,"not_actionable":0,"skipped":0,"released":0,"split":0}' > "$T/tally.json"
echo 0 > "$T/arch/deferred"; rm -f "$T/arch/in_flight"
OUT=$(sh "$T/next_issue.sh" 2>&1)
check "next: first issue"              "11"    "$(cat "$T/issue_number")"
check "next: publishes issue_number"   "11"    "$(jq -r '.context_updates.issue_number' <<<"$(lastjson "$OUT")")"
check "next: not done"                 "false" "$(jq -r '.context_updates.queue_done' <<<"$(lastjson "$OUT")")"
echo ready > "$T/triage_outcome"
sh "$T/next_issue.sh" >/dev/null 2>&1
check "next: counts ready"             "1"     "$(jq -r .ready "$T/tally.json")"
check "next: second issue"             "12"    "$(cat "$T/issue_number")"
echo needs_info > "$T/triage_outcome"
OUT=$(sh "$T/next_issue.sh" 2>&1)
check "next: counts needs_info"        "1"     "$(jq -r .needs_info "$T/tally.json")"
echo '[5]' > "$T/queue.json"; echo 0 > "$T/queue_index"; rm -f "$T/arch/in_flight"
echo '{"ready":0,"split":0,"needs_info":0,"not_actionable":0,"skipped":0,"released":0}' > "$T/tally.json"
sh "$T/next_issue.sh" >/dev/null 2>&1
echo split > "$T/triage_outcome"
sh "$T/next_issue.sh" >/dev/null 2>&1
check "next: counts split"             "1"     "$(jq -r .split "$T/tally.json")"
check "next: done at the end"          "true"  "$(jq -r '.context_updates.queue_done' <<<"$(lastjson "$OUT")")"

# 3. The 6-hour budget stops new triages and records what is left.
echo '[21,22,23]' > "$T/queue.json"; echo 1 > "$T/queue_index"; rm -f "$T/arch/in_flight"
echo $(( $(date +%s) - 21601 )) > "$T/run_started"
OUT=$(sh "$T/next_issue.sh" 2>&1)
check "budget: done"                   "true"  "$(jq -r '.context_updates.queue_done' <<<"$(lastjson "$OUT")")"
check "budget: deferred"               "2"     "$(cat "$T/arch/deferred")"

# 4. summarize: the full line.
echo ok > "$T/arch/scan_status"; echo '[401,402,403]' > "$T/arch/filed.json"
echo '{"ready":10,"split":1,"needs_info":1,"not_actionable":1,"skipped":0,"released":1}' > "$T/tally.json"
echo 2 > "$T/arch/deferred"
check "summary: full line" \
    "jelly-swipe: 3 filed (#401 #402 #403); 14 triaged: 10 agent, 1 split, 1 needs-info, 1 not actionable, 1 released; 2 left for the next review" \
    "$(jq -r '.context_updates.arch_summary' <<<"$(lastjson "$(sh "$T/summarize.sh" 2>&1)")")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# review-merge merge_gate — an Architecture PR is never auto-merged (ADR 0012 D8)
# ---------------------------------------------------------------------------
echo ""
echo "review-merge merge_gate architecture"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/mgate"; mkdir -p "$T/bin" "$T/review"
extract_from "$SHARED" merge_gate | sed "s#/tmp/fabro#$T#g" > "$T/merge_gate.sh"
if ! sh -n "$T/merge_gate.sh" 2>"$T/merge_gate.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL merge_gate is not valid POSIX sh\n'
fi
# `gh pr view ... --jq <expr>` is emulated by running jq on the fixture, so each
# check sees exactly what it would see from GitHub for that PR.
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "pr view" ]; then
  J=""; P=""
  for a in "$@"; do [ "$P" = "--jq" ] && J="$a"; P="$a"; done
  if [ -n "$J" ]; then jq -r "$J" "$GH_STATE/pr.fixture.json"; else cat "$GH_STATE/pr.fixture.json"; fi
  exit 0
fi
exit 0
STUB
chmod +x "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"
echo 1 > "$T/auto_merge"; echo 5 > "$T/pr_number"

# 1. agent-authored and architecture: blocked, with the reason.
echo '{"labels":[{"name":"agent-authored"},{"name":"architecture"}],"state":"OPEN","isDraft":false}' > "$T/pr.fixture.json"
rm -f "$T/merge_block_reason"
OUT=$(sh "$T/merge_gate.sh" 2>&1); RC=$?
check "arch PR: exit 0"                "0"     "$RC"
check "arch PR: not eligible"          "false" "$(jq -r '.context_updates.merge_eligible' <<<"$(lastjson "$OUT")")"
check "arch PR: reason names it"       "1"     "$(grep -c 'Architecture issue' "$T/merge_block_reason")"

# 2. No architecture label: this check passes, and a later one decides.
echo '{"labels":[{"name":"agent-authored"}],"state":"CLOSED","isDraft":false}' > "$T/pr.fixture.json"
rm -f "$T/merge_block_reason"
sh "$T/merge_gate.sh" >/dev/null 2>&1
check "plain PR: not blocked by 2b"    "0"     "$(grep -c 'Architecture issue' "$T/merge_block_reason")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# review-merge hygiene — tamper and erosion counters (ADR 0013 D1, D2), real git
# ---------------------------------------------------------------------------
# The fixtures ARE the counters' contract (docs/merge-gate/00, C5): a pattern
# change starts here. Real git, because what is under test is what
# `git diff -U0 origin/main...HEAD` shows for each kind of change.
echo ""
echo "review-merge hygiene (real git)"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/hyg"; mkdir -p "$T"
extract_from "$SHARED" hygiene | sed "s#/tmp/fabro#$T#g" > "$T/hygiene_node.sh"
if ! sh -n "$T/hygiene_node.sh" 2>"$T/hygiene_node.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL hygiene is not valid POSIX sh\n'
fi

# A fresh upstream with a Python test, a module and a Rust file, cloned onto a run
# branch. $1, when given, is the base branch's .fabro/hygiene.json.
hy_repo() {
    rm -rf "$T/up" "$T/wt" "$T/review" "$T/_hygiene"
    mkdir -p "$T/up/tests" "$T/up/src" "$T/up/.fabro"
    git -C "$T/up" init -q -b main .
    git -C "$T/up" config user.email t@t
    git -C "$T/up" config user.name t
    printf '%s\n' 'def test_a():' '    assert add(1, 2) == 3' '    assert add(0, 0) == 0' > "$T/up/tests/test_math.py"
    printf '%s\n' 'def add(a, b):' '    return a + b' > "$T/up/src/math.py"
    printf '%s\n' 'fn f() -> u8 { 1 }' > "$T/up/src/lib.rs"
    if [ -n "${1:-}" ]; then printf '%s' "$1" > "$T/up/.fabro/hygiene.json"; fi
    git -C "$T/up" add -A
    git -C "$T/up" commit -qm init
    git clone -q "$T/up" "$T/wt"
    git -C "$T/wt" config user.email t@t
    git -C "$T/wt" config user.name t
    git -C "$T/wt" checkout -qb fabro/run/01TEST
    echo main > "$T/base_ref"
}
hy_commit() { git -C "$T/wt" add -A; git -C "$T/wt" commit -qm change; }
hy_run() { ( cd "$T/wt" && sh "$T/hygiene_node.sh" >/dev/null 2>&1 ); }
hy() { jq -r "$1" "$T/review/hygiene.json"; }

# 1. A real change with no test impact is clear, and leaves no reason file.
hy_repo
printf '%s\n' 'def add(a, b):' '    """Add."""' '    return a + b' > "$T/wt/src/math.py"; hy_commit; hy_run
check "hygiene clean: nothing blocks"       "0"       "$(hy '.blocking | length')"
check "hygiene clean: default config"       "default" "$(hy .config)"
check "hygiene clean: no reason file"       "absent"  "$([ -f "$T/review/hygiene_reason.txt" ] && echo present || echo absent)"

# 2. Moving a test file is a rename, not a deletion, and must pass.
hy_repo
mkdir -p "$T/wt/tests/unit"; git -C "$T/wt" mv tests/test_math.py tests/unit/test_math.py; hy_commit; hy_run
check "hygiene moved test: nothing blocks"  "0" "$(hy '.blocking | length')"

# 3. tests_deleted, and the deleted file's assertions are not counted twice.
hy_repo
git -C "$T/wt" rm -q tests/test_math.py; hy_commit; hy_run
check "tests_deleted: counted"              "1"             "$(hy .tamper.tests_deleted)"
check "tests_deleted: blocks alone"         "tests_deleted" "$(hy '.blocking | join(",")')"
check "tests_deleted: asserts not doubled"  "0"             "$(hy .tamper.asserts_removed)"
check "tests_deleted: reason names it"      "1"             "$(grep -c 'tests_deleted=1' "$T/review/hygiene_reason.txt")"

# 4. skips_added, with the new-file line number as the sample.
hy_repo
printf '%s\n' 'import pytest' '@pytest.mark.skip' 'def test_a():' '    assert add(1, 2) == 3' '    assert add(0, 0) == 0' > "$T/wt/tests/test_math.py"; hy_commit; hy_run
check "skips_added: pytest mark"            "1" "$(hy .tamper.skips_added)"
check "skips_added: sample line"            "2" "$(hy '.samples[0].line')"

# 5. A JS skip/only counts in a test file, and `stream.skip(` in source does not.
hy_repo
mkdir -p "$T/wt/web"
printf '%s\n' 'it.skip("x", () => {})' 'describe.only("y", () => {})' > "$T/wt/web/a.test.ts"
printf '%s\n' 'stream.skip(3)' > "$T/wt/web/a.ts"; hy_commit; hy_run
check "skips_added: js test file only"      "2" "$(hy .tamper.skips_added)"

# 6. Rust keeps tests beside the code, so #[ignore] counts in any file.
hy_repo
printf '%s\n' 'fn f() -> u8 { 1 }' '#[test]' '#[ignore]' 'fn t() {}' > "$T/wt/src/lib.rs"; hy_commit; hy_run
check "skips_added: rust ignore"            "1" "$(hy .tamper.skips_added)"

# 7. asserts_removed: one assertion gone, nothing added in its hunk.
hy_repo
printf '%s\n' 'def test_a():' '    assert add(1, 2) == 3' > "$T/wt/tests/test_math.py"; hy_commit; hy_run
check "asserts_removed: counted"            "1" "$(hy .tamper.asserts_removed)"

# 8. ...but an assertion rewritten in place is not a removal.
hy_repo
printf '%s\n' 'def test_a():' '    assert add(1, 2) == 3' '    assert add(0, 0) == 0 and add(0, 1) == 1' > "$T/wt/tests/test_math.py"; hy_commit; hy_run
check "asserts_removed: rewrite is not"     "0" "$(hy .tamper.asserts_removed)"

# 9. tautologies_added: a literal subject, a same-argument matcher, `assert True`
#    and `assert x == x`; `expect(y).toEqual(z)` is not one.
hy_repo
mkdir -p "$T/wt/web"
printf '%s\n' 'expect(true).toBe(true)' 'expect(x).toEqual(x)' 'expect(y).toEqual(z)' > "$T/wt/web/b.spec.ts"
printf '%s\n' 'def test_a():' '    assert True' '    assert add(1, 2) == 3' '    assert add(0, 0) == 0' '    assert x == x' > "$T/wt/tests/test_math.py"; hy_commit; hy_run
check "tautologies_added: four"             "4" "$(hy .tamper.tautologies_added)"

# 10. Erosion counters report, and do not block, in the default report mode.
hy_repo
printf '%s\n' 'def add(a, b):' '    try:' '        return a + b' '    except Exception:' '        return 0  # type: ignore' '    # TODO fix this' '    # TODO(#12) linked' > "$T/wt/src/math.py"; hy_commit; hy_run
check "erosion: broad_except"               "1" "$(hy .erosion.broad_except)"
check "erosion: type_escape"                "1" "$(hy .erosion.type_escape)"
check "erosion: only the unlinked TODO"     "1" "$(hy .erosion.todo_unlinked)"
check "erosion: report mode never blocks"   "0" "$(hy '.blocking | length')"

# 11. Four lint disables: over the default threshold of 3. Report mode lets it
#     through; block mode on the base branch blocks it; a per-counter threshold of 5
#     lets it through again.
hy_lint() { printf '%s\n' 'x = 1  # noqa' 'y = 2  # noqa' 'z = 3  # noqa' 'w = 4  # noqa' > "$T/wt/src/math.py"; hy_commit; hy_run; }
hy_repo; hy_lint
check "lint_disable: counted"               "4"            "$(hy .erosion.lint_disable)"
check "lint_disable: report mode passes"    "0"            "$(hy '.blocking | length')"
hy_repo '{"erosion_mode":"block"}'; hy_lint
check "lint_disable: block mode blocks"     "lint_disable" "$(hy '.blocking | join(",")')"
check "block mode: config read from base"   "repo"         "$(hy .config)"
hy_repo '{"erosion_mode":"block","erosion_thresholds":{"lint_disable":5}}'; hy_lint
check "per-counter threshold raises it"     "0"            "$(hy '.blocking | length')"

# 12. rust_unwrap in non-test Rust.
hy_repo
printf '%s\n' 'fn f() -> u8 { "1".parse().unwrap() }' > "$T/wt/src/lib.rs"; hy_commit; hy_run
check "rust_unwrap: counted"                "1" "$(hy .erosion.rust_unwrap)"

# 13. A PR that edits .fabro/hygiene.json trips config_changed, and its own
#     loosened threshold is NOT the one used.
hy_repo
mkdir -p "$T/wt/.fabro"; printf '%s' '{"erosion_mode":"report","erosion_threshold":99}' > "$T/wt/.fabro/hygiene.json"; hy_commit; hy_run
check "config_changed: tamper"              "1" "$(hy .tamper.config_changed)"
check "config_changed: PR config ignored"   "3" "$(hy .erosion_threshold)"

# 14. An invalid base config blocks rather than falling back silently.
hy_repo '{"erosion_mode":"loud"}'; hy_lint
check "invalid config: blocks"              "config_invalid" "$(hy '.blocking | join(",")')"

# 15. Lockfiles are excluded entirely.
hy_repo
printf '%s\n' '# TODO nothing' > "$T/wt/Cargo.lock"; hy_commit; hy_run
check "exclude_paths: lockfile ignored"     "0" "$(hy .erosion.todo_unlinked)"

# 16. A removed SQL comment is `--- ...` in the diff body, not a file header.
hy_repo
printf '%s\n' '-- a comment' 'select 1;' > "$T/wt/src/q.sql"; hy_commit
printf '%s\n' 'select 1;' > "$T/wt/src/q.sql"; hy_commit; hy_run
check "diff body '---' is not a header"     "0" "$(hy '.blocking | length')"

# 17. A base that cannot be diffed fails closed.
hy_repo
echo nosuch > "$T/base_ref"; hy_run
check "no base: hygiene_error blocks"       "hygiene_error" "$(hy '.blocking | join(",")')"

# hy_base PATH LINE... : give the base branch PATH with these lines, and restart the
# run branch from it, for cases whose "before" is not hy_repo's.
hy_base() {
    F="$1"; shift
    mkdir -p "$(dirname "$T/up/$F")"; printf '%s\n' "$@" > "$T/up/$F"
    git -C "$T/up" add -A; git -C "$T/up" commit -qm base
    git -C "$T/wt" fetch -q origin; git -C "$T/wt" reset -q --hard origin/main
}

# 18. Rust's `.expect(` is a Result method, not an assertion: replacing it with `?`
#     in ordinary code is the fix rust_unwrap asks for, and must not count as a
#     removed assertion.
hy_repo
hy_base src/lib.rs 'fn f() -> u8 {' '    let x = g().expect("g failed");' '    x' '}'
printf '%s\n' 'fn f() -> Result<u8> {' '    let x = g()?;' '    Ok(x)' '}' > "$T/wt/src/lib.rs"; hy_commit; hy_run
check "rust .expect( removed: not an assert" "0" "$(hy .tamper.asserts_removed)"
check "rust .expect( removed: clear"         "0" "$(hy '.blocking | length')"

# 19. A test moved to another file carries its assertion with it: the same text is
#     added elsewhere in the PR, so it is not a removal.
hy_repo
printf '%s\n' 'def test_a():' '    assert add(1, 2) == 3' > "$T/wt/tests/test_math.py"
printf '%s\n' 'def test_zero():' '    assert add(0, 0) == 0' > "$T/wt/tests/test_zero.py"; hy_commit; hy_run
check "assert moved across files: not removed" "0" "$(hy .tamper.asserts_removed)"

# 20. ...but an unrelated assertion added in another file does not excuse one removed.
hy_repo
printf '%s\n' 'def test_a():' '    assert add(1, 2) == 3' > "$T/wt/tests/test_math.py"
printf '%s\n' 'def test_two():' '    assert add(2, 2) == 4' > "$T/wt/tests/test_two.py"; hy_commit; hy_run
check "unrelated assert elsewhere: still removed" "1" "$(hy .tamper.asserts_removed)"

# 21. A base config that is not one JSON object, or whose per-counter threshold is
#     not a number, is invalid -- not a silent "repo" config that loosens a counter.
hy_repo 'null'; hy_lint
check "config null: invalid"                "config_invalid" "$(hy '.blocking | join(",")')"
hy_repo '{"erosion_mode":"block","erosion_thresholds":{"lint_disable":"9"}}'; hy_lint
check "config string threshold: invalid"    "invalid"        "$(hy .config)"

PATH="$SAVED_PATH"

# ---------------------------------------------------------------------------
# review-merge merge_gate — hygiene and Refuter checks (ADR 0013 D1, D5)
# ---------------------------------------------------------------------------
# The architecture section above stops at check 2b. These cases need a PR that
# passes checks 1 to 12, so the stub also answers the GraphQL thread count and the
# fixture carries a Conventional title and a commit-body block.
echo ""
echo "review-merge merge_gate hygiene and refuter"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/mgate2"; mkdir -p "$T/bin" "$T/review"
extract_from "$SHARED" merge_gate | sed "s#/tmp/fabro#$T#g" > "$T/merge_gate.sh"
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1" = api ]; then echo 0; exit 0; fi
if [ "$1 $2" = "pr view" ]; then
  J=""; P=""
  for a in "$@"; do [ "$P" = "--jq" ] && J="$a"; P="$a"; done
  if [ -n "$J" ]; then jq -r "$J" "$GH_STATE/pr.fixture.json"; else cat "$GH_STATE/pr.fixture.json"; fi
  exit 0
fi
exit 0
STUB
chmod +x "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

# Check 13b compares the tree the counters read with HEAD's, so the gate runs in a
# real repository. `code/` is the only tracked path; the sandbox files stay untracked.
git -C "$T" init -q -b run .
git -C "$T" config user.email t@t
git -C "$T" config user.name t
mkdir -p "$T/code"; echo one > "$T/code/a.txt"
git -C "$T" add code; git -C "$T" commit -qm init

# Everything green: every check from 1 to 14 passes.
mg_green() {
    echo 1 > "$T/auto_merge"; echo 5 > "$T/pr_number"; echo main > "$T/base_ref"
    echo '{"url":"https://github.com/o/r/pull/5"}' > "$T/pr.json"
    jq -n '{labels:[{name:"agent-authored"}], state:"OPEN", isDraft:false, reviewDecision:"APPROVED",
            commits:[], title:"fix: a thing",
            body:(["x","<!-- fabro:commit-body:start -->","Resolves #1","<!-- fabro:commit-body:end -->"] | join("\n"))}' > "$T/pr.fixture.json"
    echo '{"outcome":"no_changes_needed","risk":1,"not_fixed":[],"own_findings":[],"fixes_applied":[]}' > "$T/review/fix_result.json"
    jq -n --arg h "$(git -C "$T" rev-parse HEAD)" '{blocking:[], head:$h}' > "$T/review/hygiene.json"
    echo pass > "$T/review/refute_verdict"
    rm -f "$T/merge_block_reason" "$T/review/hygiene_reason.txt" "$T/review/refute_reason.txt"
}
mg_run() { OUT=$( cd "$T" && sh "$T/merge_gate.sh" 2>&1 ); jq -r '.context_updates.merge_eligible' <<<"$(lastjson "$OUT")"; }

# 1. The all-green fixture really is eligible, so every block below is the new check.
mg_green
check "all green: eligible"                 "true"  "$(mg_run)"

# 2. Check 13 blocks on a non-empty `blocking`, with the counters' own reason.
mg_green
echo '{"blocking":["skips_added"]}' > "$T/review/hygiene.json"
echo 'Diff hygiene blocks the merge: skips_added=1.' > "$T/review/hygiene_reason.txt"
check "hygiene blocking: not eligible"      "false" "$(mg_run)"
check "hygiene blocking: reason"            "1"     "$(grep -c 'skips_added=1' "$T/merge_block_reason")"

# 3. ...and fails closed on a missing file, or one with no `blocking` array.
mg_green; rm -f "$T/review/hygiene.json"
check "hygiene missing: not eligible"       "false" "$(mg_run)"
check "hygiene missing: reason"             "1"     "$(grep -c 'missing or invalid' "$T/merge_block_reason")"
mg_green; echo '{}' > "$T/review/hygiene.json"
check "hygiene without blocking: blocks"    "false" "$(mg_run)"

# 4. Check 14 blocks on a `fail` verdict, with the gate's reason.
mg_green
echo fail > "$T/review/refute_verdict"
echo 'The refuter could not confirm the work is done: not_met: logout works.' > "$T/review/refute_reason.txt"
check "refute fail: not eligible"           "false" "$(mg_run)"
check "refute fail: reason"                 "1"     "$(grep -c 'not_met: logout' "$T/merge_block_reason")"

# 5. ...and on NO verdict: the Refuter timed out or its provider was down.
mg_green; rm -f "$T/review/refute_verdict"
check "refute missing: not eligible"        "false" "$(mg_run)"
check "refute missing: reason"              "1"     "$(grep -c 'did not return a verdict' "$T/merge_block_reason")"

# 6. Check 13b: an empty checkpoint commit after the counters ran moves HEAD but not
# the tree, and must not block...
mg_green; git -C "$T" commit -q --allow-empty -m checkpoint
check "13b empty checkpoint: eligible"      "true"  "$(mg_run)"

# 7. ...while a stage that edited the code after them (the refuter) blocks.
mg_green; echo two > "$T/code/a.txt"; git -C "$T" commit -qam "refute edited"
check "13b tree changed: not eligible"      "false" "$(mg_run)"
check "13b tree changed: reason"            "1"     "$(grep -c 'code changed after the diff-hygiene' "$T/merge_block_reason")"

# 8. ...and a hygiene.json with no head fails closed.
mg_green; echo '{"blocking":[]}' > "$T/review/hygiene.json"
check "13b no head: not eligible"           "false" "$(mg_run)"

# Check 6, real git. The run branch holds a fabro-co-authored commit and a merge of a
# moved origin/main, authored by the agent identity alone (what next_task and
# open_pr_prep produce). `cm_state` writes the PR's commit list the way gh reports it.
git -C "$T" branch -q -f base HEAD
git -C "$T" update-ref refs/remotes/origin/main "$(git -C "$T" rev-parse base)"
git -C "$T" checkout -q -b runbr
echo w > "$T/code/w.txt"; git -C "$T" add code; git -C "$T" commit -qm "fabro work"
W=$(git -C "$T" rev-parse HEAD)
git -C "$T" checkout -q -b mainmoved base
echo m > "$T/code/m.txt"; git -C "$T" add code; git -C "$T" commit -qm "main moved"
git -C "$T" update-ref refs/remotes/origin/main "$(git -C "$T" rev-parse HEAD)"
git -C "$T" checkout -q runbr
git -C "$T" merge -q --no-edit origin/main
G=$(git -C "$T" rev-parse HEAD)
git -C "$T" checkout -q -b other base
echo o > "$T/code/o.txt"; git -C "$T" add code; git -C "$T" commit -qm "unmerged side branch"
git -C "$T" checkout -q runbr
git -C "$T" merge -q --no-edit other
GO=$(git -C "$T" rev-parse HEAD)
cm_state() { # cm_state merge|other|human: the fabro commit W plus that case's commits
    mg_green
    jq --arg w "$W" --arg g "$G" --arg go "$GO" --arg e "$1" '
      .commits = [{oid:$w, authors:[{email:"noreply@fabro.sh"}]}]
        + (if $e == "merge" then [{oid:$g, authors:[{email:"bot@users.noreply.github.com"}]}]
           elif $e == "other" then [{oid:$g, authors:[{email:"bot@users.noreply.github.com"}]},
                                    {oid:$go, authors:[{email:"bot@users.noreply.github.com"}]}]
           elif $e == "human" then [{oid:$w, authors:[{email:"someone@example.com"}]}]
           else [] end)' "$T/pr.fixture.json" > "$T/pr.fixture.tmp" && mv "$T/pr.fixture.tmp" "$T/pr.fixture.json"
    jq -n --arg h "$(git -C "$T" rev-parse HEAD)" '{blocking:[], head:$h}' > "$T/review/hygiene.json"
}
# 9. Only fabro-co-authored commits and a merge of origin/main: not blocked by check 6.
git -C "$T" checkout -q -B runbr "$G"
cm_state merge
check "6 merge of main: eligible"           "true"  "$(mg_run)"
# 10. A commit by a human address (no fabro co-author, one parent) still blocks.
cm_state human
check "6 human commit: not eligible"        "false" "$(mg_run)"
check "6 human commit: reason"              "1"     "$(grep -c 'not authored by the automation' "$T/merge_block_reason")"
# 11. A merge that brings in a branch not contained in origin/main still blocks.
git -C "$T" checkout -q -B runbr "$GO"
cm_state other
check "6 merge of other branch: blocks"     "false" "$(mg_run)"
check "6 merge of other branch: reason"     "1"     "$(grep -c 'not authored by the automation' "$T/merge_block_reason")"
# 12. A commit gh reports but git cannot find fails closed.
git -C "$T" checkout -q -B runbr "$G"
cm_state merge
jq '.commits += [{oid:"0000000000000000000000000000000000000001", authors:[{email:"x@y"}]}]' "$T/pr.fixture.json" > "$T/pr.fixture.tmp" && mv "$T/pr.fixture.tmp" "$T/pr.fixture.json"
check "6 unknown commit: fails closed"      "false" "$(mg_run)"
# 13. A gh error fails closed.
cm_state merge; echo 'not json' > "$T/pr.fixture.json"
check "6 gh error: fails closed"            "false" "$(mg_run)"
# 14. A commit git shows with no parents (a root, or a shallow boundary) blocks even
# when it is in origin/main: `cut -s` keeps its own SHA from reading as its parents.
cm_state merge
R=$(git -C "$T" rev-list --max-parents=0 HEAD)
jq --arg r "$R" '.commits += [{oid:$r, authors:[{email:"x@y"}]}]' "$T/pr.fixture.json" > "$T/pr.fixture.tmp" && mv "$T/pr.fixture.tmp" "$T/pr.fixture.json"
check "6 parentless commit: blocks"         "false" "$(mg_run)"
check "6 parentless commit: reason"         "1"     "$(grep -c 'not authored by the automation' "$T/merge_block_reason")"
# 15. A missing base_ref fails closed: there is no origin/<base> to test parents against.
cm_state merge; rm -f "$T/base_ref"
check "6 no base_ref: fails closed"         "false" "$(mg_run)"
check "6 no base_ref: reason"               "1"     "$(grep -c 'not authored by the automation' "$T/merge_block_reason")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE

# ---------------------------------------------------------------------------
# review-merge ci_fix_gate — the counters run again before a CI fix is pushed
# (ADR 0013 D3), real git
# ---------------------------------------------------------------------------
echo ""
echo "review-merge ci_fix_gate hygiene (real git)"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/cfg"; mkdir -p "$T/review"
extract_from "$SHARED" ci_fix_gate | sed "s#/tmp/fabro#$T#g" > "$T/ci_fix_gate.sh"
extract_from "$SHARED" hygiene | sed "s#/tmp/fabro#$T#g" > "$T/hygiene_node.sh"
if ! sh -n "$T/ci_fix_gate.sh" 2>"$T/ci_fix_gate.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL ci_fix_gate is not valid POSIX sh\n'
fi
mkreadok ok1 ci_fix.md changed_files.txt diff.patch diffstat.txt commits.txt pr.json

# An upstream whose `feat` branch is the PR head, cloned the way the sandbox is.
# `hygiene` runs once first, as it does in the graph, to write hygiene.sh.
cf_repo() {
    rm -rf "$T/up" "$T/wt"; mkdir -p "$T/up/tests"
    git -C "$T/up" init -q -b main .
    git -C "$T/up" config user.email t@t
    git -C "$T/up" config user.name t
    git -C "$T/up" config receive.denyCurrentBranch ignore
    printf '%s\n' 'def test_a():' '    assert 1 == 1 + 0' > "$T/up/tests/test_a.py"
    echo x > "$T/up/a.py"
    git -C "$T/up" add -A; git -C "$T/up" commit -qm init
    git -C "$T/up" checkout -qb feat; echo y >> "$T/up/a.py"; git -C "$T/up" commit -qam work
    git -C "$T/up" checkout -q main
    git clone -q "$T/up" "$T/wt"
    git -C "$T/wt" config user.email t@t
    git -C "$T/wt" config user.name t
    git -C "$T/wt" checkout -q -b feat origin/feat
    echo main > "$T/base_ref"; echo feat > "$T/head_ref"; echo 5 > "$T/pr_number"
    printf '%s\n' a.py tests/test_a.py > "$T/review/merge_base_files.txt"
    echo '{"outcome":"fixed","scope":"ci_only","summary":"s","_io":{"visit":"ok1","binary":"0.1.0","stage":"review_merge.ci_fix_t1"}}' > "$T/review/ci_fix_result.json"
    rm -f "$T/merge_block_reason"
    ( cd "$T/wt" && sh "$T/hygiene_node.sh" >/dev/null 2>&1 )
}
cf_run() { OUT=$( cd "$T/wt" && sh "$T/ci_fix_gate.sh" 2>&1 ); jq -r '.context_updates.ci_fix_ok' <<<"$(lastjson "$OUT")"; }

# 1. A CI fix that touches only code is pushed as before.
cf_repo
echo z >> "$T/wt/a.py"; git -C "$T/wt" commit -qam fix
check "ci fix clean: pushed"                "true"  "$(cf_run)"

# 2. A CI fix that skips the failing test is refused, not pushed, and says why.
cf_repo
printf '%s\n' 'import pytest' '@pytest.mark.skip' 'def test_a():' '    assert 1 == 1 + 0' > "$T/wt/tests/test_a.py"
git -C "$T/wt" commit -qam fix
check "ci fix skip: refused"                "false" "$(cf_run)"
check "ci fix skip: reason"                 "1"     "$(grep -c 'skips_added=1' "$T/merge_block_reason")"
check "ci fix skip: not pushed"             "2"     "$(git -C "$T/up" rev-list --count feat)"

PATH="$SAVED_PATH"

# ---------------------------------------------------------------------------
# review-merge refute_prep — the Refuter's inputs (ADR 0013 D4), real git
# ---------------------------------------------------------------------------
echo ""
echo "review-merge refute_prep (real git)"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/rprep"; mkdir -p "$T/bin" "$T/review"
extract_from "$SHARED" refute_prep | sed "s#/tmp/fabro#$T#g" > "$T/refute_prep.sh"
if ! sh -n "$T/refute_prep.sh" 2>"$T/refute_prep.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL refute_prep is not valid POSIX sh\n'
fi
# Issue 99 cannot be read; any other number returns a body.
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
[ "$3" = 99 ] && exit 1
printf '{"number":%s,"title":"T","body":"- [ ] it works"}' "$3"
STUB
chmod +x "$T/bin/gh"
rm -rf "$T/up" "$T/wt"; mkdir -p "$T/up"
git -C "$T/up" init -q -b main .
git -C "$T/up" config user.email t@t
git -C "$T/up" config user.name t
echo a > "$T/up/a.txt"; git -C "$T/up" add -A; git -C "$T/up" commit -qm init
git clone -q "$T/up" "$T/wt"
git -C "$T/wt" config user.email t@t
git -C "$T/wt" config user.name t
git -C "$T/wt" checkout -qb fabro/run/01TEST
echo small >> "$T/wt/a.txt"
mkdir -p "$T/wt/d e"; echo sp > "$T/wt/d e/f g.txt"
awk 'BEGIN { for (i = 0; i < 40000; i++) print "line number " i }' > "$T/wt/big.txt"
git -C "$T/wt" add -A; git -C "$T/wt" commit -qm change
echo main > "$T/base_ref"
echo '[{"number":5},{"number":99}]' > "$T/linked_issues.json"
echo stale > "$T/review/refute_verdict"
OUT=$( cd "$T/wt" && PATH="$T/bin:$SAVED_PATH" sh "$T/refute_prep.sh" 2>&1 ); RC=$?

# 1. Issue text is fetched, and an unreadable issue is recorded, not fatal.
check "refute_prep: exits 0"                "0"                 "$RC"
check "refute_prep: both issues listed"     "2"                 "$(jq length "$T/review/refute_issues.json")"
check "refute_prep: body fetched"           "- [ ] it works"    "$(jq -r '.[0].body' "$T/review/refute_issues.json")"
check "refute_prep: unreadable recorded"    "could not be read" "$(jq -r '.[1].error' "$T/review/refute_issues.json")"

# 2. A stale verdict from an earlier pass is cleared before the agent runs.
check "refute_prep: stale verdict cleared"  "absent" "$([ -f "$T/review/refute_verdict" ] && echo present || echo absent)"

# 3. The cap: the largest file is listed, not diffed; small files and a path with
#    spaces are diffed.
check "refute_prep: big file omitted"       "1" "$(grep -c '^big.txt (40000 lines changed)' "$T/review/refute_omitted.txt")"
check "refute_prep: small file diffed"      "1" "$(grep -c '^+small' "$T/review/refute_diff.patch")"
check "refute_prep: spaced path diffed"     "1" "$(grep -c '^+sp$' "$T/review/refute_diff.patch")"

# 4. No linked issue is an empty list, which the prompt handles.
echo '[]' > "$T/linked_issues.json"
( cd "$T/wt" && PATH="$T/bin:$SAVED_PATH" sh "$T/refute_prep.sh" >/dev/null 2>&1 )
check "refute_prep: no issues is []"        "[]" "$(jq -c . "$T/review/refute_issues.json")"

PATH="$SAVED_PATH"

# ---------------------------------------------------------------------------
# review-merge refute_gate — receipt check (ADR 0016 C7), one repair turn, computed
# verdict (ADR 0013 D5). Fixtures are generated by fabro-io `submit` with a fixed
# visit (ops/fabro-io/tests/fixtures/refute-{pass,fail}.json) and committed.
# ---------------------------------------------------------------------------
echo ""
echo "review-merge refute_gate"
T="$WORK/rgate"; rm -rf "$T"; mkdir -p "$T/review" "$T/.io"
FIX_DIR="$(cd "$(dirname "$0")" && pwd)/fabro-io/tests/fixtures"
extract_from "$SHARED" refute_gate | sed "s#/tmp/fabro#$T#g" > "$T/refute_gate.sh"
if ! sh -n "$T/refute_gate.sh" 2>"$T/refute_gate.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL refute_gate is not valid POSIX sh\n'
fi
FV=fac3fac3fac3fac3fac3fac3fac3fac3
rg_stage() { printf '%s' "{\"workflow\":\"backlog\",\"node\":\"review_merge.refute\",\"visit\":\"$FV\",\"started\":\"2026-09-27T00:00:00Z\"}" > "$T/.io/stage.json"; }
rg_verdict() { OUT=$(sh "$T/refute_gate.sh" 2>&1); jq -r '.context_updates.refute_verdict' <<<"$(lastjson "$OUT")"; }

# 1. A valid receipt, every criterion met, no defects: pass.
rg_stage; cp "$FIX_DIR/refute-pass.json" "$T/review/refute.json"
echo 0 > "$T/refute_attempts"
check "refute_gate: valid receipt all met is pass" "pass" "$(rg_verdict)"
check "refute_gate: verdict file"                 "pass" "$(cat "$T/review/refute_verdict")"

# 2. A valid receipt, one not_met: fail, and the reason names it and the defect.
rg_stage; cp "$FIX_DIR/refute-fail.json" "$T/review/refute.json"
echo 0 > "$T/refute_attempts"
check "refute_gate: not_met is fail"               "fail" "$(rg_verdict)"
check "refute_gate: reason names the criterion"     "1"    "$(grep -c 'rate limited' "$T/review/refute_reason.txt")"
check "refute_gate: reason names the defect"        "1"    "$(grep -c 'defect at auth.py:9' "$T/review/refute_reason.txt")"

# 3. Hand-written content (no _io) is refused with the submit-tool message: first a
#    repair turn (exit 1, message mentions the submit tool), then `invalid`.
rg_stage
printf '%s' '{"summary":"s","criteria":[{"criterion":"a","status":"met","evidence":"x"}],"defects":[]}' > "$T/review/refute.json"
echo 0 > "$T/refute_attempts"
OUT=$(sh "$T/refute_gate.sh" 2>&1); RC=$?
check "refute_gate: no receipt gets a repair"       "1"    "$RC"
check "refute_gate: repair says submit tool"        "1"    "$(grep -c 'submit tool' <<<"$OUT")"
sh "$T/refute_gate.sh" >/dev/null 2>&1
check "refute_gate: second no-receipt invalid"      "invalid" "$(cat "$T/review/refute_verdict")"

# 4. A valid receipt whose visit does not match stage.json (a stale file) is a repair.
rg_stage; cp "$FIX_DIR/refute-pass.json" "$T/review/refute.json"
echo 0 > "$T/refute_attempts"
printf '%s' '{"workflow":"backlog","node":"review_merge.refute","visit":"ffffffffffffffffffffffffffffffff"}' > "$T/.io/stage.json"
sh "$T/refute_gate.sh" >/dev/null 2>&1; RC=$?
check "refute_gate: stale receipt gets a repair"    "1"    "$RC"

# 5. stage.json missing (visit empty) is a repair, never pass.
rm -f "$T/.io/stage.json"; cp "$FIX_DIR/refute-pass.json" "$T/review/refute.json"
echo 0 > "$T/refute_attempts"
OUT=$(sh "$T/refute_gate.sh" 2>&1); RC=$?
check "refute_gate: missing stage is a repair"      "1"    "$RC"
check "refute_gate: missing stage is never pass"    "0"    "$(grep -c '"refute_verdict":"pass"' <<<"$OUT")"

# ---------------------------------------------------------------------------
# review-merge refute guard — Sealed paths block the refuter's reads (ADR 0016
# D8). Needs fabro-io on PATH; SKIPs otherwise (present in profile images).
# ---------------------------------------------------------------------------
echo ""
echo "review-merge refute guard"
if ! command -v fabro-io >/dev/null 2>&1; then
    echo "  SKIP refute guard (no fabro-io on PATH; present in profile images)"
else
    PATH="$ORIG_PATH"
    T="$WORK/rguard"; rm -rf "$T"; mkdir -p "$T/.io"
    export FABRO_IO_ROOT="$T"
    export FABRO_IO_MANIFEST='{"version":1,"min_binary":"0.0.0","stages":{"review_merge.refute":{"inputs":[],"sealed":["/tmp/fabro/review/standards.json","/tmp/fabro/review/spec.json","/tmp/fabro/review/fix_result.json","/tmp/fabro/review/ci_fix_result.json","/tmp/fabro/feedback/**"]}}}'
    # refute node: a read of a Sealed path is blocked (exit 2) with a reason.
    printf '%s' '{"workflow":"backlog","node":"review_merge.refute","visit":"fac3fac3fac3fac3fac3fac3fac3fac3"}' > "$T/.io/stage.json"
    export FABRO_HOOK_CONTEXT='{"tool_input":{"path":"/tmp/fabro/review/standards.json"}}'
    OUT=$(fabro-io guard 2>&1); RC=$?
    check "refute guard: sealed read blocked"        "2" "$RC"
    check "refute guard: block reason"               "1" "$(grep -c 'is sealed for this stage' <<<"$OUT")"
    # a sealed glob also blocks.
    export FABRO_HOOK_CONTEXT='{"tool_input":{"path":"/tmp/fabro/feedback/rework.md"}}'
    fabro-io guard >/dev/null 2>&1; RC=$?
    check "refute guard: sealed glob blocked"        "2" "$RC"
    # a node with no sealed entry (spec) proceeds.
    printf '%s' '{"workflow":"backlog","node":"review_merge.spec","visit":"fac3fac3fac3fac3fac3fac3fac3fac3"}' > "$T/.io/stage.json"
    export FABRO_HOOK_CONTEXT='{"tool_input":{"path":"/tmp/fabro/review/standards.json"}}'
    fabro-io guard >/dev/null 2>&1; RC=$?
    check "refute guard: unsealed node proceeds"     "0" "$RC"
    unset FABRO_IO_ROOT FABRO_IO_MANIFEST FABRO_HOOK_CONTEXT
    rm -rf "$T"
fi

# ---------------------------------------------------------------------------
# improve end to end — the REAL fabro-io `submit`, under backlog's GENERATED
# manifest, then the REAL improve_gate. The flat `ready` shape (task_title,
# task_body, ...) must survive the schema and the receipt, and the gate must build
# current_task.json from it. Needs fabro-io on PATH; SKIPs otherwise.
# ---------------------------------------------------------------------------
echo ""
echo "improve end to end"
if ! command -v fabro-io >/dev/null 2>&1; then
    echo "  SKIP improve end to end (no fabro-io on PATH; present in profile images)"
else
    PATH="$ORIG_PATH"
    T="$WORK/ie2e"; rm -rf "$T"; mkdir -p "$T"
    export FABRO_IO_ROOT="$T"
    MANIFEST_TOML="$(cd "$(dirname "$0")/.." && pwd)/.fabro/workflows/backlog/workflow.toml"
    # The generated line is `FABRO_IO_MANIFEST = '''<json>'''`: keep from the first `{`
    # to the last `}`, then point the absolute sandbox paths at the scratch root.
    FABRO_IO_MANIFEST=$(grep '^FABRO_IO_MANIFEST' "$MANIFEST_TOML" | sed -e 's/^[^{]*//' -e 's/[^}]*$//' | sed "s#/tmp/fabro#$T#g")
    export FABRO_IO_MANIFEST
    check "improve e2e: manifest extracted" "1" "$([ -n "$FABRO_IO_MANIFEST" ] && echo 1 || echo 0)"
    extract improve_gate | sed "s#/tmp/fabro#$T#g" > "$T/improve_gate.sh"
    printf '%s' '{"id":"big","title":"Big","body":"b","files":["f.py"],"covers":["c1","c2"],"priority":"high","source":"decompose"}' > "$T/current_task.json"
    printf '%s' '[{"id":"big","title":"Big","body":"b","files":[],"covers":["c1"],"source":"decompose"}]' > "$T/tasks.json"
    printf '%s' '{"number":1,"title":"t","body":"b","comments":[],"labels":[]}' > "$T/issue.json"
    echo 1 > "$T/task_index"
    FABRO_WORKFLOW=backlog FABRO_NODE_ID=improve fabro-io stage >/dev/null 2>&1
    fabro-io inputs >/dev/null 2>&1
    # the nested shape is refused by submit, with the schema's own message
    printf '%s' '{"disposition":"ready","reason":"r","task":{"id":"big","title":"S","body":"nb"}}' > "$T/nested.json"
    fabro-io submit --file "$T/nested.json" >/dev/null 2>&1; RC=$?
    check "improve e2e: nested task refused by submit" "4" "$RC"
    # the flat shape is written with a receipt, and the gate builds the task from it
    printf '%s' '{"disposition":"ready","reason":"r","task_title":"Sharpened","task_body":"new body","task_covers":["c1"]}' > "$T/flat.json"
    fabro-io submit --file "$T/flat.json" >/dev/null 2>&1; RC=$?
    check "improve e2e: flat ready accepted by submit" "0" "$RC"
    OUT=$( (cd "$T" && sh "$T/improve_gate.sh") 2>/dev/null); RC=$?
    check "improve e2e: gate exits 0"        "0"         "$RC"
    check "improve e2e: gate routes ready"   "ready"     "$(jq -r '.context_updates.task_disposition' <<<"$(lastjson "$OUT")")"
    check "improve e2e: title"               "Sharpened" "$(jq -r .title "$T/current_task.json")"
    check "improve e2e: body"                "new body"  "$(jq -r .body "$T/current_task.json")"
    check "improve e2e: id kept"             "big"       "$(jq -r .id "$T/current_task.json")"
    check "improve e2e: covers overridden"   "c1"        "$(jq -r '.covers|join(" ")' "$T/current_task.json")"
    check "improve e2e: files defaulted"     "f.py"      "$(jq -r '.files|join(" ")' "$T/current_task.json")"
    check "improve e2e: priority defaulted"  "high"      "$(jq -r .priority "$T/current_task.json")"
    unset FABRO_IO_ROOT FABRO_IO_MANIFEST
    rm -rf "$T"
fi

# ---------------------------------------------------------------------------
# reviewer gates — C7 receipt check on the migrated reviewer stages (ADR 0016
# task 08): backlog review/standards/spec/quality and review-merge standards/spec.
# A real submit stamps `_io.visit`; hand-written or stale files are refused.
# ---------------------------------------------------------------------------
echo ""
echo "reviewer gates"
T="$WORK/rgates"; rm -rf "$T"; mkdir -p "$T/review" "$T/extra" "$T/feedback" "$T/.io"
extract_from "$GRAPH"  review_gate    | sed "s#/tmp/fabro#$T#g" > "$T/review.sh"
extract_from "$GRAPH"  standards_gate | sed "s#/tmp/fabro#$T#g" > "$T/standards.sh"
extract_from "$GRAPH"  spec_gate      | sed "s#/tmp/fabro#$T#g" > "$T/spec.sh"
extract_from "$GRAPH"  quality_gate   | sed "s#/tmp/fabro#$T#g" > "$T/quality.sh"
extract_from "$SHARED" standards_gate | sed "s#/tmp/fabro#$T#g" > "$T/phase_standards.sh"
extract_from "$SHARED" spec_gate      | sed "s#/tmp/fabro#$T#g" > "$T/phase_spec.sh"
for s in review standards spec quality phase_standards phase_spec; do
    sh -n "$T/$s.sh" >/dev/null 2>"$T/$s.syntax" || {
        FAIL=$((FAIL+1)); printf '  FAIL %s gate is not valid POSIX sh\n' "$s"; sed 's/^/       /' "$T/$s.syntax"; }
done
FV=fac3fac3fac3fac3fac3fac3fac3fac3
vg_stage() { printf '%s' "{\"workflow\":\"backlog\",\"node\":\"review\",\"visit\":\"$FV\",\"started\":\"2026-09-27T00:00:00Z\"}" > "$T/.io/stage.json"; }
vg_stamp() { jq --arg v "$FV" '._io.visit = $v' > "$1"; }

# --- backlog review ---------------------------------------------------------
vg_stage; printf '%s' '{"decision":"approved","summary":"ok","findings":[]}' | vg_stamp "$T/review/verdict.json"
OUT=$(sh "$T/review.sh" 2>&1)
check "review: approved routes approved" "approved" "$(jq -r '.context_updates.review_decision' <<<"$(lastjson "$OUT")")"
# changes_requested with a blocking finding writes rework.md and routes
printf '%s' '{"decision":"changes_requested","summary":"fix X","findings":[{"severity":"blocking","message":"a.ts:1 must change"}]}' | vg_stamp "$T/review/verdict.json"
OUT=$(sh "$T/review.sh" 2>&1)
check "review: changes_requested routes"   "changes_requested" "$(jq -r '.context_updates.review_decision' <<<"$(lastjson "$OUT")")"
check "review: rework.md has the finding"  "1" "$(grep -c 'a.ts:1 must change' "$T/feedback/rework.md")"
# changes_requested with no blocking finding is a repair turn
printf '%s' '{"decision":"changes_requested","summary":"x","findings":[{"severity":"nit","message":"n"}]}' | vg_stamp "$T/review/verdict.json"
echo 0 > "$T/review_attempts"
sh "$T/review.sh" >/dev/null 2>&1; RC=$?
check "review: changes w/o blocking is repair" "1" "$RC"
# hand-written (unstamped) is refused, then invalid after two tries
printf '%s' '{"decision":"approved","summary":"s","findings":[]}' > "$T/review/verdict.json"
echo 0 > "$T/review_attempts"
OUT=$(sh "$T/review.sh" 2>&1); RC=$?
check "review: unstamped gets a repair"  "1" "$RC"
check "review: repair says submit tool"  "1" "$(grep -c 'submit tool' <<<"$OUT")"
OUT=$(sh "$T/review.sh" 2>&1)
check "review: second unstamped is invalid" "invalid" "$(jq -r '.context_updates.review_decision' <<<"$(lastjson "$OUT")")"

# --- the five decision-enum gates -------------------------------------------
rg_case() { # $1 script $2 key $3 decision $4 output path
  local s="$1" dec="$3"
  vg_stage; printf '%s' "{\"decision\":\"$dec\",\"summary\":\"s\",\"findings\":[]}" | vg_stamp "$4"
  OUT=$(sh "$T/$s.sh" 2>&1)
  check "$s: $dec routes" "$dec" "$(jq -r ".context_updates.$2" <<<"$(lastjson "$OUT")")"
}
rg_case standards standards_status approve "$T/extra/standards.json"
rg_case spec spec_status findings "$T/extra/spec.json"
rg_case quality quality_status needs_human_review "$T/extra/quality.json"
rg_case phase_standards standards_status approve "$T/review/standards.json"
rg_case phase_spec spec_status findings "$T/review/spec.json"
# unstamped extra verdict is a repair, then invalid
printf '%s' '{"decision":"approve","summary":"s","findings":[]}' > "$T/extra/standards.json"
echo 0 > "$T/standards_attempts"
OUT=$(sh "$T/standards.sh" 2>&1); RC=$?
check "standards: unstamped is repair"   "1" "$RC"
check "standards: repair says submit"     "1" "$(grep -c 'submit tool' <<<"$OUT")"
OUT=$(sh "$T/standards.sh" 2>&1)
check "standards: second unstamped invalid" "invalid" "$(jq -r '.context_updates.standards_status' <<<"$(lastjson "$OUT")")"
# stale receipt (visit mismatch) is a repair, never the decision
vg_stage; printf '%s' '{"decision":"approve","summary":"s","findings":[]}' | vg_stamp "$T/extra/standards.json"
printf '%s' '{"workflow":"backlog","node":"review","visit":"ffffffffffffffffffffffffffffffff"}' > "$T/.io/stage.json"
echo 0 > "$T/standards_attempts"
sh "$T/standards.sh" >/dev/null 2>&1; RC=$?
check "standards: stale receipt is repair" "1" "$RC"
check "standards: stale never routes approve" "0" "$(sh "$T/standards.sh" 2>&1 | grep -c '"standards_status":"approve"')"


# ---------------------------------------------------------------------------
# implementer-gate read receipt — the reads-only form of C7 (ADR 0016 task 09).
# A stale served visit (or a missing required input) must not accept the stage's
# result: the repair-turn gates exit 1 on the first stale read, and the
# failure-routing gates (resolve_merge/ci_fix/rebase) route to their failure
# path instead (exit 0, never their success routing).
# ---------------------------------------------------------------------------
echo ""
echo "implementer gate read receipt"
T="$WORK/rcneg"; mkdir -p "$T"
rorect_run() { # $1 graph(G/A/P/S/T) $2 node
    local gr
    case "$1" in
        G) gr="$GRAPH";; A) gr="$ARCH";; P) gr="$PR";; S) gr="$SHARED";; T) gr="$SHARED_TRIAGE";;
    esac
    rm -rf "$T"; mkdir -p "$T/.io"
    extract_from "$gr" "$2" | sed "s#/tmp/fabro#$T#g" > "$T/g.sh" || return 99
    printf '%s' '{"visit":"fac3fac3fac3fac3fac3fac3fac3fac3"}' > "$T/.io/stage.json"
    printf '%s' '{"visit":"ffffffffffffffffffffffffffffffff","inputs":{}}' > "$T/.io/served.json"
    (cd "$T" && sh "$T/g.sh") >/dev/null 2>&1; echo $?
}
check "decompose stale read is repair"   "1" "$(rorect_run G decompose_gate)"
check "improve stale read is repair"     "1" "$(rorect_run G improve_gate)"
check "extra stale read is repair"       "1" "$(rorect_run G extra_gate)"
check "scan stale read is repair"        "1" "$(rorect_run A scan_gate)"
check "review_fix stale read is repair"  "1" "$(rorect_run S fix_gate)"
check "triage_gate stale read is repair" "1" "$(rorect_run T triage_gate)"
# The repair hint after a receipt miss is C7's submit-tool message in every gate
# with a repair turn; a stamped but invalid contract keeps the gate's own message.
rorect_msg() { # $1 graph $2 node -> the gate's output under a stale receipt
    local gr
    case "$1" in
        G) gr="$GRAPH";; A) gr="$ARCH";; S) gr="$SHARED";; T) gr="$SHARED_TRIAGE";;
    esac
    rm -rf "$T"; mkdir -p "$T/.io" "$T/arch" "$T/review" "$T/extra"
    extract_from "$gr" "$2" | sed "s#/tmp/fabro#$T#g" > "$T/g.sh"
    printf '%s' '{"visit":"fac3fac3fac3fac3fac3fac3fac3fac3"}' > "$T/.io/stage.json"
    (cd "$T" && sh "$T/g.sh") 2>&1
}
for g in G:decompose_gate G:improve_gate G:extra_gate A:scan_gate S:fix_gate T:improve_gate T:triage_gate; do
    check "${g#*:} receipt miss says submit tool" "1" "$(rorect_msg "${g%%:*}" "${g#*:}" | grep -c 'submit tool')"
done
rm -rf "$T"; mkdir -p "$T/.io"
extract_from "$GRAPH" decompose_gate | sed "s#/tmp/fabro#$T#g" > "$T/g.sh"
printf '%s' '{"visit":"fac3fac3fac3fac3fac3fac3fac3fac3"}' > "$T/.io/stage.json"
printf '%s' '{"status":"bogus","_io":{"visit":"fac3fac3fac3fac3fac3fac3fac3fac3"}}' > "$T/decomposition.json"
OUT=$( (cd "$T" && sh "$T/g.sh") 2>&1)
check "decompose: stamped but invalid keeps its hint" "1" "$(grep -c 'rewrite it exactly per the contract' <<<"$OUT")"
check "decompose: stamped but invalid not submit hint" "0" "$(grep -c 'submit tool' <<<"$OUT")"

# ---------------------------------------------------------------------------
# receipt routing on the failure-routing gates (real git). resolve_merge_gate,
# rebase_gate and ci_fix_gate have no repair edge, so a stale or incomplete
# receipt must ROUTE to the failure path; an exit code alone proves nothing,
# because the success path exits 0 as well. rebase_gate's counter is the tier
# ladder: a receipt miss on tier 1 must reach tier 2 (rebase_attempts=1), not
# exit 1, which would take the unconditional edge to entry_failed.
# ---------------------------------------------------------------------------
echo ""
echo "receipt routing (real git)"
PATH="$ORIG_PATH"
T="$WORK/rcroute"
rc_repo() {
    rm -rf "$T"; mkdir -p "$T/up" "$T/.io" "$T/review"
    git -C "$T/up" init -q -b main .
    git -C "$T/up" config user.email t@t
    git -C "$T/up" config user.name t
    echo a > "$T/up/a.txt"; git -C "$T/up" add -A; git -C "$T/up" commit -qm init
    git clone -q "$T/up" "$T/wt"
    echo main > "$T/base_ref"
    git -C "$T/wt" rev-parse HEAD > "$T/pre_rebase_sha"
    git -C "$T/wt" rev-parse HEAD > "$T/pre_merge_sha"
    printf '%s' '{"visit":"fac3fac3fac3fac3fac3fac3fac3fac3"}' > "$T/.io/stage.json"
}
rc_gate() { # $1 graph $2 node -> sets J (the routing JSON) and RC
    extract_from "$1" "$2" | sed "s#/tmp/fabro#$T#g" > "$T/g.sh"
    OUT=$( cd "$T/wt" && sh "$T/g.sh" 2>&1 ); RC=$?
    J=$(lastjson "$OUT")
}
rc_served() { # $1 visit $2 parts of issue.json $3 served list of issue.json
    jq -n --arg v "$1" --argjson p "$2" --argjson s "$3" '{visit:$v, inputs:{
      "pre_merge_sha":{status:"ok",parts:1,served:[1]},
      "current_task.json":{status:"ok",parts:1,served:[1]},
      "issue.json":{status:"ok",parts:$p,served:$s}}}' > "$T/.io/served.json"
}

rc_repo; rc_served fac3fac3fac3fac3fac3fac3fac3fac3 1 '[1]'
rc_gate "$GRAPH" resolve_merge_gate
check "resolve_merge: complete read, clean merge" "true"  "$(jq -r .context_updates.merge_ok <<<"$J")"
rc_repo; rc_served ffffffffffffffffffffffffffffffff 1 '[1]'
rc_gate "$GRAPH" resolve_merge_gate
check "resolve_merge: stale served visit fails"   "false" "$(jq -r .context_updates.merge_ok <<<"$J")"
check "resolve_merge: stale is attempt 1"         "1"     "$(jq -r .context_updates.merge_attempts <<<"$J")"
rc_repo; rc_served fac3fac3fac3fac3fac3fac3fac3fac3 2 '[1]'
rc_gate "$GRAPH" resolve_merge_gate
check "resolve_merge: incomplete input fails"     "false" "$(jq -r .context_updates.merge_ok <<<"$J")"

rc_repo; echo '{"_io":{"visit":"fac3fac3fac3fac3fac3fac3fac3fac3"}}' > "$T/rebase_result.json"
rc_gate "$PR" rebase_gate
check "rebase: valid receipt, clean rebase"       "true"  "$(jq -r .context_updates.rebase_ok <<<"$J")"
rc_repo; echo '{"_io":{"visit":"ffffffffffffffffffffffffffffffff"}}' > "$T/rebase_result.json"
rc_gate "$PR" rebase_gate
check "rebase: stale receipt exits 0 (routes)"    "0"     "$RC"
check "rebase: stale receipt is not ok"           "false" "$(jq -r .context_updates.rebase_ok <<<"$J")"
check "rebase: stale receipt escalates to tier 2" "1"     "$(jq -r .context_updates.rebase_attempts <<<"$J")"
check "rebase: reason names the submit tool"      "1"     "$(grep -c 'submit tool' "$T/needs_human_reason")"
echo 1 > "$T/rebase_attempts"; rm -f "$T/rebase_result.json"
rc_gate "$PR" rebase_gate
check "rebase: tier-2 receipt miss ends the ladder" "2"   "$(jq -r .context_updates.rebase_attempts <<<"$J")"

rc_repo
rc_gate "$SHARED" ci_fix_gate
check "ci_fix: no receipt routes not ok"          "false" "$(jq -r .context_updates.ci_fix_ok <<<"$J")"

# ---------------------------------------------------------------------------
# review-merge render — the Refuter and hygiene rows and sections (ADR 0013 D5)
# ---------------------------------------------------------------------------
echo ""
echo "review-merge render"
PATH="$ORIG_PATH"
T="$WORK/render"; mkdir -p "$T/review"
extract_from "$SHARED" rm_setup | sed "s#/tmp/fabro#$T#g" > "$T/rm_setup.sh"
echo 5 > "$T/pr_number"
for f in base_ref head_ref run_base_sha; do echo x > "$T/$f"; done
echo '{"url":"https://github.com/o/r/pull/5"}' > "$T/pr.json"
echo '[]' > "$T/linked_issues.json"
FABRO_AUTO_MERGE=1 sh "$T/rm_setup.sh" >/dev/null 2>&1
echo '{"summary":"s","criteria":[{"criterion":"logout works","status":"not_met","evidence":"no handler"}],"defects":[{"file":"a.py","line":9,"scenario":"crashes on empty"}]}' > "$T/review/refute.json"
echo fail > "$T/review/refute_verdict"
echo '{"tamper":{"skips_added":1},"erosion":{"lint_disable":0},"erosion_mode":"report","blocking":["skips_added"],"samples":[{"counter":"skips_added","file":"t.py","line":2,"text":"@pytest.mark.skip"}]}' > "$T/review/hygiene.json"
C=$( cd "$T" && sh "$T/render_comment.sh" blocked 2>&1 )
check "render: refuter row"                 "1" "$(grep -c '^| Refuter | fail |$' <<<"$C")"
check "render: refuter defect"              "1" "$(grep -c '^- \*\*defect\*\* - a.py:9 - crashes on empty$' <<<"$C")"
check "render: hygiene sample"              "1" "$(grep -c 't.py:2. skips_added' <<<"$C")"
echo '{"version":1,"config":"default","error":"git diff against the base failed","tamper":{},"erosion":{},"blocking":["hygiene_error"],"samples":[]}' > "$T/review/hygiene.json"
C=$( cd "$T" && sh "$T/render_comment.sh" blocked 2>&1 )
check "render: hygiene error shown"         "1" "$(grep -c 'could not be computed: git diff against the base failed' <<<"$C")"
check "render: hygiene error not 'none'"    "0" "$(grep -c 'No counter fired' <<<"$C")"
echo '{"config":"invalid","tamper":{"skips_added":0},"erosion":{},"erosion_mode":"report","blocking":["config_invalid"],"samples":[]}' > "$T/review/hygiene.json"
C=$( cd "$T" && sh "$T/render_comment.sh" blocked 2>&1 )
check "render: invalid config explained"    "1" "$(grep -c 'hygiene.json is not valid' <<<"$C")"

# ---------------------------------------------------------------------------
# fabro-code — the index wrapper (docs/code-context C1/C2/C3/C4).
# ---------------------------------------------------------------------------
# This section needs a REAL codegraph binary, which is present inside every
# profile image (and nowhere on this Mac), so it runs here only when `codegraph`
# is on PATH and prints SKIP otherwise. It exercises the exact-resolve rules,
# the D1 footer, the freshness sync, the tests probe, the map renderer and the
# .git/info/exclude guard against a real index of a real-git fixture -- the
# only way the wrapper's claims about codegraph's behaviour are actually tested.
#
# `opens source` layout:
#   mod_a.py      def get_thing()  +  class Config: FLAG
#   mod_b.py      calls get_thing() and reads Config.FLAG
#   file1.py / file2.py   two different functions both named helper
#   tests/test_mod_a.py and test_root.py   both import mod_a (`tests` finds both)
#   mod_u.py      a multi-line signature holding `|` (the map rendered it as garbage)
#   mod_many.py   25 callers of get_thing (codegraph's callers stopped at 20)
#   app.ts        a class with a `get` method, and a call to `get_thing` that must
#                 not count as a caller of the Python function (cross-language)
#   hooks.ts      `export const useThing = () => ...` (TS declares most functions
#                 this way) and a local const inside a function
#
# Rebase the wrapper's /tmp/fabro state onto $T so the section is hermetic.
# ---------------------------------------------------------------------------
echo ""
echo "git-guard hook wrapper (docs/coder-tweaks 05)"
# Any non-zero hook exit but 2 BLOCKS in fabro (parse_decision), so a hook whose program
# is missing or crashing would fail every shell call it matches. The wrapper maps every
# code but 2 to 0. It is extracted from each workflow.toml and run, not retyped.
for W in backlog pr-review issue-triage arch-review; do
    TOML="$REPO_ROOT/.fabro/workflows/$W/workflow.toml"
    HOOK=$(python3.11 -c 'import sys,tomllib;d=tomllib.load(open(sys.argv[1],"rb"));print(next(h["script"] for h in d["run"]["hooks"] if h["id"]=="git-guard"))' "$TOML" 2>/dev/null \
        || sed -n 's/^script = "\(fabro-io git-guard[^"]*\)"$/\1/p' "$TOML" | head -1)
    check "$W: git-guard hook script found" "1" "$(grep -c '^fabro-io git-guard' <<<"$HOOK")"
    T="$WORK/gg-$W"; mkdir -p "$T/bin" "$T/empty"
    ln -sf "$(command -v sh)" "$T/empty/sh" 2>/dev/null
    printf '#!/bin/sh\necho "{\\"decision\\":\\"block\\"}"; exit 2\n' > "$T/bin/fabro-io"; chmod +x "$T/bin/fabro-io"
    OUT=$(PATH="$T/bin:$ORIG_PATH" sh -c "$HOOK"); RC=$?
    check "$W: git-guard exit 2 blocks"           "2" "$RC"
    check "$W: git-guard block decision on stdout" "1" "$(grep -c '"decision":"block"' <<<"$OUT")"
    for code in 0 1 64 127; do
        printf '#!/bin/sh\nexit %s\n' "$code" > "$T/bin/fabro-io"
        PATH="$T/bin:$ORIG_PATH" sh -c "$HOOK" >/dev/null 2>&1; RC=$?
        check "$W: git-guard exit $code proceeds" "0" "$RC"
    done
    rm -f "$T/bin/fabro-io"
    PATH="$T/bin:$ORIG_PATH" sh -c "$HOOK" >/dev/null 2>&1; RC=$?
    check "$W: no fabro-io on PATH proceeds"      "0" "$RC"
done

echo ""
echo "fabro-code"
if ! command -v codegraph >/dev/null 2>&1; then
    echo "  SKIP fabro-code (no codegraph on PATH; present in profile images)"
else
    SAVED_PATH="$PATH"
    PATH="$ORIG_PATH"      # this section builds a real git repo; earlier
                            # sections leave a `git` stub on PATH.
    FABRO_CODE=$(command -v fabro-code || echo "$REPO_ROOT/ops/profile-images/fabro-code")
    T="$WORK/fabrocode"; mkdir -p "$T"
    FC="$FABRO_CODE"
    export FABRO_CODE_STATE="$T/code_index"
    export FABRO_CODE_MAP="$T/repomap.md"

    # A fresh fixture repo, then a second bare clone is NOT needed: fabro-code
    # resolves and syncs against the working tree, so we build index in place.
    fc_repo() {
        rm -rf "$T/up" "$T/wt"
        mkdir -p "$T/up/frontend"
        git -C "$T/up" init -q -b main .
        git -C "$T/up" config user.email t@t
        git -C "$T/up" config user.name t
        cat > "$T/up/mod_a.py" <<'EOF'
class Config:
    FLAG = "on"

def get_thing():
    return Config.FLAG
EOF
        cat > "$T/up/mod_b.py" <<'EOF'
from mod_a import get_thing, Config

def call_it():
    return get_thing() and Config.FLAG
EOF
        cat > "$T/up/file1.py" <<'EOF'
def helper(a):
    return a + 1
EOF
        cat > "$T/up/file2.py" <<'EOF'
def helper(a, b):
    return a + b
EOF
        mkdir -p "$T/up/tests"
        cat > "$T/up/tests/test_mod_a.py" <<'EOF'
from mod_a import get_thing

def test_get_thing():
    assert get_thing() == "on"
EOF
        cat > "$T/up/test_root.py" <<'EOF'
import mod_a

def test_root():
    assert mod_a.get_thing() == "on"
EOF
        cat > "$T/up/mod_u.py" <<'EOF'
from mod_a import get_thing

def union_thing(
    a: int | None,
    b: str | None = None,
) -> int | None:
    return get_thing() and a
EOF
        {
            echo 'from mod_a import get_thing'
            i=1
            while [ "$i" -le 25 ]; do
                printf '\ndef many_%s():\n    return get_thing()\n' "$i"
                i=$((i + 1))
            done
        } > "$T/up/mod_many.py"
        cat > "$T/up/frontend/app.ts" <<'EOF'
export class App {
  get(key: string): string {
    return key;
  }
}

export function useIt(): unknown {
  return get_thing();
}
EOF
        cat > "$T/up/frontend/hooks.ts" <<'EOF'
export const useThing = (key: string): string => {
  const inner = key.trim();
  return inner;
};
EOF
        git -C "$T/up" add -A
        git -C "$T/up" commit -qm init
        git clone -q "$T/up" "$T/wt"
        git -C "$T/wt" config user.email t@t
        git -C "$T/wt" config user.name t
        # C4: the .git/info/exclude guard prep writes before it builds the index.
        printf '%s\n' '.codegraph/' >> "$T/wt/.git/info/exclude"
        rm -f "$FABRO_CODE_STATE" "$FABRO_CODE_MAP"
    }
    fc_run() { ( cd "$T/wt" && "$FC" "$@" 2>&1 ); }

    # 1. Exact resolution of a unique function.
    fc_repo
    fc_run index
    OUT=$(fc_run def get_thing); RC=$?
    check "fc: def get_thing one line"     "1" "$(wc -l <<<"$OUT" | tr -d ' ')"
    check "fc: def get_thing rc=0"          "0" "$RC"

    # 2. A class attribute is not a resolvable definition.
    OUT=$(fc_run def FLAG); RC=$?
    check "fc: def FLAG rc=1"               "1" "$RC"
    check "fc: def FLAG says fields not indexed" "1" \
        "$(grep -c 'fields and attributes are not indexed' <<<"$OUT")"

    # 3. An ambiguous name lists every path and chooses none.
    OUT=$(fc_run def helper); RC=$?
    check "fc: def helper ambiguous rc=1"   "1" "$RC"
    check "fc: def helper lists file1"      "1" "$(grep -c 'file1.py' <<<"$OUT")"
    check "fc: def helper lists file2"      "1" "$(grep -c 'file2.py' <<<"$OUT")"
    OUT=$(fc_run show helper); RC=$?
    check "fc: show helper ambiguous rc=1"  "1" "$RC"
    check "fc: show helper lists both"      "2" "$(grep -c 'file[12]\.py' <<<"$OUT")"

    # 4. No fuzzy matching: a near-miss name is not found.
    OUT=$(fc_run def get_thin); RC=$?
    check "fc: def get_thin rc=1 (no fuzzy)" "1" "$RC"

    # 5. callers lists the calling file and carries the D1 footer.
    OUT=$(fc_run callers get_thing); RC=$?
    check "fc: callers get_thing rc=0"      "0" "$RC"
    check "fc: callers lists mod_b"         "1" "$(grep -c 'mod_b.py' <<<"$OUT")"
    check "fc: callers has footer"          "1" "$(grep -c 'confirm with grep -rnw' <<<"$OUT")"

    # 6. Freshness: an edit with no explicit sync is covered (wrapper syncs).
    cat > "$T/wt/mod_c.py" <<'EOF'
from mod_a import get_thing

def also_call():
    return get_thing()
EOF
    OUT=$(fc_run callers get_thing)
    check "fc: callers sees uncommitted edit" "1" "$(grep -c 'mod_c.py' <<<"$OUT")"

    # 7. tests probe: a test that imports the module is found.
    OUT=$(fc_run tests mod_a.py); RC=$?
    check "fc: tests mod_a rc=0"            "0" "$RC"
    check "fc: tests lists test_mod_a"      "1" "$(grep -c 'test_mod_a.py' <<<"$OUT")"

    # 8. map: <=16,000 bytes, Repo map header, no generated rows.
    fc_run map
    check "fc: map wrote"                   "1" "$([ -f "$FABRO_CODE_MAP" ] && echo 1 || echo 0)"
    check "fc: map <=16000 bytes"           "1" "$([ "$(wc -c < "$FABRO_CODE_MAP" | tr -d ' ')" -le 16000 ] && echo 1 || echo 0)"
    check "fc: map header"                  "1" "$(grep -c '^# Repo map (' "$FABRO_CODE_MAP")"

    # 9. C4: the index stays out of every commit (`git add -A` adds nothing).
    ( cd "$T/wt" && git add -A >/dev/null 2>&1 && git status --porcelain ) > "$T/porcelain"
    check "fc: .codegraph not in porcelain" "0" "$(grep -c '\.codegraph' "$T/porcelain")"

    # 5b. No 20-row limit, and a same-name call from another language is not a caller.
    OUT=$(fc_run callers get_thing)
    check "fc: callers lists all 25 in mod_many" "25" "$(grep -c 'mod_many.py' <<<"$OUT")"
    check "fc: callers nothing cut"          "0" "$(grep -c 'lines cut' <<<"$OUT")"
    check "fc: callers skips the TS call"    "0" "$(grep -c 'app.ts' <<<"$OUT")"

    # 5c. The TS probes: a method named `get`, and a top-level const function
    #     resolve; a local const inside a function does not.
    OUT=$(fc_run def get); RC=$?
    check "fc: def get rc=0"                 "0" "$RC"
    check "fc: def get is the TS method"     "1" "$(grep -c 'frontend/app.ts' <<<"$OUT")"
    OUT=$(fc_run def useThing); RC=$?
    check "fc: def useThing (TS const) rc=0" "0" "$RC"
    check "fc: def useThing in hooks.ts"     "1" "$(grep -c 'frontend/hooks.ts' <<<"$OUT")"
    OUT=$(fc_run def inner); RC=$?
    check "fc: def inner (local) rc=1"       "1" "$RC"

    # 5d. show resolves exactly and prints the source and the call trail.
    OUT=$(fc_run show get_thing); RC=$?
    check "fc: show get_thing rc=0"          "0" "$RC"
    check "fc: show prints the source"       "1" "$(grep -c '^def get_thing' <<<"$OUT")"
    check "fc: show prints called-by"        "1" "$(grep -c '^## called by' <<<"$OUT")"
    OUT=$(fc_run show mod_a.py:5); RC=$?
    check "fc: show path:line rc=0"          "0" "$RC"
    check "fc: show path:line is get_thing"  "1" "$(grep -c '^# mod_a.py:4-5  function  get_thing' <<<"$OUT")"

    # 7b. tests: a root-level test file is found, and nothing outside the imports is.
    OUT=$(fc_run tests mod_a.py)
    check "fc: tests lists test_root"        "1" "$(grep -c '^test_root.py$' <<<"$OUT")"
    check "fc: tests lists no TS file"       "0" "$(grep -c 'frontend/' <<<"$OUT")"

    # 8b. The map keeps a multi-line signature holding `|` on one line, and leaves
    #     test files out of the ranking.
    fc_run map >/dev/null
    check "fc: map one line for union_thing" "1" "$(grep -c 'union_thing' "$FABRO_CODE_MAP")"
    check "fc: map keeps the | in the sig"   "1" "$(grep -cF 'union_thing(a: int | None, b: str | None = None) -> int | None' "$FABRO_CODE_MAP")"
    check "fc: map every line is well formed" "0" "$(grep -cvE '^(# Repo map [(]|## |- |$)' "$FABRO_CODE_MAP")"
    check "fc: map leaves test files out"    "0" "$(grep -cE '^## (tests/|test_)' "$FABRO_CODE_MAP")"
    check "fc: map keeps mod_a"              "1" "$(grep -c '^## mod_a.py$' "$FABRO_CODE_MAP")"

    # 9b. Usage errors are 64, never 2 ("no index").
    fc_run def >/dev/null; RC=$?
    check "fc: usage rc=64"                  "64" "$RC"

    # 9b2. search (docs/coder-tweaks/03, C3): the definition first, then each match
    #      under the narrowest symbol holding it, every match line `path:line:text`.
    OUT=$(fc_run search get_thing); RC=$?
    check "fc: search rc=0"                  "0" "$RC"
    check "fc: search definition first"      "definition: mod_a.py:4  function  get_thing" "$(head -n 1 <<<"$OUT" | cut -c1-48)"
    check "fc: search groups under caller"   "1" "$(grep -c '^mod_b.py:[0-9]*-[0-9]* function call_it$' <<<"$OUT")"
    check "fc: search match line is grep's"  "1" "$(grep -cF 'mod_b.py:4:    return get_thing() and Config.FLAG' <<<"$OUT")"
    OUT=$(fc_run search get_thing tests)
    check "fc: search path limits it"        "0" "$(grep -c '^mod_' <<<"$OUT")"
    check "fc: search path keeps tests"      "1" "$(grep -c '^tests/test_mod_a.py:[0-9]*:' <<<"$OUT" | awk '$1 > 0 {print 1}')"
    OUT=$(fc_run search 'FLAG = ')
    check "fc: search text is not a name"    "0" "$(grep -c '^definition: ' <<<"$OUT")"
    check "fc: search text match line"       "1" "$(grep -cF 'mod_a.py:2:    FLAG = "on"' <<<"$OUT")"
    OUT=$(fc_run search no_such_text_anywhere); RC=$?
    check "fc: search no match rc=1"         "1" "$RC"
    printf 'failed: x\n' > "$FABRO_CODE_STATE"
    OUT=$(fc_run search get_thing); RC=$?
    check "fc: search without an index rc=0" "0" "$RC"
    check "fc: search without an index is plain" "0" "$(grep -cE '^(definition: |[^:]+:[0-9]+-[0-9]+ )' <<<"$OUT")"
    check "fc: search without an index has matches" "1" "$(grep -cF 'mod_b.py:4:    return get_thing() and Config.FLAG' <<<"$OUT")"
    fc_run index

    # 9c. A database removed under a live `ok` state (a `git clean -x`) fails closed
    #     with exit 2, never prints junk as an answer.
    mv "$T/wt/.codegraph" "$T/wt/.cg-away"
    OUT=$(fc_run def get_thing); RC=$?
    check "fc: deleted db rc=2"              "2" "$RC"
    check "fc: deleted db says no index"     "1" "$(grep -c 'no code index; use grep' <<<"$OUT")"
    OUT=$(fc_run callers get_thing); RC=$?
    check "fc: deleted db callers rc=2"      "2" "$RC"
    mv "$T/wt/.cg-away" "$T/wt/.codegraph"

    # 10. A failed index state fails every query verb closed (exit 2).
    fc_run index
    rm -f "$FABRO_CODE_MAP"
    git -C "$T/wt" reset -q --hard HEAD >/dev/null 2>&1
    rm -f "$T/wt/mod_c.py"
    printf 'failed: something broke\n' > "$FABRO_CODE_STATE"
    for v in "def get_thing" "show get_thing" "callers get_thing" "callees get_thing" "impact get_thing" "tests mod_a.py" "map"; do
        OUT=$(fc_run $v); RC=$?
        check "fc: no-index $v rc=2" "2" "$RC"
        check "fc: no-index $v msg" "1" "$(grep -c 'no code index; use grep' <<<"$OUT")"
    done
fi

# ---------------------------------------------------------------------------
# fabro-io — the Stage-I/O binary (docs/stage-io C1-C5). Present inside every
# profile image but not assumed on the Mac PATH, so this prints SKIP when absent.
# When present, probe `version` and the `stage` subcommand under a minimal manifest.
echo "fabro-io"
if ! command -v fabro-io >/dev/null 2>&1; then
    echo "  SKIP fabro-io (no fabro-io on PATH; present in profile images)"
else
    VER=$(fabro-io version 2>&1)
    check "fabro-io prints a version" "1" "$(grep -c '^fabro-io ' <<<"$VER")"
    TMPIO=$(mktemp -d)
    export FABRO_IO_ROOT="$TMPIO" FABRO_NODE_ID=probe
    export FABRO_IO_MANIFEST='{"version":1,"min_binary":"0.0.0","stages":{}}'
    fabro-io stage >/dev/null 2>&1; RC=$?
    check "fabro-io stage exits 0 under probe manifest" "0" "$RC"
    NODE_LINE=$(grep -c '"node": "probe"' "$TMPIO/.io/stage.json" 2>/dev/null || echo 0)
    check "fabro-io stage wrote stage.json for probe" "1" "$NODE_LINE"
    unset FABRO_IO_ROOT FABRO_NODE_ID FABRO_IO_MANIFEST
    rm -rf "$TMPIO"
fi

echo ""
echo "the suite leaves the checkout alone"
check "repo HEAD and tree are unchanged" "$REPO_STATE_BEFORE" "$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)|$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null | cksum)"
if [ "$FAIL" -eq 0 ]; then
    echo "PASS: $PASS checks"
    exit 0
fi
echo "FAIL: $FAIL of $((PASS + FAIL)) checks"
exit 1
