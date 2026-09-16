# Fabro PR-Review Workflow — Overview & Canonical Contracts

**Read this first.** Every task in this folder assumes the architecture and file
contracts defined here. This document is the single source of truth.

## What this is

A new fabro workflow, `pr-review`, that takes **one pull request** as an input, makes
sure it is rebased on the tip of its base branch, reviews it on three axes, applies a
round of fixes, re-reviews, runs CI, and leaves a PR a human can merge.

Ported from `/Users/andrew/Documents/code/Sandcastle-loop/run-pr-review-v1.mts`, with
deliberate differences (§"Differences from Sandcastle").

The `backlog` workflow now feeds its PRs straight into this one: it fires a `pr-review`
run as soon as it opens a pull request. See
`docs/pr-review-bridge/00-overview-and-contracts.md`.

Making that happen did **not** modify this workflow. Its input contract, graph and
`workflow.toml` are unchanged, and it stays independently runnable by hand — the bridge
registers this package as a workflow version and fires it through the API, exactly as a
manual fire does.

## Operator decisions (settled — do not re-litigate)

| # | Decision |
|---|---|
| 1 | Rebase logic lives **inline** in this workflow. Extract to a shared child workflow only once proven. |
| 2 | Input is **`pr_number` only**; the repository comes from the run's git target. |
| 3 | **Three reviewers**, all on `glm-5.3`. |
| 4 | One review → fix → re-review, **max 2 fix attempts**. |
| 5 | Terminal state is a **label + comment**. The workflow never merges. |
| 6 | Work on the **PR head branch**; `[run.run_branch] enabled = true, push = false`. |
| 7 | **Four automations**, one per repo, each with the right `environment_id`. |
| 8 | Rebase **first**, then a cheap re-check before delivery. |
| 9 | Port Sandcastle's PR-review prompts, not backlog's. |
| 10 | CI (`./.fabro/setup.sh && ./.fabro/ci.sh`) runs after the fix pass; red CI blocks the ready label. |
| 11 | One `ai-review-needs-human` label for every failure path; detail in the comment. No risk labels in v1. |
| 12 | Backlog's broken merge-conflict path gets the same node design, ported **last** (task 08). |
| 13 | Backlog keeps `git merge` for accumulation; only pr-review rebases. |
| 14 | The conflict-resolution prompt is **duplicated** into each workflow, not shared via `@../`. |
| 15 | A `--force-with-lease` rejection **stops the run** — never `--force`. |
| 16 | Rebase agent: **one attempt on `coders`, then one on `glm-5.3`, then needs-human.** |

## Differences from Sandcastle

| Sandcastle | Here |
|---|---|
| polls the GitHub API for PRs on a loop | takes `pr_number` as a run input |
| "PR settling" window (`--settle-seconds`) | dropped — the caller decides when a PR is ready |
| label-based PR discovery and filtering | dropped — the caller names the PR |
| separate models for standards / spec / fixer | one model, `glm-5.3`, for review and fix |
| no rebase at all (`grep rebase` is empty) | **rebase onto base tip is a first-class stage** |
| structured-result MCP tool | file-backed contracts + `jq` command gates |
| risk labels (`allRiskLabels()`) | dropped in v1 |

## Architecture: file-backed contracts + jq gates

Identical to the `backlog` workflow. Every agent writes its deliverable to a file under
`/tmp/fabro/`; a tiny command gate validates it with `jq` and emits routing state:

```json
{"context_updates": {"some_key": "some_value"}}
```

**Every command node that prints `context_updates` MUST also declare
`output_schema="routing"`.** Without it fabro never scans the node's stdout, the
updates are silently dropped, and routing is inert with no error anywhere. This cost
the backlog series a live debugging round — do not rediscover it.

`"routing"` is a reserved keyword, not a JSON schema. Agent nodes carry **no**
`output_schema` at all.

## The input contract

The run is created with:

```json
{"trigger": "manual", "inputs": {"pr_number": 376}}
```

- `inputs` is supported on `RunIntent` / `RunManifest` (values may be string, number or
  integer).
- **Scripts** interpolate it as `{{ inputs.pr_number }}`.
- **Prompts must not.** Agents receive PR data through files under `/tmp/fabro/`, the
  same as every other agent in this deployment. This is the one place this workflow's
  rule differs from backlog's "prompts may use `{{ goal }}` only" — scripts here may
  additionally use `{{ inputs.* }}`.

Validate the input in the first command stage: a non-numeric or missing `pr_number`
must fail fast with a clear message, not 40 minutes later.

## Branch mechanics (read carefully — this is the subtle part)

Fabro's clone **always** creates `fabro/run/<run_id>` from the target branch tip. There
is no "check out an existing branch" intent. So:

