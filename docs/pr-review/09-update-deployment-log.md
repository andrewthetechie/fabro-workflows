# Task 09 — Record the pr-review workflow in the deployment log

**Depends on:** 07, 08. **LLM level:** local is fine.

**File:** `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` (mode 600, outside the public
repo working tree). Append a dated section; do not edit existing entries — they are a
chronological record and stay true as history.

## What to record

### 1. What the workflow is

A `pr-review` workflow that takes a PR number as a run input, rebases the PR onto its
base tip, reviews it on three axes with `glm-5.3`, applies one round of fixes,
re-verifies with CI, and labels the PR for a human. It never merges.

Note what was deliberately dropped from Sandcastle's `run-pr-review-v1.mts`: the polling
loop, the PR-settling window, and label-based PR discovery — the caller names the PR.

### 2. The design decisions and why

Copy the sixteen operator decisions from `00-overview-and-contracts.md`. The ones a
future reader will most want the reasoning for:

- **`run_branch.enabled = true, push = false`** — `enabled` is what makes checkpoints
  commit between stages, and since `claim` does `gh pr checkout`, those commits land on
  the PR branch. `push = false` stops the stale `fabro/run/<id>` being pushed each
  checkpoint.
- **The rebase agent performs the rebase itself.** A conflicted rebase cannot be handed
  across a stage boundary: fabro's checkpoint would `git add -A` the conflict-marked
  files, which git treats as resolved, and commit markers.
- **One attempt on `coders`, then one on `glm-5.3`, then needs-human** — and
  `rebase_gate` verifies the result from git rather than trusting the agent's claim.
- **`--force-with-lease` rejection stops the run.** Never `--force`.

### 3. The backlog bug this surfaced

The `next_task` mainline-merge conflict path aborted the merge before any agent ran,
then asked an agent forbidden from using git to resolve a conflict that no longer
existed. Unreachable until now because `origin/main` never moved during a run; landing
PRs makes it reachable. Record the fix from task 08.

### 4. The E2E result

Run id, PR number, and the nine claims from task 07 step 5 with what each showed. If any
was not proven, say which and why — an entry claiming a clean pass it did not get is
worse than no entry.

### 5. Operational facts

- Four `pr-review-*` automations, manual-trigger only, one per repo with the matching
  `environment_id`. No schedule trigger — there is nothing to poll.
- How to fire one (the `curl` with `{"inputs":{"pr_number":N}}`).
- The two labels: `ai-review-complete`, `ai-review-needs-human`.
- `ops/provision-server-state.sh` now provisions eight automations, not four.

### 6. Known limits

- No hooks. Discord notifications are wired to backlog's node ids and would need new
  matchers; a follow-up once this has run a few times.
- The rebase logic is **duplicated** between the two workflows rather than shared.
  Sharing needs either a proven `stack.manager_loop` child workflow or a proven
  cross-directory `@../` prompt import; neither is exercised in this deployment.
  Revisit when the copies drift.
- Backlog still **merges** rather than rebases, so its PRs carry merge commits that
  pr-review then rebases past. If that proves messy in practice, switching backlog to
  rebase-per-task is the fix.

## Done when

- The section exists, is dated, and reads as a record of what happened.
- The E2E run id and PR link are in it.
- The backlog bug and its fix are recorded.
- **No credential appears.** Reference where secrets live, never a value.

## Pitfalls

- Do not edit earlier entries. Append and, where something is superseded, say so
  explicitly in the new section.
- The log lives at `~/.fabro-deploy/docs/`, not in the fabro checkout — task 10 of the
  backlog series moved it out of a public repo's working tree. Do not recreate a copy
  inside the checkout.
