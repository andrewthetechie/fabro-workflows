# An Architecture issue is never merged automatically

## Tracer-Bullet Outcome
A `backlog` run that works an issue labelled `architecture` opens its PR with the
`architecture` label. The shared merge phase then refuses to auto-merge that PR. It
reports `blocked` with the reason, so the existing `report_blocked` Discord hook asks a
human to merge it. A Remainder issue filed for an Architecture issue keeps the label, so
its PR is blocked too. A PR without the label merges exactly as before.

## User Story
As the operator, I want every refactor that an architecture review proposed to wait for
my merge click, so that no LLM-proposed change reaches `main` (and, on `lawncare-saas`,
production) without a human.

## Description
**Before you edit any `.fabro` file, invoke the `/fabro-workflow` skill.** Read
`docs/architecture-review/00-overview-and-contracts.md` first (decision 14, contract
C3, rules 1 to 12). This task does not depend on any other task in the series.

1. `.fabro/workflows/backlog/workflow.fabro`, node `open_pr`: add the label step.
2. `.fabro/workflows/_shared/review-merge/review-merge.fabro`, node `merge_gate`: add
   check 2b.
3. `.fabro/workflows/backlog/workflow.fabro`, node `file_remainder`: copy the label.
4. `ops/test-task-gates.sh`: add the checks below.
5. `AGENTS.md`: add one row to the *Deployment invariants* table (below).

## Context Pack
- Source decisions: ADR 0012 D8, overview decision 14.
- Repo facts:
  - `claim` in `backlog` writes `/tmp/fabro/issue.json` with
    `gh issue view $ISSUE --json state,number,title,body,labels`. `.labels` is an array
    of objects with a `name` field. Test fixtures often have no `labels` key, which is
    why every read here uses `.labels[]?`.
  - `open_pr` runs under `set -e`. It sets `B=$(git rev-parse --abbrev-ref HEAD)` and
    names the branch in every `gh pr` call, because the sandbox clone is single-branch
    and `gh` cannot infer it (`AGENTS.md`). The new call is `gh pr edit "$B" ...` for the
    same reason. The existing harness check "every pr view names branch" only reads
    `pr view` lines.
  - `open_pr` today contains this block, verbatim. The new lines go immediately after it:
    ```
if [ -z \"$PR_URL\" ]; then
  echo 'no PR is visible for head branch '\"$B\"' after create; refusing to report an empty pr_url' >&2
  exit 1
fi
    ```
  - `merge_gate` numbers its checks with inline `# N.` lines. This node is the one
    place where the repository keeps `#` comments inside a script, and the DOT comment
    above it says so. Keep that style for 2b. Its helper is
    `fail() { E=false; printf '%s' "$1" > /tmp/fabro/merge_block_reason; }`, and each
    check is guarded by `if [ "$E" = true ]`. Check 2 today, verbatim:
    ```
    # 2. agent-authored label
    if [ \"$E\" = true ]; then
      L=$(gh pr view \"$PR\" --json labels --jq '[.labels[].name] | index(\"agent-authored\") != null' 2>/dev/null || echo bad)
      if [ \"$L\" != true ]; then fail 'The PR does not carry the agent-authored label, so it is not eligible for automatic merge.'; fi
    fi
    ```
  - `merge_gate`'s ineligible path publishes `{"context_updates":{"merge_eligible":false}}`,
    and the edge `merge_gate -> report_blocked [condition="context.merge_eligible=false"]`
    sends it to `report_blocked`, which posts the reason on the PR. The
    `discord-review-blocked` hooks in both `backlog` and `pr-review` already fire on it.
  - `file_remainder` today files the issue with this line, verbatim:
    ```
    URL=$(gh issue create --title \"Remainder of #$N: $T\" --body-file /tmp/fabro/remainder_body.md --label agent-remainder --label ai-generated 2>/dev/null) || URL=''
    ```
    Its early exits (nothing to file, already filed) come before this line and must stay
    free of `gh` calls: the harness checks "no remainder: no gh call".
- Non-goals: do not change the scheduler, the other merge checks, `render.jq`, or any
  `workflow.toml`. Do not add the label anywhere else.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft.

## Implementation Contract
- Expected files: `.fabro/workflows/backlog/workflow.fabro`,
  `.fabro/workflows/_shared/review-merge/review-merge.fabro`, `ops/test-task-gates.sh`,
  `AGENTS.md`.
- Interfaces and names: label `architecture`, colour `0E8A16`, description
  `Architecture review: a deepening candidate (ADR 0012)` (contract C3).
- Verified external contracts: None new. `gh pr edit <branch> --add-label <name>` and
  `gh label create` are the same commands the graphs use today.
- Behavior rules:
  - Check 2b fails closed: any answer other than `false`, including an error, blocks the
    merge.
  - In `open_pr`, a failed `gh pr edit` fails the node (under `set -e`), which routes to
    `human_rescue`. A PR that should carry the label must never be left without it.
- Error and security rules: None beyond the fail-closed rules above.

