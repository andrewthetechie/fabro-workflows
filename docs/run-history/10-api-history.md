# Add `GET /api/history`

## Tracer-Bullet Outcome
`curl 'http://<host>:32280/api/history?sort=finished_at&dir=desc&limit=50'` returns the
released runs as JSON, newest first, sorted in SQL before the limit. A bad `sort` or a
junk `limit` returns the default page rather than an error.

## User Story
As the operator, I want the run history readable by machine as well as by eye, so that a
monitor condition or a log-reconstruction script can ask the scheduler what it did
instead of parsing HTML.

## Description
Add one route to `build_app`, one payload helper beside the existing ones, and one query
parser that task 11 and task 12 will share.

The query parser is the part worth care. It reads three values off a URL, and every one of
them is attacker-shaped input in the sense that matters here: it arrives as text and ends
up in SQL. The parser's job is to turn anything at all into a valid triple, never to
reject. `Store.history_rows` already falls back on an unknown sort column; the parser
handles direction and limit the same way, so the page a typo produces is the ordinary
page.

## Context Pack
- Source decisions: ADR 0008 decision 8 — 200 rows by default, `?limit=all` as the escape
  hatch, sort applied in SQL before the limit. This series' own decision: `/api/history`
  is in scope alongside the HTML page.
- Repo facts: `app.py` already serves `/api/queue`, `/api/repos` and `/api/pools`, each a
  plain `def` returning `list[dict[str, object]]` and each backed by a module-level
  `_*_payload` function. `_iso(value: datetime | None) -> str | None` already exists
  (`app.py:723-724`). Routes are declared inside `build_app`, which closes over `store`.
