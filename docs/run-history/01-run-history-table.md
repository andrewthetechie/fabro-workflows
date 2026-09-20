# Add the `run_history` table and the `RunOutcome` contract

## Tracer-Bullet Outcome
Open a fresh scheduler database and `run_history` is there with all fourteen columns,
alongside a `RunOutcome` dataclass the later tasks construct. Nothing writes to it yet.
This is the prefactor that makes every other task possible, and the one change that
cannot be corrected after deployment.

## User Story
As the operator, I want the run-history schema decided in one reviewed step so that a
column we forgot is never a hand-written `ALTER TABLE` against a live database.

## Description
Add one `CREATE TABLE IF NOT EXISTS` block to the `SCHEMA` string in `store.py`, and one
frozen dataclass beside the existing `RepoFetch`. No method, no caller, no route.

The obstacle this removes is stated in the code twice already: `CREATE TABLE IF NOT
EXISTS` **will not add a column to a table that already exists** (`store.py:85-87`,
`lease.py:37-39`). `Store.__init__` runs `SCHEMA` through `executescript` on every open
(`store.py:200`), so a new table appears by itself — but a new *column* on an existing
table does not. Every column the series will ever use is therefore created now,
including the four that nothing writes until task 08.

`RunOutcome` lives in `store.py` and not in `reconcile.py` because `reconcile.py` already
imports `Store`; putting it the other way round is a circular import.

## Context Pack
- Source decisions: ADR 0008 — the schema is the one irreversible decision; `requeue_attempt`
  is an integer, not a boolean (decision 3); `pr_lookup` is tri-state (decision 7).
- Repo facts: `SCHEMA` is a module-level string in `store.py` starting at line 51 and
  currently declaring seven tables — `issue_cache`, `repo_etag`, `leases`, `repo_affinity`,
  `overrides`, `pool_state`, `requeue_counts`. Each carries a `--` SQL comment above it
  explaining why it exists; match that register. `RepoFetch` is the existing frozen
  dataclass in the same module (`store.py:156-168`).
- Non-goals: no `Store` method, no `reconcile.py` change, no index. At a few thousand rows
  a year the table is scanned, and an index on `finished_at` is not worth a column that
  cannot be removed.

