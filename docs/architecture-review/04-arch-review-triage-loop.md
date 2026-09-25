# `arch-review` triages its queue after filing

## Tracer-Bullet Outcome
After `file_issues`, the same `arch-review` run triages a queue of issues through the
shared triage phase. First come the issues that it filed this run. Then come at most 10
others: answered `needs-info` issues, then `needs-triage` issues, oldest first. It starts
no new triage after the run is 6 hours old. A failed scan still triages. For each issue
that needs a human, Discord receives one message with that issue's link. The summary
now carries the counts, for example
`jelly-swipe: 3 filed (#401 #402 #403); 13 triaged: 10 agent, 1 needs-info, 1 not actionable, 1 released; 2 left for the next review`.

## User Story
As the operator, I want each review to leave its new issues and the waiting ones ready
for `backlog`, so that the backlog fills without my attention and only real questions
reach me.

## Description
**Before you edit any `.fabro` or `workflow.toml` file, invoke the `/fabro-workflow`
skill.** Read `docs/architecture-review/00-overview-and-contracts.md` first. This task
uses decisions 7, 8 and 9, contracts C1, C4 and C5, and rules 1 to 12. Tasks 01 and 03
are merged: the triage phase is at `.fabro/workflows/_shared/triage/triage.fabro`, and
`.fabro/workflows/arch-review/` exists.

1. Replace `.fabro/workflows/arch-review/workflow.fabro` with *Target file 1*. Compared
   with task 03 it adds `build_queue`, `next_issue` and the `triage_phase` import,
   extends `summarize` with the counts, and sends every scan-side failure edge to
   `build_queue` instead of `summarize`. `prep`, `history`, `scan`, `scan_gate`,
   `file_issues` and `finish` are unchanged.
2. Append *workflow.toml addition* to the end of
   `.fabro/workflows/arch-review/workflow.toml`.
3. In `ops/test-task-gates.sh`, in the `arch-review scan and file` section, change the
   two `summarize` checks as shown in *Test Expectations*, and add the new section.

## Context Pack
- Source decisions: ADR 0012 D2, D3, D5, D7, D9. Overview decisions 7, 8, 9.
- Repo facts:
  - Contract C1: the phase reads only `/tmp/fabro/issue_number`. It writes the outcome
    word to `/tmp/fabro/triage_outcome` and publishes `context.triage_outcome`. The
    words are `ready`, `needs_info`, `not_actionable`, `skipped`, `released`.
  - The import contract (fabro-workflow skill): the edges into and out of an import
    placeholder carry no `condition`. So `triage_phase -> next_issue` is unconditional,
    and `next_issue` reads the outcome from the file, not from context. A command node
    cannot read context.
  - `backlog` loops the same way: its `next_task` node reads a queue file and a cursor
    file, publishes `tasks_done`, and the edges route on it.
  - `prep` (task 03) writes `/tmp/fabro/run_started` with `date +%s`, and
    `/tmp/fabro/arch/filed.json` with `[]` before `file_issues` runs.
  - The notify script (tasks 01 and 03) already has the `triage-question`,
    `triage-failed` and `arch-summary` kinds, and reads `issue_url`,
    `triage_questions` and `arch_summary` with `tail -1`.
  - `gh issue list --label needs-info --json number,comments` gives each issue's
    comments oldest first, so `.comments[-1]` is the newest one. `issue-triage`'s
    `acquire` uses the same rule: an issue whose newest comment has no `fabro:triage-`
    marker has a human reply.
- Non-goals: do not change the triage phase, `issue-triage`, `backlog` or the notify
  script. Do not add automation rows (task 06).

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft.

## Implementation Contract
- Expected files: `.fabro/workflows/arch-review/workflow.fabro`,
  `.fabro/workflows/arch-review/workflow.toml`, `ops/test-task-gates.sh`.
- Interfaces and names: node ids `build_queue`, `next_issue`, placeholder
  `triage_phase`. Context keys `queue_length`, `queue_done` (the strings `"true"` or
  `"false"`). Files `queue.json`, `queue_index`, `tally.json`, `arch/deferred`,
  `arch/in_flight` (C4).
