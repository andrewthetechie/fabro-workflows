# Add `Store.history_rows`: sorted, limited reads

## Tracer-Bullet Outcome
Ask the store for history and get rows back sorted by any allowed column, in either
direction, capped at a limit — with the sort applied **in SQL before the limit**, so
"the longest run" means the longest of all of them and not the longest of the first page.
Nothing calls it yet.

## User Story
As the operator, I want the history read in one place with a fixed set of sortable
columns, so that a sort parameter arriving from a URL can never become SQL.

## Description
Add one read method to `Store`, plus a module-level frozenset naming the sortable
columns.

Two things make this more than a `SELECT *`.

**The sort column is interpolated into SQL, so it must be whitelisted.** SQLite cannot
parameterise an `ORDER BY` column name. The frozenset is the whitelist, membership is
checked before interpolation, and anything not in it falls back to the default rather
than raising — a bad URL should show the operator the default page, not a 500.

**Sort before limit.** The `ORDER BY` and the `LIMIT` are in one statement, which is what
makes this true. Sorting a fetched page in Python would silently answer a different
question.

## Context Pack
- Source decisions: ADR 0008 decision 8 — 200 rows by default, `?limit=all` as the escape
  hatch, sort applied in SQL before the limit.
- Repo facts: `Store` holds one connection under one lock and every statement, reads
  included, runs inside `with self._lock`. Read methods return `sqlite3.Row` and take the
  lock without `with self._conn` — see `lease_rows` (`store.py:278-290`), the closest
  analog.
- Non-goals: no route, no payload shape, no pagination offset. There is no `offset`
  because there is no second page: the default is 200 and the escape hatch is everything.

## Delivery Strategy
- Shape: Prefactor
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/store.py     (HISTORY_SORT_COLUMNS, history_rows)
  ops/scheduler/tests/test_store_history.py      (extend)
  ```

- Interfaces and names:

  **The closest existing read method**, copied verbatim from `store.py:278-290` — note
  the bare `with self._lock` and the `sqlite3.Row` return:

  ```python
  def lease_rows(self) -> list[sqlite3.Row]:
      """Every active lease, one row per coder instance, ordered by pool.

      Plain columns rather than a `Lease`: that dataclass lives in `lease.py`,
      which imports this module, and a persistence layer that returned a domain
      type would have to import it back. `LeaseStore` is where the mapping
      belongs.
      """
      with self._lock:
          return self._conn.execute(
              "SELECT coder_pool, repo, issue_number, run_id, dispatched_at, "
              "queued_since FROM leases ORDER BY coder_pool"
          ).fetchall()
  ```

  Add near the top of `store.py`, beside the other module constants:

  ```python
  # The columns `/history` may be sorted by. This is a WHITELIST, not documentation:
  # SQLite cannot parameterise an ORDER BY column, so the name is interpolated into
  # the statement and membership here is the only thing between a URL query string
  # and arbitrary SQL. Adding a column to `run_history` does not add it here.
  HISTORY_SORT_COLUMNS = frozenset({
      "finished_at",
      "dispatched_at",
      "repo",
      "issue_number",
      "coder_pool",
      "kind",
      "merged",
      "requeue_attempt",
  })

  # Most-recently-finished first, and the page size the operator gets when they ask
  # for nothing in particular.
  DEFAULT_HISTORY_SORT = "finished_at"
  DEFAULT_HISTORY_LIMIT = 200
  ```

  Add to `Store`:

  ```python
  def history_rows(
      self,
      *,
      sort: str = DEFAULT_HISTORY_SORT,
      descending: bool = True,
      limit: int | None = DEFAULT_HISTORY_LIMIT,
  ) -> list[sqlite3.Row]:
      """Released runs, newest first by default.

      `sort` is interpolated, not parameterised -- SQLite has no placeholder for an
      ORDER BY column -- so it is checked against `HISTORY_SORT_COLUMNS` first. An
      unknown column falls back to the default rather than raising: this is reached
      from a URL query string, and a typo should show the operator the ordinary page
      rather than a 500.

      `limit=None` means every row, which is what `?limit=all` asks for.

      The ORDER BY and the LIMIT are one statement on purpose. Sorting a fetched
      page in Python would answer a different question -- "the longest of the most
      recent 200" rather than "the longest" -- and it would look right.

      `run_id` is the tiebreaker on every sort so the order is total and a page does
      not reshuffle between two requests that sort on equal values.
      """
      column = sort if sort in HISTORY_SORT_COLUMNS else DEFAULT_HISTORY_SORT
      direction = "DESC" if descending else "ASC"
      statement = (
          f"SELECT * FROM run_history ORDER BY {column} {direction}, run_id {direction}"
      )
      params: tuple[object, ...] = ()
      if limit is not None:
          statement += " LIMIT ?"
          params = (limit,)
      with self._lock:
          return self._conn.execute(statement, params).fetchall()
  ```

- Verified external contracts: None. No HTTP surface.

- Behavior rules:
  - `sort` not in `HISTORY_SORT_COLUMNS` → fall back to `"finished_at"`. Do **not** raise
    and do **not** interpolate the unknown value.
  - `run_id` is the tiebreaker, in the same direction as the primary sort.
  - `limit=None` emits no `LIMIT` clause. `limit=0` emits `LIMIT 0` and returns nothing —
    that is the caller's problem to prevent, not this method's.
  - A negative limit is passed through to SQLite, which treats a negative `LIMIT` as
    unlimited. The route clamps; the store does not.
  - `SELECT *` is deliberate. The column set is fixed by task 01 and cannot change, so
    naming fourteen columns here would be a second list to keep in step for no benefit.

- Error and security rules: the whitelist is the security boundary. A test asserts that a
  SQL-injection-shaped `sort` value returns the default ordering rather than executing.

## Acceptance Criteria
- [ ] With no arguments, rows come back ordered by `finished_at` descending.
- [ ] `descending=False` reverses that.
- [ ] `sort="repo"` orders by repo; `sort="issue_number"` orders numerically.
- [ ] `sort="nonsense"` and `sort="finished_at; DROP TABLE run_history"` both fall back to
      `finished_at` and leave the table intact.
- [ ] `limit=2` returns two rows, and they are the top two **of the whole table** under
      that sort — not the first two inserted.
- [ ] `limit=None` returns every row.
- [ ] Two rows with the same `finished_at` come back in a stable `run_id` order.

## Test Expectations
Framework: **pytest 8**. Command:
`cd ops/scheduler && uv run pytest tests/test_store_history.py`.
Extend `ops/scheduler/tests/test_store_history.py` from tasks 01 and 02, reusing its
`_acquire` helper and `store` fixture.

```python
from fabro_scheduler.store import DEFAULT_HISTORY_LIMIT, HISTORY_SORT_COLUMNS, RunOutcome


