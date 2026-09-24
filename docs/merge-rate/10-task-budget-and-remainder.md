# `backlog` implements at most 8 tasks and files the rest as one remainder issue

## Tracer-Bullet Outcome
A `backlog` run stops starting new tasks once 8 tasks have reached a coder. Every task
still queued, whether it came from `decompose`, an `improve` split, or an extra-review
follow-up added later, goes to `/tmp/fabro/remainder.json`. After the PR opens, a new
node `file_remainder` files those tasks as **one** GitHub issue, labelled
`agent-remainder` (held, **not** `agent`), with a machine-readable marker as its first
line, and posts a PR comment naming it. The scheduler's promoter (task 09) queues that
issue with `priority` once the PR merges. A large issue becomes a series of PRs of at most
8 tasks each, with no human step.

## User Story
As the operator, I want each run's PR bounded to 8 tasks, with the leftover work queued
automatically as the next issue, so that PRs stay small enough to review and merge, and a
large issue does not hold a coder box for 13–19 hours and then fail to merge.

## Description
All edits are in `.fabro/workflows/backlog/workflow.fabro`, plus one new test section
and one `AGENTS.md` row:

1. `prep`: reset the budget counter and delete the remainder files at run start.
2. `improve_gate`: add 1 to `/tmp/fabro/tasks_coded` whenever it routes a task to
   `coder` (disposition `ready`).
3. `next_task`: when `tasks_coded >= 8` and tasks remain, move them to
   `remainder.json`, trim the queue, and report `tasks_done=true`.
4. `extra_prep`: after the first extra-review round, start no further round once a
   remainder exists.
5. New node `file_remainder`, between `open_pr` and `pr_handoff`.
6. Edges: `open_pr → file_remainder → pr_handoff`.
7. `ops/test-task-gates.sh`: new section.
8. `AGENTS.md`: one invariant row.

## Context Pack
- Source decisions: overview decisions 10 and 11, contracts C1 (marker), C2 (labels)
  and C3 (files). ADR 0011 D6, amended 2026-09-24.
- Evidence: 6 of 38 runs reached 9–17 coder visits. They held 42% of all lease hours,
  none auto-merged, and their PRs had a median of 30+ files. They started at only 4–8
  tasks. The growth came from `improve` splits (+1 to +3) and extra-review follow-ups
  (+3 to +10).
- Why count at `improve_gate`: a split rewinds `task_index` and `next_task` selects the
  same slot again, so counting selections would count a split task twice. `improve_gate`
  routes to `coder` exactly when its final disposition is `ready`, which is the event
  the budget is about. A task `improve` calls `redundant` does not count.
