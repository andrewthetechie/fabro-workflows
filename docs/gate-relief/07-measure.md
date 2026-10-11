# 07: measure (operator, after push and a week of runs)

Not for the implementing agent. After the operator pushes:

- Refuter block rate, split by `not_met` / `unverifiable` / defects / invalid, and how many
  `not_met` cleared after the fix turn. Baseline from #28/#29: 18 blocks, 8 `unverifiable`
  on deploy-only criteria, 10 `not_met` (2026-09-25..10-11).
- Operator-commit rate on factory PRs. Baseline 50/183 (27%); architecture 23/97 (24%).
- Architecture PR open-to-merge time. Baseline p50 5.2h, p90 34h.
- Any architecture PR merged automatically that the operator would have held: the
  #30 risk to watch.

If #22 (scorecard) lands first, read these from it; otherwise stream run summaries
(never export raw events on the host; it filled the disk on 2026-10-11).