def _archive(store, run_id: str, *, finished: str, kind: str = "succeeded",
             repo: str = FF, number: int = 350) -> None:
    store.acquire_lease(
        coder_pool="coders-a", repo=repo, issue_number=number,
        run_id=run_id, dispatched_at=DISPATCHED,
    )
    store.archive_and_release_lease(
        run_id,
        RunOutcome(kind=kind, finished_at=datetime.fromisoformat(finished)),
    )


def test_newest_finished_first_by_default(store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00")
    _archive(store, "C", finished="2026-09-20T11:00:00+00:00")

    assert [r["run_id"] for r in store.history_rows()] == ["B", "C", "A"]
    assert [r["run_id"] for r in store.history_rows(descending=False)] == ["A", "C", "B"]


def test_sort_by_issue_number(store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00", number=9)
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00", number=350)

    rows = store.history_rows(sort="issue_number", descending=False)
    assert [r["issue_number"] for r in rows] == [9, 350]


@pytest.mark.parametrize(
    "bad", ["nonsense", "finished_at; DROP TABLE run_history", "", "1) --"]
)
def test_an_unknown_sort_falls_back_and_executes_nothing(store, bad):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00")

    assert [r["run_id"] for r in store.history_rows(sort=bad)] == ["B", "A"]
    assert len(store.history_rows()) == 2          # the table still exists


def test_the_limit_applies_after_the_sort(store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00")
    _archive(store, "C", finished="2026-09-20T11:00:00+00:00")

    # B and C are the two newest, even though A was inserted first.
    assert [r["run_id"] for r in store.history_rows(limit=2)] == ["B", "C"]
    assert len(store.history_rows(limit=None)) == 3


def test_ties_break_on_run_id(store):
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00")
    _archive(store, "A", finished="2026-09-20T12:00:00+00:00")

    assert [r["run_id"] for r in store.history_rows()] == ["B", "A"]
    assert [r["run_id"] for r in store.history_rows(descending=False)] == ["A", "B"]


def test_the_sort_whitelist_is_a_subset_of_the_table(store):
    columns = {
        r["name"]
        for r in store._conn.execute("PRAGMA table_info(run_history)").fetchall()
    }
    assert HISTORY_SORT_COLUMNS <= columns
    assert DEFAULT_HISTORY_LIMIT == 200
```

## Dependencies
- Blocked by: `Add the run_history table and the RunOutcome contract`
- Why blocked: supplies the table this reads. In practice run it after task 02 as well,
  since the tests above write rows through `archive_and_release_lease`.
- Blocks: `Add GET /api/history`, `Add GET /history and the page`

## Labels
`feature`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
2 - One read method. The interpolated `ORDER BY` is the only sharp edge, and the whitelist
plus its injection test are the guard.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full. The repository is valid: the new
method has no callers.