- Repo facts. These are the current node texts; each edit below is an exact
  find-and-replace inside them:
  - `prep`:
    ```
    prep [label="Prep workspace and install deps", shape=parallelogram, timeout="20m", max_retries=1,
        script="set -e
mkdir -p /tmp/fabro/review /tmp/fabro/feedback /tmp/fabro/extra
echo 0 > /tmp/fabro/extra_round
echo 0 > /tmp/fabro/split_rounds
./.fabro/setup.sh"]
    ```
  - `next_task`:
    ```
    next_task [label="Next task + mainline refresh", shape=parallelogram, output_schema="routing",
        script="IDX=$(cat /tmp/fabro/task_index 2>/dev/null || echo 0)
COUNT=$(jq length /tmp/fabro/tasks.json)
if [ $IDX -ge $COUNT ]; then echo '{\"context_updates\":{\"tasks_done\":true}}'; exit 0; fi
jq '.['$IDX']' /tmp/fabro/tasks.json > /tmp/fabro/current_task.json
echo $((IDX+1)) > /tmp/fabro/task_index
echo 0 > /tmp/fabro/round
echo 0 > /tmp/fabro/review_attempts
echo 0 > /tmp/fabro/improve_attempts
echo 0 > /tmp/fabro/merge_attempts
rm -f /tmp/fabro/improve_result.json
git rev-parse HEAD > /tmp/fabro/pre_merge_sha
if git fetch origin main && git merge --no-edit origin/main; then
  git rev-parse HEAD > /tmp/fabro/task_base_sha
  echo '{\"context_updates\":{\"tasks_done\":false,\"rebase_failed\":false,\"review_decision\":\"none\",\"task_disposition\":\"none\",\"diff_too_large\":false}}'
else
  git merge --abort || true
  git rev-parse HEAD > /tmp/fabro/task_base_sha
  echo 'The mainline merge was attempted and aborted: leaving it in the tree would let the next checkpoint commit conflict markers. A dedicated resolve_merge stage redoes the merge and resolves it, so no rework agent should touch git.' > /tmp/fabro/feedback/rework.md
  echo '{\"context_updates\":{\"tasks_done\":false,\"rebase_failed\":true,\"review_decision\":\"none\",\"task_disposition\":\"none\",\"diff_too_large\":false}}'
fi"]
    ```
  - `improve_gate` ends with these two lines, verbatim:
    ```
    TC=$(jq length /tmp/fabro/tasks.json 2>/dev/null || echo 0)
    echo '{\"context_updates\":{\"task_disposition\":\"'$D'\",\"task_count\":'$TC'}}'"]
    ```
    Its routing edge `improve_gate -> coder [condition="context.task_disposition=ready"]`
    is what "routed to the coder" means.
  - `extra_prep`:
    ```
    extra_prep [label="Prepare extra review", shape=parallelogram, output_schema="routing",
        script="ER=$(cat /tmp/fabro/extra_round 2>/dev/null || echo 0)
if [ $ER -ge 2 ]; then echo '{\"context_updates\":{\"extra_done\":true}}'; exit 0; fi
echo $((ER+1)) > /tmp/fabro/extra_round
git fetch origin main
git diff origin/main...HEAD > /tmp/fabro/extra/diff.patch
git diff --stat origin/main...HEAD > /tmp/fabro/extra/diffstat.txt
git diff --name-only origin/main...HEAD > /tmp/fabro/extra/changed_files.txt
echo '{\"context_updates\":{\"extra_done\":false,\"standards_status\":\"none\",\"spec_status\":\"none\",\"quality_status\":\"none\",\"new_tasks\":0,\"followup_status\":\"none\"}}'"]
    ```
  - `extra_gate` appends extra-review follow-ups to `tasks.json`, and its edge
    `extra_gate -> next_task [condition="context.new_tasks>0"]` sends them through
    `next_task`, which is where the budget moves them to the remainder.
  - The edges to change, verbatim:
    ```
        open_pr_prep -> open_pr         [condition="outcome=succeeded"]
        open_pr_prep -> human_rescue
        open_pr -> pr_handoff           [condition="outcome=succeeded"]
        open_pr -> human_rescue
    ```
  - `open_pr` writes `/tmp/fabro/pr_number` (digits only). `claim` writes
    `/tmp/fabro/issue.json`, which has `.number` and `.title`. The run branch is
    `fabro/run/<RUN_ID>`, so `basename "$(git rev-parse --abbrev-ref HEAD)"` is the run
    id, the same derivation `AGENTS.md` describes for `trigger_review`.
  - The PR body keeps `Resolves #<parent>`, which the merge gate requires, so the parent
    issue closes when the PR merges. The remainder issue carries the unfinished scope.
- Non-goals: do not touch the shared merge phase, `render.jq`, the scheduler (task 09),
  `open_pr` or `open_pr_prep`. Do not label anything `agent`. Do not change the split
  budget.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft. **Operator: deploy task 09's
  scheduler change before this reaches `main`.**

## Implementation Contract
- Expected files:
  ```
  .fabro/workflows/backlog/workflow.fabro
  ops/test-task-gates.sh
  AGENTS.md
  ```
- **Edit 1: `prep`.** Replace
  ```
echo 0 > /tmp/fabro/split_rounds
./.fabro/setup.sh"]
  ```
  with
  ```
echo 0 > /tmp/fabro/split_rounds
echo 0 > /tmp/fabro/tasks_coded
rm -f /tmp/fabro/remainder.json /tmp/fabro/remainder_issue
./.fabro/setup.sh"]
  ```
- **Edit 2: `improve_gate`.** Replace its last two lines
  ```
TC=$(jq length /tmp/fabro/tasks.json 2>/dev/null || echo 0)
echo '{\"context_updates\":{\"task_disposition\":\"'$D'\",\"task_count\":'$TC'}}'"]
  ```
  with
  ```
if [ \"$D\" = ready ]; then
  TCD=$(cat /tmp/fabro/tasks_coded 2>/dev/null || echo 0)
  case \"$TCD\" in ''|*[!0-9]*) TCD=0 ;; esac
  echo $((TCD+1)) > /tmp/fabro/tasks_coded
fi
TC=$(jq length /tmp/fabro/tasks.json 2>/dev/null || echo 0)
echo '{\"context_updates\":{\"task_disposition\":\"'$D'\",\"task_count\":'$TC'}}'"]
  ```