0. `claim` refuses a PR that is not `OPEN`, and refuses a cross-repository (fork) PR:
   `deliver` pushes to `origin`, which for a fork would create a branch in the base
   repository and leave the PR untouched while still commenting "review complete".
   `claim` also deepens the clone — `[run.clone]` defaults to depth 100
   (`RunCloneSettings::DEFAULT_DEPTH`), and every git operation this workflow relies
   on needs the true merge base. In a shallow clone
   `git diff origin/<base>...HEAD` dies with `fatal: no merge base`,
   `git log origin/<base>..HEAD` lists commits that are already on the base branch,
   and `merge-base --is-ancestor` answers no when the answer is yes. Set
   `[run.clone] depth = 0` in `workflow.toml` as well (task 05); `depth = 0` means
   full history. The key is `depth`, not `n` — `RunCloneLayer` is
   `#[serde(deny_unknown_fields)]`, so a wrong key is a hard startup error, not a
   silent default.
1. `claim` runs `gh pr checkout <pr_number>`, moving HEAD to the PR head branch.
2. Fabro's checkpoint is `git add -A && git commit` with **no branch argument** — it
   commits to whatever HEAD is. So every stage's changes land on the PR branch. This is
   what we want and it is why `run_branch.enabled` stays `true`.
3. `fabro/run/<run_id>` is left behind pointing at the base tip, unused.
   `[run.run_branch] push = false` stops it being pushed on every checkpoint.
   That is legal here because there is no `[run.pull_request]` block.
4. Delivery is one explicit `git push --force-with-lease origin HEAD:<head_ref>`.

## The rebase ladder

Ported from `Sandcastle-loop/pr-merge-rebase.mts`, which already has the right shape:

| Case | Action | Agent? |
|---|---|---|
| base tip is already an ancestor of HEAD | nothing | no |
| plain `git rebase origin/<base>` completes clean | force-with-lease push | no |
| `git rebase` hits conflicts | hand to the rebase agent, conflict left **in the tree** | yes |
| plain rebase fails for a non-conflict reason | do **not** escalate — needs-human | no |

That last row matters: a non-conflict git failure (corrupt index, unrelated histories)
is not something a model can fix, and handing it one wastes a tier.

## Canonical state files (all under `/tmp/fabro/`)

| File | Written by | Contract |
|---|---|---|
| `pr_number` | `validate_input` | the validated PR number; every later stage reads it from here rather than re-interpolating `{{ inputs.pr_number }}` |
| `render.jq`, `render_comment.sh` | `validate_input` | the PR-comment renderer, written once and used by both `deliver` and `mark_needs_human` |
| `pr_url`, `run_base_sha` | `claim` | the PR's URL, and HEAD before this run's first checkpoint, so the report can link the commits that actually changed files |
| `pr.json` | `claim` | `gh pr view N --json number,title,body,state,isCrossRepository,headRefName,baseRefName,headRefOid,labels,closingIssuesReferences` |
| `linked_issues.json` | `claim` | array of linked issues; `[]` when the PR has none |
| `base_ref`, `head_ref` | `claim` | plain text branch names |
| `rebase_state` | `rebase_check` | `not_needed` \| `clean` \| `conflict` \| `broken` |
| `rebase_result.json` | rebase agent | `{outcome: "resolved"\|"unresolved", conflicted_files[], resolution_summaries[], diagnostics[]}` |
| `rebase_attempts` | rebase gate | integer; tier 1 = `coders`, tier 2 = `glm-5.3` |
| `review/diff.patch`, `diffstat.txt`, `changed_files.txt`, `commits.txt` | `prep_review` | `origin/<base>...HEAD` after rebase |
| `review/standards.json` | `standards` | `{decision, summary, findings:[{message,files[],suggestion}]}` |
| `review/spec.json` | `spec` | same shape |
| `review/fix_result.json` | `review_fix` | `{outcome: "fixed"\|"no_changes_needed"\|"needs_human", summary, changes[]}` |
| `validate_output.log` | `validate` | setup + ci log |
| `feedback/fix.md` | `validate` | markdown fed to the next fix attempt |
| `fix_attempts` | `validate` | integer, max 2 |
| `needs_human_reason` | any failing stage | one line, quoted into the `mark_needs_human` comment |
| `pr_comment.md` | `deliver`, `mark_needs_human` | the rendered report, posted to the PR |

**The PR comment is rendered from the contract files, never hardcoded.** v1 posted a
fixed three-bullet string and discarded every finding the reviewers produced; run
`01M2JS6Y37TTQ2QDMQZK7GKB9N` did a correct review of PR 2588 and reported none of it.
`render.jq` turns `standards.json`, `spec.json` and `fix_result.json` into the
sandcastle layout: verdict table, Findings, Fixes applied, Not fixed and why, the
commits that changed files, and Notes.

