# Task 03 — `pr-review` graph: the nine merge nodes

**Depends on:** 01, 02, 04. **Blocks:** 05, 09. **LLM level:** yes. This is the stage.

Add nine nodes and twenty-four edges to `.fabro/workflows/pr-review/workflow.fabro`,
and move one thing out of `deliver`.

| | Before | After |
|---|---|---|
| `PrReview` | 19 nodes, 40 edges | **28 nodes, 64 edges** |
| `Backlog` | 38 nodes, 86 edges | **unchanged** |

## What changes in `deliver`

`deliver` keeps the `git push --force-with-lease`, keeps the `ai-review-complete`
label, and keeps emitting `delivered`. **It stops posting the comment.**

The comment moves to `report_merged` / `report_blocked` (task 05). The reason is
`ci_fix`: the loop can force-push twice after `deliver` runs, so a comment posted
there describes a HEAD that no longer exists and its "Reviewed HEAD: `<sha>`" line is
wrong. Rendering once, at the end, against final HEAD, also lets one comment carry the
merge outcome instead of two comments contradicting each other.

`mark_needs_human` is unchanged and still renders its own comment — a run that dies
mid-merge must still report, and it cannot rely on a node it never reached.

The edge `deliver -> exit [condition="context.delivered=true"]` becomes
`deliver -> merge_gate [condition="context.delivered=true"]`. `deliver -> mark_needs_human`
stays.

## `merge_gate`

`shape=parallelogram`, `output_schema="routing"`. Emits `merge_eligible` on every
branch, and writes `/tmp/fabro/merge_block_reason` whenever it emits `false`.

Evaluates, in this order, cheapest and most-likely-to-fail first:

| # | Check | Source |
|---|---|---|
| 1 | `/tmp/fabro/auto_merge` is exactly `1` | task 06 |
| 2 | PR carries the `agent-authored` label | `gh pr view --json labels` |
| 3 | PR `state` is `OPEN`, `isDraft` is false | `gh pr view --json state,isDraft` |
| 4 | `reviewDecision` is not `CHANGES_REQUESTED` | `gh pr view --json reviewDecision` |
| 5 | No unresolved review thread | `gh api graphql`, below |
| 6 | No commit authored by anyone but `noreply@fabro.sh` | `gh pr view --json commits` |
| 7 | `fix_outcome` is `fixed` or `no_changes_needed` | `fix_result.json` |
| 8 | `risk` is an integer 0-5 **and ≤ 3** | `fix_result.json` |
| 9 | `not_fixed` is empty | `fix_result.json` |
| 10 | Every `own_findings` entry with `severity="error"` appears in `fixes_applied` by id — a backstop; `fix_gate` enforces the same accounting with a repair turn (task 01) | `fix_result.json` |
| 11 | The live PR title matches the Conventional Commits regex | `gh pr view --json title` |
| 12 | The marker block extracts to a non-empty body containing `Resolves #` | the live PR description |

Any failure sets `merge_eligible=false`, writes a one-line reason naming the check,
and stops. **Every check fails closed** — an absent file, an unparseable field, a
failed `gh` call, all mean not eligible. The reason line is published verbatim in the
report comment, so write it for the human reading the PR, not for a log.

On success it writes `/tmp/fabro/commit_subject.txt` (the live title),
`/tmp/fabro/commit_body.md` (the extracted block),
`/tmp/fabro/review/merge_base_files.txt` (the scope ceiling for `ci_fix`), `0` to
`/tmp/fabro/gh_fix_attempts` and `/tmp/fabro/strict_retry`, and `$(date +%s)` plus 3600
to `/tmp/fabro/merge_deadline`.

### Checks 5 and 6, concretely

`reviewThreads` is **not** a `gh pr view --json` field — verified, it is rejected with
`Unknown JSON field`. Use GraphQL:

```sh
UNRESOLVED=$(gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{isResolved}}}}}' -F o=\"$OWNER\" -F r=\"$NAME\" -F n=\"$PR\" --jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length' 2>/dev/null || echo bad)
```

`bad` is not `0`, so a failed call blocks. That is the intent.