- **Edit 3: `next_task`.** Replace its third line
  ```
if [ $IDX -ge $COUNT ]; then echo '{\"context_updates\":{\"tasks_done\":true}}'; exit 0; fi
  ```
  with
  ```
if [ $IDX -ge $COUNT ]; then echo '{\"context_updates\":{\"tasks_done\":true}}'; exit 0; fi
TCD=$(cat /tmp/fabro/tasks_coded 2>/dev/null || echo 0)
case \"$TCD\" in ''|*[!0-9]*) TCD=0 ;; esac
if [ $TCD -ge 8 ]; then
  jq --argjson i $IDX '.[$i:]' /tmp/fabro/tasks.json > /tmp/fabro/remainder.new.json
  if [ -s /tmp/fabro/remainder.json ]; then cp /tmp/fabro/remainder.json /tmp/fabro/remainder.old.json; else echo '[]' > /tmp/fabro/remainder.old.json; fi
  jq -s '(.[0] + .[1]) | reduce .[] as $t ([]; if any(.[]; .id == $t.id) then . else . + [$t] end)' /tmp/fabro/remainder.old.json /tmp/fabro/remainder.new.json > /tmp/fabro/remainder.json
  jq --argjson i $IDX '.[0:$i]' /tmp/fabro/tasks.json > /tmp/fabro/tasks.trim.json
  mv /tmp/fabro/tasks.trim.json /tmp/fabro/tasks.json
  echo 'task budget of 8 spent: '$((COUNT-IDX))' task(s) moved to the remainder, which file_remainder files as one issue after open_pr'
  echo '{\"context_updates\":{\"tasks_done\":true}}'
  exit 0
fi
  ```
  Notes: `8` is the task budget (overview decision 10). The `reduce … any(…)` jq
  program appends the new tasks to the existing remainder and drops any whose `id` is
  already there, keeping order. `\"` is the only backslash.
- **Edit 4: `extra_prep`.** Replace its second line
  ```
if [ $ER -ge 2 ]; then echo '{\"context_updates\":{\"extra_done\":true}}'; exit 0; fi
  ```
  with
  ```
if [ $ER -ge 2 ]; then echo '{\"context_updates\":{\"extra_done\":true}}'; exit 0; fi
if [ $ER -ge 1 ] && [ -s /tmp/fabro/remainder.json ]; then echo 'task budget spent: no further extra-review round; its follow-ups would only join the remainder'; echo '{\"context_updates\":{\"extra_done\":true}}'; exit 0; fi
  ```