## Delivery Strategy
- Shape: Prefactor
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/store.py     (SCHEMA + RunOutcome)
  ops/scheduler/tests/test_store_history.py      (new)
  ```

- Interfaces and names:

  Append to the `SCHEMA` string in `store.py`, after the `requeue_counts` block and before
  the closing `"""`:

  ```sql
  -- One row per released coder lease: what the run turned out to be. Written inside
  -- the same transaction that deletes the lease, so a crash cannot lose the row and
  -- the lease together (ADR 0008). Point-in-time: it records what the ending WAS, not
  -- what became of the PR afterwards.
  --
  -- Every column the series needs is here, including the four the PR lookup does not
  -- fill until a later task, because `CREATE TABLE IF NOT EXISTS` will not add a
  -- column to a table that already exists -- the same constraint that put
  -- `queued_since` on `leases` a draft early.
  --
  -- `run_id` is the key because it is already UNIQUE on `leases` and one dispatch
  -- produces exactly one run. A requeued issue comes back under a NEW run id, so a
  -- second attempt is a second row rather than a collision.
  CREATE TABLE IF NOT EXISTS run_history (
    run_id          TEXT PRIMARY KEY,
    coder_pool      TEXT NOT NULL,
    repo            TEXT NOT NULL,
    issue_number    INTEGER NOT NULL,
    dispatched_at   TEXT NOT NULL,  -- ISO-8601 UTC, copied from `leases.dispatched_at`
    finished_at     TEXT NOT NULL,  -- ISO-8601 UTC
    kind            TEXT NOT NULL,  -- "succeeded" | "failed" | "dead" | "lost"
    reason          TEXT,
    category        TEXT,
    requeue_attempt INTEGER NOT NULL DEFAULT 0,
    pr_lookup       TEXT NOT NULL,  -- "found" | "none" | "failed"
    pr_number       INTEGER,
    pr_url          TEXT,
    merged          INTEGER         -- 0 | 1 | NULL
  );
  ```

  Add beside `RepoFetch` in `store.py`. `datetime` and `dataclass` are already imported
  (`from dataclasses import dataclass`, `from datetime import UTC, datetime`):

  ```python
  @dataclass(frozen=True)
  class RunOutcome:
      """What a released run turned out to be, for its `run_history` row.

      Every field after `kind` has a default, so the paths that know less -- a run
      fabro has lost, a release taken before the PR lookup exists -- construct one
      without inventing facts they do not have. `finished_at=None` means "use the
      release time", which is the only answer the lost-run path has.
      """

      kind: str                              # "succeeded" | "failed" | "dead" | "lost"
      reason: str | None = None
      category: str | None = None
      finished_at: datetime | None = None
      requeue_attempt: int = 0
      pr_lookup: str = "none"                # "found" | "none" | "failed"
      pr_number: int | None = None
      pr_url: str | None = None
      merged: bool | None = None
  ```

  For reference, the existing dataclass to match in style (`store.py:156-168`):

  ```python
  @dataclass(frozen=True)
  class RepoFetch:
      """What is known about one repo's last attempt at the inventory. ..."""

      repo: str
      etag: str | None
      last_attempt_at: datetime | None
      last_success_at: datetime | None
      last_error: str | None
  ```

- Verified external contracts: None. This task touches no HTTP surface.

- Behavior rules:
  - Column order in the `CREATE TABLE` must match the block above exactly. Later tasks
    read rows by name, but the operator reads `PRAGMA table_info` in task 15.
  - `merged` is `INTEGER`, not `BOOLEAN`. SQLite has no boolean type and Python's
    `sqlite3` adapts `True`/`False` to `1`/`0` on write, returning `int` on read.
  - Do not add an index.
  - Do not change any existing table. `CREATE TABLE IF NOT EXISTS` on an unchanged
    database is a no-op, so an existing `/data/scheduler.db` gains only the new table.

- Error and security rules: None. No credential, no log line, no network.

## Acceptance Criteria
- [ ] Opening a `Store` against a new path creates a `run_history` table.
- [ ] `PRAGMA table_info(run_history)` reports exactly fourteen columns, in the order above.
- [ ] `run_id` is the primary key; `coder_pool`, `repo`, `issue_number`, `dispatched_at`,
      `finished_at`, `kind` and `pr_lookup` are `NOT NULL`; `requeue_attempt` defaults to `0`.
- [ ] `RunOutcome(kind="succeeded")` constructs, with `reason is None`, `requeue_attempt == 0`,
      `pr_lookup == "none"` and `merged is None`.
- [ ] Opening a `Store` twice against the same path does not raise.

## Test Expectations
Framework: **pytest 8**. Command: `cd ops/scheduler && uv run pytest tests/test_store_history.py`.
New file `ops/scheduler/tests/test_store_history.py`. Use the store fixture copied
verbatim from `tests/test_reconcile.py:60-65`:

```python
from __future__ import annotations

import pytest

from fabro_scheduler.store import RunOutcome, Store


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def test_run_history_table_has_every_column(store):
    rows = store._conn.execute("PRAGMA table_info(run_history)").fetchall()
    assert [r["name"] for r in rows] == [
        "run_id", "coder_pool", "repo", "issue_number", "dispatched_at",
        "finished_at", "kind", "reason", "category", "requeue_attempt",
        "pr_lookup", "pr_number", "pr_url", "merged",
    ]
    not_null = {r["name"] for r in rows if r["notnull"]}
    assert not_null == {
        "coder_pool", "repo", "issue_number", "dispatched_at",
        "finished_at", "kind", "pr_lookup",
    }
    assert [r["name"] for r in rows if r["pk"]] == ["run_id"]


def test_run_outcome_defaults():
    outcome = RunOutcome(kind="succeeded")
    assert outcome.reason is None
    assert outcome.category is None
    assert outcome.finished_at is None
    assert outcome.requeue_attempt == 0
    assert outcome.pr_lookup == "none"
    assert outcome.merged is None


def test_reopening_the_store_is_a_noop(tmp_path):
    path = tmp_path / "scheduler.db"
    first = Store(path)
    first.close()
    second = Store(path)
    second.close()
```

`PRAGMA table_info` returns `notnull` as `0`/`1`. In SQLite a `TEXT PRIMARY KEY` column
is **not** implicitly `NOT NULL` — only an `INTEGER PRIMARY KEY` rowid alias is — so
`run_id` reports `notnull = 0` and is deliberately absent from the `not_null` set above.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: `archive_and_release_lease: the atomic release seam`, `_outcome_from_run: a RunOutcome from a terminal projection`, `Store.history_rows: sorted, limited reads`

## Labels
`feature`, `scheduler`, `priority:high`

## Estimate
Small

## Risk
3 - The schema cannot gain a column after task 15 deploys without a hand-written
`ALTER TABLE` against the live database on the `scheduler-data` volume. Low complexity,
but the one decision in the series with no cheap undo.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full. The repository is valid: the new
table and dataclass have no callers, and no existing behavior changed.
