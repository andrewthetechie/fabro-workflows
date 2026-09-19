# Fabro Auto-Merge — Overview & Canonical Contracts

**Read this first.** Every task in this folder assumes the architecture, the
contracts, and the findings recorded here. This document is the single source of
truth.

## What this is

The fourth stage of the fabro integration. After `pr-review` has rebased a pull
request, reviewed it on three axes, applied fixes and delivered its report, it now
**decides whether to squash-merge the PR and does so**.

`docs/pr-review-bridge/00-overview-and-contracts.md` decision 5 said the chain "ends
at review posted. No auto-merge. Nothing here may foreclose adding it later." This is
the stage it did not foreclose, and it keys on exactly the terminal signal that
document predicted: the `ai-review-complete` state plus green CI.

The merge lives in **`pr-review`**, not `backlog`. `backlog` fires a review and exits;
it never sees the `risk` score the gate turns on. `backlog` changes only to produce a
title and commit body that are mergeable, and inherits auto-merge through the existing
bridge.

## Operator decisions (settled — do not re-litigate)

| # | Decision |
|---|---|
| 1 | The merge stages live in **`pr-review`, after `deliver`**. Not in `backlog`, not in a fifth workflow. `backlog` gets auto-merge through the bridge for free. |
| 2 | **"Passes CI" means both**: the sandbox `./.fabro/ci.sh` that already gates `deliver`, *and* the GitHub Actions checks on the pushed head. |
| 3 | A PR is mergeable when `not_fixed` is empty, every `own_findings` entry with `severity="error"` is accounted for in `fixes_applied` (the prompt and `fix_gate` require that accounting, so `merge_gate` is a backstop), `fix_outcome` is `fixed` or `no_changes_needed`, and `risk` is present and **≤ 3**. A reviewer `decision=findings` is **not** disqualifying on its own — the contract already forces every finding into `fixes_applied` or `not_fixed`. |
| 4 | PR titles are **Conventional Commits**; the `pr-review` report comment stays Conventional-Comments-shaped. These are different specs and were being conflated. |
| 5 | Eligible PRs carry the **`agent-authored`** label. Human-opened PRs are reviewed as before and never auto-merged. |
| 6 | Green is **self-evaluated** from `gh pr checks`: every reported check must be `bucket` `pass` or `skipping`. Not `mergeStateStatus`, not `--required`, not `gh pr merge --auto`. See finding 1. |
| 7 | Wait 30m on the first check pass and 15m on retries, under a **single 60m wall-clock budget** for the whole merge phase. Timeout, budget exhaustion and **zero checks reported** all mean *do not merge*. |
| 8 | Failing GitHub CI gets **two coder attempts**, laddered `coders` → `glm-5.3`, mirroring the existing rebase pair. A dedicated `ci_fix` agent, not `review_fix`. |
| 9 | **No re-review after a CI fix.** Instead `ci_fix` is constrained to files already in the PR's changed set, verified from git rather than trusted. |
| 10 | Auto-merge is **on by default, on all four repos**. Two independent kill switches exist instead of an opt-in. |
| 11 | An **expected** block keeps `ai-review-complete` and explains itself in the report comment. An **unexpected** failure routes to `mark_needs_human`. |
| 12 | Merged PRs are **not** branch-deleted by the merge (`--delete-branch=false`, finding 10); `fabro-branch-sweep.sh` reaps the run branch at its own grace. The linked issue closes via `Resolves #N` and loses its `Review` label. |
| 13 | Discord notifies on **merges only** — `pr-review`'s first hook. |
| 14 | The squash **subject is read live from the PR title**, never round-tripped. The **body** rides in a delimited block in the PR description. |
| 15 | `risk` becomes **gate-enforced** in `fix_gate`, not merely documented in the prompt. |
| 16 | The commit body is the Summary paragraph plus `Resolves #N` — **not** the task list and **not** the `## Validation` section. |
| 17 | The merge node **re-reads PR state from GitHub immediately before merging**. A PR that is no longer `OPEN` is "already handled": no merge, no failure. |
| 18 | Rollout is the kill switch, not a dry-run mode. Merge to `main` with `FABRO_AUTO_MERGE=0` already set on the host. |