- **Edit 5: new node.** Insert this block (comment and node) directly before the line
  `    // ---- Review and merge, in this run ----`, followed by a blank line:
  ```
    // file_remainder: the tasks this run did not start because its task budget of 8
    // was spent (ADR 0011 D6; `next_task` moved them to /tmp/fabro/remainder.json)
    // become ONE issue, labelled `agent-remainder` and `ai-generated` -- NOT `agent`.
    // The scheduler's remainder promoter adds `agent` and `priority` once this PR
    // merges (automatically or by hand), so the remainder is worked next and never
    // starts from a `main` that lacks this PR. The first body line is the marker the
    // promoter parses: `<!-- fabro:remainder parent=<N> pr=<P> -->`.
    //
    // Always exits 0: the PR is already open, and a failure here must not park the run
    // on human_rescue. If the issue cannot be filed, the unstarted tasks are listed on
    // the PR instead, so they are never lost silently. /tmp/fabro/remainder_issue makes
    // a second visit (human_rescue -> [P] -> open_pr -> here) a no-op.
    //
    // No output_schema: this node publishes no context key.
    file_remainder [label="File the remainder issue", shape=parallelogram,
        script="R=/tmp/fabro/remainder.json
if [ ! -s $R ] || [ \"$(jq length $R 2>/dev/null || echo 0)\" = 0 ]; then echo 'no remainder: every task fit the task budget'; exit 0; fi
if [ -s /tmp/fabro/remainder_issue ]; then echo 'remainder already filed as #'$(cat /tmp/fabro/remainder_issue); exit 0; fi
N=$(jq -r .number /tmp/fabro/issue.json)
T=$(jq -r .title /tmp/fabro/issue.json)
PR=$(cat /tmp/fabro/pr_number)
K=$(jq length $R)
RUN=$(basename \"$(git rev-parse --abbrev-ref HEAD)\")
gh label create agent-remainder --color C5DEF5 2>/dev/null || true
gh label create ai-generated --color EDEDED 2>/dev/null || true
gh label create priority --color B60205 2>/dev/null || true
{
  echo '<!-- fabro:remainder parent='$N' pr='$PR' -->'
  echo
  echo 'This issue continues #'$N'. fabro run `'$RUN'` implemented the first 8 tasks in PR #'$PR' and stopped at its task budget (fabro-workflows ADR 0011). The '$K' task(s) below were not started.'
  echo
  echo 'It is held with `agent-remainder`. The scheduler adds `agent` and `priority` as soon as PR #'$PR' merges, so it is worked next. If PR #'$PR' is closed without merging, the scheduler labels it `agent-stuck` instead. Do not add `agent` by hand before the PR merges.'
  echo
  echo 'These tasks were decomposed against an older `main`. Decompose again against the current `main`; treat them as a starting point, not a plan.'
  echo
  echo '## Remaining tasks'
  echo
  jq -r '.[] | \"### \" + .title + ([10]|implode) + ([10]|implode) + .body + ([10]|implode)' $R
} > /tmp/fabro/remainder_body.md
URL=$(gh issue create --title \"Remainder of #$N: $T\" --body-file /tmp/fabro/remainder_body.md --label agent-remainder --label ai-generated 2>/dev/null) || URL=''
M=$(printf '%s' \"$URL\" | sed 's|.*/||')
case \"$M\" in
  '' | *[!0-9]*)
    echo 'WARNING: could not file the remainder issue; listing the unstarted tasks on the PR instead' >&2
    {
      echo 'fabro could not file a remainder issue for this PR. These tasks from #'$N' were NOT implemented:'
      echo
      jq -r '.[] | \"- \" + .title' $R
    } > /tmp/fabro/remainder_comment.md
    gh pr comment \"$PR\" --body-file /tmp/fabro/remainder_comment.md || true
    exit 0 ;;
esac
echo $M > /tmp/fabro/remainder_issue
{
  echo 'This PR implements the first 8 tasks of #'$N'. The other '$K' are in #'$M'.'
  echo
  echo 'The scheduler queues #'$M' with `priority` as soon as this PR merges, whether it is merged automatically or by hand.'
} > /tmp/fabro/remainder_comment.md
gh pr comment \"$PR\" --body-file /tmp/fabro/remainder_comment.md || true
echo 'remainder filed as #'$M"]
  ```
  DOT notes: every `"` inside the script is `\"`; `#` appears only inside quoted
  strings; newlines inside the jq program come from `([10]|implode)`; there is no `\n`
  anywhere.
- **Edit 6: edges.** Replace
  `    open_pr -> pr_handoff           [condition="outcome=succeeded"]` with
  `    open_pr -> file_remainder       [condition="outcome=succeeded"]`, and directly after
  `    open_pr -> human_rescue` add:
  ```
      file_remainder -> pr_handoff    [condition="outcome=succeeded"]
      file_remainder -> human_rescue
  ```
