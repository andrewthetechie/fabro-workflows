# Task 04: scheduler: the body hash, a precheck per hash, the hold and the sharpen queue

Read `00-overview-and-contracts.md` first (D3, D5, D13, D15–D17; C7, C11). Read `ops/README.md`
*The coder scheduler*, `precheck.py`, `background.py` (`_apply_hold`, `_record_marker`,
`outcome_for`), `queue.py`, `inventory.py`, `github.py` (the issue list and `Issue`),
`store.py` (the background job table and `background_job_exists`), `remainder.py` (label order)
and `config.py`. Read the scheduler tests for `precheck.py` and `calibration.py`.

## Goal

The scheduler knows each queued issue's body hash. It prechecks every new hash, holds an
`unverifiable` issue, and keeps a `sharpen` queue that task 05 dispatches from. Nothing is
dispatched in this task.

## Steps

1. **Body hash in the issue cache.** The inventory's issue list response carries `title` and
   `body`. Compute `body_hash` with the Facts formula, add it to `Issue` and to the cached issue
   row, with a store migration (`NULL` for old rows, filled on the next poll). Write one test that
   pins the Python hash to the shell pipeline's output for one fixture, including a body that is
   `null` and one with a trailing newline.
2. **A precheck per body hash** (D13).
   - Precheck jobs record the hash they were queued for: a new nullable `target_hash` column on
     the background job table, or a field in `inputs`, whichever the table's code makes simpler.
     Say which in the commit message.
   - `PrecheckProducer` skips an issue when a precheck job exists for `(repo, number, body_hash)`,
     or when a `sharpen` row has `applied_hash == body_hash` and a non-empty `recheck` (C11).
   - The outcome recorded from the marker keeps `verdict=<v>` first, then adds
     ` body=<hash>`, so existing readers of `verdict=` keep working. Check every reader of
     `outcome` (calibration, measure, `_apply_hold`) against the new form.
3. **The `sharpen` table.** Columns: `id`, `repo`, `issue_number`, `body_hash`, `state`
   (`queued|running|applied|capped|churn|stale|cannot_sharpen|not_needed|failed|superseded`),
   `attempts`, `next_eligible_at`, `run_id`, `applied_hash`, `recheck`, `round`, `rule`,
   `transformations`, `created_at`, `finished_at`, `box_minutes`. At most one non-terminal row per
   `(repo, issue_number)`. Store methods for queue, claim, finish, retry and supersede, with tests.
4. **The hold** (C7, D16):
   - When a precheck job finishes `done` with `verdict=unverifiable` and a hash, and C7's rule 2
     holds: add `precheck-unverifiable`, then insert the `sharpen` row. A label write that fails
     is logged and retried on the next inventory pass; the row is not inserted without the label.
   - On each inventory pass, a `queued` row whose issue's current `body_hash` differs from the
     row's becomes `superseded`, and the label is removed. A `running` row is left to its run:
     `apply`'s stale check handles it.
   - Remove `unverifiable` from what `SCHEDULER_PRECHECK_HOLD_VERDICTS` may name, and from
     `DEFAULT_PRECHECK_HOLD_VERDICTS`. A value that names it is a `ConfigError` whose message
     points at ADR 0033. The live `.env` (`already_done,blocked`) is unaffected.
   - `queue.build_queue` already drops `precheck-*` issues, so check that a sharpen-held issue
     appears on the queue page under *Held by precheck*, with the reason "being sharpened"
     (queued or running) and a link to the issue.
5. **Back-fill** (D17), once per scheduler start, after the first inventory poll. For every
   queued issue whose newest `done` precheck job's outcome is `verdict=unverifiable` (any hash,
   or none), with no `sharpen` row for that issue at all: hold it and insert a row with
   `body_hash` = the current hash. Log the count. It is idempotent: a second start finds the rows.
6. **Onboarding label.** Add `precheck-unverifiable` to
   `skills/setup-repo-for-factory/data/labels.toml` with the description "Held while the factory
   sharpens its acceptance criteria". Update `tests/test_onboard.py`, which ties the
   `precheck-*` labels to `HOLD_VERDICTS`, so that the sharpen label is accounted for.
7. **Tests** (`ops/scheduler/tests/`):
   - the hash pin;
   - a new precheck on a changed hash, and none on an unchanged one or on a hash `recheck`
     already judged;
   - the hold on `unverifiable` (label then row), and no hold on a hash a sharpen row names as
     `body_hash` or `applied_hash`;
   - a failed label write (no row);
   - supersede on a body change while queued, and no supersede while running;
   - the `ConfigError`;
   - the back-fill: once, idempotent, skips issues with a row;
   - the queue page's held list.

## Done when

- `uv run pytest` and `make check` pass.
- The commit is `feat(scheduler): precheck per body hash, the unverifiable hold and the sharpen queue (issue-sharpening 04)`.
