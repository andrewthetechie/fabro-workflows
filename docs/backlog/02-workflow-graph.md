# Task 02 — Write the new `workflow.fabro` (full redesign)

**Depends on:** 00 (read it first). **Blocks:** 08, 09.
**LLM level:** **smarter LLM recommended.** The graph is fully specified below — the
job is faithful transcription, then fixing any `fabro validate` complaints in task 08.
DOT escaping mistakes are the main risk.

> **The graph below is the transcription source, not the current graph.** It has
> moved on since — timeouts were resized from measurement, `_shared/review-merge/`
> was factored out, and `improve_gate` gained a `split` disposition. Read
> `.fabro/workflows/backlog/workflow.fabro` for what actually runs; it carries the
> reasoning for each change inline.

## Goal

Replace `~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/workflow.fabro`
with the redesigned graph: deterministic acquire/claim, file-backed agent contracts
with jq gates, sequential per-task loop, 4-tier rework escalation, extra review
rounds, and deterministic delivery.

## Critical authoring rules (violations break the run)

- No `#` **comments** in the DOT — fabro's lexer only strips `//` and `/* */`, so a
  `#` used as a comment is a parse error. Use `//`.
- A `#` **inside a double-quoted string is fine and is required in several places**
  (`#'$N'` in the PR title, `Resolves #'$N'`, `## ` markdown headings written into
  feedback and PR-body files). Verified against the container's fabro 0.354.0-nightly:
  a graph with `#` inside `script="..."` validates clean. Do not strip them.
- Multi-line double-quoted strings are legal (see `model_stylesheet` below).
- Any literal `"` inside a DOT string MUST be written as `\"`.
- **No other backslashes anywhere in scripts** — no `\$`, no regex escapes. Every
  script below is already written to comply; do not "fix" jq/sed quoting.
- Scripts must be POSIX sh (no `[[ ]]`, no `pipefail`, no arrays, no `local`).
- Conditions support `=`, `!=`, `>`, `<`, `>=`, `<=`, `&&`, `||`, `!`, `contains`,
  and parentheses-free precedence (`&&` binds tighter than `||`). Context values
  set by gate JSON are compared as strings (`true`/`false`) or numbers.
- Do NOT add `max_visits` to per-task stages (`improve`, `review`): visit counts
  persist across tasks within one run and would break multi-task runs. Retry
  bounding is done by the `*_attempts` counter files in the gates (max 2 per task).
- **Every command node that prints `context_updates` MUST carry
  `output_schema="routing"`.** This is a reserved keyword
  (`handler/structured_output.rs` `ROUTING_KEYWORD`), not a JSON schema, and it is
  what enables fabro to scan the node's stdout for routing JSON. Without it the
  `context_updates` are **silently ignored** and every gate's routing is inert — the
  run walks its unconditional edges and nothing works, with no error anywhere.
  14 nodes need it: `claim`, `decompose_gate`, `next_task`, `resolve_merge_gate`,
  `improve_gate`, `prep_review`, `review_gate`, `rework_router`, `extra_prep`,
  `standards_gate`, `spec_gate`, `quality_gate`, `extra_gate`, `open_pr`.
- Do NOT put `output_schema` on any **agent** node, and never use a value other than
  `"routing"`. A custom schema on an agent node is the original P0 bug: it disables
  the routing scan and stores the validated object where conditions cannot read it.

## The complete graph

Write exactly this content to
`~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/workflow.fabro`:

```dot
digraph Backlog {
    graph [
        goal="Backlog: decompose the next agent-labeled issue, implement each task with review, and open a PR",
        model_stylesheet="
            *                { model: coders; }
            .decomp          { model: glm-5.3; }
            .improve         { model: glm-5.3; }
            .coder           { model: coders; }
            .coder-t2        { model: glm-4.7; }
            .coder-t3        { model: kimi-for-coding; }
            .coder-t4        { model: glm-5.3; }
            .review          { model: glm-5.3; }
            .review-frontier { model: kimi-k3; }
        "
    ]
    rankdir=LR

    start [shape=Mdiamond, label="Start"]
    exit  [shape=Msquare, label="Exit"]

    // ---- Deterministic acquisition (no LLM) ----
    acquire [label="Acquire issue", shape=parallelogram,
        script="mkdir -p /tmp/fabro && gh issue list --label agent --state open --json number,title,labels --limit 10 > /tmp/fabro/candidates.json && jq '[.[] | select(([.labels[].name] | contains([\"agent-in-progress\"]) | not))] | sort_by(.number) | .[0:1]' /tmp/fabro/candidates.json > /tmp/fabro/acquire.json && test \"$(jq length /tmp/fabro/acquire.json)\" != \"0\""]

    claim [label="Claim issue and fetch it", shape=parallelogram, output_schema="routing",
        script="set -e
N=$(jq -r '.[0].number' /tmp/fabro/acquire.json)
gh label create agent-in-progress --color FBCA04 2>/dev/null || true
gh label create agent-stuck --color D93F0B 2>/dev/null || true
gh label create agent-authored --color 1D76DB 2>/dev/null || true
gh label create Review --color 0E8A16 2>/dev/null || true
gh issue edit $N --add-label agent-in-progress --remove-label agent
gh issue view $N --json number,title,body,labels,comments > /tmp/fabro/issue.json
echo '{\"context_updates\":{\"issue_number\":'$N'}}'"]

    prep [label="Prep workspace and install deps", shape=parallelogram, timeout="20m",
        script="set -e
mkdir -p /tmp/fabro/review /tmp/fabro/feedback /tmp/fabro/extra
echo 0 > /tmp/fabro/extra_round
./.fabro/setup.sh"]

    // ---- Decomposition (agent writes file; gate validates) ----
    decompose [label="Decompose issue", class="decomp", prompt="@prompts/decompose.md.j2"]

    decompose_gate [label="Validate decomposition", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/decomposition.json
A=$(cat /tmp/fabro/decompose_attempts 2>/dev/null || echo 0)
S=$(jq -r .status $F 2>/dev/null || echo invalid)
if [ \"$S\" != issues ] && [ \"$S\" != no_work ] && [ \"$S\" != needs_human_review ]; then S=invalid; fi
if [ \"$S\" = issues ]; then
  T=$(jq -r '.issues | type' $F 2>/dev/null || echo none)
  N=$(jq '.issues | length' $F 2>/dev/null || echo 0)
  BAD=$(jq '[.issues[] | select((.id | type) != \"string\" or (.title | type) != \"string\" or (.body | type) != \"string\" or (.files | type) != \"array\" or (.id | length) == 0 or (.title | length) == 0 or (.body | length) == 0)] | length' $F 2>/dev/null || echo 99)
  if [ \"$T\" != array ] || [ \"$N\" = 0 ] || [ \"$BAD\" != 0 ]; then S=invalid; fi
fi
if [ \"$S\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/decompose_attempts
  if [ $A -lt 2 ]; then echo 'decomposition.json missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
fi
echo 0 > /tmp/fabro/decompose_attempts
if [ \"$S\" = issues ]; then
  jq '[.issues[] | . + {source: \"decompose\"}]' $F > /tmp/fabro/tasks.json
  echo 0 > /tmp/fabro/task_index
fi
CNT=$(jq '.issues | length' $F 2>/dev/null || echo 0)
echo '{\"context_updates\":{\"decomp_status\":\"'$S'\",\"task_count\":'$CNT'}}'"]

    // ---- Sequential task loop ----
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

    // ---- Mainline conflict resolution ----
    // next_task aborted the failed merge and left a clean tree. A conflicted merge
    // cannot cross a stage boundary: fabro's checkpoint would `git add -A` the
    // conflict-marked files, which git treats as resolved, and commit markers onto
    // the run branch. So the agent performs the merge itself, in one stage, and
    // resolve_merge_gate verifies the result from git rather than trusting a claim.
    resolve_merge [label="Resolve the mainline merge", class="coder-t4", prompt="@prompts/resolve_merge.md.j2"]

    // The conflict-marker scan covers what this merge can have touched — the files
    // changed since the pre-merge SHA, dirty tracked files, and untracked files the
    // next checkpoint would `git add -A` — rather than the whole worktree, which
    // would walk build output and would fire on any repository that legitimately
    // commits a conflict-marker fixture.
    resolve_merge_gate [label="Verify the mainline merge", shape=parallelogram, output_schema="routing",
        script="PRE=$(cat /tmp/fabro/pre_merge_sha 2>/dev/null || echo '')
A=$(cat /tmp/fabro/merge_attempts 2>/dev/null || echo 0)
BAD=0
if [ -e .git/MERGE_HEAD ]; then BAD=1; fi
if git diff --name-only --diff-filter=U | grep -q . ; then BAD=1; fi
: > /tmp/fabro/merge_scan.txt
if [ -n \"$PRE\" ]; then git diff --name-only \"$PRE\" HEAD >> /tmp/fabro/merge_scan.txt 2>/dev/null || true; fi
git diff --name-only HEAD >> /tmp/fabro/merge_scan.txt 2>/dev/null || true
git ls-files --others --exclude-standard >> /tmp/fabro/merge_scan.txt 2>/dev/null || true
while IFS= read -r f; do
  [ -f \"$f\" ] || continue
  if grep -qI '<<<<<<< ' \"$f\"; then BAD=1; fi
done < /tmp/fabro/merge_scan.txt
if ! git merge-base --is-ancestor origin/main HEAD; then BAD=1; fi
if [ $BAD -ne 0 ]; then
  git merge --abort 2>/dev/null || true
  if [ -n \"$PRE\" ]; then git reset --hard \"$PRE\" || true; fi
  A=$((A+1))
  echo $A > /tmp/fabro/merge_attempts
  echo 'The merge agent left conflict markers, an in-progress merge, or a branch that still does not contain origin/main.' > /tmp/fabro/needs_human_reason
  echo '{\"context_updates\":{\"merge_ok\":false,\"merge_attempts\":'$A'}}'
else
  git rev-parse HEAD > /tmp/fabro/task_base_sha
  echo '{\"context_updates\":{\"merge_ok\":true,\"merge_attempts\":'$A'}}'
fi"]

    improve [label="Improve task spec", class="improve", prompt="@prompts/improve.md.j2"]

    improve_gate [label="Validate improved task", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/improve_result.json
A=$(cat /tmp/fabro/improve_attempts 2>/dev/null || echo 0)
D=$(jq -r .disposition $F 2>/dev/null || echo invalid)
if [ \"$D\" != ready ] && [ \"$D\" != redundant ] && [ \"$D\" != needs_human ]; then D=invalid; fi
if [ \"$D\" = ready ]; then
  jq -e '.task and ((.task.title | type) == \"string\") and ((.task.body | type) == \"string\") and ((.task.body | length) > 0)' $F >/dev/null 2>&1 || D=invalid
fi
if [ \"$D\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/improve_attempts
  if [ $A -lt 2 ]; then echo 'improve_result.json missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
fi
echo 0 > /tmp/fabro/improve_attempts
if [ \"$D\" = ready ]; then jq .task $F > /tmp/fabro/current_task.json; fi
echo '{\"context_updates\":{\"task_disposition\":\"'$D'\"}}'"]

    coder [label="Implement task (tier 1)", class="coder", prompt="@prompts/coder.md.j2"]

    prep_review [label="Prepare review inputs", shape=parallelogram, output_schema="routing",
        script="set -e
BASE=$(cat /tmp/fabro/task_base_sha)
git diff $BASE..HEAD > /tmp/fabro/review/diff.patch
git diff --stat $BASE..HEAD > /tmp/fabro/review/diffstat.txt
git diff --name-only $BASE..HEAD > /tmp/fabro/review/changed_files.txt
BYTES=$(wc -c < /tmp/fabro/review/diff.patch | tr -d ' ')
if [ $BYTES -gt 2000000 ]; then
  echo '## Diff too large' > /tmp/fabro/feedback/rework.md
  echo 'The change diff is '$BYTES' bytes (limit 2000000). Split or shrink the change so it stays reviewable, then continue the current task in /tmp/fabro/current_task.json.' >> /tmp/fabro/feedback/rework.md
  echo '{\"context_updates\":{\"diff_too_large\":true}}'
else
  echo '{\"context_updates\":{\"diff_too_large\":false}}'
fi"]

    validate [label="Validate (setup + ci)", shape=parallelogram, timeout="20m",
        script="./.fabro/setup.sh > /tmp/fabro/validate_output.log 2>&1 && ./.fabro/ci.sh >> /tmp/fabro/validate_output.log 2>&1
RC=$?
tail -c 8000 /tmp/fabro/validate_output.log
if [ $RC -ne 0 ]; then
  echo '## Validation failed (exit '$RC'): ./.fabro/setup.sh && ./.fabro/ci.sh' > /tmp/fabro/feedback/rework.md
  echo >> /tmp/fabro/feedback/rework.md
  tail -c 6000 /tmp/fabro/validate_output.log >> /tmp/fabro/feedback/rework.md
  exit 1
fi"]

    review [label="Review task", class="review", prompt="@prompts/reviewer.md.j2"]

    review_gate [label="Validate verdict", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/review/verdict.json
A=$(cat /tmp/fabro/review_attempts 2>/dev/null || echo 0)
D=$(jq -r .decision $F 2>/dev/null || echo invalid)
if [ \"$D\" != approved ] && [ \"$D\" != changes_requested ] && [ \"$D\" != needs_human_review ]; then D=invalid; fi
if [ \"$D\" = changes_requested ]; then
  B=$(jq '[.findings[]? | select(.severity == \"blocking\")] | length' $F 2>/dev/null || echo 0)
  if [ \"$B\" = 0 ]; then D=invalid; fi
fi
if [ \"$D\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/review_attempts
  if [ $A -lt 2 ]; then echo 'verdict.json is missing, malformed, or requests changes with no blocking finding; rewrite it exactly per the contract' >&2; exit 1; fi
fi
echo 0 > /tmp/fabro/review_attempts
if [ \"$D\" = changes_requested ]; then
  echo '## Review findings to fix' > /tmp/fabro/feedback/rework.md
  jq -r '.summary // empty' $F >> /tmp/fabro/feedback/rework.md
  echo >> /tmp/fabro/feedback/rework.md
  jq -r '.findings[]? | select(.severity == \"blocking\") | .message' $F | sed 's/^/- /' >> /tmp/fabro/feedback/rework.md
fi
echo '{\"context_updates\":{\"review_decision\":\"'$D'\"}}'"]

    integrate [label="Integrate task", shape=parallelogram,
        script="T=$(jq -r .title /tmp/fabro/current_task.json)
S=$(jq -r '.summary // empty' /tmp/fabro/review/verdict.json 2>/dev/null || true)
{ echo '### Task: '$T; echo '- Review: '$S; echo; } >> /tmp/fabro/completed.md
echo integrated"]

    rework_router [label="Escalate rework round", shape=parallelogram, output_schema="routing",
        script="R=$(cat /tmp/fabro/round 2>/dev/null || echo 0)
R=$((R+1))
echo $R > /tmp/fabro/round
echo '{\"context_updates\":{\"round\":'$R'}}'"]

    rework_t1 [label="Rework (tier 1: coders)", class="coder", prompt="@prompts/rework.md.j2"]
    rework_t2 [label="Rework (tier 2: glm-4.7)", class="coder-t2", prompt="@prompts/rework.md.j2"]
    rework_t3 [label="Rework (tier 3: kimi-for-coding)", class="coder-t3", prompt="@prompts/rework.md.j2"]
    rework_t4 [label="Rework (tier 4: glm-5.3)", class="coder-t4", prompt="@prompts/rework.md.j2"]

    // ---- Extra review rounds (whole branch) ----
    extra_prep [label="Prepare extra review", shape=parallelogram, output_schema="routing",
        script="ER=$(cat /tmp/fabro/extra_round 2>/dev/null || echo 0)
if [ $ER -ge 2 ]; then echo '{\"context_updates\":{\"extra_done\":true}}'; exit 0; fi
echo $((ER+1)) > /tmp/fabro/extra_round
git fetch origin main
git diff origin/main...HEAD > /tmp/fabro/extra/diff.patch
git diff --stat origin/main...HEAD > /tmp/fabro/extra/diffstat.txt
git diff --name-only origin/main...HEAD > /tmp/fabro/extra/changed_files.txt
echo '{\"context_updates\":{\"extra_done\":false,\"standards_status\":\"none\",\"spec_status\":\"none\",\"quality_status\":\"none\",\"new_tasks\":0,\"followup_status\":\"none\"}}'"]

    standards [label="Standards review", class="review", prompt="@prompts/standards.md.j2"]
    standards_gate [label="Validate standards output", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/extra/standards.json
A=$(cat /tmp/fabro/standards_attempts 2>/dev/null || echo 0)
D=$(jq -r .decision $F 2>/dev/null || echo invalid)
if [ \"$D\" != approve ] && [ \"$D\" != findings ] && [ \"$D\" != needs_human_review ]; then D=invalid; fi
if [ \"$D\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/standards_attempts
  if [ $A -lt 2 ]; then echo 'standards.json missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
fi
echo 0 > /tmp/fabro/standards_attempts
echo '{\"context_updates\":{\"standards_status\":\"'$D'\"}}'"]

    spec [label="Spec review", class="review-frontier", prompt="@prompts/spec.md.j2"]
    spec_gate [label="Validate spec output", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/extra/spec.json
A=$(cat /tmp/fabro/spec_attempts 2>/dev/null || echo 0)
D=$(jq -r .decision $F 2>/dev/null || echo invalid)
if [ \"$D\" != approve ] && [ \"$D\" != findings ] && [ \"$D\" != needs_human_review ]; then D=invalid; fi
if [ \"$D\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/spec_attempts
  if [ $A -lt 2 ]; then echo 'spec.json missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
fi
echo 0 > /tmp/fabro/spec_attempts
echo '{\"context_updates\":{\"spec_status\":\"'$D'\"}}'"]

    quality [label="Code quality review", class="review", prompt="@prompts/quality.md.j2"]
    quality_gate [label="Validate quality output", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/extra/quality.json
A=$(cat /tmp/fabro/quality_attempts 2>/dev/null || echo 0)
D=$(jq -r .decision $F 2>/dev/null || echo invalid)
if [ \"$D\" != approve ] && [ \"$D\" != findings ] && [ \"$D\" != needs_human_review ]; then D=invalid; fi
if [ \"$D\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/quality_attempts
  if [ $A -lt 2 ]; then echo 'quality.json missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
fi
echo 0 > /tmp/fabro/quality_attempts
echo '{\"context_updates\":{\"quality_status\":\"'$D'\"}}'"]

    extra_decompose [label="Follow-up decomposer", class="review-frontier", prompt="@prompts/extra_decompose.md.j2"]
    extra_gate [label="Merge follow-up tasks", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/extra/followups.json
A=$(cat /tmp/fabro/extra_decompose_attempts 2>/dev/null || echo 0)
S=$(jq -r .status $F 2>/dev/null || echo invalid)
if [ \"$S\" != issues ] && [ \"$S\" != no_work ] && [ \"$S\" != needs_human_review ]; then S=invalid; fi
NEWCNT=0
if [ \"$S\" = issues ]; then
  T=$(jq -r '.issues | type' $F 2>/dev/null || echo none)
  N=$(jq '.issues | length' $F 2>/dev/null || echo 0)
  BAD=$(jq '[.issues[] | select((.id | type) != \"string\" or (.title | type) != \"string\" or (.body | type) != \"string\" or (.files | type) != \"array\" or (.id | length) == 0 or (.title | length) == 0 or (.body | length) == 0)] | length' $F 2>/dev/null || echo 99)
  if [ \"$T\" != array ] || [ \"$N\" = 0 ] || [ \"$BAD\" != 0 ]; then S=invalid; fi
fi
if [ \"$S\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/extra_decompose_attempts
  if [ $A -lt 2 ]; then echo 'followups.json missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
fi
echo 0 > /tmp/fabro/extra_decompose_attempts
if [ \"$S\" = issues ]; then
  OLD=$(jq length /tmp/fabro/tasks.json)
  jq -n --slurpfile cur /tmp/fabro/tasks.json --slurpfile new $F '$cur[0] + [$new[0].issues[] | select(.id as $i | [$cur[0][].id] | index($i) | not) | . + {source: \"extra-review\"}]' > /tmp/fabro/tasks.merged.json
  mv /tmp/fabro/tasks.merged.json /tmp/fabro/tasks.json
  NOW=$(jq length /tmp/fabro/tasks.json)
  NEWCNT=$((NOW-OLD))
fi
echo '{\"context_updates\":{\"followup_status\":\"'$S'\",\"new_tasks\":'$NEWCNT'}}'"]

    // ---- Delivery ----
    open_pr_prep [label="Final aggregate and PR prep", shape=parallelogram, timeout="20m",
        script="set -e
git fetch origin main
git merge --no-edit origin/main
if ./.fabro/setup.sh > /tmp/fabro/validate_output.log 2>&1 && ./.fabro/ci.sh >> /tmp/fabro/validate_output.log 2>&1; then
  tail -c 4000 /tmp/fabro/validate_output.log
else
  tail -c 4000 /tmp/fabro/validate_output.log
  exit 1
fi
N=$(jq -r .number /tmp/fabro/issue.json)
T=$(jq -r .title /tmp/fabro/issue.json)
echo 'agent: '$T' (#'$N')' > /tmp/fabro/pr_title.txt
{ echo '## Summary'; echo; echo 'Resolves #'$N': '$T; echo; cat /tmp/fabro/completed.md 2>/dev/null || true; echo '## Validation'; echo; echo '- ./.fabro/setup.sh && ./.fabro/ci.sh green after final merge with origin/main'; } > /tmp/fabro/pr_body.md"]

    open_pr [label="Push and open PR", shape=parallelogram, output_schema="routing",
        script="set -e
git push -u origin HEAD
N=$(jq -r .number /tmp/fabro/issue.json)
T=$(cat /tmp/fabro/pr_title.txt)
gh pr create --title \"$T\" --body-file /tmp/fabro/pr_body.md --label agent-authored || gh pr view --json url > /dev/null
PR_URL=$(gh pr view --json url --jq .url)
gh issue edit $N --remove-label agent-in-progress --add-label Review || true
gh issue comment $N --body 'Agent run complete. PR: '$PR_URL || true
echo '{\"context_updates\":{\"pr_url\":\"'$PR_URL'\"}}'"]

    close_noop [label="Close (no actionable work)", shape=parallelogram,
        script="N=$(jq -r .number /tmp/fabro/issue.json)
gh issue edit $N --remove-label agent-in-progress || true
gh issue comment $N --body 'Agent run found no actionable decomposition for this issue; leaving it for human triage.' || true
echo done"]

    human_rescue [shape=hexagon, label="Agent stuck — human decision needed"]

    mark_stuck [label="Mark stuck", shape=parallelogram,
        script="N=$(jq -r .number /tmp/fabro/issue.json 2>/dev/null || echo 0)
if [ \"$N\" != 0 ]; then
  gh issue edit $N --remove-label agent-in-progress --add-label agent-stuck || true
  gh issue comment $N --body 'Agent run could not complete this issue and gave up. See the fabro run for details.' || true
fi
echo done"]

    // ---- Edges ----
    start -> acquire
    acquire -> claim
    acquire -> exit                 [condition="outcome=failed"]
    claim -> prep                   [condition="outcome=succeeded"]
    claim -> human_rescue
    prep -> decompose               [condition="outcome=succeeded"]
    prep -> human_rescue

    decompose -> decompose_gate     [condition="outcome=succeeded"]
    decompose -> human_rescue
    decompose_gate -> decompose     [condition="outcome=failed"]
    decompose_gate -> next_task     [condition="context.decomp_status=issues"]
    decompose_gate -> close_noop    [condition="context.decomp_status=no_work"]
    decompose_gate -> human_rescue

    next_task -> extra_prep         [condition="context.tasks_done=true"]
    next_task -> resolve_merge      [condition="context.rebase_failed=true"]
    next_task -> improve

    // A failed node still routes: resolve_merge's unconditional edge leads to its
    // gate, which detects the bad state and either retries once or asks for a
    // human. `weight` keeps the success edge winning over the merge_attempts=1
    // retry edge after a successful second attempt.
    resolve_merge -> resolve_merge_gate [condition="outcome=succeeded"]
    resolve_merge -> resolve_merge_gate
    resolve_merge_gate -> improve       [condition="context.merge_ok=true", weight=10]
    resolve_merge_gate -> resolve_merge [condition="context.merge_attempts=1"]
    resolve_merge_gate -> human_rescue

    improve -> improve_gate         [condition="outcome=succeeded"]
    improve -> human_rescue
    improve_gate -> improve         [condition="outcome=failed"]
    improve_gate -> next_task       [condition="context.task_disposition=redundant"]
    improve_gate -> coder           [condition="context.task_disposition=ready"]
    improve_gate -> human_rescue

    coder -> prep_review            [condition="outcome=succeeded"]
    coder -> human_rescue

    prep_review -> rework_router    [condition="context.diff_too_large=true"]
    prep_review -> validate

    validate -> review              [condition="outcome=succeeded"]
    validate -> rework_router

    review -> review_gate           [condition="outcome=succeeded"]
    review -> human_rescue
    review_gate -> review           [condition="outcome=failed"]
    review_gate -> integrate        [condition="context.review_decision=approved"]
    review_gate -> rework_router    [condition="context.review_decision=changes_requested"]
    review_gate -> human_rescue

    integrate -> next_task

    rework_router -> rework_t1      [condition="context.round<2"]
    rework_router -> rework_t2      [condition="context.round>=2 && context.round<4"]
    rework_router -> rework_t3      [condition="context.round=4"]
    rework_router -> rework_t4      [condition="context.round=5"]
    rework_router -> human_rescue

    rework_t1 -> prep_review        [condition="outcome=succeeded"]
    rework_t1 -> human_rescue
    rework_t2 -> prep_review        [condition="outcome=succeeded"]
    rework_t2 -> human_rescue
    rework_t3 -> prep_review        [condition="outcome=succeeded"]
    rework_t3 -> human_rescue
    rework_t4 -> prep_review        [condition="outcome=succeeded"]
    rework_t4 -> human_rescue

    extra_prep -> open_pr_prep      [condition="context.extra_done=true"]
    extra_prep -> standards

    standards -> standards_gate     [condition="outcome=succeeded"]
    standards -> human_rescue
    standards_gate -> standards     [condition="outcome=failed"]
    standards_gate -> human_rescue  [condition="context.standards_status=needs_human_review || context.standards_status=invalid"]
    standards_gate -> spec

    spec -> spec_gate               [condition="outcome=succeeded"]
    spec -> human_rescue
    spec_gate -> spec               [condition="outcome=failed"]
    spec_gate -> human_rescue       [condition="context.spec_status=needs_human_review || context.spec_status=invalid"]
    spec_gate -> quality

    quality -> quality_gate         [condition="outcome=succeeded"]
    quality -> human_rescue
    quality_gate -> quality         [condition="outcome=failed"]
    quality_gate -> human_rescue    [condition="context.quality_status=needs_human_review || context.quality_status=invalid"]
    quality_gate -> extra_decompose

    extra_decompose -> extra_gate   [condition="outcome=succeeded"]
    extra_decompose -> human_rescue
    extra_gate -> extra_decompose   [condition="outcome=failed"]
    extra_gate -> next_task         [condition="context.new_tasks>0"]
    extra_gate -> human_rescue      [condition="context.followup_status=needs_human_review"]
    extra_gate -> open_pr_prep

    open_pr_prep -> open_pr         [condition="outcome=succeeded"]
    open_pr_prep -> human_rescue
    open_pr -> exit                 [condition="outcome=succeeded"]
    open_pr -> human_rescue

    close_noop -> exit

    human_rescue -> rework_t4       [label="[R] Retry with guidance", freeform=true]
    human_rescue -> open_pr_prep    [label="[P] Accept partial"]
    human_rescue -> mark_stuck      [label="[X] Abandon"]

    mark_stuck -> exit
}
```