Human commits are identified by **author email**, not login — `commits[].authors[].login`
is empty on fabro commits, measured on #378:

```sh
FOREIGN=$(gh pr view \"$PR\" --json commits --jq '[.commits[] | select([.authors[].email] | index(\"noreply@fabro.sh\") | not)] | length' 2>/dev/null || echo bad)
```

## `watch_checks`

`shape=parallelogram`, `output_schema="routing"`, `timeout="35m"` (the stage timeout is
a backstop above the in-script wait, not the wait itself). Emits `checks_ok`,
`checks_blocked` and `gh_fix_attempts` on **every** branch.

First, the budget. Before waiting at all:

```sh
NOW=$(date +%s); DL=$(cat /tmp/fabro/merge_deadline 2>/dev/null || echo 0)
if [ \"$NOW\" -ge \"$DL\" ]; then  # blocked: budget exhausted
```

Then **poll**, never sleep a flat interval. A blind `sleep 1800` holds one of three
concurrency slots doing nothing in the common case where the checks have already
settled, and it spends the budget before the second fix tier can be read: 1800 + agent
+ 900 + agent exceeds 3600, so `ci_fix_t2` is structurally dead. Loop every 30 seconds
until no check is in `bucket` `pending`, with the deadline checked on each pass.

Zero checks reported gets a **five-minute grace** of its own before it blocks. GitHub
takes a moment to register checks after a push, and spinning out the whole budget would
report the wrong reason for the right outcome.

`gh pr checks --json` exits 0 even when a check is failing or pending — unlike the plain
form, which exits 1 and 8 — so `|| echo '[]'` fires only on a real error and never
discards a good result.

Then evaluate. `gh pr checks --json name,state,bucket`, and **every** reported check
must have `bucket` of `pass` or `skipping`:

```sh
TOTAL=$(jq length /tmp/fabro/checks.json)
BAD=$(jq '[.[] | select(.bucket != \"pass\" and .bucket != \"skipping\")] | length' /tmp/fabro/checks.json)
```

- `TOTAL = 0` → **blocked**. No CI ran; that is not the same as CI passing. On three
  of four repos nothing is required, so this is the only thing stopping a merge with
  no evidence behind it.
- `BAD = 0` → `checks_ok=true`.
- `BAD > 0` and `gh_fix_attempts < 2` → increment, write the failing check names and
  their failing logs to `/tmp/fabro/feedback/ci_fix.md`, emit `checks_ok=false` with
  the new count.

  **`gh run view --log-failed` needs a run id.** With no argument it fails outright —
  "run or job ID required when not running interactively" — which leaves the feedback
  file holding check names and no logs, while `ci_fix.md.j2` promises the agent that
  output. Take `link` from `gh pr checks --json name,state,bucket,link`, strip it to
  the run id with `sed 's|.*/actions/runs/||; s|/.*||'`, dedupe, and view each. A
  non-Actions check (CodeQL posts `/runs/<id>`, not `/actions/runs/<id>`) survives the
  first substitution unchanged and is dropped by a numeric guard.
- `BAD > 0` and `gh_fix_attempts = 2` → **blocked**, reason naming the checks.

`bucket`, not `state`: it normalises to five values where `state` has a dozen and
gains more. `cancel` is not in the pass set — a cancelled check is not a passed one.

## `ci_fix_t1`, `ci_fix_t2`, `ci_fix_gate`

Agent pair on the model ladder, mirroring `rebase_agent_t1` / `rebase_agent_t2`
exactly — same two-node shape, same both-edges-to-the-gate pattern, same `weight`
discipline. `ci_fix_t1` gets `class="rebase"` (`coders`); `ci_fix_t2` gets
`class="rebase-t2"` (`glm-5.3`). Prompt is `@prompts/ci_fix.md.j2`, task 04.

Reuse the existing stylesheet classes rather than adding `.ci-fix` ones: they already
resolve to the two models this needs, and the stylesheet selector rule
(`[a-z0-9-]` only) makes every new class a chance to write `expected '{' after selector`.

`ci_fix_gate` — `output_schema="routing"`, emits `ci_fix_ok` on both branches:

