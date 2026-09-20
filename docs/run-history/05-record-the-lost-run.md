# Record the run fabro lost

## Tracer-Bullet Outcome
Fabro answers `404` for a run the scheduler is holding a lease on. The lease is released
and the issue requeued exactly as today — and now a row appears with `kind="lost"`
instead of the run vanishing without trace.

## User Story
As the operator, I want a run fabro has lost to still appear in the history, because it
held a coder instance for however long it held the lease, and a box-hour that is
unaccounted for is the one the history page exists to stop me reconstructing by hand.

## Description
One call site. The 404 branch of `reconcile_leases` releases through `_requeue` with no
`outcome`, which task 04 made mean "release without archiving". Give it an outcome.

This path is different from the three terminal ones in a way that matters: **there is no
run projection at all.** Fabro has no such run, so there is no `kind`, no `reason` and no
`timestamps.completed_at` to read. The row is built from the lease alone, with `kind` and
`reason` written as literals and `finished_at` left to the store's release-time fallback.

## Context Pack
- Source decisions: ADR 0008 decision 4 — a run fabro has lost is recorded as
  `kind="lost"`; the ADR's release-paths table names `reason="fabro 404"` as the literal.
- Repo facts: this branch already requeues the issue and logs
  `"reconcile: %s#%s run %s lost by fabro; requeued"`. It does **not** bump
  `requeue_counts`, and this task must not start.