- Non-goals: no HTML, no auth, no pagination offset, no filtering by repo or kind. The
  page is task 11.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/app.py     (_history_query, _history_payload, the route)
  ops/scheduler/tests/test_app.py              (extend)
  ```

- Interfaces and names:

  **The closest existing route**, copied verbatim from `app.py:322-332`, for shape:

  ```python
      @app.get("/api/pools")
      def api_pools() -> list[dict[str, object]]:
          """Every coder instance, with its drain flag and its current lease or null.
          ...
          """
          return [_pool_payload(state) for state in pool_states()]
  ```

  **The closest existing payload helper**, copied verbatim from `app.py:699-721`:

  ```python
  def _pool_payload(state: PoolState) -> dict[str, object]:
      """`GET /api/pools`'s documented shape: the lease as a flat object, or `null`.
      ...
      """
      lease = state.lease
      return {
          "coder_pool": state.coder_pool,
          "drained": state.drained,
          "lease": (
              None if lease is None else {
                  "repo": lease.repo,
                  "issue_number": lease.issue_number,
                  "run_id": lease.run_id,
                  "dispatched_at": lease.dispatched_at.isoformat(),
              }
          ),
      }
  ```

  Add to `app.py`, at module level beside the other helpers:

  ```python
  def _history_query(
      sort: str | None, direction: str | None, limit: str | None
  ) -> tuple[str, bool, int | None]:
      """Three URL values into `(sort, descending, limit)`. Never raises.

      Shared by `/api/history` and `/history` so the two can never disagree about
      what a query string means.

      Everything here arrives as text from a URL and ends up shaping SQL, so the
      rule is "turn anything into a valid triple", not "reject the invalid". A
      typo shows the operator the ordinary page rather than a 422:

      * an unknown sort column is left for `Store.history_rows`, whose whitelist
        is the actual guard and which falls back to `finished_at`;
      * any direction other than the literal `"asc"` means descending, which is
        the useful default for a history;
      * `limit="all"` means every row; a non-integer or a non-positive value means
        the default page.
      """
      descending = (direction or "").lower() != "asc"

      if (limit or "").lower() == "all":
          capped: int | None = None
      else:
          try:
              parsed = int(limit) if limit else DEFAULT_HISTORY_LIMIT
          except ValueError:
              parsed = DEFAULT_HISTORY_LIMIT
          capped = parsed if parsed > 0 else DEFAULT_HISTORY_LIMIT

      return (sort or DEFAULT_HISTORY_SORT), descending, capped


  def _history_payload(row: sqlite3.Row) -> dict[str, object]:
      """One `run_history` row as JSON.

      `merged` is stored as 0/1/NULL because SQLite has no boolean, and is
      published as a real `true`/`false`/`null` -- a consumer should not have to
      know about the storage type. `pr_lookup` is published as-is: it is the field
      that says whether `merged` means anything, and flattening it into the null
      would lose exactly the distinction it exists to make.
      """
      merged = row["merged"]
      return {
          "run_id": row["run_id"],
          "coder_pool": row["coder_pool"],
          "repo": row["repo"],
          "issue_number": row["issue_number"],
          "dispatched_at": row["dispatched_at"],
          "finished_at": row["finished_at"],
          "kind": row["kind"],
          "reason": row["reason"],
          "category": row["category"],
          "requeue_attempt": row["requeue_attempt"],
          "pr_lookup": row["pr_lookup"],
          "pr_number": row["pr_number"],
          "pr_url": row["pr_url"],
          "merged": None if merged is None else bool(merged),
      }
  ```

  Add inside `build_app`, beside the other `/api` routes:

  ```python
      @app.get("/api/history")
      def api_history(
          sort: str | None = None,
          dir: str | None = None,
          limit: str | None = None,
      ) -> list[dict[str, object]]:
          """Released runs, newest finished first. The JSON twin of `/history`.

          `limit` is a string rather than an int because `all` is a legal value;
          `_history_query` is what turns it into `None`.
          """
          column, descending, capped = _history_query(sort, dir, limit)
          return [
              _history_payload(row)
              for row in store.history_rows(
                  sort=column, descending=descending, limit=capped
              )
          ]
  ```

  Imports to widen in `app.py`. It currently imports from `.store` — add the three names,
  and add `sqlite3` to the stdlib imports for the payload helper's type hint:

  ```python
  import sqlite3
  from .store import DEFAULT_HISTORY_LIMIT, DEFAULT_HISTORY_SORT, Store
  ```

  The store method from task 09:

  ```python
  def history_rows(
      self, *, sort: str = DEFAULT_HISTORY_SORT, descending: bool = True,
      limit: int | None = DEFAULT_HISTORY_LIMIT,
  ) -> list[sqlite3.Row]: ...
  ```

- Verified external contracts: None. This adds no outbound call.

- Behavior rules:
  - The query parameter is `dir`, not `direction` — it is what the header links in task 12
    will emit and it is short enough to read in a URL.
  - `dir` is descending unless it is exactly `asc` (case-insensitively).
  - `limit=all` → every row. `limit=0`, `limit=-5`, `limit=abc`, `limit=` → the default 200.
  - `sort` is passed through untouched; `Store.history_rows` owns the whitelist. Do not
    duplicate the whitelist in `app.py`.
  - `merged` is published as `true`/`false`/`null`, never `1`/`0`.
  - An empty table returns `[]` with a `200`, not a `404`.

- Error and security rules: no route parameter can produce a `4xx` or a `5xx`. There is no
  auth, consistent with decision 16 — "LAN-only, no auth". The payload carries no token and
  no host path; every field is a scheduler-owned fact or a public GitHub URL.

## Acceptance Criteria
- [ ] `GET /api/history` on an empty table returns `200` and `[]`.
- [ ] With three rows it returns them newest-`finished_at` first.
- [ ] `?dir=asc` reverses the order; `?dir=ASC` also reverses it; `?dir=banana` does not.
- [ ] `?limit=2` returns two rows; `?limit=all` returns every row; `?limit=abc`, `?limit=0`
      and `?limit=-1` return the default page.
- [ ] `?sort=issue_number` sorts by issue number; `?sort=nonsense` returns the default
      order with a `200`.
- [ ] `merged` is `true`, `false` or `null` in the JSON — never `1` or `0`.
- [ ] Every one of the fourteen columns appears in each object.

## Test Expectations
Framework: **pytest 8** with FastAPI's `TestClient`. Command:
`cd ops/scheduler && uv run pytest tests/test_app.py`.
Extend `ops/scheduler/tests/test_app.py`, copying its existing app/client fixture setup
rather than building a second one.

```python
from datetime import UTC, datetime