1. `ci_fix_result.json` parses, `outcome` is `fixed` or `cannot_fix`, `scope` is
   exactly `ci_only`.
2. **The scope check, from git, not from the agent's claim.** Every path in
   `git diff --name-only origin/<base>...HEAD` must already appear in
   `/tmp/fabro/review/merge_base_files.txt`, which `merge_gate` snapshots. A CI fix
   that adds a file is outside what the reviewers saw.

   **Not `prep_review`'s `changed_files.txt`.** That file predates `review_fix`, so a
   file `review_fix` legitimately added would read as out-of-scope here and block the
   merge over a file `ci_fix` never touched. The snapshot has to be the set the merge
   decision was actually made against.
3. No conflict markers, no in-progress merge or rebase — the same scan
   `rebase_gate` runs, for the same reason.
4. On success, `git push --force-with-lease origin HEAD:<head_ref>`. A rejected push
   means the branch moved under us: `ci_fix_ok=false`, reason says so.

`cannot_fix`, a scope violation, or a rejected push all set `ci_fix_ok=false` and route
to `report_blocked`. They are expected outcomes, not errors.

## `merge`

`output_schema="routing"`. Emits `merge_state` on every branch.

**Re-read PR state first**, immediately before merging:

```sh
S=$(gh pr view \"$PR\" --json state --jq .state 2>/dev/null || echo UNKNOWN)
if [ \"$S\" != OPEN ]; then  # merge_state=closed
```

A PR that is no longer open was handled by something else — a human, or a second
`pr-review` run fired by hand, which the bridge's per-run marker file does not prevent.
That is **not** a failure: report it, exit clean. Labelling a correctly-merged PR as
needing a human is the bug this check exists to avoid.

Then:

```sh
gh pr merge \"$PR\" --squash --delete-branch --subject \"$(cat /tmp/fabro/commit_subject.txt)\" --body-file /tmp/fabro/commit_body.md
```

Classify the result:

| Outcome | `merge_state` | Goes to |
|---|---|---|
| merged | `merged` | `report_merged` |
| rejected because the branch is behind the base, and `strict_retry` is 0 | `stale` | `remerge_base` |
| rejected because the branch is behind, and `strict_retry` is 1 | `blocked` | `report_blocked` |
| PR not `OPEN` | `closed` | `report_blocked` |
| anything else | *node fails* | `mark_needs_human` via the unconditional edge |

`stale` is reachable only on `jelly-swipe`, the one repo with
`required_status_checks.strict = true`. Everything else — a token without
`contents: write`, a merge conflict that appeared since the rebase, an API 5xx — is
an unexpected failure and must be loud.

## `remerge_base`

Fetch the base, rebase onto its tip, force-push. On conflict: abort, `remerge_ok=false`,
reason says a conflict appeared after review. Sets `strict_retry=1` so this happens at
most once.

On success it routes back to **`watch_checks`, not to `merge`.** The force-push created
a new HEAD, so the checks that were green belong to a SHA that is no longer the head.
Merging immediately would merge a commit no check has reported on — the same hole as
`TOTAL = 0`, with extra steps. The 60-minute budget is what stops this from looping.

A conflict here does not escalate to an agent. `rebase_agent_t1`/`t2` exist for the
review path, where a conflict is expected on arrival; a conflict appearing *after* a
clean rebase and a full review means the base moved substantially, and the right answer
is a human.

## `report_merged` and `report_blocked`

Rendering is task 05. The graph obligations:

- `report_merged` carries `output_schema="routing"` and emits **`pr_url` and
  `issue_number`**. Those two keys are what let `discord-notify.sh` enrich the merge
  notification with no change to its enrichment logic — it already greps run state for
  exactly those names. `issue_number` comes from `/tmp/fabro/linked_issues.json`,
  which `claim` writes; `0` when there is none.
- `report_merged` also removes the `Review` label from the linked issue. The issue
  itself closes via `Resolves #N` in the commit body.
- Both go to `exit`, unconditionally. They are terminal reporting nodes; they have no
  failure branch worth routing, and both are `|| true` on every `gh` call for the same
  reason `mark_needs_human` is.

