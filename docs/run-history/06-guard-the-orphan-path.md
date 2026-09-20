# Guard: the orphan path writes no `run_history` row

## Tracer-Bullet Outcome
The GitHub receipt scan finds an `agent-in-progress` issue that no lease and no live
fabro run accounts for, requeues it as it always has, and writes **no** history row. A
regression test now fails if anyone ever routes that path through the archiving release.

## User Story
As the operator, I want the orphan repair to stay out of the history, because it has no
run, no box and no dispatch time — and a row claiming otherwise would be a fabrication in
the one record I use to reconstruct what the factory did.

## Description
This is a **guard**, not a behavior change. After task 04 the orphan path already does
the right thing, because `_requeue`'s `outcome` parameter defaults to `None` and the
GitHub pass does not pass one. That correctness is currently an accident of a default,
and the next person to "tidy up" `_requeue` by making `outcome` required will break it in
a way no existing test notices.

Add the test that notices, plus a comment at the call site saying why the omission is
deliberate.

The reason it must write nothing is concrete. `_orphan_lease` synthesises
`Lease(coder_pool="", repo=..., issue_number=..., run_id="", dispatched_at=now)` for a
receipt with no row behind it. `run_id` is the empty string, and `run_id` is the primary
key of `run_history` — so the first orphan would insert a row keyed on `""` with an empty
coder pool and a dispatch time that is really the repair time, and the second orphan
would collide with it.

## Context Pack
- Source decisions: ADR 0008 decision 4 and its release-paths table — the orphan path
  writes no row.
- Repo facts: `_orphan_lease` is `reconcile.py:512-521` and is called once, from the
  GitHub pass at `reconcile.py:494`. Its own docstring already explains that `run_id=""`
  makes `leases.release` a no-op. The GitHub pass runs at startup **and** periodically
  (`DEFAULT_RECEIPT_SCAN_SECONDS = 600.0`), gated on confirmation across two scans.
- Non-goals: no change to when an orphan is requeued, to the confirmation gate, or to
  `_orphan_lease` itself.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/reconcile.py     (one comment at the call site)
  ops/scheduler/tests/test_reconcile.py              (extend)
  ```

- Interfaces and names:

  **The call site**, copied verbatim from `reconcile.py:490-499`:

  ```python
                  continue
              _requeue(store, leases, _orphan_lease(repo.name, number), github_token)
              orphaned.append(key)
              log.info(
                  "recover: %s#%s had the receipt with no lease; requeued",
                  repo.name, number,
              )
  ```

  **Add the comment; the call is unchanged:**

  ```python
                  continue
              # No `outcome`, and that is the point: this path has no run, no box
              # and no dispatch time. `_orphan_lease` synthesises `run_id=""`, and
              # `run_id` is `run_history`'s primary key -- archiving here would
              # write a row keyed on the empty string, with an empty coder pool and
              # a "dispatched_at" that is really the repair time, and the second
              # orphan would collide with the first. ADR 0008's release-paths table.
              _requeue(store, leases, _orphan_lease(repo.name, number), github_token)
  ```

  **`_orphan_lease`, for reference** (`reconcile.py:512-521`) — do not change it:

  ```python
  def _orphan_lease(repo: str, number: int) -> Lease:
      """A synthetic lease for a receipt with no row behind it.

      Enough for `_requeue` to restore the cache row and free nothing (no run_id,
      so `leases.release` is a no-op). `queued_since=None` falls back to now, which
      is correct: there is no lease to read the original wait from.
      """
      return Lease(
          coder_pool="", repo=repo, issue_number=number, run_id="",
          dispatched_at=datetime.now(UTC),
      )
  ```

- Verified external contracts:

  The receipt scan reads `agent-in-progress` per repo through `fetch_in_progress`
  (`github.py:227`), and asks fabro which issues its live runs are working through
  `FabroClient.active_issue_claims`. Both are untouched here.

- Behavior rules:
  - `_requeue` must keep `outcome: RunOutcome | None = None` with the `None` default.
    Making it required would force this call site to invent an outcome.
  - The orphan path still requeues: the label writes, the `store.requeue` and the
    `leases.release` no-op are all unchanged.
  - Zero rows in `run_history` after an orphan repair. Not a row with empty strings —
    zero rows.

- Error and security rules: None new.

## Acceptance Criteria
- [ ] A GitHub receipt with no lease and no live fabro run is still requeued: the
      `agent-in-progress` label is removed, `agent` is added, and the issue returns to the
      cache.
- [ ] `run_history` is **empty** after that repair.
- [ ] `RecoveryReport.orphaned` still names the repaired `(repo, issue)`.
- [ ] The comment at `reconcile.py:494` states why no `outcome` is passed.
- [ ] Every pre-existing orphan test in `test_reconcile.py` passes unchanged.

## Test Expectations
Framework: **pytest 8** with **respx**. Command:
`cd ops/scheduler && uv run pytest tests/test_reconcile.py`.
Extend `ops/scheduler/tests/test_reconcile.py`, reusing `_history` from task 04.

**Copy the setup from the existing orphan test verbatim** —
`test_an_orphaned_receipt_is_unlabelled_and_requeued` (`tests/test_reconcile.py:432-445`).
Startup `recover` repairs an orphan on a **single** pass; the two-scan confirmation gate
belongs to the periodic `receipt_tick`, not to `recover`, so do not call `recover` twice.
The two helpers it uses already exist in the file (`tests/test_reconcile.py:396-428`):

```python
def _mock_in_progress(in_progress=None, idle_slugs=(...)) -> None:
    """Answer the four repos' `/issues` polls; only jelly-swipe carries work.
    Defaults to [{"number": 9, "labels": [{"name": "agent-in-progress"}]}]."""


def _mock_active_runs(runs=None) -> None:
    """`GET /runs` -- the live-run list recovery asks before un-labelling."""
```

The guard test is the existing orphan test plus one assertion:

```python
@respx.mock
def test_an_orphan_repair_writes_no_history_row(config, store, leases, fabro):
    _install_labels()
    _mock_in_progress()
    _mock_active_runs()

    report = recover(config, store, leases, fabro, GH_TOKEN)

    assert report.orphaned == [(FF, 9)]
    assert store.get_issue(FF, 9) is not None
    assert _history(store) == []


def test_requeue_still_defaults_to_not_archiving():
    import inspect

    from fabro_scheduler.reconcile import _requeue

    assert inspect.signature(_requeue).parameters["outcome"].default is None
```

The second test is the actual guard: it is what fails if someone makes `outcome`
required, which is the change that would silently start writing empty-keyed rows.

`RecoveryReport`'s shape, for the first test's assertion (`reconcile.py:383-390`):

```python
@dataclass(frozen=True)
class RecoveryReport:
    """What one startup recovery did, for logging and for tests."""

    fabro_pass: list[ReleaseAction]
    orphaned: list[tuple[str, int]]  # (repo, issue) requeued by the GitHub pass
    github_errors: list[str]
```

## Dependencies
- Blocked by: `Write a row on every terminal release`
- Why blocked: supplies the `outcome` parameter whose `None` default this guards. Before
  it, the parameter does not exist and there is nothing to assert on.
- Blocks: `Deploy and verify on the host`

## Labels
`test`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
1 - One comment and two tests. No behavior changes.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full.