There is no `verdict` file and no `final_gate` / `review_gate` node: the terminal state
*is* the label plus the comment (decision 5), and `deliver` / `mark_needs_human` are the
only stages that write to GitHub. `review_fix` writes `fix_result.json` only — it does
not publish a separate findings file, because nothing downstream reads one.

**Every agent contract file is deleted before the agent that writes it runs**
(`prep_review` clears the three review files, `rebase_check` clears
`rebase_result.json`, `validate` clears `fix_result.json` on the red path). Without
that, an agent that exits succeeded without writing hands the gate its predecessor's
result.

## Model map

| Class | Model | Nodes |
|---|---|---|
| `.review` | `glm-5.3` | `standards`, `spec`, `review_fix` |
| `.rebase` | `coders` | `rebase_agent_t1` |
| `.rebase-t2` | `glm-5.3` | `rebase_agent_t2` |

**Class selectors accept `[a-z0-9-]` only — no underscores.** `.rebase_t2` fails to
parse with `expected '{' after selector`. Hyphenate.

`[run.model.fallbacks]` mirrors backlog so a provider outage degrades rather than fails.

## Run shape

```
start → validate_input → claim (gh pr checkout) → rebase_check
  rebase_check →(not_needed|clean)→ prep_review
               →(conflict)→ rebase_router → rebase_agent_t1/t2 ⇄ rebase_gate
                                              → resolved → prep_review
                                              → exhausted → mark_needs_human
               →(broken)→ mark_needs_human
  prep_review → standards ⇄ gate → spec ⇄ gate → review_fix ⇄ fix_gate
  fix_gate →(fixed)→ validate →(green)→ rebase_recheck → deliver
                             →(red)→ review_fix   (max 2 attempts)
  fix_gate →(no_changes_needed)→ validate
  fix_gate →(needs_human)→ mark_needs_human
  rebase_recheck →(base moved)→ rebase_check   (once)
  deliver → push --force-with-lease, label ai-review-complete, comment → exit
  mark_needs_human → label ai-review-needs-human, comment → exit
```

## Golden rules — traps this deployment has already paid for

1. **`output_schema="routing"`** on every `context_updates`-emitting command node.
   Nothing else in fabro tells you it is missing.
2. **Class selectors are `[a-z0-9-]` only.** Underscores fail validation.
3. **Hook matchers are unanchored regexes** tested against `node_id`, `handler_type`,
   `edge_to`, `edge_from` and `tool_name`. Anchor anything that is a prefix of another
   node id (`^open_pr$`, not `open_pr`).
4. **`#` is legal inside a quoted DOT string** and is required (`Resolves #N`, `## `
   headings). It is only illegal as a *comment* — use `//`. Never strip a `#` to make a
   grep pass.
5. **`tomllib` needs Python 3.11+.** macOS ships 3.9; use `python3.11` or the host.
6. **`sh -n` cannot see inside `jq '...'`.** Compile-check jq programs separately.
7. **Reset per-iteration context keys, and weight every tie.** Fabro's edge selection
   takes *every* matching condition, then picks the highest `weight` and breaks the
   remaining tie by **lowest target node id** (`best_by_weight_then_lexical` in
   `fabro-workflow/src/graph/routing.rs`). A stale key from a previous iteration can
   therefore beat the intended edge, and an unweighted tie is decided by the alphabet.
   Put an explicit `weight=10` on the edge that must win.
7b. **A failed node still routes** — the default is `OnFailure::Route`, which takes the
   node's *unconditional* edge. So every command node's unconditional edge must lead
   somewhere safe. If the unconditional edge is the happy path, add
   `|| outcome=failed` to the escape edge's condition.
8. **Scripts are POSIX `sh`** — no `[[ ]]`, no `pipefail`, no arrays.
9. **Agents never run `git commit`/`git push`** — with one deliberate exception, the
   rebase agent (task 02), which needs `git rebase --continue` and `git add`.
10. **Never print a secret.** The workflow repo is public.

## Task index

| # | Task | Needs smarter LLM? |
|---|---|---|
| 00 | This document | — |
| 01 | Write `workflow.fabro` | **yes** |
| 02 | Rebase + conflict prompts | no |
| 03 | Standards + spec reviewer prompts | no |
| 04 | Main review-and-fix prompt | no |
| 05 | `workflow.toml` + four automations | no |
| 06 | Validate | no |
| 07 | Deploy + live E2E on a real PR | **yes** |
| 08 | Port `resolve_merge` into the backlog workflow | no |
| 09 | Update the deployment log | no |
| 10 | Report the review on the PR | no |

Do them in order. 08 depends on 07 proving the node design.
