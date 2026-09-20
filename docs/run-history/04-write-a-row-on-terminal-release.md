# Write a `run_history` row on every terminal release

## Tracer-Bullet Outcome
A backlog run reaches a terminal state, the reconcile pass frees its box, and a durable
row appears naming the coder pool, the repo, the issue, the run, when it was dispatched,
when it finished, how it ended and which requeue attempt it was. All three terminal
branches, including the one that goes through `_requeue`.

## User Story
As the operator, I want every finished scheduler-dispatched run recorded when its box is
freed, so that "what has the factory been doing" is a query rather than an ssh session
and a hand-read deployment log.

## Description
Wire `_outcome_from_run` and `archive_and_release_lease` into the three terminal exits of
`reconcile_leases`. This is the first task that changes production behavior.

`reconcile_leases` has four exits and a fifth path lives in the GitHub pass. This task
takes the three terminal ones. The 404 exit is task 05; the orphan path must keep writing
nothing, which task 06 guards.

The awkward part is that the requeue branch releases through the shared `_requeue`
helper, which is also what the 404 path and the orphan path call. `_requeue` therefore
gains an **optional** `outcome` parameter: pass one and it archives, pass nothing and it
releases exactly as today. That keeps one helper and makes the orphan path's behavior a
default rather than a special case.

A second ordering change: `requeue_count` is currently read inline to compute `spent`.
Hoist it into a named local so all three branches can record which attempt the run was,
and bump the counter **before** `_requeue` on the requeue branch so the stored attempt
number is the one this run became.

## Context Pack
- Source decisions: ADR 0008 — the row is written when the lease is released, in the
  release transaction; `requeue_attempt` is an integer.
- Repo facts: `_requeue` is called from three places — the 404 exit (`reconcile.py:232`),
  the terminal requeue branch (`reconcile.py:282`) and the GitHub orphan pass
  (`reconcile.py:494`). Its label writes are best-effort and it never raises.
  `bump_requeue_count` returns the new total and does its read and write in one statement
  under one lock.
