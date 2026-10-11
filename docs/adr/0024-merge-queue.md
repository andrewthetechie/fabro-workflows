# A scheduled `merge-queue` package merges approved PRs when their checks are green

**Status:** proposed (2026-10-11). D1, D2 and D6 are operator decisions. The operator also
required a fabro workflow, and said that a higher `max_concurrent_runs` is acceptable. The
other decisions were drafted while planning, and the operator can veto them until task 05 of
`docs/fast-path/` lands. The task series is `docs/fast-path/`.

The fast lane (ADR 0023) ends its run when the merge phase approves the PR, so it does not
hold a coder box while GitHub CI runs. Something else must merge the PR when CI is green.
Today the only merge path for an agent PR is `review_merge.watch_checks → merge`, inside a
run that holds a coder box for up to 50 minutes.

## D1. A sixth package, with no agent, on a 10-minute schedule per repository

The package is `.fabro/workflows/merge-queue/`. It has only command nodes. It uses no model
and no coder box, so the coder scheduler does not admit it (`dispatch.py`, `busy_repos`
counts only the scheduler's own leases). There are four automations, one for each
repository, named `merge-queue-<repo>`. Their schedule triggers are staggered so that no two
fire in the same minute: jelly-swipe `0-59/10`, lawncare-saas `2-59/10`,
womens-fantasy-sports `5-59/10` and writers-app `7-59/10` (four distinct residues mod 10,
the ADR 0001 rule). A run sweeps the repository it targets and then ends. It never waits
for CI. A PR that is not ready is handled again on the next tick.

## D2. The `merge-on-green` label and an approval marker are the request

Only the merge phase adds the PR label `merge-on-green`. Its new `report_queued` node does
this when `merge_gate` has passed in queue mode (ADR 0023 D5). Before it adds the label, it
posts the review comment. The first line of that comment is an approval marker with the
head SHA that the phase approved (contract C4 in the series).

Every entry into the merge phase removes `merge-on-green` from the PR (`rm_setup`), so a
later review of the same PR, from `pr-review` or a second `backlog` run, cancels the earlier
approval until it approves again.

## D3. The queue merges only the head that the merge phase approved

A PR is eligible only when all of these are true. The sweeper reads them from GitHub on
every tick.

1. The PR is open, is not a draft, and carries `merge-on-green` and `agent-authored`.
2. It does not carry `architecture` (ADR 0012 D8) or `ai-review-needs-human`.
3. `reviewDecision` is not `CHANGES_REQUESTED`, and no review thread is unresolved.
4. Its head is the approved head, or the queue reached the head from the approved head with
   its own recorded rebases (C5). A marker counts only when the token's own login wrote it.
5. The live title is a Conventional Commit, and the `fabro:commit-body` block holds a body
   that contains `Resolves #`. These are `merge_gate` checks 11 and 12. The squash commit
   uses this title and body, as `merge` does.
6. The host switch `FABRO_AUTO_MERGE` and the repository's `auto_merge` token on its
   `pr-review-<repo>` row both allow a merge. These are the same switches, read the same
   way, that `pr_handoff` reads today.

If rule 4 fails, a push that nobody reviewed reached the branch. The queue then gives the
PR to a human (D7). It does not merge content that the merge phase did not approve. When a
switch is off, the run reports the queue and changes nothing.

## D4. GitHub's state selects one action per PR

| Checks on the head | `mergeStateStatus` | Action |
|---|---|---|
| green | `CLEAN`, `HAS_HOOKS` | **merge**: squash with `--match-head-commit`, at most 5 per tick per repository |
| any | `BEHIND` | **rebase**: one in flight per repository (D5) |
| any | `DIRTY` | **hand off**: `retryable: the PR conflicts with <base>` |
| pending | other | **wait**, and hand off when the head has waited 2 hours |
| red | other | **rerun** once per head, then **hand off** (D6) |
| any | `UNKNOWN` | **wait**: GitHub has not computed mergeability yet |
| green | `BLOCKED`, `UNSTABLE` | **hand off**: branch protection or a check that the rollup does not show blocks the merge |

"Green" uses the buckets of `watch_checks`: at least one check, every check `pass` or
`skipping`, and none `pending`. A merge re-reads the PR first. It retries "Base branch was
modified" as `merge` and `dep_merge` do. It never passes `--delete-branch`. An "already
merged" answer from a concurrent sweep counts as merged. The cap of 5 merges per tick is a
safety valve: a bug in the decision step cannot merge a whole queue at once.

## D5. A rebase uses GitHub's API, and the sweep does not wait for it

The sweeper rebases with GraphQL `updatePullRequestBranch(updateMethod: REBASE,
expectedHeadOid: <head>)`. It needs no checkout and no push, and `expectedHeadOid` makes it
safe when two sweeps overlap. After the rebase it posts a rebase marker (C5) with the old
and new heads, and it does not wait for CI. A later tick merges the PR when CI on the new
head is green.

`BEHIND` appears only where branch protection requires an up-to-date branch. Of the four
repositories, only jelly-swipe has that rule (`docs/dependabot/00-overview-and-contracts.md`,
2026-10-10 state). Task 01 confirms this. In that repository, each merge makes every other
queued PR `BEHIND`. So the queue rebases at most one PR per repository at a time. It
rebases the oldest `BEHIND` PR, and only when no PR that it rebased earlier still waits for
its checks. Rebasing all of them would start one CI run per PR for every merge, and only
one of those runs can produce a merge. In the other three repositories, a PR that is behind
but has no conflict reads `CLEAN` and merges without a rebase. Its CI ran on an older base.
That is the same risk that a human merge has today.

## D6. Red CI is rerun once, then handed to a human

This is the operator's decision of 2026-10-11. When a check on the head fails, the sweeper
reruns the failed jobs of each failing GitHub Actions run once for that head, if the run is
on its first attempt. On a later tick, if that check is still red, or if the failing check
is not an Actions run, the queue hands the PR off. ADR 0011 D2 measured that most red CI on
agent PRs passed on a plain rerun.

## D7. A hand-off removes the label and asks for a human

A hand-off removes `merge-on-green`, adds `ai-review-needs-human` and posts one comment with
a hand-off marker and the reason (C6). A mechanical reason (red CI after the rerun, a
conflict, CI that did not settle in 2 hours, a failed rebase) starts with `retryable:`. This
is the prefix that `mark_needs_human` writes for #19 and that the PR shepherd of #31 reads.
A reason that is a judgment (the head moved without review, branch protection blocks the
merge) has no prefix.

## D8. How the package works around fabro's limits

| Limit | Workaround |
|---|---|
| A schedule trigger fires whether or not its last run is still running or still queued (`automation_scheduler.rs`, `fire_scheduled_automation_run`: no overlap policy). | `prep` lists the active runs and ends the sweep when an older `merge-queue` run for the same repository is still active. Every action is also idempotent: `--match-head-commit`, `expectedHeadOid`, and "already merged" counts as merged. |
| One global cap, `max_concurrent_runs = 4`, with a FIFO queue (ADR 0001, ADR 0005). A tick can wait behind long runs. | Raise the cap from 4 to 8 (the draft said 6; the operator chose 8 on 2026-10-11 so B5's fleet needs no second restart), which needs a fabro restart in a drain window. A tick that starts late is safe because of the guard. Every node has a short timeout, and `stall_timeout` is 15m, so one sweep holds a slot for minutes. |
| A run checks out its target repository and commits a checkpoint after every stage. | `[run.run_branch] push = false` and no `[run.pull_request]`, so 576 runs a day create no branch and no PR. Task 01 measures how long the checkout takes. |
| 576 runs a day in the run store. | A daily host cron job runs `fabro system prune --workflow merge-queue --older-than 7d --yes` (the CLI's `RunsPruneArgs`, `--workflow` filter). Task 01 confirms that the 0.362 binary has it. |
| `fabro-monitor.sh` reads only the newest page of 20 runs (C3 and C4). Sweeps would push a stuck `backlog` run off that page within the hour, and would hide an idle factory. | The monitor, `fabro-export-runs.sh` and the other tools that page `GET /runs` exclude `merge-queue` runs. |
| A hook fires on every run. | Discord gets one `merge-queue-summary` only when a tick merged, rebased, reran or handed off a PR. A repeated stop message is posted at most once an hour per repository. |

## Rejected alternatives

- **A loop in the coder scheduler.** It is deterministic Python with a database, and it uses
  no run slot. The operator wants the sweeper to be a fabro workflow, so that it ships on a
  push to `main` like the other workflows and its runs appear in fabro.
- **GitHub auto-merge (`gh pr merge --auto`).** All four repositories have
  `allow_auto_merge: false`. Auto-merge also needs branch protection, and three of the four
  are private repositories on a free plan with no protection (`GET .../protection` returns
  403).
- **GitHub's merge queue.** GitHub offers it only for repositories that an organization
  owns. These four belong to a personal account. Task 01 records the check.
- **Waiting for CI in the run.** That is the cost this change removes.

## Consequences

- A sixth package: `check-graph-invariants.py` finds it. `ops/test-task-gates.sh` and the
  `AGENTS.md` tables name it explicitly.
- The shared merge phase gains a mode (`/tmp/fabro/merge_mode`), an outcome (`queued`) and
  a report node (`report_queued`). Every graph that imports the phase gets the
  `checkpoint_saved` hook for it (R7). Only `backlog` writes `queue`.
- In jelly-swipe, merges from the queue are serial: about one per CI duration plus up to 10
  minutes. The other three repositories merge as soon as checks are green, up to 5 PRs per
  tick.
- A queued PR loses `ci_fix`. A red check gets one rerun, then a human (or #31).
- Merges happen while nobody watches. The host switch and the per-repository switch stop
  them, as they stop every other agent merge today.