## Sanity self-check before finishing (do this by hand)

1. Every agent node (`decompose`, `improve`, `resolve_merge`, `coder`, `review`,
   `rework_t1..4`,
   `standards`, `spec`, `quality`, `extra_decompose`) has a `<node> -> <gate or next>`
   edge with `condition="outcome=succeeded"` **and** an unconditional
   `<node> -> human_rescue` edge.
2. Every gate has a `-> <its agent> [condition="outcome=failed"]` retry edge,
   conditional happy-path edges, and exactly one unconditional edge.
3. Every command node has either a conditional success edge plus an unconditional
   edge, or a single unconditional edge.
4. No `#` **outside** a quoted string. No `output_schema`. No backslashes other than `\"`.
5. The only `{{`/`}}` template syntax anywhere is inside prompt files (tasks 03–06),
   and there only `{{ goal }}` is used.

## Already verified for you

This graph, with the fixes above applied, was extracted and validated against the
**container's** fabro (0.354.0-nightly) during planning: `Workflow: Backlog (35 nodes,
80 edges) — Validation: OK` for the graph as first written; the `resolve_merge` port
of task 08 re-validates at **37 nodes, 85 edges**. All command-node scripts pass
`dash -n`. If your
transcription fails validation, you introduced a typo — diff against the block above
before changing any logic.

## Done when

- The file exists at the path above with exactly this content (whitespace may vary).
- Task 08's `fabro validate` passes (that is where validation happens; if
  transcription introduced DOT errors, fix them there).

## Notes

- `rework_router -> human_rescue` unconditional edge is the round>=6 escape —
  round ticks 1..5 route to tiers, tick 6+ falls through to rescue. That is the
  max-review-rounds=6 policy.
- `human_rescue -> rework_t4` sends guided retries to the strongest coder tier
  without ticking the round counter. Human guidance text is available to the agent
  in the run context (`human.gate.text`); the rework prompt (task 04) accounts for it.
- `open_pr` creates the PR with a deterministic title/body and swaps issue labels
  (`agent-in-progress` → `Review`), then comments the PR link on the issue.