- Verified external contracts: `gh issue list --json number,comments`, see Repo facts.
- Behavior rules:
  - Queue order: `filed.json` in its order, then answered `needs-info` issues sorted by
    number, then `needs-triage` issues sorted by number. The second group excludes the
    first, and neither includes an issue in `filed.json`. At most 10 issues follow the
    filed ones.
  - The 6-hour budget is checked before each new triage. The issue that is in progress
    is never stopped.
  - `summarize` counts a missing file as zero.
- Error and security rules: a failed `gh issue list` counts as an empty list. A failed
  triage counts as `released`.

### Target file 1: `.fabro/workflows/arch-review/workflow.fabro`

````dot
digraph ArchReview {
    graph [
        goal="Architecture review: scan the repository for deepening candidates, file the strongest as issues, triage the waiting issues toward the agent label, and report",
        default_fidelity="truncate",
        // Above every node timeout: `scan` is 50m.
        stall_timeout="60m",
        model_stylesheet="
            *         { model: high-reasoning; }
            .scan     { model: high-reasoning; }
            .improve  { model: high-reasoning; }
            .triage   { model: high-reasoning; }
        "
    ]
    rankdir=LR

    start [shape=Mdiamond, label="Start"]
    exit  [shape=Msquare, label="Exit"]

    // Resets every run-local file. scan_status starts as `failed` and only scan_gate
    // sets it to `ok`, so any path that skips the scan reports it as failed.
    prep [label="Prepare the workspace", shape=parallelogram,
        script="set -e
mkdir -p /tmp/fabro/arch
rm -f /tmp/fabro/arch/candidates.json /tmp/fabro/arch/existing.json /tmp/fabro/arch/hotspots.txt
echo 0 > /tmp/fabro/arch/scan_attempts
echo '[]' > /tmp/fabro/arch/filed.json
echo failed > /tmp/fabro/arch/scan_status
date +%s > /tmp/fabro/run_started
echo ready"]

    // 90 days of history for the hot spots (ADR 0012 D1), and the architecture issues
    // that already exist, open and closed, so the scan does not propose them again.
    history [label="Read history and existing issues", shape=parallelogram,
        script="set -e
git fetch --shallow-since=\"90 days ago\" origin main || true
git log --since=\"90 days ago\" --name-only --format= HEAD 2>/dev/null | grep -v '^$' | sort | uniq -c | sort -rn | head -60 > /tmp/fabro/arch/hotspots.txt || true
gh issue list --label architecture --state all --limit 500 --json number,title,state,stateReason,body > /tmp/fabro/arch/existing_raw.json
jq '[.[] | {number, title, state, stateReason, slug: ([(.body // \"\") | capture(\"fabro:arch-candidate slug=(?<s>[a-z0-9-]+)\")? | .s][0])}]' /tmp/fabro/arch/existing_raw.json > /tmp/fabro/arch/existing.json
echo 'history: '$(wc -l < /tmp/fabro/arch/hotspots.txt)' hot files, '$(jq length /tmp/fabro/arch/existing.json)' existing architecture issues'"]

    scan [label="Scan for deepening candidates", class="scan", prompt="@prompts/scan.md.j2", timeout="50m", max_retries=1]

    // Validates the candidates contract. One repair turn; a second invalid file sets
    // scan_status=failed and exits ZERO, so the run still reports (ADR 0012 D5).
    scan_gate [label="Validate the candidates", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/arch/candidates.json
A=$(cat /tmp/fabro/arch/scan_attempts 2>/dev/null || echo 0)
OK=$(jq -r 'if (.candidates | type) != \"array\" then \"no\"
  elif (.candidates | length) > 20 then \"no\"
  elif ([.candidates[] | select(
      ((.slug // \"\") | tostring | test(\"^[a-z0-9][a-z0-9-]{2,59}$\") | not)
      or ((.title // \"\") | tostring | test(\"^refactor([(][a-z0-9 ./_-]+[)])?: .+\") | not)
      or (((.strength // \"\") as $s | $s == \"Strong\" or $s == \"Worth exploring\" or $s == \"Speculative\") | not)
      or ((.files | type) != \"array\") or ((.files | length) == 0)
      or ((.problem // \"\") == \"\") or ((.solution // \"\") == \"\") or ((.benefits // \"\") == \"\")
    )] | length) > 0 then \"no\"
  elif ([.candidates[].slug] | length) != ([.candidates[].slug] | unique | length) then \"no\"
  else \"yes\" end' $F 2>/dev/null || echo no)
if [ \"$OK\" != yes ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/arch/scan_attempts
  if [ $A -lt 2 ]; then echo 'candidates.json is missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
  echo failed > /tmp/fabro/arch/scan_status
  jq -nc '{context_updates:{scan_status:\"failed\",candidate_count:0}}'
  exit 0
fi
echo ok > /tmp/fabro/arch/scan_status
jq -c '{context_updates:{scan_status:\"ok\",candidate_count:(.candidates | length)}}' $F"]

    // Files at most 8 Strong / Worth exploring candidates. The architecture issues are
    // read again before every create, so a slug filed by a second review running at the
    // same time is still skipped (ADR 0012 D5). A create that fails is skipped, not
    // fatal. Labels are created first because `gh issue create` rejects an unknown one.
    file_issues [label="File Architecture issues", shape=parallelogram, output_schema="routing",
        script="set -e
F=/tmp/fabro/arch/candidates.json
gh label create architecture --color 0E8A16 --description 'Architecture review: a deepening candidate (ADR 0012)' 2>/dev/null || true
gh label create needs-triage --color D4C5F9 --description 'Waiting for triage' 2>/dev/null || true
gh label create ai-generated --color EDEDED --description 'Written by an agent' 2>/dev/null || true
jq -c '[.candidates[] | select(.strength == \"Strong\" or .strength == \"Worth exploring\")] | sort_by(if .strength == \"Strong\" then 0 else 1 end)' $F > /tmp/fabro/arch/eligible.json
echo '[]' > /tmp/fabro/arch/filed.json
K=$(jq length /tmp/fabro/arch/eligible.json)
I=0
while [ $I -lt $K ]; do
  if [ \"$(jq length /tmp/fabro/arch/filed.json)\" -ge 8 ]; then break; fi
  jq --argjson i $I '.[$i]' /tmp/fabro/arch/eligible.json > /tmp/fabro/arch/cand.json
  I=$((I+1))
  S=$(jq -r .slug /tmp/fabro/arch/cand.json)
  gh issue list --label architecture --state all --limit 500 --json body > /tmp/fabro/arch/live.json
  if jq -e --arg s \"$S\" '[.[] | (.body // \"\") | capture(\"fabro:arch-candidate slug=(?<s>[a-z0-9-]+)\")? | .s] | index($s) != null' /tmp/fabro/arch/live.json > /dev/null; then
    echo 'skip '$S': an architecture issue already has this slug'
    continue
  fi
  jq -r '([10]|implode) as $nl | \"<!-- fabro:arch-candidate slug=\" + .slug + \" -->\" + $nl + $nl
    + \"> Filed by an automated architecture review (fabro-workflows ADR 0012). Strength: **\" + .strength + \"**.\" + $nl + $nl
    + \"## Files\" + $nl + $nl + ([.files[] | \"- `\" + . + \"`\"] | join($nl)) + $nl + $nl
    + \"## Problem\" + $nl + $nl + .problem + $nl + $nl
    + \"## Solution\" + $nl + $nl + .solution + $nl + $nl
    + \"## Benefits\" + $nl + $nl + .benefits + $nl
    + (if (.diagram // \"\") == \"\" then \"\" else $nl + \"## Before and after\" + $nl + $nl + \"```mermaid\" + $nl + .diagram + $nl + \"```\" + $nl end)' /tmp/fabro/arch/cand.json > /tmp/fabro/arch/body.md
  T=$(jq -r .title /tmp/fabro/arch/cand.json)
  URL=$(gh issue create --title \"$T\" --body-file /tmp/fabro/arch/body.md --label architecture --label needs-triage --label ai-generated) || URL=''
  M=$(printf '%s' \"$URL\" | sed 's|.*/||')
  case \"$M\" in
    '' | *[!0-9]*) echo 'WARNING: could not file '$S >&2; continue ;;
  esac
  jq --argjson m \"$M\" '. + [$m]' /tmp/fabro/arch/filed.json > /tmp/fabro/arch/filed.next
  mv /tmp/fabro/arch/filed.next /tmp/fabro/arch/filed.json
  echo 'filed '$S' as #'$M
done
jq -c '{context_updates:{filed_count:length}}' /tmp/fabro/arch/filed.json"]

    // ---- Triage (ADR 0012 D5) ----
    // Budget 1 is every issue this run filed. Budget 2 is at most 10 others: answered
    // needs-info issues (the newest comment carries no fabro:triage- marker, so a human
    // replied), then needs-triage issues, oldest first in each group. Issues this run
    // filed are never counted twice. A failed list read counts as empty.
    build_queue [label="Build the triage queue", shape=parallelogram, output_schema="routing",
        script="set -e
mkdir -p /tmp/fabro/arch
gh issue list --label needs-info --state open --json number,comments --limit 100 > /tmp/fabro/arch/q_info.json || echo '[]' > /tmp/fabro/arch/q_info.json
gh issue list --label needs-triage --state open --json number --limit 100 > /tmp/fabro/arch/q_triage.json || echo '[]' > /tmp/fabro/arch/q_triage.json
for f in q_info q_triage; do
  jq -e 'type == \"array\"' /tmp/fabro/arch/$f.json > /dev/null 2>&1 || echo '[]' > /tmp/fabro/arch/$f.json
done
test -s /tmp/fabro/arch/filed.json || echo '[]' > /tmp/fabro/arch/filed.json
jq -n --slurpfile i /tmp/fabro/arch/q_info.json --slurpfile t /tmp/fabro/arch/q_triage.json --slurpfile f /tmp/fabro/arch/filed.json '
  ($f[0]) as $filed
  | ([$i[0][] | select((.comments | length) > 0) | select((.comments[-1].body | test(\"fabro:triage-\")) | not) | .number] | sort) as $a
  | ([$t[0][].number] | sort) as $n
  | $filed + ((($a + ($n - $a)) - $filed) | .[0:10])' > /tmp/fabro/queue.json
echo 0 > /tmp/fabro/queue_index
echo '{\"ready\":0,\"needs_info\":0,\"not_actionable\":0,\"skipped\":0,\"released\":0}' > /tmp/fabro/tally.json
echo 0 > /tmp/fabro/arch/deferred
rm -f /tmp/fabro/arch/in_flight
jq -c '{context_updates:{queue_length:length}}' /tmp/fabro/queue.json"]

    // Counts the outcome of the issue that just left the triage phase, then picks the
    // next one. `in_flight` marks that an issue was handed to the phase, so the first
    // visit counts nothing. After 6 hours (ADR 0012 D8) no new triage starts; the rest
    // wait for the next review and are counted as deferred. The phase reads only
    // /tmp/fabro/issue_number (contract C1).
    next_issue [label="Next issue", shape=parallelogram, output_schema="routing",
        script="set -e
if [ -f /tmp/fabro/arch/in_flight ]; then
  O=$(cat /tmp/fabro/triage_outcome 2>/dev/null || echo released)
  case \"$O\" in ready|needs_info|not_actionable|skipped|released) ;; *) O=released ;; esac
  jq --arg o \"$O\" '.[$o] += 1' /tmp/fabro/tally.json > /tmp/fabro/tally.next
  mv /tmp/fabro/tally.next /tmp/fabro/tally.json
  rm -f /tmp/fabro/arch/in_flight
fi
I=$(cat /tmp/fabro/queue_index)
K=$(jq length /tmp/fabro/queue.json)
if [ $I -ge $K ]; then
  jq -nc '{context_updates:{queue_done:\"true\"}}'
  exit 0
fi
ST=$(cat /tmp/fabro/run_started 2>/dev/null || date +%s)
if [ $(( $(date +%s) - ST )) -ge 21600 ]; then
  echo $((K - I)) > /tmp/fabro/arch/deferred
  echo 'time budget of 6 hours spent; '$((K - I))' issues left for the next review'
  jq -nc '{context_updates:{queue_done:\"true\"}}'
  exit 0
fi
jq -r --argjson i $I '.[$i]' /tmp/fabro/queue.json > /tmp/fabro/issue_number
echo $((I + 1)) > /tmp/fabro/queue_index
rm -f /tmp/fabro/triage_outcome
: > /tmp/fabro/arch/in_flight
echo 'triaging #'$(cat /tmp/fabro/issue_number)
jq -nc '{context_updates:{queue_done:\"false\"}}'"]

    triage_phase [import="../_shared/triage/triage.fabro"]

    // One line for Discord (ADR 0012 D9). `\"` is stripped: the hook reads the value
    // with a grep that stops at the first quote. Missing files count as zero, so a
    // review that failed early still reports.
    summarize [label="Summarize the review", shape=parallelogram, output_schema="routing",
        script="S=$(cat /tmp/fabro/arch/scan_status 2>/dev/null || echo failed)
F=$(jq length /tmp/fabro/arch/filed.json 2>/dev/null || echo 0)
L=$(jq -r 'map(\"#\" + tostring) | join(\" \")' /tmp/fabro/arch/filed.json 2>/dev/null || echo '')
REPO=$(gh repo view --json name --jq .name 2>/dev/null || echo repository)
TL=/tmp/fabro/tally.json
RD=$(jq -r '.ready // 0' $TL 2>/dev/null || echo 0)
NI=$(jq -r '.needs_info // 0' $TL 2>/dev/null || echo 0)
NA=$(jq -r '.not_actionable // 0' $TL 2>/dev/null || echo 0)
SK=$(jq -r '.skipped // 0' $TL 2>/dev/null || echo 0)
RL=$(jq -r '.released // 0' $TL 2>/dev/null || echo 0)
TR=$((RD + NI + NA + SK + RL))
DF=$(cat /tmp/fabro/arch/deferred 2>/dev/null || echo 0)
if [ \"$S\" = ok ]; then M=\"$REPO: $F filed\"; else M=\"$REPO: scan failed, 0 filed\"; fi
if [ -n \"$L\" ]; then M=\"$M ($L)\"; fi
M=\"$M; $TR triaged: $RD agent, $NI needs-info, $NA not actionable\"
if [ \"$SK\" != 0 ]; then M=\"$M, $SK skipped\"; fi
if [ \"$RL\" != 0 ]; then M=\"$M, $RL released\"; fi
if [ \"$DF\" != 0 ]; then M=\"$M; $DF left for the next review\"; fi
M=$(printf '%s' \"$M\" | tr -d '\"')
echo \"$M\"
jq -nc --arg m \"$M\" '{context_updates:{arch_summary:$m}}'"]

    // A no-op whose stage_start fires the `discord-arch-summary` hook, after
    // `summarize` has published `arch_summary`.
    finish [label="Finish", shape=parallelogram, script="echo done"]

    // ---- Edges ----
    // A failed scan still triages (ADR 0012 D5): every scan-side failure lands on
    // `build_queue`. `summarize` is the failure sink for the triage side, so a failed
    // review still reports.
    start -> prep
    prep -> history                 [condition="outcome=succeeded"]
    prep -> summarize
    history -> scan                 [condition="outcome=succeeded"]
    history -> build_queue
    scan -> scan_gate               [condition="outcome=succeeded"]
    scan -> build_queue
    scan_gate -> scan               [condition="outcome=failed"]
    scan_gate -> file_issues        [condition="outcome=succeeded && context.scan_status=ok"]
    scan_gate -> build_queue        [condition="outcome=succeeded && context.scan_status=failed"]
    scan_gate -> build_queue
    file_issues -> build_queue      [condition="outcome=succeeded"]
    file_issues -> build_queue
    build_queue -> next_issue       [condition="outcome=succeeded"]
    build_queue -> summarize
    next_issue -> triage_phase      [condition="outcome=succeeded && context.queue_done=false"]
    next_issue -> summarize         [condition="outcome=succeeded && context.queue_done=true"]
    next_issue -> summarize
    triage_phase -> next_issue
    summarize -> finish             [condition="outcome=succeeded"]
    summarize -> finish
    finish -> exit
}
````

### workflow.toml addition

```toml
# The triage phase is imported as `triage_phase`, so its node ids are prefixed
# (`triage_phase.post_questions`). A bare `^post_questions$` matches nothing.
# One message per issue that needs a human, with the issue link (ADR 0012 D3).
[[run.hooks]]
id = "discord-triage-question"
event = "stage_start"
matcher = "(^|[.])post_questions$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh triage-question"

[[run.hooks]]
id = "discord-triage-failed"
event = "stage_start"
matcher = "(^|[.])release$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh triage-failed"
```

## Acceptance Criteria
- [ ] `arch-review/workflow.fabro` matches Target file 1.
- [ ] `arch-review/workflow.toml` has four hooks, and the tomllib check passes.
- [ ] `./ops/test-task-gates.sh` prints `PASS: 314 checks` (301 plus 13).
- [ ] Operator check in task 07: `fabro validate` prints
      `Workflow: ArchReview (21 nodes, 44 edges)` and `Validation: OK` with no warning.

## Test Expectations
Framework: the bash harness `ops/test-task-gates.sh`. Run `./ops/test-task-gates.sh`.
The helpers `extract_from`, `check` and `lastjson` and the `ARCH` variable already exist.

1. In the `arch-review scan and file` section, the two `summarize` checks now expect the
   triage counts. The test directory has no `tally.json`, so they are zero. Replace the
   two expected strings:
   - `"jelly-swipe: 2 filed (#401 #402)"` becomes
     `"jelly-swipe: 2 filed (#401 #402); 0 triaged: 0 agent, 0 needs-info, 0 not actionable"`
   - `"jelly-swipe: scan failed, 0 filed"` becomes
     `"jelly-swipe: scan failed, 0 filed; 0 triaged: 0 agent, 0 needs-info, 0 not actionable"`

2. Insert this section immediately before the final `echo ""` and
   `if [ "$FAIL" -eq 0 ]; then` block:

```bash
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

# 2. next_issue walks the queue and counts each outcome.
echo '[11,12]' > "$T/queue.json"; echo 0 > "$T/queue_index"; date +%s > "$T/run_started"
echo '{"ready":0,"needs_info":0,"not_actionable":0,"skipped":0,"released":0}' > "$T/tally.json"
echo 0 > "$T/arch/deferred"; rm -f "$T/arch/in_flight"
OUT=$(sh "$T/next_issue.sh" 2>&1)
check "next: first issue"              "11"    "$(cat "$T/issue_number")"
check "next: not done"                 "false" "$(jq -r '.context_updates.queue_done' <<<"$(lastjson "$OUT")")"
echo ready > "$T/triage_outcome"
sh "$T/next_issue.sh" >/dev/null 2>&1
check "next: counts ready"             "1"     "$(jq -r .ready "$T/tally.json")"
check "next: second issue"             "12"    "$(cat "$T/issue_number")"
echo needs_info > "$T/triage_outcome"
OUT=$(sh "$T/next_issue.sh" 2>&1)
check "next: counts needs_info"        "1"     "$(jq -r .needs_info "$T/tally.json")"
check "next: done at the end"          "true"  "$(jq -r '.context_updates.queue_done' <<<"$(lastjson "$OUT")")"

# 3. The 6-hour budget stops new triages and records what is left.
echo '[21,22,23]' > "$T/queue.json"; echo 1 > "$T/queue_index"; rm -f "$T/arch/in_flight"
echo $(( $(date +%s) - 21601 )) > "$T/run_started"
OUT=$(sh "$T/next_issue.sh" 2>&1)
check "budget: done"                   "true"  "$(jq -r '.context_updates.queue_done' <<<"$(lastjson "$OUT")")"
check "budget: deferred"               "2"     "$(cat "$T/arch/deferred")"

# 4. summarize: the full line.
echo ok > "$T/arch/scan_status"; echo '[401,402,403]' > "$T/arch/filed.json"
echo '{"ready":10,"needs_info":1,"not_actionable":1,"skipped":0,"released":1}' > "$T/tally.json"
echo 2 > "$T/arch/deferred"
check "summary: full line" \
    "jelly-swipe: 3 filed (#401 #402 #403); 13 triaged: 10 agent, 1 needs-info, 1 not actionable, 1 released; 2 left for the next review" \
    "$(jq -r '.context_updates.arch_summary' <<<"$(lastjson "$(sh "$T/summarize.sh" 2>&1)")")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE
```

That is 13 new `check` calls.

## Dependencies
- Blocked by: 01 (the triage phase it imports), 03 (the package it extends)
- Why blocked: Target file 1 imports `../_shared/triage/triage.fabro` from task 01 and
  replaces the graph that task 03 creates.
- Blocks: 06, 07

## Labels
`feature`, `arch-review`, `priority:high`

## Estimate
Medium

## Risk
3 - one run now changes labels on as many as 18 issues and can promote them to `agent`,
which the scheduler then works. Task 05 must merge before any schedule is enabled
(task 06 and task 07 enforce that order).

## Validator Stopping Point
`./ops/test-task-gates.sh` prints `PASS: 314 checks`, and the tomllib check passes.