## Findings established against the live deployment

These were measured on 2026-09-15 against the four target repositories and the
installed `gh`. Several of them invalidate the obvious design.

### 1. Three of four repos cannot have branch protection, so GitHub cannot be the gate

`GET /repos/{owner}/{repo}/branches/main/protection`:

| Repo | Result |
|---|---|
| `jelly-swipe` | public — protected. Required contexts `["test"]` only, `strict: true`, `required_approving_review_count: 0` |
| `lawncare-saas` | `"Upgrade to GitHub Pro or make this repository public to enable this feature."` |
| `womens-fantasy-sports` | same |
| `writers-app` | same |

Three consequences, all load-bearing:

- **`gh pr merge --auto` is unavailable on three of four repos.** GitHub's auto-merge
  requires branch protection or a ruleset. The obvious design — hand the merge to
  GitHub and exit — cannot ship here.
- **`mergeStateStatus` is worthless as a CI gate.** With nothing required it reports
  `CLEAN` regardless of check results. Gating on it would merge red PRs on three repos.
- **Gating on *required* checks is gating on nothing** on those three. Hence decision
  6: evaluate every reported check.

On `jelly-swipe`, `strict: true` means the branch must be current with `main` at merge
time, so a merge can be rejected as stale even after `rebase_recheck` passed. That is
the `remerge_base` path, not a failure.

### 2. The `agent: ` PR title prefix fails CI today

`jelly-swipe` runs `amannn/action-semantic-pull-request@v6` (`pr-lint.yml`,
`requireScope: false`), types: `feat fix docs chore refactor test ci build perf revert`.
`lawncare-saas` runs one too.

