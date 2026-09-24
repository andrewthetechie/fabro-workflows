# `watch_checks` reruns the failed jobs once before it calls `ci_fix`

## Tracer-Bullet Outcome
When a PR's GitHub checks fail in the merge phase, the run first reruns the failed Actions
jobs **once**. If they pass, the PR merges and `ci_fix` never runs. If they fail again, the
run goes on to `ci_fix_t1` as it does today. A flaky test or a CI infrastructure fault no
longer blocks an auto-merge.

## User Story
As the operator, I want a flaky check retried once before an agent tries to "fix" it, so
that PRs whose only problem was a flake merge on their own, and I stop merging them by
hand.

## Description
Two edits in `.fabro/workflows/_shared/review-merge/review-merge.fabro` and one new test
section in `ops/test-task-gates.sh`:

1. Replace the whole `watch_checks` node with the version given below. The new version
   wraps the existing poll loop in an outer loop that allows one rerun.
2. In `merge_gate`, delete the rerun marker next to the other per-phase resets.
3. Add a `//` comment above `watch_checks` (text below).
4. Add the test section (text below).

This file is imported by both `backlog` and `pr-review`, so the change applies to both
workflows. That is intended.

## Context Pack
- Source decisions: overview decision 4. ADR 0011 D2.
- Evidence: 4 of the 7 PRs where `ci_fix` "could not fix" were a flaky Rust test
  (writers-app#966), a known-flaky Playwright test (womens-fantasy-sports#1246), a cold
  registry mirror (womens-fantasy-sports#1245) and a registry-cache upload error
  (lawncare-saas#2608). The operator merged all four unchanged.
- Repo facts:
  - Routing out of `watch_checks` is unchanged. These are its edges, verbatim; do not
    edit them:
    ```
    watch_checks  -> report_blocked    [condition="context.checks_blocked=true", weight=20]
    watch_checks  -> merge             [condition="context.checks_ok=true", weight=10]
    watch_checks  -> ci_fix_t1         [condition="context.checks_ok=false && context.gh_fix_attempts=1"]
    watch_checks  -> ci_fix_t2         [condition="context.checks_ok=false && context.gh_fix_attempts=2"]
    watch_checks  -> mark_needs_human
    ```
  - `merge_gate` writes `merge_deadline` = now + 3600 s when the PR is eligible. These are
    the lines to edit, verbatim, with the two-space indent they have inside
    `if [ \"$E\" = true ]; then`:
    ```
      echo 0 > /tmp/fabro/gh_fix_attempts
      echo 0 > /tmp/fabro/strict_retry
    ```
  - A failing check's `link` for a GitHub Actions job looks like
    `https://github.com/<owner>/<repo>/actions/runs/<RUN_ID>/job/<JOB_ID>`. The existing
    node already turns it into `<RUN_ID>` with
    `sed 's|.*/actions/runs/||; s|/.*||'` plus a digits-only `case` guard. A non-Actions
    check (CodeQL posts `…/runs/<id>`) fails the guard and is skipped.
  - `gh run rerun <run-id> --failed` reruns only the failed jobs of that run. It is part
    of the installed `gh` CLI, and the merge phase already uses `gh run view <id>
    --log-failed` the same way.
- Non-goals: do not change the node's `timeout="50m"`, the edges, the 60-minute
  budget, the 5-minute grace, `ci_fix_t1`/`ci_fix_t2`, or `render.jq`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  .fabro/workflows/_shared/review-merge/review-merge.fabro   (watch_checks, merge_gate, one comment)
  ops/test-task-gates.sh                                     (one new section)
  ```

- **Edit 1: replace `watch_checks`.** The node currently reads, verbatim:
  ```
    watch_checks [label="Watch GitHub checks", shape=parallelogram, timeout="50m", output_schema="routing",
        script="PR=$(cat /tmp/fabro/pr_number)
A=$(cat /tmp/fabro/gh_fix_attempts 2>/dev/null || echo 0)
DL=$(cat /tmp/fabro/merge_deadline 2>/dev/null || echo 0)
blocked() {
  printf '%s' \"$1\" > /tmp/fabro/merge_block_reason
  echo '{\"context_updates\":{\"checks_ok\":false,\"checks_blocked\":true,\"gh_fix_attempts\":'$A'}}'
  exit 0
}
GRACE=$(( $(date +%s) + 300 ))
while : ; do
  NOW=$(date +%s)
  if [ \"$NOW\" -ge \"$DL\" ]; then blocked 'The 60-minute merge budget is exhausted; the merge phase was abandoned before CI settled.'; fi
  gh pr checks \"$PR\" --json name,state,bucket,link > /tmp/fabro/checks.json 2>/dev/null || echo '[]' > /tmp/fabro/checks.json
  TOTAL=$(jq length /tmp/fabro/checks.json 2>/dev/null || echo 0)
  PEND=$(jq '[.[] | select(.bucket == \"pending\")] | length' /tmp/fabro/checks.json 2>/dev/null || echo 99)
  if [ \"$TOTAL\" = 0 ]; then
    if [ \"$NOW\" -ge \"$GRACE\" ]; then blocked 'No CI checks were reported on the PR head within five minutes; a merge with no evidence is refused.'; fi
  elif [ \"$PEND\" = 0 ]; then
    break
  fi
  sleep 30
done
BAD=$(jq '[.[] | select(.bucket != \"pass\" and .bucket != \"skipping\")] | length' /tmp/fabro/checks.json 2>/dev/null || echo 99)
if [ \"$BAD\" = 0 ]; then
  echo '{\"context_updates\":{\"checks_ok\":true,\"checks_blocked\":false,\"gh_fix_attempts\":'$A'}}'
  exit 0
fi
NAMES=$(jq -r '[.[] | select(.bucket != \"pass\" and .bucket != \"skipping\") | .name] | join(\", \")' /tmp/fabro/checks.json 2>/dev/null || echo unknown)
if [ \"$A\" -ge 2 ]; then
  blocked 'CI is still failing after two fix attempts ('\"$NAMES\"'); a human must merge this PR.'
fi
A=$((A+1))
echo $A > /tmp/fabro/gh_fix_attempts
rm -f /tmp/fabro/review/ci_fix_result.json
{
  echo '## Failing GitHub checks'
  echo
  echo \"$NAMES\"
  echo
  jq -r '[.[] | select(.bucket != \"pass\" and .bucket != \"skipping\") | .link] | .[]' /tmp/fabro/checks.json 2>/dev/null \
    | sed 's|.*/actions/runs/||; s|/.*||' | sort -u | while IFS= read -r RID; do
      case \"$RID\" in '' | *[!0-9]*) continue ;; esac
      echo '### Failing log for run '$RID
      echo
      gh run view \"$RID\" --log-failed 2>/dev/null | tail -c 20000 || echo '(logs unavailable)'
      echo
    done
} > /tmp/fabro/feedback/ci_fix.md
echo '{\"context_updates\":{\"checks_ok\":false,\"checks_blocked\":false,\"gh_fix_attempts\":'$A'}}'
exit 0"]
  ```
  Replace it with exactly:
  ```
    watch_checks [label="Watch GitHub checks", shape=parallelogram, timeout="50m", output_schema="routing",
        script="PR=$(cat /tmp/fabro/pr_number)
A=$(cat /tmp/fabro/gh_fix_attempts 2>/dev/null || echo 0)
DL=$(cat /tmp/fabro/merge_deadline 2>/dev/null || echo 0)
T0=$(date +%s)
blocked() {
  printf '%s' \"$1\" > /tmp/fabro/merge_block_reason
  echo '{\"context_updates\":{\"checks_ok\":false,\"checks_blocked\":true,\"gh_fix_attempts\":'$A'}}'
  exit 0
}
while : ; do
  GRACE=$(( $(date +%s) + 300 ))
  while : ; do
    NOW=$(date +%s)
    if [ \"$NOW\" -ge \"$DL\" ]; then blocked 'The 60-minute merge budget is exhausted; the merge phase was abandoned before CI settled.'; fi
    gh pr checks \"$PR\" --json name,state,bucket,link > /tmp/fabro/checks.json 2>/dev/null || echo '[]' > /tmp/fabro/checks.json
    TOTAL=$(jq length /tmp/fabro/checks.json 2>/dev/null || echo 0)
    PEND=$(jq '[.[] | select(.bucket == \"pending\")] | length' /tmp/fabro/checks.json 2>/dev/null || echo 99)
    if [ \"$TOTAL\" = 0 ]; then
      if [ \"$NOW\" -ge \"$GRACE\" ]; then blocked 'No CI checks were reported on the PR head within five minutes; a merge with no evidence is refused.'; fi
    elif [ \"$PEND\" = 0 ]; then
      break
    fi
    sleep 30
  done
  BAD=$(jq '[.[] | select(.bucket != \"pass\" and .bucket != \"skipping\")] | length' /tmp/fabro/checks.json 2>/dev/null || echo 99)
  if [ \"$BAD\" = 0 ]; then
    echo '{\"context_updates\":{\"checks_ok\":true,\"checks_blocked\":false,\"gh_fix_attempts\":'$A'}}'
    exit 0
  fi
  NOW=$(date +%s)
  if [ -f /tmp/fabro/ci_rerun_done ]; then break; fi
  if [ $((DL - NOW)) -lt 1800 ]; then echo 'not rerunning: under 30 minutes of the merge budget left'; break; fi
  if [ $((NOW - T0)) -ge 1200 ]; then echo 'not rerunning: this node has already waited 20 minutes'; break; fi
  : > /tmp/fabro/ci_rerun_done
  RERAN=0
  for RID in $(jq -r '[.[] | select(.bucket != \"pass\" and .bucket != \"skipping\") | .link] | .[]' /tmp/fabro/checks.json 2>/dev/null | sed 's|.*/actions/runs/||; s|/.*||' | sort -u); do
    case \"$RID\" in '' | *[!0-9]*) continue ;; esac
    if gh run rerun \"$RID\" --failed; then RERAN=$((RERAN+1)); fi
  done
  if [ \"$RERAN\" = 0 ]; then echo 'nothing to rerun: no failing check is a GitHub Actions run'; break; fi
  echo 'reran the failed jobs of '$RERAN' Actions run(s) once; waiting for them before calling ci_fix'
  sleep 60
done
NAMES=$(jq -r '[.[] | select(.bucket != \"pass\" and .bucket != \"skipping\") | .name] | join(\", \")' /tmp/fabro/checks.json 2>/dev/null || echo unknown)
if [ \"$A\" -ge 2 ]; then
  blocked 'CI is still failing after two fix attempts ('\"$NAMES\"'); a human must merge this PR.'
fi
A=$((A+1))
echo $A > /tmp/fabro/gh_fix_attempts
rm -f /tmp/fabro/review/ci_fix_result.json
{
  echo '## Failing GitHub checks'
  echo
  echo \"$NAMES\"
  echo
  jq -r '[.[] | select(.bucket != \"pass\" and .bucket != \"skipping\") | .link] | .[]' /tmp/fabro/checks.json 2>/dev/null \
    | sed 's|.*/actions/runs/||; s|/.*||' | sort -u | while IFS= read -r RID; do
      case \"$RID\" in '' | *[!0-9]*) continue ;; esac
      echo '### Failing log for run '$RID
      echo
      gh run view \"$RID\" --log-failed 2>/dev/null | tail -c 20000 || echo '(logs unavailable)'
      echo
    done
} > /tmp/fabro/feedback/ci_fix.md
echo '{\"context_updates\":{\"checks_ok\":false,\"checks_blocked\":false,\"gh_fix_attempts\":'$A'}}'
exit 0"]
  ```
  What changed:
  - `T0=$(date +%s)` records when the node started.
  - `GRACE=…` moved inside a new outer `while : ; do … done`, so the 5-minute "no checks
    yet" grace starts again after a rerun.
  - After the inner poll loop, a green result exits as before. A red result reruns once
    when all of these hold: no `/tmp/fabro/ci_rerun_done` marker exists,
    `DL - NOW >= 1800`, and `NOW - T0 < 1200`. The marker is written **before** the
    rerun, so a crash cannot cause a second one. `sleep 60` gives GitHub time to mark the
    rerun jobs as queued before the next poll.
  - Everything from `NAMES=` to the end is byte-for-byte unchanged.
  - The only backslashes are `\"` and the one pre-existing line-continuation `\` at the
    end of the `jq -r … 2>/dev/null \` line, which was already there and works.

- **Edit 2: `merge_gate`.** Change
  ```
    echo 0 > /tmp/fabro/gh_fix_attempts
    echo 0 > /tmp/fabro/strict_retry
  ```
  to
  ```
    echo 0 > /tmp/fabro/gh_fix_attempts
    rm -f /tmp/fabro/ci_rerun_done
    echo 0 > /tmp/fabro/strict_retry
  ```

- **Edit 3: comment.** Add these lines at the **end** of the `//` comment block directly
  above `watch_checks [label=…`:
  ```
      //
      // One rerun before ci_fix (ADR 0011 D2). When the checks settle red, the failed jobs
      // of each failing Actions run are rerun ONCE per merge phase (`ci_rerun_done`,
      // cleared by merge_gate), and only while >= 30 min of merge_deadline remain and this
      // node has run < 20 min -- so a real failure still leaves ci_fix its budget, and the
      // second wait cannot reach this node's 50m timeout, which routes to mark_needs_human.
      // 4 of 7 "CI-fix could not fix" blocks in the 2026-09-24 review were flakes that
      // passed on a plain rerun (writers-app#966, womens-fantasy-sports#1245/#1246,
      // lawncare-saas#2608).
  ```

- **Edit 4: test section.** Insert it into `ops/test-task-gates.sh` directly before the
  final block that begins `# ------…` / `echo ""` / `if [ "$FAIL" -eq 0 ]; then`. Harness
  helpers it uses, already defined at the top of the file: `extract_from "$SHARED" <node>`
  prints a node's script from `review-merge.fabro`; `lastjson "$OUT"` prints the last
  JSON object; `check "<name>" "<expected>" "<actual>"` records ok/FAIL; `$WORK` is the
  scratch root.
  ```bash
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

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE
  ```

- Interfaces and names: the context keys (`checks_ok`, `checks_blocked`,
  `gh_fix_attempts`) and their meanings are unchanged. New file:
  `/tmp/fabro/ci_rerun_done`, an empty marker.
- Verified external contracts: `gh pr checks <pr> --json name,state,bucket,link` returns
  a JSON array. `bucket` is one of `pass`, `fail`, `pending`, `skipping`, `cancel`
  (unchanged from today's node). `gh run rerun <id> --failed` exits 0 when it has queued
  the rerun.
- Behavior rules: at most one `gh run rerun` batch per merge phase. A failing check
  with no Actions run id falls through to `ci_fix` at once.
- Error and security rules: a failed `gh run rerun` is not counted. If none succeeded,
  the node goes straight to `ci_fix`.

## Acceptance Criteria
- [ ] `watch_checks` matches the replacement text exactly, and everything from `NAMES=`
      down is unchanged.
- [ ] `merge_gate` removes `/tmp/fabro/ci_rerun_done`.
- [ ] `./ops/test-task-gates.sh` passes with 14 more checks than before this task.

## Test Expectations
- Framework: the bash harness `ops/test-task-gates.sh`. Command: `./ops/test-task-gates.sh`.
- Cases and expected literals (all in the section above):
  1. fail then pass: `checks_ok` = `true`; `gh.log` has exactly one line
     `run rerun 111 --failed`; no `gh_fix_attempts` file.
  2. fail then fail: last JSON gives `false:1`; one rerun; `ci_fix.md` contains
     `the failing log`.
  3. marker present and `gh_fix_attempts`=1: no rerun; `gh_fix_attempts` = `2`.
  4. deadline 1000 s away: no rerun; output contains `under 30 minutes`.
  5. stub clock advancing 700 s per `date +%s` call: no rerun; output contains
     `already waited 20 minutes`.
  6. non-Actions link `https://github.com/o/r/runs/5`: no rerun; `gh_fix_attempts` = `1`.
- This section was run against a scratch copy with Edits 1 and 2 applied (2026-09-24),
  and all 14 checks passed.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: 11 (validate)

## Labels
`enhancement`, `review-merge`, `priority:high`

## Estimate
Small

## Risk
2 - This adds one wait before `ci_fix` on a real failure. It is bounded by two guards,
and routing is unchanged.

## Validator Stopping Point
`./ops/test-task-gates.sh` passes, and its "review-merge watch_checks" section prints 14
`ok` lines.
