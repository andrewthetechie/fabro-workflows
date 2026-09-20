# Add `_outcome_from_run`: a `RunOutcome` from a terminal projection

## Tracer-Bullet Outcome
Hand `_outcome_from_run` a terminal fabro run projection and a requeue attempt number,
and it returns the `RunOutcome` that the release path will store — `kind`, `reason`,
`category` and `finished_at` read out of the projection, with no invention. Nothing
calls it yet.

## User Story
As the operator, I want the mapping from fabro's projection to our record to be one pure,
tested function so that a change in fabro's response shape fails in one visible place
rather than three release branches.

## Description
Add one module-level function to `reconcile.py`, beside the existing `_classify`. It
reads only fields the reconcile pass has already fetched; it makes no call of its own.

Two fields need care, and both are already established facts in this repository.

`timestamps.completed_at` is optional in fabro's own type. When it is absent the function
leaves `finished_at` as `None`, which `archive_and_release_lease` turns into the release
time — do **not** substitute `datetime.now()` here, because the store already owns that
fallback and two sources for one value is how they drift.

`category` is not present for every failure and must not be fetched here.
`_with_category` (`reconcile.py:324-337`) already ran before this function is called and
deliberately skipped the events call for `terminated` and `cancelled`, because the reason
alone decides the requeue and the category costs a second HTTP call. Read whatever
`_with_category` attached at `run["_last_failure"]["category"]` and accept `None`.

## Context Pack
- Source decisions: ADR 0008 — `finished_at` is `timestamps.completed_at` else release
  time; `category` is stored "only when the release pass already read it".
- Repo facts: the reconcile pass attaches the category under the private key
  `_last_failure` on the run dict it already holds, not on a fetch of its own.
- Non-goals: no call site, no HTTP call, no `category` fetch. Wiring is task 04.

## Delivery Strategy
- Shape: Prefactor
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/reconcile.py     (add _outcome_from_run)
  ops/scheduler/tests/test_reconcile.py              (extend)
  ```

- Interfaces and names:

  The **existing** neighbour to place it beside, copied verbatim from
  `reconcile.py:180-195`:

  ```python
  def _classify(run: Mapping[str, object]) -> str:
      """A one-line reason for the log: what a terminal run was and why it is gone."""
      lifecycle = run.get("lifecycle")
      status = lifecycle.get("status") if isinstance(lifecycle, Mapping) else None
      if isinstance(status, Mapping):
          kind = status.get("kind")
          reason = status.get("reason")
          if reason:
              return f"{kind}/{reason}"
          if kind:
              return str(kind)
      return "unknown"
  ```

  Add, immediately after it:

  ```python
  def _outcome_from_run(
      run: Mapping[str, object], *, requeue_attempt: int = 0
  ) -> RunOutcome:
      """The `run_history` row's raw facts, read off a terminal run projection.

      Reads only what the reconcile pass already holds. In particular it does NOT
      fetch the failure category: `_with_category` has already run and has
      deliberately skipped that call for `terminated` and `cancelled`, whose reason
      alone decides the requeue. A `None` category here is correct and expected for
      the two most common failures.

      `finished_at` is left `None` when fabro reports no `completed_at`, because
      `Store.archive_and_release_lease` owns the fallback to the release time and
      two sources for one value is how they drift.

      The PR fields are not set. They belong to the enrichment, which runs later
      and against GitHub, not against this projection.
      """
      lifecycle = run.get("lifecycle")
      status = lifecycle.get("status") if isinstance(lifecycle, Mapping) else None
      if not isinstance(status, Mapping):
          status = {}

      kind = status.get("kind")
      reason = status.get("reason")

      failure = run.get("_last_failure")
      category = failure.get("category") if isinstance(failure, Mapping) else None

      return RunOutcome(
          kind=str(kind) if kind else "unknown",
          reason=str(reason) if reason else None,
          category=str(category) if category else None,
          finished_at=_completed_at(run),
          requeue_attempt=requeue_attempt,
      )


  def _completed_at(run: Mapping[str, object]) -> datetime | None:
      """`timestamps.completed_at` as a datetime, or `None` when fabro has none.

      Optional in fabro's own type (`RunTimestamps.completed_at:
      Option<DateTime<Utc>>`), so absent, null and unparseable all mean the same
      thing here: the caller's release time is the better answer.
      """
      timestamps = run.get("timestamps")
      if not isinstance(timestamps, Mapping):
          return None
      raw = timestamps.get("completed_at")
      if not isinstance(raw, str) or not raw:
          return None
      try:
          return datetime.fromisoformat(raw.replace("Z", "+00:00"))
      except ValueError:
          return None
  ```

  `reconcile.py` already imports `from datetime import UTC, datetime` and
  `from collections.abc import Mapping`. Widen the store import from
  `from .store import Store` to `from .store import RunOutcome, Store`.

- Verified external contracts:

  fabro's run projection, `GET /runs/{id}` (`docs/scheduler/00-overview-and-contracts.md`,
  finding 9, and observed live):

  ```json
  {"lifecycle": {"status": {"kind": "succeeded", "reason": "completed"},
                 "error": null, "archived": false},
   "timestamps": {"created_at": "2026-09-20T12:00:00Z",
                  "started_at": "2026-09-20T12:00:03Z",
                  "last_event_at": "2026-09-20T15:59:58Z",
                  "completed_at": "2026-09-20T16:00:00Z"}}
  ```

  The type behind it (`context/fabro/lib/foundation/fabro-types/src/run_summary.rs:238-245`):

  ```rust
  pub struct RunTimestamps {
      pub created_at:    DateTime<Utc>,
      #[serde(default)] pub started_at:    Option<DateTime<Utc>>,
      #[serde(default)] pub last_event_at: Option<DateTime<Utc>>,
      #[serde(default)] pub completed_at:  Option<DateTime<Utc>>,
  }
  ```

  The terminal kinds are exactly `succeeded | failed | dead`. There is no `cancelled`
  kind — a cancel is `{"kind": "failed", "reason": "cancelled"}`, one L.

- Behavior rules:
  - A missing or non-string `kind` yields `"unknown"`, never an empty string, so the
    `NOT NULL` column always has a value.
  - An empty-string `reason` or `category` becomes `None`, not `""`.
  - `completed_at` of `null`, absent, or unparseable all yield `None`.
  - Accept a trailing `Z`: fabro emits `2026-09-20T16:00:00Z`, and
    `datetime.fromisoformat` on Python 3.11 handles `Z` — the `.replace` is belt and
    braces for a `+00:00`-less variant and costs nothing.
  - Never set `pr_lookup`, `pr_number`, `pr_url` or `merged`. The default
    `pr_lookup="none"` is what the row carries until task 08.

- Error and security rules: the function never raises. Every read is guarded by an
  `isinstance` check, because this parses a remote response.

## Acceptance Criteria
- [ ] A succeeded projection yields `kind="succeeded"`, `reason="completed"`,
      `category=None`, `finished_at` parsed from `timestamps.completed_at`.
- [ ] A failed projection with `_last_failure.category` set yields that category.
- [ ] A failed projection with no `_last_failure` yields `category=None`.
- [ ] A projection with no `timestamps` key yields `finished_at=None`.
- [ ] A projection with `"completed_at": null` yields `finished_at=None`.
- [ ] A projection with an unparseable `completed_at` yields `finished_at=None` and
      does not raise.
- [ ] `requeue_attempt` is carried through from the keyword argument.
- [ ] `pr_lookup` is `"none"` and `merged` is `None` on every outcome this produces.

## Test Expectations
Framework: **pytest 8**. Command: `cd ops/scheduler && uv run pytest tests/test_reconcile.py`.
Extend `ops/scheduler/tests/test_reconcile.py`, which already has the `_run` and
`_failed_run` helpers this uses (`tests/test_reconcile.py:88-101`):

```python
def _run(**overrides) -> dict:
    run = {"id": "R1", "lifecycle": {"status": {"kind": "running"}}}
    run.update(overrides)
    return run