from fabro_scheduler.store import RunOutcome

DISPATCHED = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
FF = "andrewthetechie/jelly-swipe"


def _archive(store, run_id: str, *, finished: str, number: int = 350, **outcome) -> None:
    store.acquire_lease(
        coder_pool="coders-a", repo=FF, issue_number=number,
        run_id=run_id, dispatched_at=DISPATCHED,
    )
    store.archive_and_release_lease(
        run_id,
        RunOutcome(
            kind=outcome.pop("kind", "succeeded"),
            finished_at=datetime.fromisoformat(finished),
            **outcome,
        ),
    )


def test_history_is_empty_before_anything_is_released(client):
    response = client.get("/api/history")
    assert response.status_code == 200
    assert response.json() == []


def test_history_is_newest_finished_first(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00")

    body = client.get("/api/history").json()

    assert [row["run_id"] for row in body] == ["B", "A"]
    assert body[0]["repo"] == FF
    assert body[0]["coder_pool"] == "coders-a"
    assert body[0]["kind"] == "succeeded"
    assert set(body[0]) == {
        "run_id", "coder_pool", "repo", "issue_number", "dispatched_at",
        "finished_at", "kind", "reason", "category", "requeue_attempt",
        "pr_lookup", "pr_number", "pr_url", "merged",
    }


def test_merged_is_published_as_a_real_boolean(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00",
             pr_lookup="found", pr_number=388, pr_url="https://example.invalid/388",
             merged=True)
    _archive(store, "B", finished="2026-09-20T09:00:00+00:00")

    by_id = {row["run_id"]: row for row in client.get("/api/history").json()}

    assert by_id["A"]["merged"] is True
    assert by_id["A"]["pr_number"] == 388
    assert by_id["B"]["merged"] is None
    assert by_id["B"]["pr_lookup"] == "none"


@pytest.mark.parametrize("query,expected", [
    ("?dir=asc", ["A", "B"]),
    ("?dir=ASC", ["A", "B"]),
    ("?dir=banana", ["B", "A"]),
    ("", ["B", "A"]),
])
def test_direction_is_ascending_only_for_asc(client, store, query, expected):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00")

    assert [r["run_id"] for r in client.get(f"/api/history{query}").json()] == expected


@pytest.mark.parametrize("limit,count", [
    ("?limit=2", 2), ("?limit=all", 3), ("?limit=abc", 3),
    ("?limit=0", 3), ("?limit=-1", 3),
])
def test_limit_parsing(client, store, limit, count):
    for name, hour in (("A", 10), ("B", 11), ("C", 12)):
        _archive(store, name, finished=f"2026-09-20T{hour}:00:00+00:00")

    assert len(client.get(f"/api/history{limit}").json()) == count


def test_an_unknown_sort_column_is_not_an_error(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    response = client.get("/api/history?sort=nonsense")
    assert response.status_code == 200
    assert [r["run_id"] for r in response.json()] == ["A"]
```

## Dependencies
- Blocked by: `Add Store.history_rows: sorted, limited reads`
- Why blocked: supplies the read method and the two default constants this route calls.
- Blocks: `Deploy and verify on the host`

## Labels
`feature`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
2 - One read-only route on a LAN-only, unauthenticated service. It publishes only facts
the scheduler already owns.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full.
