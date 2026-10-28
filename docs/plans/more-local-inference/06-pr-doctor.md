# Task 06: the PR doctor, and its one hosted review

Read `00-overview-and-contracts.md` first (D9, D10, D13, D14; C6, C7). **Invoke
`/fabro-workflow`.** Read `docs/agents/invariants-merge.md` and
`docs/agents/invariants-merge-queue.md` in full, then
`ops/scheduler/src/fabro_scheduler/prfacts.py`. Copy `box-noop` (task 04) for the package
shape.

**Start only after task 12's Phase 1 exit check has passed.**

## Goal

A blocked factory pull request gets one box attempt to fix what its merge-phase report says is
wrong, then one hosted review. If that does not merge it, a human takes it. This is the job that
turns hosted spend already made into merges.

## Rules (D13, D14)

- **Eligible:** an open factory PR (`pr_facts.state = open`) whose last report is `blocked` or
  `needs-human`, whose `**Reason:**` falls in an eligible category, and which has no
  `<!-- fabro:pr-doctor` comment.
- **Eligible categories**, matched on the reason text:
  - unfixed review findings (`There are unfixed findings`);
  - Refuter `not_met` or `unverifiable`;
  - CI still failing after the fix attempts (`CI is still failing`);
  - diff hygiene (`Diff hygiene blocks the merge`).
- **Never eligible:**
  - `config_changed` (a human merges those by design);
  - a `do-not-merge` label;
  - an `ai-review-needs-human` label set by this job;
  - `retryable: ... (transient_infra)`, which wants a re-review and not a fix;
  - an unknown reason. Log it, do not guess.
- **One attempt, ever.** The marker comment is the record. A PR with a marker is never
  enqueued again, whatever happened.

## Steps

1. **`.fabro/workflows/pr-doctor/`**, a background package (R16): every agent stage on `.bg`.
   Inputs are `pr_number` and `coder_pool`. Graph:
   - `claim`: write `/tmp/fabro/box`, fetch the PR (head SHA, base, labels) and its comments.
     Find the last merge-phase report. Exit to `stop` if the PR is merged, closed, already has
     a marker, or carries `do-not-merge`.
   - `prep`: check out the PR head and install deps, as `pr-review`'s `prep` does. Write the
     report's findings, Refuter verdict, hygiene samples and failing check names to
     `/tmp/fabro/doctor/report.md`.
   - `fix` (agent, `.bg`, 45m): fix exactly what the report lists, nothing else. The prompt
     names the four categories and forbids adding skips, deleting tests or editing
     `.fabro/*` (the hygiene rules).
   - `validate`: run the repository's `.fabro/ci.sh` (or `task-check`, as `backlog`'s
     `validate` does). On red, one more `fix` visit with the failure. Red again → `report`
     with `pushed=none`.
   - `push` (the only GitHub write besides the comment):
     - remove `merge-on-green` if present;
     - push the fix commits to the PR branch with the run's identity;
     - then `report`.
   - `report`: post the C6 marker comment with what was fixed, what was not, and the new head.
2. **Stage manifest:** `pr-doctor` stages with `"box": true`; generator and `WF_ORDER`;
   `BACKGROUND_PACKAGES` gains `pr-doctor`.
3. **Scheduler producer** (`ops/scheduler`):
   - On each `pr_facts` refresh, enqueue a `pr-doctor` job for each eligible PR, with
     `typical_box_minutes` 30.
   - Eligibility lives in a pure function with a test per category and per exclusion.
4. **The one hosted review (D14).** When a `pr-doctor` run ends `done` with `pushed=<sha>`, the
   scheduler queues one `pr-review` dispatch for that PR as a **coder loop**:
   - primary lease, lane `coder`;
   - it obeys the peak-hours pause;
   - inputs `pr_number`, `auto_merge` (the repo's switch, as `fire-pr-review.sh` reads it) and
     `coder_pool`.

   Record it on the job (`outcome=review_fired`).
5. **The hand-off.** When that `pr-review` run is released, read the PR's latest report:
   - `merged` or `queued` → job outcome `merged`.
   - Anything else → add `ai-review-needs-human` and post one comment:
     `The PR doctor and one follow-up review did not get this merged; a human needs to take it from here.`
     Job outcome `handed_off`.
   - `pushed=none` (the doctor could not fix it) → hand off at once, with no review.
6. **Tests:** the eligibility table, the one-attempt rule (a marker blocks every later enqueue),
   the review dispatch shape (lease, labels, inputs), and the hand-off on each outcome.
7. **Gate tests** for `claim`'s and `validate`'s shell, following `ops/gate_tests/`.

## Done when

- `make check` and `uv run pytest` pass, and the job is enqueued only behind `SCHEDULER_BACKGROUND`.
- Commit: `feat(pr-doctor): one box fix and one hosted review per blocked PR (more-local-inference 06)`.

**Stop here.** Report to the operator, who runs task 12, Phase 2.
