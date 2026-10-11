# Invariants: the merge queue

Read before changing `.fabro/workflows/merge-queue/`, the `report_queued` node in `_shared/review-merge/`, or the host's cap, schedules and run retention for it. The contracts are in `docs/fast-path/00-overview-and-contracts.md` (C4-C8, C10); the decisions are ADR 0024.

Every rule here passes `fabro validate` and fails at runtime, usually silently.

| Rule | What happens otherwise |
|---|---|
| The queue merges only the **expected head**: the newest approval marker's head, followed through rebase markers whose `from` equals the previous head (C7) | Any other push is unreviewed code. The PR is handed off, never merged. |
| A marker counts only when its comment author's login equals the run token's login (`gh api user`, read in `prep`) | Anyone who can comment could forge an approval and get a PR squash-merged. |
| `decide` is a pure `jq` program over `prs.json` and `env.json`: no network call, no clock except `env.json`'s `now` | Its fixtures could no longer prove the policy. |
| The queue never waits, never checks out a PR and never pushes; it merges, rebases (`updatePullRequestBranch`), reruns and hands off through the GitHub API | A waiting sweep holds a run slot; a push from the queue would be an unreviewed head. |
| The queue never uses `--admin`, and merges with `--match-head-commit` | `--admin` bypasses branch protection; without the match a head pushed after the read would merge. |
| After `updatePullRequestBranch` the new head is **re-read** (the mutation returns the old one) and recorded in a rebase marker; a marker that cannot be posted makes the next tick hand the PR off | Recording the mutation's return value would write a marker whose `to` never matches. |
| Overlapping sweeps are idempotent: `expectedHeadOid`, `--match-head-commit`, "already merged" counts as merged, and `prep` skips when an older `merge-queue` run for the repository is active | fabro has no overlap policy; two sweeps would double-rebase or double-comment. |
| The guard filters `GET /runs` client-side on `.automation.id`; the endpoint ignores `workflow=` | The guard would count other repositories' sweeps, or none. |
| `merge-queue` runs are excluded from every ops tool that reads a fixed page of runs (`fabro-monitor.sh` C3/C4, `fabro-export-runs.sh --workflow all`) | 576 runs a day push the real runs out of the page, and the monitor goes blind. |
| The schedules are created disabled and enabled by the operator; `max_concurrent_runs` is 6 and changes only by drain, edit, restart, `StartedAt` check | A change without a restart is reported but not enforced. |