- Non-goals: the 404 path (task 05), the orphan guard (task 06), the PR lookup (task 08).
  `pr_lookup` stays at its `"none"` default on every row this task writes.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/reconcile.py     (the three terminal branches, _requeue)
  ops/scheduler/tests/test_reconcile.py              (extend)
  ```

- Interfaces and names:

  **The code to change**, copied verbatim from `reconcile.py:280-318`. The `spent` line
  and all three `leases.release` / `_requeue` calls are inside the `for lease in
  leases.active():` loop, after `classified = _with_category(run, lease, fabro_client)`:

  ```python
          spent = store.requeue_count(lease.repo, lease.issue_number) >= MAX_REQUEUES
          if should_requeue(classified) and not spent:
              _requeue(store, leases, lease, github_token)
              attempt = store.bump_requeue_count(lease.repo, lease.issue_number)
              actions.append(
                  ReleaseAction(lease, "released+requeued", _classify(run))
              )
              log.info(
                  "reconcile: %s#%s run %s terminal (%s); requeued (%d of %d)",
                  lease.repo, lease.issue_number, lease.run_id, _classify(run),
                  attempt, MAX_REQUEUES,
              )
          elif should_requeue(classified):
              store.forget_issue(lease.repo, lease.issue_number)
              leases.release(lease.run_id)
              actions.append(
                  ReleaseAction(lease, "released", f"{_classify(run)}, requeue budget spent")
              )
              log.error(...)
          else:
              store.forget_issue(lease.repo, lease.issue_number)
              store.clear_requeue_count(lease.repo, lease.issue_number)
              leases.release(lease.run_id)
              actions.append(ReleaseAction(lease, "released", _classify(run)))
              log.info(
                  "reconcile: %s#%s run %s terminal (%s); released, not requeued",
                  lease.repo, lease.issue_number, lease.run_id, _classify(run),
              )
  ```

  **Replace it with** (log calls and `ReleaseAction` appends unchanged, elided here only
  where they are byte-for-byte identical — keep them):

  ```python
          attempts = store.requeue_count(lease.repo, lease.issue_number)
          spent = attempts >= MAX_REQUEUES
          if should_requeue(classified) and not spent:
              # Bumped BEFORE the release so the stored attempt number is the one
              # this run became, not the one it started as.
              attempt = store.bump_requeue_count(lease.repo, lease.issue_number)
              _requeue(
                  store, leases, lease, github_token,
                  outcome=_outcome_from_run(classified, requeue_attempt=attempt),
              )
              actions.append(
                  ReleaseAction(lease, "released+requeued", _classify(run))
              )
              log.info(...)          # unchanged
          elif should_requeue(classified):
              store.forget_issue(lease.repo, lease.issue_number)
              leases.archive_and_release(
                  lease.run_id,
                  _outcome_from_run(classified, requeue_attempt=attempts),
              )
              actions.append(...)    # unchanged
              log.error(...)         # unchanged
          else:
              store.forget_issue(lease.repo, lease.issue_number)
              # Read the attempt count BEFORE clearing it: the row records how many
              # requeues this issue took to get here, and `clear_requeue_count` is
              # the issue leaving the queue on its own terms.
              leases.archive_and_release(
                  lease.run_id,
                  _outcome_from_run(classified, requeue_attempt=attempts),
              )
              store.clear_requeue_count(lease.repo, lease.issue_number)
              actions.append(ReleaseAction(lease, "released", _classify(run)))
              log.info(...)          # unchanged
  ```

  **`_requeue`'s current signature and body**, copied verbatim from `reconcile.py:340-370`:

  ```python
  def _requeue(
      store: Store,
      leases: LeaseStore,
      lease: Lease,
      github_token: str,
  ) -> None:
      """Requeue = remove the receipt, restore the queue label, restore the item with
      its original wait, then free the box. ..."""
      for label, action in (
          (IN_PROGRESS_LABEL, _remove_label),
          (REQUIRED_LABEL, _add_label),
      ):
          try:
              action(lease.repo, lease.issue_number, label, github_token)
          except GitHubError as exc:
              log.error(
                  "reconcile: requeue %s#%s: could not %s %s: %s",
                  lease.repo, lease.issue_number, action.__name__, label, exc,
              )
      store.requeue(lease.repo, lease.issue_number, first_seen=lease.queued_since)
      leases.release(lease.run_id)
  ```

  **Change only its signature and its last line:**

  ```python
  def _requeue(
      store: Store,
      leases: LeaseStore,
      lease: Lease,
      github_token: str,
      *,
      outcome: RunOutcome | None = None,
  ) -> None:
      """... (docstring unchanged, plus:)

      `outcome` is what the run turned out to be, when the caller knows. With one
      the release is archived into `run_history`; without one it is a plain
      release. The default is `None` because one caller genuinely has nothing to
      record: the GitHub orphan pass synthesises a lease with an empty `run_id`
      and no run behind it at all.
      """
      # ... the label loop and store.requeue call are unchanged ...
      store.requeue(lease.repo, lease.issue_number, first_seen=lease.queued_since)
      if outcome is None:
          leases.release(lease.run_id)
      else:
          leases.archive_and_release(lease.run_id, outcome)
  ```

  The `LeaseStore` method this calls, added in task 02:

  ```python
  def archive_and_release(self, run_id: str, outcome: RunOutcome) -> Lease | None: ...
  ```

  The helper from task 03:

  ```python
  def _outcome_from_run(run: Mapping[str, object], *, requeue_attempt: int = 0) -> RunOutcome: ...
  ```

- Verified external contracts: None new. `classified` is the run projection with
  `_last_failure` already attached by `_with_category`, which this task does not touch.

- Behavior rules:
  - Pass `classified`, not `run`, to `_outcome_from_run`. `classified` is the same dict
    with `_last_failure` attached; `run` would silently lose every category.
  - The requeue branch bumps the counter **before** releasing, so the row records the
    attempt this run became. A run requeued for the first time records
    `requeue_attempt = 1`.
  - The non-requeued branch archives **before** `clear_requeue_count`, so a run that
    succeeded on its third attempt records `requeue_attempt = 2` — the two requeues it
    took to get there — rather than `0`.
  - `pr_lookup` is left at its default `"none"` on every row. Task 08 is what changes it.
  - Do not change `_with_category`, `should_requeue`, `is_terminal`, `_classify`, the
    `ReleaseAction` values or any log line. A test asserting on the log text or the
    action strings must keep passing.
  - Do not touch the 404 branch at `reconcile.py:232-241` — task 05 owns it, and it still
    calls `_requeue` with no `outcome`, which now means "release without archiving". That
    is a temporary, correct intermediate state.

- Error and security rules: no new failure mode. `archive_and_release` returns `None` for
  a lease that is not there, exactly as `release` does, and the pass continues.

## Acceptance Criteria
- [ ] A terminal `succeeded` run releases its lease and leaves one `run_history` row with
      `kind="succeeded"`, the lease's `coder_pool`/`repo`/`issue_number`/`dispatched_at`,
      and `requeue_attempt=0`.
- [ ] A terminal infra-shaped failure requeues **and** leaves a row with `kind="failed"`
      and `requeue_attempt=1`.
- [ ] A terminal failure whose requeue budget is spent leaves a row with
      `requeue_attempt=3` and does not requeue.
- [ ] A run that succeeds after two requeues records `requeue_attempt=2`, not `0`.
- [ ] Every row this task writes has `pr_lookup="none"` and `merged is None`.
- [ ] A non-terminal run is adopted, releases nothing and writes no row.
- [ ] The existing `test_reconcile.py` suite passes unchanged — no `ReleaseAction`
      outcome string, log line or requeue behavior changed.

## Test Expectations
Framework: **pytest 8** with **respx** for HTTP. Command:
`cd ops/scheduler && uv run pytest tests/test_reconcile.py`.
Extend `ops/scheduler/tests/test_reconcile.py`, which already carries the `store`,
`leases`, `fabro` and `config` fixtures, the `_lease`/`_run`/`_failed_run` helpers and
`_install_labels()` (`tests/test_reconcile.py:60-112`). A history helper:

```python
def _history(store) -> list:
    return store._conn.execute(
        "SELECT * FROM run_history ORDER BY run_id"
    ).fetchall()
