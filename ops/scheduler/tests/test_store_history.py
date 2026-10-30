from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
        # ADR 0029 O21: the run's arm, copied from its lease on release.
        "workflow",
        # more-local-inference 06: 'issue' or 'pr'. A row written before the doctor's review
        # is an issue run by construction.
        "target_kind",
        # more-local-inference 08 (D21): the lane's notes on the issue at dispatch.
        "had_pack", "pack_run", "precheck_verdict",
    ]
    not_null = {r["name"] for r in rows if r["notnull"]}
    # `requeue_attempt` is `NOT NULL DEFAULT 0` in the canonical schema block, so
    # PRAGMA reports it as notnull=1. The draft's expectation omitted it; the
    # schema block is the authoritative contract, so it belongs in the set.
    assert not_null == {
        "coder_pool", "repo", "issue_number", "dispatched_at",
        "finished_at", "kind", "pr_lookup", "requeue_attempt",
        # `NOT NULL DEFAULT 'backlog'`: a row written before the trial is a
        # `backlog` run by construction (ADR 0029 O21).
        "workflow",
        "target_kind",
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


def test_the_migration_adds_the_plan_trial_columns_to_existing_scorecard_tables(tmp_path):
    """The host's scorecard tables predate #25: per-stage tokens and the coder's first
    edit must be ALTERed in on open, and an old row reads NULL, never zero."""
    import sqlite3

    path = tmp_path / "scheduler.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE run_summary (
          run_id TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1,
          summarized_at TEXT NOT NULL, terminal_node TEXT, path TEXT, refute TEXT,
          events INTEGER, error TEXT
        );
        CREATE TABLE run_stage_stats (
          run_id TEXT NOT NULL, node_id TEXT NOT NULL, handler TEXT NOT NULL,
          visits INTEGER NOT NULL, wall_ms INTEGER NOT NULL, box_ms INTEGER NOT NULL,
          timeouts INTEGER NOT NULL, failures TEXT NOT NULL, PRIMARY KEY (run_id, node_id)
        );
        INSERT INTO run_stage_stats VALUES ('R0', 'improve', 'agent', 1, 10, 10, 0, '{}');
        """
    )
    conn.commit()
    conn.close()

    reopened = Store(path)
    try:
        def columns(table):
            return {r["name"] for r in reopened._conn.execute(f"PRAGMA table_info({table})")}

        assert {"input_tokens", "output_tokens", "cache_read_tokens"} <= columns("run_stage_stats")
        assert {"first_edit_ms", "dispositions"} <= columns("run_summary")
        old = reopened._conn.execute("SELECT * FROM run_stage_stats").fetchone()
        assert old["input_tokens"] is None
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


def test_resummarize_window_deletes_only_its_summary_rows(tmp_path):
    """`resummarize_window` (ADR 0025, C9) puts a window back in the collector's queue.

    It deletes `run_summary` and `run_stage_stats` rows for runs dispatched in the
    window and nothing else: the `run_history` rows, and every row of a run outside
    the window, are the window's own record.
    """
    from collections import Counter

    from fabro_scheduler.summary import RunSummary, StageStat

    store = Store(tmp_path / "scheduler.db")
    try:
        spread = (
            ("IN1", DISPATCHED),
            ("IN2", DISPATCHED),
            ("OUT", DISPATCHED - timedelta(days=1)),
        )
        for run_id, at in spread:
            store.acquire_lease(
                coder_pool="coders-a", repo=FF, issue_number=1, run_id=run_id, dispatched_at=at
            )
            store.archive_and_release_lease(
                run_id, RunOutcome(kind="succeeded", finished_at=at + timedelta(days=1))
            )
        for run_id, _ in spread:
            store.save_run_summary(
                RunSummary(
                    run_id=run_id,
                    stages={"validate": StageStat("validate", "command", 1, 1, 0)},
                    failovers=Counter(),
                    path=[],
                    terminal_node="exit",
                    refute=[],
                    events=1,
                )
            )

        runs, summaries, stages = store.resummarize_window(
            DISPATCHED, DISPATCHED + timedelta(days=1)
        )
        assert (runs, summaries, stages) == (2, 2, 2)
        assert {r["run_id"] for r in store._conn.execute("SELECT run_id FROM run_summary")} == {
            "OUT"
        }
        assert {
            r["run_id"] for r in store._conn.execute("SELECT run_id FROM run_stage_stats")
        } == {"OUT"}
        assert {r["run_id"] for r in _history(store)} == {"IN1", "IN2", "OUT"}
    finally:
        store.close()


def test_refute_row_round_trips_the_new_field_keys(tmp_path):
    # #40: the stored refute row carries unverifiable_only and ci_credited, so the
    # scorecard can split the "other" bucket and count CI-credited criteria.
    from collections import Counter

    from fabro_scheduler.summary import RefuteVerdict, RunSummary

    store = Store(tmp_path / "scheduler.db")
    try:
        store.save_run_summary(
            RunSummary(
                run_id="RX", stages={}, failovers=Counter(), path=[], terminal_node="x",
                refute=[RefuteVerdict("fail", False, True, 3)], events=1,
            )
        )
        raw = store._conn.execute(
            "SELECT refute FROM run_summary WHERE run_id = ?", ("RX",)
        ).fetchone()["refute"]
        assert raw == ('[{"verdict": "fail", "not_met": false, '
                       '"unverifiable_only": true, "ci_credited": 3}]')
    finally:
        store.close()


def test_the_migration_adds_the_workflow_column_to_leases_and_history(tmp_path):
    """ADR 0029 O21. A host DB that predates the trial has neither table's
    `workflow` column; `__init__` must ALTER both in on open, and a row written
    before it reads `backlog`, which is what it was."""
    import sqlite3

    path = tmp_path / "scheduler.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE leases (
          coder_pool TEXT PRIMARY KEY, repo TEXT NOT NULL,
          issue_number INTEGER NOT NULL, run_id TEXT NOT NULL UNIQUE,
          dispatched_at TEXT NOT NULL, queued_since TEXT
        );
        CREATE TABLE run_history (
          run_id TEXT PRIMARY KEY, coder_pool TEXT NOT NULL,
          repo TEXT NOT NULL, issue_number INTEGER NOT NULL,
          dispatched_at TEXT NOT NULL, finished_at TEXT NOT NULL,
          kind TEXT NOT NULL, reason TEXT, category TEXT,
          requeue_attempt INTEGER NOT NULL DEFAULT 0,
          pr_lookup TEXT NOT NULL, pr_number INTEGER, pr_url TEXT, merged INTEGER
        );
        INSERT INTO run_history
          (run_id, coder_pool, repo, issue_number, dispatched_at, finished_at,
           kind, pr_lookup)
        VALUES ('R0', 'coders-a', 'o/a', 1, '2026-09-01T00:00:00+00:00',
                '2026-09-01T01:00:00+00:00', 'succeeded', 'none');
        """
    )
    conn.commit()
    conn.close()

    reopened = Store(path)
    try:
        for table in ("leases", "run_history"):
            columns = {
                r["name"]
                for r in reopened._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert "workflow" in columns, table
        assert reopened.history_rows()[0]["workflow"] == "backlog"

        # And a lease taken after the migration still round-trips its arm.
        LeaseStore(reopened).acquire(
            Lease("coders-b", "o/a", 2, "R2", datetime.now(UTC), workflow="ralph")
        )
        assert LeaseStore(reopened).active()[0].workflow == "ralph"
        LeaseStore(reopened).archive_and_release(
            "R2", RunOutcome(kind="succeeded")
        )
        assert {
            row["run_id"]: row["workflow"] for row in reopened.history_rows()
        } == {"R0": "backlog", "R2": "ralph"}
    finally:
        reopened.close()


# --- the schema upgrade (more-local-inference 06) -----------------------------------
#
# `target_kind` and the doctor's columns arrive on a database that already holds rows: the
# real one lives on the `scheduler-data` volume and has been collecting leases since draft 08.
# `CREATE TABLE IF NOT EXISTS` never alters an existing table, so `Store._apply_migrations`
# does it, and this test opens a database in the shape that shipped BEFORE task 06.

OLD_SCHEMA = """
CREATE TABLE leases (
  coder_pool    TEXT PRIMARY KEY,
  repo          TEXT NOT NULL,
  issue_number  INTEGER NOT NULL,
  run_id        TEXT NOT NULL UNIQUE,
  dispatched_at TEXT NOT NULL,
  queued_since  TEXT,
  workflow      TEXT NOT NULL DEFAULT 'backlog'
);
CREATE TABLE run_history (
  run_id          TEXT PRIMARY KEY,
  coder_pool      TEXT NOT NULL,
  repo            TEXT NOT NULL,
  issue_number    INTEGER NOT NULL,
  dispatched_at   TEXT NOT NULL,
  finished_at     TEXT NOT NULL,
  kind            TEXT NOT NULL,
  reason          TEXT,
  category        TEXT,
  requeue_attempt INTEGER NOT NULL DEFAULT 0,
  pr_lookup       TEXT NOT NULL,
  pr_number       INTEGER,
  pr_url          TEXT,
  merged          INTEGER,
  local_tokens    INTEGER,
  hosted_tokens   INTEGER,
  local_cost_usd_micros  INTEGER,
  hosted_cost_usd_micros INTEGER,
  workflow      TEXT NOT NULL DEFAULT 'backlog'
);
CREATE TABLE pr_facts (
  repo             TEXT    NOT NULL,
  pr_number        INTEGER NOT NULL,
  fetched_at       TEXT    NOT NULL,
  state            TEXT    NOT NULL,
  created_at       TEXT,
  merged_at        TEXT,
  closed_at        TEXT,
  labels           TEXT    NOT NULL,
  additions        INTEGER NOT NULL,
  deletions        INTEGER NOT NULL,
  commits          INTEGER NOT NULL,
  operator_commits INTEGER NOT NULL,
  report           TEXT,
  reason           TEXT,
  queue_approved   INTEGER NOT NULL,
  queue_handoff    INTEGER NOT NULL,
  PRIMARY KEY (repo, pr_number)
);
CREATE TABLE background_jobs (
  job_id              INTEGER PRIMARY KEY AUTOINCREMENT,
  kind                TEXT    NOT NULL,
  repo                TEXT    NOT NULL,
  target              TEXT    NOT NULL DEFAULT '',
  inputs_json         TEXT    NOT NULL DEFAULT '{}',
  priority            INTEGER NOT NULL,
  typical_box_minutes INTEGER NOT NULL,
  created_at          TEXT    NOT NULL,
  state               TEXT    NOT NULL,
  run_id              TEXT,
  box                 TEXT,
  cancels             INTEGER NOT NULL DEFAULT 0,
  last_started_at     TEXT,
  last_finished_at    TEXT,
  outcome             TEXT
);
"""


def test_an_old_database_upgrades_and_its_rows_read_as_issue_rows(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO leases (coder_pool, repo, issue_number, run_id, dispatched_at,"
        " queued_since, workflow) VALUES ('coders-a', 'o/r', 7, 'RUN1',"
        " '2026-10-29T00:00:00+00:00', NULL, 'backlog')"
    )
    conn.execute(
        "INSERT INTO run_history (run_id, coder_pool, repo, issue_number, dispatched_at,"
        " finished_at, kind, pr_lookup) VALUES ('RUN0', 'coders-a', 'o/r', 6,"
        " '2026-10-28T00:00:00+00:00', '2026-10-28T01:00:00+00:00', 'succeeded', 'none')"
    )
    conn.execute(
        "INSERT INTO pr_facts (repo, pr_number, fetched_at, state, labels, additions,"
        " deletions, commits, operator_commits, queue_approved, queue_handoff)"
        " VALUES ('o/r', 7, '2026-10-29T00:00:00+00:00', 'open', '[]', 1, 1, 1, 0, 0, 0)"
    )
    conn.execute(
        "INSERT INTO background_jobs (kind, repo, target, inputs_json, priority,"
        " typical_box_minutes, created_at, state) VALUES ('box-noop', 'o/r', '', '{}',"
        " 5, 5, '2026-10-29T00:00:00+00:00', 'done')"
    )
    conn.commit()
    conn.close()

    upgraded = Store(path)
    try:
        columns = {
            row["name"] for row in upgraded._conn.execute("PRAGMA table_info(leases)")
        }
        assert "target_kind" in columns
        lease = upgraded.lease_rows()[0]
        assert lease["target_kind"] == "issue", "an older lease is an issue lease"
        history = upgraded.history_rows()[0]
        assert history["target_kind"] == "issue"
        facts = upgraded.pr_facts_row("o/r", 7)
        assert facts["head_sha"] is None
        assert facts["report_items"] is None
        assert facts["doctor_marker"] is None
        job = upgraded.background_jobs()[0]
        assert (job["review_run_id"], job["review_dispatched_at"]) == (None, None)
        assert job["review_failures"] == 0
        # And the upgraded database still answers the queries the new code makes.
        assert upgraded.pending_review_jobs() == []
        assert upgraded.reviews_dispatched_since(
            datetime(2026, 10, 1, tzinfo=UTC)
        ) == 0
    finally:
        upgraded.close()