## Edges

Twenty-five lines below, of which **twenty-four are new** — `deliver -> merge_gate`
replaces the existing `deliver -> exit` and does not change the count.

Every merge-phase command node's **unconditional** edge lands on `mark_needs_human`;
expected blocks are conditional edges to `report_blocked`.

```
deliver       -> merge_gate      [condition="context.delivered=true"]
merge_gate    -> watch_checks    [condition="context.merge_eligible=true", weight=10]
merge_gate    -> report_blocked  [condition="context.merge_eligible=false"]
merge_gate    -> mark_needs_human

watch_checks  -> merge           [condition="context.checks_ok=true", weight=10]
watch_checks  -> ci_fix_t1       [condition="context.checks_ok=false && context.gh_fix_attempts=1"]
watch_checks  -> ci_fix_t2       [condition="context.checks_ok=false && context.gh_fix_attempts=2"]
watch_checks  -> report_blocked  [condition="context.checks_blocked=true"]
watch_checks  -> mark_needs_human

ci_fix_t1     -> ci_fix_gate     [condition="outcome=succeeded"]
ci_fix_t1     -> ci_fix_gate
ci_fix_t2     -> ci_fix_gate     [condition="outcome=succeeded"]
ci_fix_t2     -> ci_fix_gate
ci_fix_gate   -> watch_checks    [condition="context.ci_fix_ok=true", weight=10]
ci_fix_gate   -> report_blocked  [condition="context.ci_fix_ok=false"]
ci_fix_gate   -> mark_needs_human

merge         -> report_merged   [condition="context.merge_state=merged", weight=10]
merge         -> remerge_base    [condition="context.merge_state=stale"]
merge         -> report_blocked  [condition="context.merge_state=closed || context.merge_state=blocked"]
merge         -> mark_needs_human

remerge_base  -> watch_checks    [condition="context.remerge_ok=true", weight=10]
remerge_base  -> report_blocked  [condition="context.remerge_ok=false"]
remerge_base  -> mark_needs_human

report_merged  -> exit
report_blocked -> exit
```

**Booleans are emitted bare**, `{"merge_eligible":true}`, never as the string
`"true"`. Every pre-existing gate in both graphs does this — `"merge_ok":false`,
`"tasks_done":true`, `"ci_ok":false` — and a quoted boolean that no condition matches
falls through the unconditional edge with nothing reported anywhere. `merge_state` is a
string because it is an enum, like `rebase_state`.

`weight=10` on every success edge that shares a source with a retry or block edge.
Fabro picks the highest-weight matching edge and otherwise falls back to the lowest
target node id, so without a weight `ci_fix_gate`'s routing would depend on the
alphabet — `mark_needs_human` sorts before `watch_checks`.

The two `ci_fix_t* -> ci_fix_gate` pairs are deliberate duplicates: a failed agent
still routes, and it takes its unconditional edge, so both outcomes must reach the gate
that can detect the bad state. This is the `rebase_agent_t1` pattern, verbatim.

## Update the graph goal

`graph [goal=…]` still reads "…and leave it ready for a human to merge". Change the
tail to "…and squash-merge it when the review, the risk rating and CI all allow".
`goal` is one of the three templated attributes, so this is rendered text an agent may
see — leaving it stale tells every agent in the workflow the wrong thing about what
happens next.

## Acceptance

- `fabro validate` in the container reports **`PrReview (28 nodes, 64 edges)`** with
  exactly one warning, still `pr_number` unbound in `validate_input`.
- `Backlog` still reports 38 nodes, 86 edges.
- `fabro graph` renders and every new node is reachable from `start`; nothing but
  `exit` is a sink.
- No `.fabro` line contains a backslash other than `\"`.
- `sh -n` passes on all nine scripts, and every embedded `jq` program compiles
  standalone with `jq -n`.
- Every new command node that prints `context_updates` carries
  `output_schema="routing"`. Grep for it; this has cost a live debugging round before.
- Every new command node has exactly one unconditional edge, and it points at
  `mark_needs_human`.