```

```python
@respx.mock
def test_a_succeeded_run_is_recorded(config, store, leases, fabro):
    leases.acquire(_lease())
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(200, json={
        "id": "R1",
        "lifecycle": {"status": {"kind": "succeeded", "reason": "completed"}},
        "timestamps": {"completed_at": "2026-09-20T16:00:00Z"},
    }))

    reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    (row,) = _history(store)
    assert row["run_id"] == "R1"
    assert row["kind"] == "succeeded"
    assert row["reason"] == "completed"
    assert row["coder_pool"] == "coders-a"
    assert row["issue_number"] == 7
    assert row["finished_at"] == "2026-09-20T16:00:00+00:00"
    assert row["requeue_attempt"] == 0
    assert row["pr_lookup"] == "none"
    assert row["merged"] is None
    assert leases.active() == []


@respx.mock
def test_a_requeued_failure_is_recorded_with_its_attempt(config, store, leases, fabro):
    leases.acquire(_lease())
    _install_labels()
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(200, json={
        "id": "R1", "lifecycle": {"status": {"kind": "failed", "reason": "terminated"}},
    }))

    reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    (row,) = _history(store)
    assert row["kind"] == "failed"
    assert row["reason"] == "terminated"
    assert row["requeue_attempt"] == 1


@respx.mock
def test_a_run_that_succeeds_after_two_requeues_records_two(config, store, leases, fabro):
    store.bump_requeue_count(FF, 7)
    store.bump_requeue_count(FF, 7)
    leases.acquire(_lease())
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(200, json={
        "id": "R1", "lifecycle": {"status": {"kind": "succeeded"}},
    }))

    reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    (row,) = _history(store)
    assert row["requeue_attempt"] == 2
    assert store.requeue_count(FF, 7) == 0    # still cleared afterwards


@respx.mock
def test_a_non_terminal_run_writes_nothing(config, store, leases, fabro):
    leases.acquire(_lease())
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(200, json=_run()))

    reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert _history(store) == []
    assert len(leases.active()) == 1
```

The module constants these use are already at the top of the file
(`tests/test_reconcile.py:36-52`): `FF = "andrewthetechie/jelly-swipe"`,
`GH_TOKEN = "ghp_test"`, and `RUNS = re.compile(rf"{re.escape(FABRO_API)}/runs/(?P<id>[^/]+)$")`.

## Dependencies
- Blocked by: `Add archive_and_release_lease: the atomic release seam`,
  `Add _outcome_from_run: a RunOutcome from a terminal projection`
- Why blocked: the first supplies the atomic write; the second supplies the `RunOutcome`
  this passes to it.
- Blocks: `Record the run fabro lost`, `Guard: the orphan path writes no row`,
  `Resolve the PR at release`

## Labels
`feature`, `scheduler`, `priority:high`

## Estimate
Medium

## Risk
3 - First task that changes the live release path. A mistake here either loses rows
silently or, worse, changes requeue behavior — which is what keeps work moving across a
fabro restart. The existing `test_reconcile.py` suite is the guard: it must pass unchanged.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full, including every pre-existing
`test_reconcile.py` case. The repository is valid: the 404 path still releases without
archiving, which task 05 completes.
