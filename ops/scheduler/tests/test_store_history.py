from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fabro_scheduler.lease import Lease, LeaseStore
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
        "local_tokens", "hosted_tokens",
        "local_cost_usd_micros", "hosted_cost_usd_micros",
    ]
    not_null = {r["name"] for r in rows if r["notnull"]}
    # `requeue_attempt` is `NOT NULL DEFAULT 0` in the canonical schema block, so
    # PRAGMA reports it as notnull=1. The draft's expectation omitted it; the
    # schema block is the authoritative contract, so it belongs in the set.
    assert not_null == {
        "coder_pool", "repo", "issue_number", "dispatched_at",
        "finished_at", "kind", "pr_lookup", "requeue_attempt",
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


DISPATCHED = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
FINISHED = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
FF = "andrewthetechie/jelly-swipe"


def _acquire(store: Store, run_id: str = "R1") -> None:
    store.acquire_lease(
        coder_pool="coders-a", repo=FF, issue_number=350,
        run_id=run_id, dispatched_at=DISPATCHED,
        queued_since=datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
    )


def _history(store: Store) -> list:
    return store._conn.execute("SELECT * FROM run_history").fetchall()


def test_archive_writes_the_row_and_drops_the_lease(store):
    _acquire(store)
    row = store.archive_and_release_lease(
        "R1", RunOutcome(kind="succeeded", reason="completed", finished_at=FINISHED)
    )
    assert row["coder_pool"] == "coders-a"
    assert store.lease_rows() == []
    (kept,) = _history(store)
    assert kept["run_id"] == "R1"
    assert kept["repo"] == FF
    assert kept["issue_number"] == 350
    assert kept["coder_pool"] == "coders-a"
    assert kept["dispatched_at"] == DISPATCHED.isoformat()
    assert kept["finished_at"] == FINISHED.isoformat()
    assert kept["kind"] == "succeeded"
    assert kept["reason"] == "completed"
    assert kept["pr_lookup"] == "none"
    assert kept["merged"] is None


def test_archive_without_a_lease_writes_nothing(store):
    assert store.archive_and_release_lease("nope", RunOutcome(kind="succeeded")) is None
    assert _history(store) == []


def test_finished_at_falls_back_to_the_release_time(store):
    _acquire(store)
    store.archive_and_release_lease("R1", RunOutcome(kind="lost"), at=FINISHED)
    (kept,) = _history(store)
    assert kept["finished_at"] == FINISHED.isoformat()


def test_archiving_twice_is_idempotent(store):
    _acquire(store)
    store.archive_and_release_lease("R1", RunOutcome(kind="succeeded"))
    assert store.archive_and_release_lease("R1", RunOutcome(kind="failed")) is None
    assert len(_history(store)) == 1


def test_lease_store_archive_preserves_queued_since(store):
    _acquire(store)
    leased = LeaseStore(store).archive_and_release("R1", RunOutcome(kind="succeeded"))
    assert isinstance(leased, Lease)
    assert leased.queued_since == datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


def test_plain_release_still_writes_no_history(store):
    _acquire(store)
    assert store.release_lease("R1") is not None
    assert _history(store) == []

from fabro_scheduler.store import DEFAULT_HISTORY_LIMIT, HISTORY_SORT_COLUMNS


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


def test_the_migration_adds_the_usage_columns_to_an_existing_table(tmp_path):
    """A host DB that predates the feature has no token columns, and `__init__`
    must ALTER them in on open rather than lose the usage the page now shows."""
    import sqlite3

    path = tmp_path / "scheduler.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE run_history (
          run_id TEXT PRIMARY KEY, coder_pool TEXT NOT NULL,
          repo TEXT NOT NULL, issue_number INTEGER NOT NULL,
          dispatched_at TEXT NOT NULL, finished_at TEXT NOT NULL,
          kind TEXT NOT NULL, reason TEXT, category TEXT,
          requeue_attempt INTEGER NOT NULL DEFAULT 0,
          pr_lookup TEXT NOT NULL, pr_number INTEGER, pr_url TEXT, merged INTEGER
        );
        """
    )
    conn.commit()
    conn.close()

    reopened = Store(path)
    try:
        columns = {
            r["name"]
            for r in reopened._conn.execute("PRAGMA table_info(run_history)").fetchall()
        }
        assert {"local_tokens", "hosted_tokens", "local_cost_usd_micros",
                "hosted_cost_usd_micros"} <= columns
    finally:
        reopened.close()


def test_a_usage_outcome_round_trips_through_the_history_row(tmp_path):
    store = Store(tmp_path / "scheduler.db")
    try:
        _acquire(store, run_id="R1")
        from fabro_scheduler.store import RunOutcome as O
        store.archive_and_release_lease(
            "R1",
            O(
                kind="succeeded",
                local_tokens=12000,
                hosted_tokens=8000,
                local_cost_usd_micros=100_000,
                hosted_cost_usd_micros=200_000,
            ),
        )
        row = store.history_rows(sort="finished_at")[0]
        assert row["local_tokens"] == 12000
        assert row["hosted_tokens"] == 8000
        assert row["local_cost_usd_micros"] == 100_000
        assert row["hosted_cost_usd_micros"] == 200_000
    finally:
        store.close()


def test_avg_run_duration_is_none_with_no_history(store):
    assert store.avg_run_duration(FF) is None


def test_avg_run_duration_is_the_mean_of_released_runs(store):
    _acquire(store, run_id="A")
    _acquire(store, run_id="B")
    _acquire(store, run_id="C")
    # A took 4h, B took 2h, C took 6h from the DISPATCHED/FINISHED constants below.
    store.archive_and_release_lease("A", RunOutcome(kind="succeeded"))
    store.archive_and_release_lease("B", RunOutcome(kind="succeeded"))
    store.archive_and_release_lease("C", RunOutcome(kind="succeeded"))

    # _archive (below) writes finished_at = FINISHED for every run, so they all
    # share one duration; use explicit varied finishes for a real mean.
    for run_id, finished in (
        ("A", "2026-09-20T16:00:00+00:00"),
        ("B", "2026-09-20T14:00:00+00:00"),  # 2h after DISPATCHED
        ("C", "2026-09-20T18:00:00+00:00"),  # 6h  after DISPATCHED
    ):
        store._conn.execute(
            "UPDATE run_history SET finished_at = ? WHERE run_id = ?",
            (finished, run_id),
        )
    store._conn.commit()

    # DISPATCHED is 2026-09-20T12:00:00Z. A:4h, B:2h, C:6h -> mean 4h.
    from datetime import timedelta as _td
    assert store.avg_run_duration(FF) == _td(hours=4)