def _failed_run(reason: str | None = None, category: str | None = None) -> dict:
    status: dict = {"kind": "failed"}
    if reason is not None:
        status["reason"] = reason
    run: dict = {"id": "R1", "lifecycle": {"status": status}}
    if category is not None:
        run["_last_failure"] = {"category": category}
    return run
```

Add, importing `_outcome_from_run` from `fabro_scheduler.reconcile`:

```python
def test_outcome_reads_kind_reason_and_completed_at():
    run = _run(
        lifecycle={"status": {"kind": "succeeded", "reason": "completed"}},
        timestamps={"completed_at": "2026-09-20T16:00:00Z"},
    )
    outcome = _outcome_from_run(run)
    assert outcome.kind == "succeeded"
    assert outcome.reason == "completed"
    assert outcome.category is None
    assert outcome.finished_at == datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
    assert outcome.pr_lookup == "none"
    assert outcome.merged is None


def test_outcome_reads_an_attached_category():
    outcome = _outcome_from_run(_failed_run("stage_failed", INFRA_CATEGORY))
    assert outcome.kind == "failed"
    assert outcome.reason == "stage_failed"
    assert outcome.category == INFRA_CATEGORY


def test_outcome_accepts_a_missing_category():
    outcome = _outcome_from_run(_failed_run("terminated"))
    assert outcome.category is None


@pytest.mark.parametrize(
    "timestamps",
    [None, {}, {"completed_at": None}, {"completed_at": ""}, {"completed_at": "nonsense"}],
)
def test_outcome_leaves_finished_at_none_when_fabro_has_no_usable_time(timestamps):
    run = _run(lifecycle={"status": {"kind": "dead"}})
    if timestamps is not None:
        run["timestamps"] = timestamps
    assert _outcome_from_run(run).finished_at is None


def test_outcome_carries_the_requeue_attempt():
    assert _outcome_from_run(_failed_run("terminated"), requeue_attempt=2).requeue_attempt == 2
```

## Dependencies
- Blocked by: `Add the run_history table and the RunOutcome contract`
- Why blocked: supplies the `RunOutcome` type this function returns.
- Blocks: `Write a row on every terminal release`

## Labels
`feature`, `scheduler`, `priority:high`

## Estimate
Small

## Risk
1 - A pure function with no caller and no I/O.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full. The repository is valid: the new
function has no callers and no existing behavior changed.
