# 06: validate everything, update baselines, stop

1. `make check` (full, ~90s). Must pass. Paste the tail into your report.
2. `make check-host` (read-only, ssh `andrew@10.10.0.32`). It runs `fabro validate` in the
   container against the working tree. Expected: `Backlog`, `PrReview`, `Dependabot` each
   gain the nodes/edges from task 05 (2 nodes; count the edges you added); `IssueTriage`
   and `ArchReview` unchanged unless task 03 added nodes; `MergeQueue` unchanged. The two
   deliberate warnings stay; no new warnings. If check-host compares against the counts
   in `AGENTS.md`, update the `Baselines` paragraph there with the new counts and today's
   date, then re-run. If ssh is unavailable, say so and do not guess the counts.
3. `docs/README.md` row for `docs/gate-relief/`: implemented locally, not pushed. Update
   this series' `00-overview-and-contracts.md` Status line with the commit SHAs.
4. Update `docs/agents/invariants-merge.md` with the new invariants: `refute_fix_attempts`
   is never reset in-run; the fixer's sealed paths; `do-not-merge` is the hold label;
   post-deploy checks are stripped in `refute_prep`.
5. `git log --oneline origin/main..HEAD` and `git status`. **Do not push.** Report to the
   operator: the commits, the check outputs, the new baselines, and every place you
   deviated from this plan and why.