### Edit 1: `open_pr`, insert immediately after the block quoted in Repo facts

```
if jq -e '[.labels[]?.name] | index(\"architecture\") != null' /tmp/fabro/issue.json > /dev/null 2>&1; then
  gh label create architecture --color 0E8A16 --description 'Architecture review: a deepening candidate (ADR 0012)' 2>/dev/null || true
  gh pr edit \"$B\" --add-label architecture
fi
```

### Edit 2: `merge_gate`, insert immediately before the line `# 3. open and not draft`

```
# 2b. architecture label: a human merges it (ADR 0012 D8)
if [ \"$E\" = true ]; then
  AR=$(gh pr view \"$PR\" --json labels --jq '[.labels[].name] | index(\"architecture\") != null' 2>/dev/null || echo bad)
  if [ \"$AR\" != false ]; then fail 'This PR implements an Architecture issue (label architecture). ADR 0012 requires a human to merge it.'; fi
fi
```

Then, in the `//` DOT comment above `merge_gate` (it begins "The twelve checks are marked
inline"), change "The twelve checks" to "The thirteen checks (1 to 12, and 2b)".

### Edit 3: `file_remainder`, replace the `URL=$(gh issue create ...` line with

```
AR=''
if jq -e '[.labels[]?.name] | index(\"architecture\") != null' /tmp/fabro/issue.json > /dev/null 2>&1; then
  gh label create architecture --color 0E8A16 --description 'Architecture review: a deepening candidate (ADR 0012)' 2>/dev/null || true
  AR='--label architecture'
fi
URL=$(gh issue create --title \"Remainder of #$N: $T\" --body-file /tmp/fabro/remainder_body.md --label agent-remainder --label ai-generated $AR 2>/dev/null) || URL=''
```

### `AGENTS.md` row

Add this row at the end of the *Deployment invariants* table:

```
| An `architecture`-labelled PR is never auto-merged: `open_pr` copies the label from the issue, `merge_gate` check 2b blocks on it, and `file_remainder` copies it to the Remainder issue | An architecture review (ADR 0012) proposes a refactor, triage promotes it, `backlog` implements it and the merge phase would squash it into `main` with no human anywhere in the chain. On `lawncare-saas` that merge is also a deploy. Check 2b fails closed on a `gh` error. Covered by `ops/test-task-gates.sh`. |
```

## Acceptance Criteria
- [ ] `open_pr` adds `architecture` to the PR when the issue has it, and only then.
- [ ] `merge_gate` blocks a PR labelled `architecture`, and its reason contains
      `Architecture issue`.
- [ ] `file_remainder` passes `--label architecture` when the parent issue has it.
- [ ] `./ops/test-task-gates.sh` passes, with 7 more checks than before this task
      (`PASS: 321 checks` when tasks 01 to 04 are merged).

## Test Expectations
Framework: the bash harness `ops/test-task-gates.sh`. Run `./ops/test-task-gates.sh`.

1. In the `open_pr` section, insert this immediately after the check named
   `"no pr_url on failure"`:

```bash
# 4. An Architecture issue's PR gets the label (ADR 0012 D8); a plain one does not.
op_setup
printf '%s' '{"number":350,"title":"t","labels":[{"name":"architecture"}]}' > "$T/issue.json"
sh "$T/open_pr.sh" >/dev/null 2>&1
check "architecture PR: labelled"  "1" "$(grep -c "^pr edit $BR --add-label architecture" "$T/gh.log")"
op_setup
sh "$T/open_pr.sh" >/dev/null 2>&1
check "plain PR: not labelled"     "0" "$(grep -c 'add-label architecture' "$T/gh.log")"
```

2. In the `task budget and remainder` section, insert this immediately after the check
   named `"create fails: nothing recorded"`:

```bash
# 10. An Architecture issue's remainder keeps the label (ADR 0012 D8).
rm -f "$T/remainder_issue" "$T/create_fails"; : > "$T/gh.log"
printf '%s' '{"number":350,"title":"Big issue","labels":[{"name":"architecture"}]}' > "$T/issue.json"
sh "$T/file_remainder.sh" >/dev/null 2>&1
check "architecture remainder: labelled" "1" "$(grep '^issue create' "$T/gh.log" | grep -c -- '--label architecture')"
```

3. Insert this section immediately before the final `echo ""` and
   `if [ "$FAIL" -eq 0 ]; then` block. `$SHARED` already names
   `_shared/review-merge/review-merge.fabro`.

```bash
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
```

That is 7 new `check` calls: 2 in `open_pr`, 1 in the remainder section, and 4 in the new
section.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: 06 (schedules must not be enabled before this block exists), 07

## Labels
`feature`, `review-merge`, `priority:high`

## Estimate
Small

## Risk
2 - it adds one fail-closed check to the shared merge phase, which both `backlog` and
`pr-review` import. A PR without the label passes the check.

## Validator Stopping Point
`./ops/test-task-gates.sh` prints `PASS` with 7 more checks than before.
