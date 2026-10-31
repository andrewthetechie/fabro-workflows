"""The repository orientation's scheduler half (more-local-inference 10).

The producer queues one `repo-orientation` job per repository when its newest job of that kind
is more than a day old or it has never had one, and never while one is queued or running — both
in one query, so a repository the table has never heard of is due rather than invisible. The
lane records a succeeded run's `orientation main=<sha12> chars=<n>` from the repository's pinned
issue, and `orientation unchanged` for a run whose `stop` found a current map. Archiving an
issue run records `had_orientation`, so Phase 4 can compare credits per `decompose` visit.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from fabro_scheduler.background import BackgroundLane
from fabro_scheduler.config import BACKGROUND_JOB_PRIORITY, load_config
from fabro_scheduler.github import Issue
from fabro_scheduler.lease import LeaseStore
from fabro_scheduler.orientation import (
    KIND,
    LABEL,
    ORIENTATION_TYPICAL_MINUTES,
    OrientationMarker,
    OrientationProducer,
    outcome_for,
    parse_marker,
)
from fabro_scheduler.store import RunOutcome, Store

from test_background import (BOXES, NOW, TRACKED_REPOS, FakeFabro, FakeHealth, lane)

JELLY = "andrewthetechie/jelly-swipe"
WRITERS = "andrewtechie/writers-app"


def config(**env: str):
    return load_config(
        TRACKED_REPOS,
        env={"SCHEDULER_BACKGROUND": "on", "SCHEDULER_BOX_HEALTH": BOXES, **env},
    )


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def seed(store: Store, repo: str, number: int = 1) -> None:
    store.upsert_issue(Issue(repo, number, f"issue {number}", frozenset({"agent"}), NOW))


def jobs(store: Store) -> list[tuple[str, str, str]]:
    with store._lock:
        rows = store._conn.execute(
            "SELECT kind, repo, state FROM background_jobs ORDER BY job_id"
        ).fetchall()
    return [tuple(row) for row in rows]


def marker(main: str = "a" * 40, *, at: str = "2026-11-01T10:00:00Z", run: str = "R1",
           body_extra: int = 0) -> OrientationMarker:
    return OrientationMarker(main=main, at=at, run=run, chars=200 + body_extra)


# --- the marker and the outcome -----------------------------------------------------


def test_the_marker_parses_the_first_line_of_the_issue_body():
    body = ("<!-- fabro:orientation main=abcdef at=2026-11-01T10:00:00Z run=01M3ABC -->\n\n"
            "**Repository orientation.** ...")
    parsed = parse_marker(body)
    assert parsed is not None
    assert (parsed.main, parsed.at, parsed.run) == ("abcdef", "2026-11-01T10:00:00Z", "01M3ABC")
    assert parsed.chars == len(body), "the body length is what a planning run pays for"


def test_a_body_of_another_kind_or_one_with_no_marker_is_not_mistaken_for_one():
    for body in (None, "", "just a body", "<!-- fabro:precheck verdict=ready body=x main=y "
                 "at=z run=r -->\nready"):
        assert parse_marker(body) is None, body


def test_the_outcome_names_the_commit_and_the_size():
    assert outcome_for(marker(main="abcdef1234567890"), "R1") == (
        "orientation main=abcdef123456 chars=200"
    )


def test_a_run_that_stopped_on_a_current_map_earned_unchanged():
    assert outcome_for(marker(at="2026-10-01T10:00:00Z", run="R0"), "R1") == (
        "orientation unchanged"
    )
    assert outcome_for(None, "R1") == "orientation unchanged"


def test_a_map_an_earlier_attempt_wrote_after_the_job_was_queued_counts():
    assert outcome_for(marker(at="2026-11-01T10:05:00Z", run="R0"), "R1",
                       "2026-11-01T10:00:00+00:00") == (
        "orientation main=" + "a" * 12 + " chars=200"
    )


# --- the producer -------------------------------------------------------------------


def test_the_producer_queues_one_job_per_repository_with_none(store):
    cfg = config()
    seed(store, JELLY)
    seed(store, WRITERS)
    created = OrientationProducer(cfg, store).tick(now=NOW)
    rows = [store.background_job(job) for job in created]
    assert sorted(row["repo"] for row in rows) == sorted(
        repo.name for repo in cfg.schedulable_repos()
    ), "every schedulable repository, whether or not it has a cached issue"
    assert all(row["target"] == "" and row["inputs_json"] == "{}" for row in rows)
    assert (rows[0]["priority"], rows[0]["typical_box_minutes"]) == (
        BACKGROUND_JOB_PRIORITY[KIND],
        ORIENTATION_TYPICAL_MINUTES,
    )
    assert BACKGROUND_JOB_PRIORITY[KIND] == 4
    assert ORIENTATION_TYPICAL_MINUTES <= 8, "a job that does not fit a gap never starts"


def one(cfg, store, *, now=NOW) -> int:
    """The single job `tick` queued, for a config with one schedulable repository."""
    created = OrientationProducer(cfg, store).tick(now=now)
    assert len(created) == 1, created
    return created[0]


def only_repo():
    """A config with one schedulable repository, so `tick` returns one job at a time."""
    full = config()
    keep = full.schedulable_repos()[0]
    return dataclasses.replace(full, repos=(keep,))


def test_one_job_per_repository_per_day(store):
    cfg = only_repo()
    producer = OrientationProducer(cfg, store)
    (job_id,) = producer.tick(now=NOW)
    assert producer.tick(now=NOW) == [], "queued, so not again"
    store.start_background_job(job_id, run_id="R1", box="coders-a", now=NOW)
    assert producer.tick(now=NOW) == [], "running, so not again"
    store.finish_background_job(job_id, state="done", outcome="orientation main=x chars=1",
                                now=NOW)
    assert producer.tick(now=NOW + timedelta(hours=23)) == [], "not a day old yet"
    assert len(producer.tick(now=NOW + timedelta(hours=25))) == 1, "a day old, so again"


def test_a_finished_batch_gives_way_to_the_next_tick_without_a_stall(store):
    """The dedupe is in the query: a producer that filtered after reading a fixed set would see
    the same covered rows forever (the calibration's stall, 2026-10-31)."""
    cfg = only_repo()
    producer = OrientationProducer(cfg, store)
    (job_id,) = producer.tick(now=NOW)
    store.finish_background_job(job_id, state="done", outcome="orientation unchanged", now=NOW)
    assert producer.tick(now=NOW) == [], "the same second"
    assert len(producer.tick(now=NOW + timedelta(hours=25))) == 1


def test_a_cancelled_job_is_requeued_and_does_not_get_a_second_row(store):
    cfg = only_repo()
    producer = OrientationProducer(cfg, store)
    (job_id,) = producer.tick(now=NOW)
    store.requeue_background_job(job_id, now=NOW)
    assert producer.tick(now=NOW) == []
    assert len(jobs(store)) == 1


def test_the_switch_is_read_at_the_tick_so_a_switched_off_lane_queues_nothing(store):
    cfg = dataclasses.replace(config(), background=False)
    assert OrientationProducer(cfg, store).enabled is False
    assert OrientationProducer(cfg, store).tick(now=NOW) == []
    assert jobs(store) == []


def test_the_kind_absent_from_bg_kinds_does_nothing(store):
    cfg = config(SCHEDULER_BG_KINDS="issue-precheck,context-pack")
    assert OrientationProducer(cfg, store).enabled is False
    assert OrientationProducer(cfg, store).tick(now=NOW) == []
    assert jobs(store) == []


# --- the lane's read-back, and had_orientation --------------------------------------


def test_the_lane_records_the_map_from_the_repository_issue(store):
    store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="", inputs={},
        priority=BACKGROUND_JOB_PRIORITY[KIND],
        typical_box_minutes=ORIENTATION_TYPICAL_MINUTES, now=NOW,
    )
    (job,) = store.queued_background_jobs()
    store.start_background_job(int(job["job_id"]), run_id="R1", box="coders-a", now=NOW)
    seen: list[str] = []

    def reader(repo: str):
        seen.append(repo)
        return marker(main="abcdef1234567890", run="R1")

    lane_ = BackgroundLane(
        config(), store, LeaseStore(store),
        FakeFabro(runs={"R1": {"lifecycle": {"status": {"kind": "succeeded"}}}}),
        health=FakeHealth(), orientation_reader=reader, clock=lambda: NOW,
    )
    lane_.sync(NOW)
    assert store.background_job(int(job["job_id"]))["outcome"] == (
        "orientation main=abcdef123456 chars=200"
    )
    assert seen == [JELLY], "read by repository, not by an issue the job has no target for"


