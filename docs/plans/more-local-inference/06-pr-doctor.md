# Task 06: the PR doctor, and its one hosted review

Read `00-overview-and-contracts.md` first (D9, D10, D13, D14; C6, C7). **Invoke
`/fabro-workflow`.** Read `docs/agents/invariants-merge.md` and
`docs/agents/invariants-merge-queue.md` in full, then
`ops/scheduler/src/fabro_scheduler/prfacts.py`, `store.py` (`leases`, `run_history`),
`reconcile.py`, and `.fabro/workflows/backlog/scripts/fire-pr-review.sh` (how a `pr-review`
run is built: version, automation row, `auto_merge`, `environment_id`). Copy `box-noop`
(task 04) for the package shape.

**Start only when the overview's Status line records Phase 1 as passed.**

## Goal

A blocked factory pull request gets one box attempt to fix what its merge-phase report says is
wrong, then one hosted review. If that does not merge it, a human takes it. This is the job that
turns hosted spend already made into merges.

## Rules (D13, D14)

- **Eligible:** an open factory PR (`pr_facts.state = open`) whose last report is `blocked` or
  `needs-human`, whose `**Reason:**` falls in an eligible category (step 1), and which has no
  `<!-- fabro:pr-doctor` comment. Its `ai-review-needs-human` label does **not** exclude it:
  the merge phase puts that label on every needs-human PR.
- **Never eligible:**
  - a `config_changed` hygiene block (a human merges those by design);
  - a `do-not-merge` label;
  - a `retryable: ...` reason (`transient_infra`), which wants a re-review and not a fix;
  - a reason the table in step 1 does not map. Log it, do not guess.
- **One attempt, ever.** The marker comment is the record, and the doctor's last node posts it
  before any other GitHub write (C6). A PR with a marker is never enqueued again, whatever
  happened.

## Steps

1. **The reason table.** List every place that writes `merge_block_reason` or
   `needs_human_reason` in `.fabro/workflows/_shared/review-merge/review-merge.fabro` and
   `ralph/workflow.fabro`, with the exact text each writes (about 30). Put them in
   `prfacts.py` (or a new `doctor.py`) as a table from a text prefix to a category:
   - `findings`: unfixed review findings (`There are unfixed findings`, and the
     error-severity-finding variant);
   - `refuter`: the Refuter's `not_met` or `unverifiable` blocks (find their exact text in
     `refute_gate` and the final gate);
   - `ci`: CI still red after the fix attempts (`CI is still red after the maximum number of
     fix attempts.`, `CI is still failing after two fix attempts`);
   - `hygiene`: `Diff hygiene blocks the merge:`, except when the report's hygiene section
     names `config_changed`;
   - `none`: every other text, which is never eligible.

   One test per text. A reason seen in `pr_facts` that matches no prefix is logged once per PR.
2. **`.fabro/workflows/pr-doctor/`**, a background package (R16): every agent node and the `*`
   rule on `.bg`'s model, the `box-guard` hook, no `box-claim`. Inputs are `pr_number` and
   `coder_pool`. Graph:
   - `claim`: write `/tmp/fabro/box`; fetch the PR (head SHA, base, labels) and its comments.
     Find the last merge-phase report. Exit to `stop` if the PR is merged or closed, already
     has a marker, or carries `do-not-merge`.
   - `prep`: check out the PR head and install deps, as `pr-review`'s `prep` does. Write the
     report's findings, Refuter verdict, hygiene samples and failing check names to
     `/tmp/fabro/doctor/report.md`.
   - `fix` (agent, `.bg`, 45m): fix exactly what the report lists, nothing else. The prompt
     names the four categories and forbids adding skips, deleting tests or editing
     `.fabro/*` (the hygiene rules). Commits use the factory's identity, the one `backlog`'s
     run commits carry (`andrews-ai-agent`; `DEFAULT_AUTOMATION_AUTHOR`), so the scorecard does
     not count them as operator commits.
   - `validate`: run the repository's `.fabro/ci.sh` (or `task-check`, as `backlog`'s
     `validate` does). On red, one more `fix` visit with the failure. Red again →
     `publish` with nothing to push.
   - `publish`, the only node that writes to GitHub, in this order:
     1. post the marker comment with `pushed=pending` (or `pushed=none` when there is nothing
        to push), and what was fixed and what was not;
     2. with something to push: remove `ai-review-needs-human` and `merge-on-green`, push the
        fix commits to the PR branch, then edit the comment to `pushed=<new head>`.
     A failure after step 1 leaves `pushed=pending`; the scheduler resolves it (step 5).