- **Edit 7: test section.** Insert into `ops/test-task-gates.sh` directly before the
  final block that begins `# ------…` / `echo ""` / `if [ "$FAIL" -eq 0 ]; then`.
  (`stage <node>` writes `$T/<node>.sh` from the backlog graph with `/tmp/fabro`
  rewritten to `$T` and runs `sh -n`; `lastjson`, `check` and `$ORIG_PATH` are the
  harness's own.)
  ```bash
# ---------------------------------------------------------------------------
# task budget and remainder (ADR 0011 D6)
#
# A run implements at most 8 tasks -- counted as tasks improve_gate routes to the
# coder -- and next_task moves the rest to remainder.json, from any source:
# decompose, splits, or extra-review follow-ups appended later. file_remainder
# then files them as ONE issue the scheduler holds until the PR merges.
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
check "spent: says so"                 "1" "$(grep -c 'task budget of 8 spent' <<<"$OUT")"

# 4. Follow-ups appended later (as extra_gate does) join the remainder, without
#    duplicating an id that is already there.
jq '. + [{"id":"t11","title":"T11","body":"b","files":[],"covers":[],"source":"extra-review"},
         {"id":"t9","title":"T9 again","body":"b","files":[],"covers":[],"source":"extra-review"}]' \
    "$T/tasks.json" > "$T/tasks.tmp" && mv "$T/tasks.tmp" "$T/tasks.json"
OUT=$(sh "$T/next_task.sh" 2>/dev/null)
check "growth: remainder t9 t10 t11"   "t9 t10 t11" "$(jq -r '[.[].id]|join(" ")' "$T/remainder.json")"
check "growth: still done"             "true" "$(jq -r '.context_updates.tasks_done' <<<"$(lastjson "$OUT")")"

# 5. extra_prep: the first round still runs; a later one does not once the budget is spent.
echo 0 > "$T/extra_round"
OUT=$(sh "$T/extra_prep.sh" 2>/dev/null)
check "first extra round still runs"   "false" "$(jq -r '.context_updates.extra_done' <<<"$(lastjson "$OUT")")"
OUT=$(sh "$T/extra_prep.sh" 2>/dev/null)
check "no second round after budget"   "true" "$(jq -r '.context_updates.extra_done' <<<"$(lastjson "$OUT")")"

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
  ```
- **Edit 8: `AGENTS.md`.** Add this row at the end of the "Deployment invariants" table:
  ```
  | A `backlog` run implements at most 8 tasks (`tasks_coded`, counted in `improve_gate` on `ready`), and never labels its remainder issue `agent` | `next_task` moves every task past the budget, from any source, to `remainder.json`, and `file_remainder` files them as one issue labelled `agent-remainder`. The scheduler adds `agent` + `priority` only once the parent PR merges (ADR 0011 D6, `ops/scheduler/src/fabro_scheduler/remainder.py`). Labelling it `agent` at filing time would let the scheduler's same-repo saturation pass start it on the other box, from a `main` without its parent's work. Covered by `ops/test-task-gates.sh`. |
  ```
- Interfaces and names: node id `file_remainder`. Files and labels exactly as overview
  C1–C3. The marker line is exactly
  `<!-- fabro:remainder parent=<N> pr=<P> -->`.
- Verified external contracts: `gh issue create --title <t> --body-file <f> --label <a>
  --label <b>` prints the new issue URL on stdout; the node takes the digits after the
  last `/`. `gh label create <name> --color <hex>` fails when the label exists, which
  the `|| true` absorbs (the same pattern `claim` uses).
- Behavior rules: see the `//` comment on the node. It always exits 0. Filing is
  idempotent through `/tmp/fabro/remainder_issue`.
- Error and security rules: if `gh issue create` fails, the node posts the task titles
  as a PR comment and exits 0. No token is ever printed.

## Acceptance Criteria
- [ ] All eight edits are applied exactly.
- [ ] `./ops/test-task-gates.sh` passes with 26 more checks than before this task.
- [ ] `grep -c 'file_remainder' .fabro/workflows/backlog/workflow.fabro` is at least 5
      (the node, its comment, and three edges).
- [ ] No line in the new code contains `--label agent ` (with a trailing space) or
      `--label agent"`.

## Test Expectations
- Framework: the bash harness `ops/test-task-gates.sh`. Command: `./ops/test-task-gates.sh`.
- Cases, with their literal expectations (all in the section above):
  1. `ready` → `tasks_coded` 3→`4`; `redundant` → stays `4`.
  2. `tasks_coded`=7, index 7 of 10 → selects `t8`; no `remainder.json`.
  3. `tasks_coded`=8, index 8 of 10 → `tasks_done`=`true`; remainder `t9 t10`; queue
     length `8`; output has `task budget of 8 spent`.
  4. appending `t11` and a duplicate `t9` → remainder `t9 t10 t11`.
  5. `extra_prep`: round 0 → `extra_done`=`false`; the next call → `true`.
  6. `file_remainder` with no remainder → exit 0, zero `gh` calls.
  7. with remainder `t9,t10`: one `issue create` with
     `--label agent-remainder --label ai-generated`; first body line
     `<!-- fabro:remainder parent=350 pr=7 -->`; two `### T` headings;
     `remainder_issue` = `77`; the PR comment contains `#77`.
  8. a second call → no `issue create`.
  9. `issue create` fails → exit 0; the PR comment has the line `- T10`; no
     `remainder_issue`.
- Run against a scratch copy with tasks 01, 02, 04 and this task applied (2026-09-24):
  `PASS: 240 checks`, of which 26 are this section.

## Dependencies
- Blocked by: 09 (Scheduler: promote a remainder issue once its parent PR merges)
- Why blocked: without the promoter deployed, a filed remainder issue is never labelled
  `agent` and nothing ever works on it. The marker format (overview C1) is the shared
  contract.
- Blocks: 11 (validate)

## Labels
`enhancement`, `backlog`, `priority:high`

## Estimate
Medium

## Risk
3 - This changes how much of an issue each run delivers, and adds a node on the
publishing path. It fails open, and it is covered by 26 offline checks. If it misbehaves
live, the parent PR still opens and merges as today, and only the remainder hand-off is
affected.

## Validator Stopping Point
`./ops/test-task-gates.sh` passes.