def test_archiving_an_issue_run_records_had_orientation(store):
    """Phase 4 compares credits per `decompose` visit with and without the map (D21's shape).

    `leases.dispatched_at` is fixed at NOW - 10 minutes by the fixture, so a map that finished
    after that is one the run never read, and a map that finished before it is one it did.
    """
    leases = LeaseStore(store)
    from test_background import held_lease

    def map_done(at, run="orientation-run"):
        job_id = store.enqueue_background_job(
            kind=KIND, repo=JELLY, target="", inputs={},
            priority=BACKGROUND_JOB_PRIORITY[KIND],
            typical_box_minutes=ORIENTATION_TYPICAL_MINUTES, now=NOW - timedelta(hours=2),
        )
        store.finish_background_job(job_id, state="done", outcome="orientation main=x chars=1",
                                    now=at)

    def had(run_id):
        return {row["run_id"]: row for row in store.history_rows()}[run_id]["had_orientation"]

    held_lease(leases, box="coders-a", issue=2, run_id="coderun")
    store.archive_and_release_lease("coderun", RunOutcome(kind="succeeded"), at=NOW)
    assert had("coderun") == 0, "no map had been written"

    map_done(NOW + timedelta(minutes=5))
    held_lease(leases, box="coders-b", issue=3, run_id="coderun2")
    store.archive_and_release_lease("coderun2", RunOutcome(kind="succeeded"), at=NOW)
    assert had("coderun2") == 0, "the map finished after this run was dispatched"

    map_done(NOW - timedelta(hours=1))
    held_lease(leases, box="coders-a", issue=4, run_id="coderun3")
    store.archive_and_release_lease("coderun3", RunOutcome(kind="succeeded"), at=NOW)
    assert had("coderun3") == 1