3. **Stage manifest:** `pr-doctor` stages with `"box": true` on its agent nodes; generator,
   `WF_ORDER` and `coder_box_stages`; `BACKGROUND_PACKAGES` gains `pr-doctor`;
   `workflow_version.py` `ENTRYPOINTS` gains `pr-doctor` and `pr-review`.
4. **Scheduler producer** (`ops/scheduler`):
   - On each `pr_facts` refresh, enqueue a `pr-doctor` job (priority 1, `typical_box_minutes`
     40, which includes prep and validation) for each eligible PR. `prfacts.py` learns to read
     the doctor's marker.
   - Eligibility lives in a pure function with a test per category and per exclusion.
5. **Ending a doctor job.** Read the PR's marker when the run ends:
   - no marker, and the run was cancelled → back to `queued` (nothing was written);
   - `pushed=<sha>` → fire the review (step 7);
   - `pushed=none` → hand off at once, with no review (step 8);
   - `pushed=pending` (the run died inside `publish`) → if the PR's head is no longer the
     marker's `head=`, treat the head as pushed and fire the review; otherwise hand off.

   A doctor job with a marker is never requeued.
6. **`pr-review` as a scheduler lease (D14).** The scheduler leases only issue runs today.
   - Schema: `leases` and `run_history` gain `target_kind TEXT NOT NULL DEFAULT 'issue'`
     (`'issue'` or `'pr'`); for `'pr'`, `issue_number` holds the PR number. Add the columns the
     way `store.py` already adds columns to existing tables, and test an upgrade from today's
     schema.
   - `build_pr_review_intent`: inputs `pr_number`, `auto_merge` and `coder_pool`; labels
     `source=scheduler`, `lane=coder`, `box=<pool>`, `pr=<n>`. Read `auto_merge` and
     `environment_id` from the repo's `pr-review-<repo>` automation row, as `fire-pr-review.sh`
     does; refuse (log, hand off) when the row is missing.
   - Release (`reconcile.py`): a `'pr'` lease makes **no** issue label writes; it only frees
     the box and records the run.
   - Order: a pending doctor review is dispatched before any issue, on the next free box that
     is not drained, and it obeys the peak-hours pause.
7. **The one hosted review.** Dispatch it through step 6, and record it on the job
   (`outcome=review_fired`, with the run id).
8. **The hand-off.** When that `pr-review` lease is released, read the PR's latest report:
   - `merged` or `queued` → job outcome `merged`.
   - Anything else → add `ai-review-needs-human` and post one comment:
     `The PR doctor and one follow-up review did not get this merged; a human needs to take it from here.`
     Job outcome `handed_off`.
   - A hand-off without a review (step 5) does the same, with outcome `handed_off_no_review`.
9. **Tests:** the reason table, the eligibility table, the one-attempt rule (a marker blocks
   every later enqueue), each branch of step 5, the schema upgrade, the review dispatch shape
   (lease, labels, inputs), release without label writes, the queue order, and the hand-off on
   each outcome.
10. **Gate tests** for `claim`'s, `validate`'s and `publish`'s shell, following
    `ops/gate_tests/`. `publish` with a stubbed `gh` and `git`: the marker is posted before the
    label removal and the push, and a failed push leaves `pushed=pending`.

## Done when

- `make check` and `uv run pytest` pass, and the job is enqueued only behind `SCHEDULER_BACKGROUND`.
- Commit: `feat(pr-doctor): one box fix and one hosted review per blocked PR (more-local-inference 06)`.

Then add task 11's Phase 2 rows (the PR doctor in `CONTEXT.md` if it needs a term, the package
in `AGENTS.md` *Layout*, the `pr-review` lease in `ops/README.md`), in their own commit, and
**stop**. Report to the operator, who runs task 12, Phase 2.