- Non-goals: do not change when the 404 branch requeues, do not add a counter bump, do
  not fetch anything. The orphan path is task 06.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/reconcile.py     (the 404 branch)
  ops/scheduler/tests/test_reconcile.py              (extend)
  ```

- Interfaces and names:

  **The code to change**, copied verbatim from `reconcile.py:229-245`, inside
  `for lease in leases.active():`:

  ```python
          try:
              run = fabro_client.get_run(lease.run_id)
          except FabroError as exc:
              if exc.status_code == 404:
                  _requeue(store, leases, lease, github_token)
                  actions.append(
                      ReleaseAction(
                          lease, "released+requeued", "fabro has no such run (404)"
                      )
                  )
                  log.info(
                      "reconcile: %s#%s run %s lost by fabro; requeued",
                      lease.repo, lease.issue_number, lease.run_id,
                  )
              else:
                  log.error(
                      "reconcile: %s#%s run %s: fabro error, keeping lease: %s",
                      lease.repo, lease.issue_number, lease.run_id, exc,
                  )
                  actions.append(ReleaseAction(lease, "failed", str(exc)))
              continue
  ```

  **Change only the `_requeue` call:**

  ```python
              if exc.status_code == 404:
                  # No run projection exists -- fabro has no such run -- so the row
                  # is built from the lease alone. `finished_at` is left to the
                  # store's release-time fallback, which is the only answer there is.
                  _requeue(
                      store, leases, lease, github_token,
                      outcome=RunOutcome(
                          kind=LOST_KIND,
                          reason=LOST_REASON,
                          requeue_attempt=store.requeue_count(
                              lease.repo, lease.issue_number
                          ),
                      ),
                  )
  ```

  Add the two literals beside the existing reason constants near the top of
  `reconcile.py` (which already declares `TERMINAL`, `INFRA_CATEGORY`,
  `TERMINATED_REASON`, `CANCELLED_REASON`, `REQUEUE_REASONS`, `MAX_REQUEUES`):

  ```python
  # The ending fabro cannot describe, because it no longer has the run. Not one of
  # fabro's kinds -- `TERMINAL` is exactly `succeeded | failed | dead` -- and
  # deliberately so: this is the scheduler's own word for "the box was held, and
  # fabro cannot say by what".
  LOST_KIND = "lost"
  LOST_REASON = "fabro 404"
  ```

  `_requeue`'s signature after task 04:

  ```python
  def _requeue(
      store: Store, leases: LeaseStore, lease: Lease, github_token: str,
      *, outcome: RunOutcome | None = None,
  ) -> None: ...
  ```

- Verified external contracts:

  `FabroError` carries the HTTP status, which is how this branch is selected
  (`fabro.py`, and `FabroClient.get_run`'s docstring at `fabro.py:625-632`):

  > Raises `FabroError` on a non-2xx; a `404` (status_code 404) is the "fabro never
  > heard of this run" case that recovery treats as lost.

- Behavior rules:
  - `kind` is `"lost"` — a fifth value beyond fabro's three terminal kinds, and the
    schema comment in task 01 already lists it.
  - `reason` is the literal `"fabro 404"`.
  - `finished_at` is **not** set on the outcome. The store writes the release time.
  - `category` is `None`. There is no failure event to read and no call to make.
  - `requeue_attempt` is the **current** count, read but not bumped. The 404 branch has
    never bumped `requeue_counts` and this task does not change that — adding a bump would
    change when a lost run exhausts its budget, which is requeue behavior, not recording.
  - The non-404 branch is untouched: a transient fabro error keeps the lease and writes
    no row, because the run is not provably over.

- Error and security rules: no new failure mode. `archive_and_release` inside `_requeue`
  returns `None` for a lease that is not there and the pass continues.

## Acceptance Criteria
- [ ] A lease whose run returns `404` releases, requeues the issue, and leaves one
      `run_history` row with `kind="lost"` and `reason="fabro 404"`.
- [ ] That row carries the lease's `coder_pool`, `repo`, `issue_number` and
      `dispatched_at`, and a `finished_at` that is the release time.
- [ ] `category` is `None` and `pr_lookup` is `"none"` on that row.
- [ ] A lease whose run returns `500` keeps its lease and writes **no** row.
- [ ] `requeue_counts` is unchanged by the 404 path — a first 404 still records
      `requeue_attempt = 0`.
- [ ] The existing 404 test in `test_reconcile.py` still passes: the issue is still
      requeued and the `ReleaseAction` is still `("released+requeued", "fabro has no such
      run (404)")`.

## Test Expectations
Framework: **pytest 8** with **respx**. Command:
`cd ops/scheduler && uv run pytest tests/test_reconcile.py`.
Extend `ops/scheduler/tests/test_reconcile.py`, reusing `_history` from task 04.

```python
@respx.mock
def test_a_run_fabro_lost_is_recorded_as_lost(config, store, leases, fabro):
    leases.acquire(_lease())
    _install_labels()
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(404, json={}))

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    (row,) = _history(store)
    assert row["kind"] == "lost"
    assert row["reason"] == "fabro 404"
    assert row["category"] is None
    assert row["pr_lookup"] == "none"
    assert row["coder_pool"] == "coders-a"
    assert row["issue_number"] == 7
    assert row["dispatched_at"] == "2026-09-18T12:00:00+00:00"
    assert row["finished_at"]                      # the release time, non-empty
    assert row["requeue_attempt"] == 0
    assert [a.outcome for a in actions] == ["released+requeued"]
    assert leases.active() == []


@respx.mock
def test_a_transient_fabro_error_writes_no_row_and_keeps_the_lease(config, store, leases, fabro):
    leases.acquire(_lease())
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(500, json={}))

    actions = reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert _history(store) == []
    assert len(leases.active()) == 1
    assert [a.outcome for a in actions] == ["failed"]
```

`_lease()`'s `dispatched_at` is `datetime(2026, 9, 18, 12, 0, tzinfo=UTC)`
(`tests/test_reconcile.py:78-87`), which is where the expected `dispatched_at` literal
above comes from.

## Dependencies
- Blocked by: `Write a row on every terminal release`
- Why blocked: supplies `_requeue`'s `outcome` parameter and the archiving release it
  performs; without it this branch has nothing to pass.
- Blocks: `Deploy and verify on the host`

## Labels
`feature`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
2 - One call site and two literals. The requeue behavior it sits on is untouched, and the
existing 404 test is the guard.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full.
