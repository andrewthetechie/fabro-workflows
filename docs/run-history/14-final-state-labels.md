# Add derived final-state labels

## Tracer-Bullet Outcome
The `Ending` column says `merged`, `not merged`, `succeeded, no PR`, `cancelled`,
`failed (fabro restart)` or `lost (fabro 404)` — plus `×2` when the issue was requeued
twice — instead of replaying fabro's raw `kind/reason` at an operator who then has to
decode it.

## User Story
As the operator, I want the history to tell me what happened in my own vocabulary, so that
scanning a week of runs is reading rather than translating.

## Description
Add one pure function to `app.py` and use it in one template cell. No new stored column:
ADR 0008 is explicit that the label is **derived at render time from the stored raw
facts**, precisely so that changing the wording is a template change rather than a
migration against a schema that cannot gain a column.

The function reads six fields — `kind`, `reason`, `category`, `merged`, `pr_lookup`,
`requeue_attempt` — and every one of them is already on the row.

The distinction that earns this task its own file is between `succeeded` and *shipped*.
A backlog run whose PR was deliberately not auto-merged — a risk-4 block — ends the run
`succeeded`, so `kind` alone cannot tell a merged run from a blocked one. That is the
whole reason the `merged` column exists, and this is where it becomes readable.

## Context Pack
- Source decisions: ADR 0008 — "The final-state label shown to the operator is *derived*
  from the stored raw facts (`kind`, `reason`, `category`, `merged`, `requeue_attempt`,
  `pr_lookup`) at render time, so changing the wording of a label is a template change,
  not a migration."
- Repo facts: a blocked merge ends the run `succeeded` (AGENTS.md; scheduler overview
  finding 9). A cancel arrives as `failed` with `reason: "cancelled"` — one L. The fabro
  restart ends every in-flight run `failed` with `reason: "terminated"` and category
  `deterministic`, which is why the reason is checked before the category. `LOST_KIND` and
  `LOST_REASON` are task 05's literals.
- Non-goals: no stored label column, no change to what is written at release, no colour
  coding. Wording only.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/app.py                    (final_state_label)
  ops/scheduler/src/fabro_scheduler/templates/history.html    (the Ending cell)
  ops/scheduler/tests/test_app.py                             (extend)
  ```

- Interfaces and names:

  Add to `app.py` at module level, beside `humanise_duration`:

  ```python
  def final_state_label(row: sqlite3.Row) -> str:
      """What a released run's ending means, in the operator's vocabulary.

      Derived, never stored (ADR 0008): the raw facts are the record and this is
      presentation, so rewording a label is this function plus its test and nothing
      else. That matters more than usual here, because `run_history` cannot gain a
      column after it ships.

      The ordering below is the whole logic, and two steps in it are not obvious:

      * `succeeded` is checked against `merged` BEFORE anything else, because a
        run whose PR was deliberately not auto-merged -- a risk-4 block -- also
        ends `succeeded`. `kind` alone cannot tell shipping from blocking.
      * a failure's REASON is checked before its CATEGORY, because the canonical
        infra failure lies about its category: a fabro restart ends every in-flight
        run `failed/terminated` with category `deterministic`, and a cancel carries
        no category in the projection at all.
      """
      kind = row["kind"]
      reason = row["reason"]

      if kind == "lost":
          return _with_requeues("lost (fabro 404)", row)

      if kind == "succeeded":
          if row["pr_lookup"] == "failed":
              return _with_requeues("succeeded, PR unknown", row)
          if row["pr_lookup"] == "none":
              return _with_requeues("succeeded, no PR", row)
          return _with_requeues("merged" if row["merged"] else "not merged", row)

      if kind == "failed":
          if reason == "terminated":
              return _with_requeues("failed (fabro restart)", row)
          if reason == "cancelled":
              return _with_requeues("cancelled", row)
          if row["category"] == "transient_infra":
              return _with_requeues("failed (infra)", row)
          return _with_requeues(f"failed ({reason})" if reason else "failed", row)

      if kind == "dead":
          return _with_requeues("dead", row)

      return _with_requeues(f"{kind}/{reason}" if reason else str(kind), row)


  def _with_requeues(label: str, row: sqlite3.Row) -> str:
      """Append the requeue count, when there was one.

      A run that took three attempts to get here is a different fact from one that
      took none, and it is the fact that precedes a human being called -- the
      requeue budget is `MAX_REQUEUES = 3`.
      """
      attempts = row["requeue_attempt"] or 0
      return f"{label} ×{attempts + 1}" if attempts else label
  ```

  Write `×` directly in the source rather than the `×` escape.

  Register it as a Jinja global inside `build_app`, beside task 12's:

  ```python
      templates.env.globals["sort_link"] = _sort_link
      templates.env.globals["final_state"] = final_state_label
  ```

  **Replace the `Ending` cell in `history.html`** — task 11 left it as:

  ```jinja
        <td>{{ row.kind }}{% if row.reason %}/{{ row.reason }}{% endif %}</td>
  ```

  with:

  ```jinja
        <td title="{{ row.kind }}{% if row.reason %}/{{ row.reason }}{% endif %}{% if row.category %} [{{ row.category }}]{% endif %}">{{ final_state(row) }}</td>
  ```

  The `title` keeps the raw facts one hover away, which is what makes the derived label
  safe to change: nothing is hidden, only translated.

  The stored values these read, from task 01's schema:

  ```sql
  kind            TEXT NOT NULL,  -- "succeeded" | "failed" | "dead" | "lost"
  reason          TEXT,
  category        TEXT,
  requeue_attempt INTEGER NOT NULL DEFAULT 0,
  pr_lookup       TEXT NOT NULL,  -- "found" | "none" | "failed"
  merged          INTEGER         -- 0 | 1 | NULL
  ```

  And the reason constants that drive two of the branches (`reconcile.py`):

  ```python
  TERMINATED_REASON = "terminated"
  CANCELLED_REASON = "cancelled"       # one L
  INFRA_CATEGORY = "transient_infra"
  ```

- Verified external contracts: None. Pure function over stored values.

- Behavior rules:
  - `requeue_attempt = 0` adds no suffix. `1` renders `×2` — the run was the second
    attempt, which is what an operator counts.
  - `succeeded` + `pr_lookup="found"` + `merged=0` → `not merged`, **not** `succeeded`.
    This is the risk-4 block and it must be visually distinct from a merge.
  - `succeeded` + `pr_lookup="failed"` → `succeeded, PR unknown`. Never claim `not merged`
    for a lookup that did not complete.
  - A failure's reason is checked before its category, in that order.
  - An unrecognised `kind` falls through to `kind/reason`, so a future fabro status shows
    up as itself rather than as a wrong label.
  - The `title` attribute carries the raw `kind`, `reason` and `category`.

- Error and security rules: the function never raises. `row["requeue_attempt"] or 0`
  tolerates a `NULL` that the schema's `NOT NULL DEFAULT 0` should already prevent.

## Acceptance Criteria
- [ ] `succeeded` + `merged=1` → `merged`.
- [ ] `succeeded` + `pr_lookup="found"` + `merged=0` → `not merged`.
- [ ] `succeeded` + `pr_lookup="none"` → `succeeded, no PR`.
- [ ] `succeeded` + `pr_lookup="failed"` → `succeeded, PR unknown`.
- [ ] `failed` + `reason="terminated"` → `failed (fabro restart)`, even with
      `category="deterministic"`.
- [ ] `failed` + `reason="cancelled"` → `cancelled`.
- [ ] `failed` + `category="transient_infra"` → `failed (infra)`.
- [ ] `failed` + `reason="stage_failed"` + no category → `failed (stage_failed)`.
- [ ] `dead` → `dead`; `lost` → `lost (fabro 404)`.
- [ ] `requeue_attempt=2` appends ` ×3`; `requeue_attempt=0` appends nothing.
- [ ] The rendered page shows the label and carries the raw facts in the cell's `title`.

## Test Expectations
Framework: **pytest 8**. Command:
`cd ops/scheduler && uv run pytest tests/test_app.py`.
Extend `ops/scheduler/tests/test_app.py`. The function takes a `sqlite3.Row`, and a plain
`dict` satisfies the `row["name"]` access it uses — so the table-driven cases need no
database:

```python
import pytest

