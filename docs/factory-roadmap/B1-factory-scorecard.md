# B1 · Factory scorecard and eval harness

**Status:** proposed. **Axis:** enabler for all three. **Effort:** M. **Feasibility:** high.
**Depends on:** nothing. **Start:** alongside A1/A2. A baseline taken after A3–A5 land is
not a baseline.

## Problem

Every change in this roadmap needs "did it help?" answered, and today the answer is assembled
by hand from the run store, as for the 2026-09-24 merge-rate review (ADR 0011). The scheduler's
`/history` page records dispatch, outcome, PR and `merged` per run
(`ops/scheduler/src/fabro_scheduler/store.py`, `run_history`). It does not record the
numbers that decide whether a lights-off factory is working:

| Metric | Why it matters | Source |
|---|---|---|
| **Revert rate**: factory PRs reverted, or re-touched by a `fix:` commit on the same lines, within 14 days | the best proxy for code quality after merge | `gh api` over merged `agent-authored` PRs, plus `git log -L` |
| **Human-touch rate**: factory PRs with a human commit before merge | how often a human finished the work | PR commits API |
| **Auto-merge rate**: merged by the workflow vs by a human vs closed | the ADR 0011 headline number | `run_history` plus PR `mergedBy` |
| **Box-hours per merged PR** | throughput | `run_history` |
| **Rework tier reached, per repo and module** | where the local coder is too weak | stage list per run (fabro API) |
| **Proposal acceptance**: `proposed` → `agent` vs closed | whether `arch-review` earns its box time | GitHub labels timeline |
| **Tokens and cost per merged PR, by model** | cost | `GET /api/v1/runs/{id}/usage` |
| **Refuter and hygiene precision** (after A4/A5) | whether the gates block the right things | PR outcome after a block |

## Design

- A **nightly job in the scheduler** (it already has the GitHub token, the run history, a
  SQLite store and a page) writes one `pr_outcomes` row per factory PR once the PR is merged
  or closed, and re-checks merged PRs at day 14 for revert and re-fix.
- Per-stage numbers come from fabro's **insights SQL API** (`POST /api/v1/insights/execute`,
  present on 0.354 and 0.362) rather than walking event logs. Save the queries in fabro
  (`/api/v1/insights/queries`) so the operator can run them from the web UI too.
- A `/scorecard` page (a weekly table per repo) and one Discord line a week.
- A **seeded-defect suite**: a scratch repository with about 20 known-bad branches, and a
  scheduled `pr-review` fire against each, weekly or on demand, recording what the merge
  phase caught. A5 uses it first. Any change to a merge-phase prompt or model re-runs it.

## Verification

Backfill: run the nightly job over 2026-09-19..today and check its auto-merge numbers against
ADR 0011's 4-of-33 for the same window.

## Open questions

1. The revert detection heuristic (a `revert:` commit, or a `fix:` commit touching the same
   hunks within 14 days) will over-count shared hot files. Report both "strict" (true revert)
   and "loose" columns.
2. `research_improvements/03` item 3 (`[run.artifacts]`) stays unneeded: the scorecard reads
   the run store and GitHub, not sandbox artifacts.