`backlog`'s `open_pr_prep` writes `'agent: '$T' (#'$N')'`
(`.fabro/workflows/backlog/workflow.fabro:314`). `agent` is not in that list.

Measured: PR #378 (`agent: [Chore] Remove the unreferenced 1 MB frontend/public/favicon.png (#377)`)
has `lint` **fail**. PR #376 (`fix(frontend): …`) has `lint` **pass**.

So this is not cosmetic. **Until task 02 lands, the check gate can never go green on
`jelly-swipe`**, and auto-merge would be permanently blocked there for the right
reason but the wrong cause.

### 3. `release-please` turns the squash subject into a version bump

`release-please.yml` runs on `jelly-swipe` and `lawncare-saas`. The subject an agent
picks moves semver: `feat:` cuts a minor release, `fix:` a patch. `lawncare-saas` also
deploys on merge to `main` (`deploy-main.yml`, `deploy-homelab.yml`, `docker-main.yml`).

This is why task 02's derivation is **deterministic and never emits `!` or a
`BREAKING CHANGE:` footer**. A model guessing `feat` versus `fix` is a model cutting
releases, and a model writing `BREAKING CHANGE:` is a model cutting a major.

### 4. `gh pr view --json` has no `reviewThreads` field

Verified against the installed `gh`: the field list ends at `url` and `reviewThreads`
is rejected with `Unknown JSON field`. Unresolved-conversation detection needs GraphQL:

```sh
gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{isResolved}}}}}' -F o=OWNER -F r=REPO -F n=NUM
```

Confirmed working — returns `{"data":{"repository":{"pullRequest":{"reviewThreads":{"totalCount":0,"nodes":[]}}}}}`
for `jelly-swipe#378`.

### 5. Human commits are identifiable by author email, not by login

Every fabro checkpoint commit is authored `Fabro <noreply@fabro.sh>` with headline
`fabro(<run_id>): <stage>`. Measured on #378: every commit, without exception.

`commits[].authors[].login` comes back **empty** on these commits, so a login-based
check finds nothing. The veto is therefore: **any commit on the head branch whose
author email is not `noreply@fabro.sh` is a human commit**, and blocks the merge.

### 6. `gh pr checks --json` exposes `bucket`, which is the right field

`bucket` normalises `state` to `pass | fail | pending | skipping | cancel`. Verified
on #378: `{"bucket":"fail","name":"lint","state":"FAILURE"}` alongside nine
`{"bucket":"pass",…}`. Gate on `bucket`, not on the raw `state` enum, which has a
dozen values and gains more.

### 7. An automation row has no `labels`, and cannot be PATCHed

`POST /automations` with a `labels` object returns **422 `unknown field 'labels'`**, and
`PATCH /automations/{id}` returns **405**. `description` is the only free-form writable
field on the row, and `PUT` is a full replacement requiring `If-Match`. So the per-repo
switch is an `auto_merge=true|false` token inside `description`, parsed identically by
`fire-pr-review.sh` and `ops/provision-server-state.sh`, and flipped by
`ops/fabro-auto-merge-switch.sh`, which performs the GET+PUT.

### 8. `${VAR}` is not interpolated in `[run.environment.env]`

Probed on 2026-09-16 with a throwaway workflow setting `PROBE = "${HOME}"`. The stage
printed `PROBE_SET_IS=[${HOME}]` — the literal text reaches the sandbox.

This matters more than it looks. A host switch spelled `"${FABRO_AUTO_MERGE}"` arrives
**set, non-empty, and not `1`**, so it pins auto-merge to off on every repo forever with
no operator action able to arm it — and the task 10 shakedown cannot detect it, because
a permanently-broken switch and a correctly disarmed one produce the identical blocked
run.

fabro names the fix in its own error for `{{ env.X }}`: *"the process environment is not
a configuration source. Use `{{ vars.X }}` for a non-sensitive value (`fabro variable
set`) or `{{ secrets.X }}` for a credential."* `{{ vars.FABRO_AUTO_MERGE }}` is verified
to arrive as its value.

**An unset variable fails the RunIntent at compile time** — `Run config variable
interpolation failed` — so no run is created. The variable is therefore provisioned,
never deleted, and `off` writes `0`. The trade buys a switch that takes effect on the
next run with no `.env` edit and no `docker compose up -d`.

### 9. `discord-notify.sh` degrades safely on an unknown kind

Its `case` ends `*) msg="ℹ️ fabro run ${run_id} notification ($kind)"`. A host copy
that predates task 08 sends a plain message rather than failing — which matters
because that script is the one file in this change that still has two copies.

### 10. `gh pr merge --delete-branch` deletes the *local* branch, and fails the run it just merged

Found on 2026-09-18, on the first run to merge a PR through this graph after the
scheduler work landed. The other findings above were measured 2026-09-15.

`gh pr merge` deletes the local head branch as well as the remote one. The merge is not
the run's last stage — `report_merged` and the terminal label work follow it — and
fabro's checkpoint commits and publishes the run branch after every later stage. So the
first publish after the merge finds no local ref and fails:

```
error: src refspec refs/heads/fabro/run/01M2TFEHPYXXTQST6VPXREA854 does not match any
error: failed to push some refs to 'https://github.com/andrewthetechie/womens-fantasy-sports'
```

It retries three times (`run.notice`, code `git_push_failed`, at `23:10:52`, `23:10:55`
and `23:10:56`) and then reports `failed` with reason `publish_failed` and category
`deterministic`. On run `01M2TFEHPYXXTQST6VPXREA854` all of that happened *after* PR
**#1206** squash-merged at `23:10:49` and issue **#1196** closed at `23:10:50`: a fully
successful run, recorded as a failure.

Exactly one of the 316 runs in the store carries `publish_failed`, and it is that one.

So decision 12 is `--delete-branch=false`, and the flag is written out rather than
omitted so the choice is visible to the next reader. Reaping the branch is
`ops/fabro-branch-sweep.sh`'s job, at its own grace: 24h for a `fabro/run/*` branch whose
tree matches the default branch, 168h for one that still differs from it — which is what
a squash merge leaves behind. Measured on jelly-swipe's 21 leaked run branches on
2026-09-18: the oldest was 126h, all within grace, so the sweeper is doing the job.

Two reasons this is worth more than tidiness. `failed` is already the largest bucket (118
of 316), so a member of it that merged, closed its issue and commented on the PR is a
status the operator cannot act on. And the near miss: the same push event, had fabro
classified it `transient_infra` instead of `deterministic`, would have requeued an issue
that was already closed and merged.

### 11. `gh pr create` cannot infer the branch in a fabro sandbox

Found 2026-09-19 on run `01M2VHAGXNM9JNEY71085HV82Y`, jelly-swipe. `backlog`'s `open_pr`
failed after 41 minutes of completed work, with the branch sitting on the remote at
exactly `HEAD` the whole time:

```
Everything up-to-date
branch 'fabro/run/01M2VHAGXNM9JNEY71085HV82Y' set up to track 'origin/fabro/run/...'.
aborted: you must first push the current branch to a remote, or use the --head flag
no pull requests found for branch "fabro/run/01M2VHAGXNM9JNEY71085HV82Y"
```

The sandbox clone is shallow and **single-branch** — `remote.origin.fetch` is
`+refs/heads/main:refs/remotes/origin/main` — so no refspec can store a remote-tracking
ref for `fabro/run/<id>`. `git push -u` still prints `set up to track` and still writes
`branch.<name>.remote`/`.merge`, but `refs/remotes/origin/<branch>` is never created and
`@{u}` fails with `not stored as a remote-tracking branch`. gh resolves the head remote
through exactly that ref. Confirmed by re-running the push in the live sandbox: still
`Everything up-to-date`, still only `origin/HEAD` and `origin/main` under `refs/remotes`.

fabro's checkpoint publishes the run branch after every stage, so `open_pr`'s own push is
always a no-op — and a no-op does no ref work, so the push can never repair it either.

Then the fallback made it worse. `gh pr create ... || gh pr view --json url > /dev/null`
reads as "tolerate an already-open PR", but it discards every real gh error, and
`gh pr view` with no argument resolves the current branch the same failing way. Both
halves failed, `set -e` killed the stage, and the run blocked at `human_rescue`.

So `open_pr` now passes `--head "$B"` to `gh pr create`, names the branch on every
`gh pr view`, and asks whether a PR exists *before* creating rather than after.
`pr-review`'s `claim` meets the same clone property and solves it the other way, widening
the refspec and fetching, which is right there because `gh pr checkout` genuinely needs a
local tracking branch. `open_pr` needs none, so removing the dependency beats satisfying
it.

**What is not explained.** The identical script succeeded on jelly-swipe (PR **#385**, run
`01M2RX2SGJJT09WK5FS8GYJCDE`) and on womens-fantasy-sports (PR **#1206**), both printing
the same first two lines. Ruled out as the trigger: fabro version (pinned
`0.354.0-nightly.0`, one image, unchanged), the recorded `clone`/`run_branch`/`checkpoint`
settings (byte-identical across all three runs), git `2.47.3` and gh `2.100.0` (same in
every profile image), image git config (empty in both), repo topology (both non-fork,
default `main`), the target repo's `.fabro/setup.sh` (no git usage), and agent-run git
commands (zero `git fetch`/`git remote` tool calls in either run). The one asymmetry left
is that jelly-swipe is public and womens-fantasy-sports is private, and on a public repo
gh looks for the viewer's fork as the head repo when it decides push access is missing —
untested, because fabro injects the GitHub token per-exec and it cannot be read back.

Treat `open_pr` as having worked on luck rather than on a guarantee. That is why the fix
removes the dependency instead of recreating the ref, and why
`ops/test-task-gates.sh` now asserts that no `gh pr create`/`gh pr view` in this graph is
left to infer the branch.

## Architecture

### `pr-review` graph delta

`deliver` keeps its push and its `ai-review-complete` label. **Its comment moves out**,
so the report is rendered once, against final HEAD, carrying the merge outcome.

```
deliver ─→ merge_gate ─┬─ ineligible ───────────────→ report_blocked
                       └─ eligible ─→ watch_checks ─┬─ green ────────→ merge
                                                    ├─ red, <2 tries ─→ ci_fix_t1/t2 ─→ ci_fix_gate ─┐
                                                    └─ blocked ──────→ report_blocked               │
                                       ▲────────────────────────────────────────────────────────────┘
merge ─┬─ merged ────────→ report_merged ─→ exit
       ├─ stale ─────────→ remerge_base ─→ watch_checks        (once)
       ├─ closed/blocked ─→ report_blocked
       └─ (unconditional) → mark_needs_human
```

Nine new nodes: `merge_gate`, `watch_checks`, `ci_fix_t1`, `ci_fix_t2`, `ci_fix_gate`,
`merge`, `remerge_base`, `report_merged`, `report_blocked`.

`backlog`'s graph is **unchanged**. Task 02 rewrites two scripts inside existing nodes
and adds no node and no edge.

### Why the merge is in-graph and not a hook

The same reasoning as the bridge: a hook runs in the server container, which has no
`gh`, no `git` checkout of the target repo, and no sandbox. Every input to the merge
decision — the fix contracts, the changed-file set, the commit body — lives in the
sandbox. A hook could only re-derive them over the API.

## Canonical state files

Existing files this stage reads:

| File | Written by | Used for |
|---|---|---|
| `/tmp/fabro/review/fix_result.json` | `review_fix` | `risk`, `fix_outcome`, `not_fixed`, `own_findings`, `fixes_applied` |
| `/tmp/fabro/review/changed_files.txt` | `prep_review` | the scope ceiling for `ci_fix` |
| `/tmp/fabro/pr_number`, `/tmp/fabro/pr_url` | `validate_input`, `claim` | identity |
| `/tmp/fabro/linked_issues.json` | `claim` | the issue to unlabel after merge |
| `/tmp/fabro/base_ref`, `/tmp/fabro/head_ref` | `claim` | `remerge_base` |
| `/tmp/fabro/needs_human_reason` | many | `mark_needs_human`'s comment |

New files this stage introduces:

| File | Written by | Contract |
|---|---|---|
| `/tmp/fabro/auto_merge` | `validate_input` | exactly `1` or `0`. Anything else is `0`. |
| `/tmp/fabro/commit_subject.txt` | `merge_gate` | the live PR title, after regex validation |
| `/tmp/fabro/commit_body.md` | `merge_gate` | extracted from the PR description's marker block; must contain a `Resolves #` line |
| `/tmp/fabro/merge_deadline` | `merge_gate` | epoch seconds; `now + 3600` |
| `/tmp/fabro/gh_fix_attempts` | `watch_checks` | 0, 1 or 2 |
| `/tmp/fabro/strict_retry` | `merge` | 0 or 1; independent of `gh_fix_attempts` |
| `/tmp/fabro/merge_block_reason` | every blocking node | one line, published in the report comment |
| `/tmp/fabro/review/ci_fix_result.json` | `ci_fix_t1` / `ci_fix_t2` | task 04 |

And in `backlog`:

| File | Written by | Contract |
|---|---|---|
| `/tmp/fabro/commit_subject.txt` | `open_pr_prep` | the derived Conventional Commits title; also becomes the PR title |
| `/tmp/fabro/commit_body.md` | `open_pr_prep` | the Summary paragraph plus `Resolves #N`; embedded into `pr_body.md` between markers |

### The commit-body marker block

`backlog` embeds this in the PR description. HTML comments render invisibly, so the
PR still reads normally:

```
<!-- fabro:commit-body:start -->
Adds a healthcheck to the backend container and enables pool_pre_ping so a
recycled connection is detected before a request uses it.

Resolves #2251
<!-- fabro:commit-body:end -->
```

`merge_gate` extracts it from the **live** PR description, not from a cached copy:

```sh
gh pr view "$PR" --json body --jq .body > /tmp/fabro/pr_body_live.md
awk '/fabro:commit-body:start/{f=1;next} /fabro:commit-body:end/{f=0} f' \
  /tmp/fabro/pr_body_live.md > /tmp/fabro/commit_body.md
```

**Editing inside the markers is the supported way for a human to correct a commit
message before the merge fires.** That is the point of reading it live.

Three rules:

- **`:start` / `:end`, never `<!-- /fabro:commit-body -->`.** A closing marker with a
  slash forces `\/` into the extraction regex, and `\"` is the only backslash a
  `.fabro` file may contain. The `awk` above has no backslash at all.
- **Absent, empty, or no `Resolves #` line ⇒ block.** No fallback. A silent
  degradation to a bodyless commit is how an issue gets orphaned with a `Review`
  label and nothing reported.
- **The subject is never in this block.** It is the live PR title. A human fixing a
  bad title must not have to fix it twice.

## Context keys

Every key is written on **every** branch of its node. A stale key from an earlier loop
iteration must never satisfy a condition meant for this one.

| Node | Keys |
|---|---|
| `merge_gate` | `merge_eligible` (`true`/`false`) |
| `watch_checks` | `checks_ok`, `checks_blocked`, `gh_fix_attempts` |
| `ci_fix_gate` | `ci_fix_ok` |
| `merge` | `merge_state` — `merged` \| `stale` \| `closed` \| `blocked` |
| `remerge_base` | `remerge_ok` |
| `report_merged` | `pr_url`, `issue_number` |

`report_merged` publishing `pr_url` and `issue_number` is not decoration. It is what
lets `discord-notify.sh` enrich the merge notification with **no change to its
enrichment logic** — the script already greps the run state for exactly those two
keys, and `pr-review` has so far kept both in files only.

## Kill switches

Two, independent. Auto-merge happens only when **both** say yes. Both fail closed on a
bad *value* — but read the absence rules below before concluding a repo is disarmed.

| Switch | Where | Scope | How fast |
|---|---|---|---|
| `auto_merge=true\|false` token | the `pr-review-<repo>` automation row's `description` | one repo | `ops/fabro-auto-merge-switch.sh <repo> off`, no deploy |
| `FABRO_AUTO_MERGE` server variable | fabro's variable store, read as `{{ vars.FABRO_AUTO_MERGE }}` | all four | `ops/fabro-auto-merge-switch.sh host off`, no restart |

Neither is where the first draft of this document put them, and both corrections are
findings rather than preferences — see findings 7 and 8 above.

A switch that is **present but not exactly its enabled value** — empty, `false`, `0`,
`TRUE`, malformed — means **disabled**. That is the fail-closed half, and it is the only
half a parser decides.

Absence is not that case, and differs per switch:

- **An absent per-repo token means on.** No mention of `auto_merge` anywhere in the row's
  `description` is decision 10: nobody has touched this switch, and auto-merge is the
  default. Both parsers implement this — `fire-pr-review.sh`'s `case "$desc" in
  *auto_merge*)` and `provision-server-state.sh`'s `description_auto_merge()` — and only
  a description that *mentions* the token is ever parsed.
- **An absent host variable means no run at all.** An unset `{{ vars.FABRO_AUTO_MERGE }}`
  fails the RunIntent at compile time, so no `pr-review` run is created — no merge, but
  no review either. This is why the variable is provisioned and never deleted, and why
  `off` writes `0`.

Default-on is therefore a decision about **both** the provisioned value and the parser,
and an untouched `pr-review-<repo>` row is **armed**. Three of the four are untouched
today. Do not read "fails closed" as "an untouched repo is safe" — it is not what the
code does, and stating otherwise sends an operator to the wrong conclusion mid-incident.

## Rules specific to this stage

The inherited golden rules all still apply — `output_schema="routing"` on every
command node that prints `context_updates`, a failed node still routes down its
*unconditional* edge, `//` is the comment, POSIX `sh` only, anchored hook matchers.
Four more matter here:

1. **`\"` is the only backslash.** This bites harder than usual in this stage, which
   is full of regex and path manipulation. Use bracket expressions for literal
   parens — `[(]` not `\(` — and `awk` field logic instead of `sed` ranges with `\/`.
2. **Every merge-phase command node's unconditional edge lands on `mark_needs_human`.**
   Expected blocks are *conditional* edges to `report_blocked`. Folding a broken merge
   into the same quiet bucket as a risk-4 PR is how a permissions regression stays
   invisible for a week.
3. **Re-read PR state from GitHub inside `merge`**, immediately before merging. The
   marker-file guard that protects the bridge is per-run and does not help against a
   second run fired by hand.
4. **Never emit `!` or `BREAKING CHANGE:`.** Finding 3.

## Known risks, recorded deliberately

| Risk | Detail |
|---|---|
| Three repos have no enforceable gate but ours | With branch protection unavailable, the check evaluation in `watch_checks` is the only thing standing between a red PR and `main` on `lawncare-saas`, `womens-fantasy-sports` and `writers-app`. A bug there is a bad merge, and on `lawncare-saas` a bad **deploy**. This is the single highest-consequence code in the stage and task 09 validates it in isolation. |
| Default-on means a new repo auto-merges the day it is provisioned | Decision 10. `provision-server-state.sh` writes the label enabled unless told otherwise, so a fifth repo inherits auto-merge without anyone choosing it. Recorded rather than mitigated; the operator asked for default-on explicitly. |
| The merge phase holds a concurrency slot for up to an hour | `server.scheduler.max_concurrent_runs = 3`. Every `backlog` schedule is disabled today (`ops/README.md`, operator decision 2026-09-14), so this is latent. Re-enabling `every-15m` on four repos **must** be accompanied by revisiting that number; task 11 writes that into `ops/README.md`. |
| `ci_fix` can make a PR green without making it correct | Two attempts at a failing check, constrained to files already in the diff, with no re-review. The scope check is structural, not semantic: it proves the agent touched nothing new, not that the fix is right. Accepted — the alternative is a full re-review loop that can never terminate. |
| A human editing the PR description can change the commit body | By design (decision 14). The same mechanism means a careless edit that breaks the markers blocks the merge rather than corrupting the commit — the failure is loud. |
| `discord-notify.sh` still has two copies | The one file here that cannot use task 07's wrapper: it runs `sandbox = false` in the server container, which has no git and no checkout. Finding 9 is the mitigation — a stale copy degrades to a plain message. |
| Auto-merge cannot be undone by another commit | AGENTS.md's usual escape — "no rollback other than another commit" — does not apply to a merge that already deployed. This is why the rollout in task 10 is kill-switch-first and why both switches fail closed. |

## Task index
| # | Task | Needs smarter LLM? |
|---|---|---|
| 00 | This document | — |
| 01 | Enforce `risk` in `fix_gate` | no |
| 02 | `backlog`: Conventional Commits title + commit-body block | **yes** |
| 03 | `pr-review` graph: the nine merge nodes | **yes** |
| 04 | The `ci_fix` prompt and its contract | **yes** |
| 05 | Report rendering: `merged` / `blocked` kinds | **yes** |
| 06 | Opt-in plumbing and both kill switches | no |
| 07 | Replace the host `fire-pr-review.sh` copy with a wrapper | no |
| 08 | `discord-notify.sh`: the `merged` kind | no |
| 09 | Validate | no |
| 10 | Deploy, shakedown with the switch off, then first live merge | **yes** |
| 11 | Correct AGENTS.md and `ops/README.md` | no |
| 12 | Update the deployment log | no |

Do them in order. 03 needs 01, 02 and 04. 05 needs 03. 07 needs 06. 09 needs 01-08. 10 needs 09.
11 and 12 need 10.