from fabro_scheduler.app import final_state_label


def _row(**overrides) -> dict:
    row = {
        "kind": "succeeded", "reason": None, "category": None,
        "requeue_attempt": 0, "pr_lookup": "none", "merged": None,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("overrides,expected", [
    ({"pr_lookup": "found", "merged": 1}, "merged"),
    ({"pr_lookup": "found", "merged": 0}, "not merged"),
    ({"pr_lookup": "none"}, "succeeded, no PR"),
    ({"pr_lookup": "failed"}, "succeeded, PR unknown"),
    ({"kind": "failed", "reason": "terminated", "category": "deterministic"},
     "failed (fabro restart)"),
    ({"kind": "failed", "reason": "cancelled"}, "cancelled"),
    ({"kind": "failed", "reason": "stage_failed", "category": "transient_infra"},
     "failed (infra)"),
    ({"kind": "failed", "reason": "stage_failed"}, "failed (stage_failed)"),
    ({"kind": "failed"}, "failed"),
    ({"kind": "dead"}, "dead"),
    ({"kind": "lost", "reason": "fabro 404"}, "lost (fabro 404)"),
    ({"kind": "wat", "reason": "huh"}, "wat/huh"),
])
def test_final_state_label(overrides, expected):
    assert final_state_label(_row(**overrides)) == expected


@pytest.mark.parametrize("attempts,suffix", [(0, ""), (1, " ×2"), (2, " ×3")])
def test_the_requeue_suffix_counts_attempts(attempts, suffix):
    row = _row(pr_lookup="found", merged=1, requeue_attempt=attempts)
    assert final_state_label(row) == f"merged{suffix}"


def test_the_page_renders_the_label_and_keeps_the_raw_facts(client, store):
    _archive(store, "A", finished="2026-09-20T12:00:00+00:00",
             pr_lookup="found", pr_number=388,
             pr_url="https://example.invalid/388", merged=True)

    body = client.get("/history").text

    assert "merged" in body
    assert 'title="succeeded' in body      # the raw kind is still one hover away
```

## Dependencies
- Blocked by: `Add GET /history and the page`
- Why blocked: supplies the template cell this replaces and the page the last test renders.
- Blocks: `Deploy and verify on the host`

## Labels
`enhancement`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
1 - A pure function and one template cell. The raw facts stay in the `title`, so a wrong
label misleads but loses nothing.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full.
