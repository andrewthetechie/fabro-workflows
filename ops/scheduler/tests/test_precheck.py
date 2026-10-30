"""The issue precheck's scheduler half (more-local-inference 07), in shadow mode.

The producer queues one `issue-precheck` job per issue for the next issues in dispatch order,
never for an issue a coder loop already holds, and skips a queued job whose issue left the
queue first. The lane records a succeeded run's verdict, read back from the issue's marker, as
the job's outcome. Nothing here holds or labels an issue.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from fabro_scheduler.config import load_config
from fabro_scheduler.github import GitHubError, Issue
from fabro_scheduler.lease import LeaseStore
from fabro_scheduler.precheck import (
    KIND,
    PRECHECK_TYPICAL_MINUTES,
    Marker,
    PrecheckProducer,
    newest_marker,
    outcome_for,
    parse_marker,
)
from fabro_scheduler.queue import build_queue
from fabro_scheduler.store import Store

from test_background import (BOXES, IDLE, NOW, TRACKED_REPOS, FakeFabro, FakeHealth, lane,
                             running)

JELLY = "andrewthetechie/jelly-swipe"
WRITERS = "andrewthetechie/writers-app"


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


def seed(store: Store, repo: str, number: int) -> None:
    store.upsert_issue(Issue(repo, number, f"issue {number}", frozenset({"agent"}), NOW))


def ranked(store: Store, cfg):
    return build_queue(cfg.schedulable_repos(), store, NOW, cfg.starvation_ceiling)


def jobs(store: Store) -> list[tuple[str, str, str, str]]:
    with store._lock:
        rows = store._conn.execute(
            "SELECT kind, repo, target, state FROM background_jobs ORDER BY job_id"
        ).fetchall()
    return [tuple(row) for row in rows]


# --- the producer -------------------------------------------------------------------


def test_the_producer_queues_the_next_issues_once_each(store):
    cfg = dataclasses.replace(config(), precheck_top_n=2)
    for n in (3, 4, 5):
        seed(store, JELLY, n)
    producer = PrecheckProducer(cfg, store)
    created = producer.tick(ranked(store, cfg), now=NOW)
    assert len(created) == 2
    assert jobs(store) == [(KIND, JELLY, "3", "queued"), (KIND, JELLY, "4", "queued")]
    row = store.background_job(created[0])
    assert (row["priority"], row["typical_box_minutes"], row["inputs_json"]) == (
        2,
        PRECHECK_TYPICAL_MINUTES,
        '{"issue_number": 3}',
    )
    assert producer.tick(ranked(store, cfg), now=NOW) == [], "one job per issue"


def test_the_producer_never_queues_an_issue_a_coder_loop_holds(store):
    cfg = dataclasses.replace(config(), precheck_top_n=1)
    seed(store, JELLY, 3)
    seed(store, JELLY, 4)
    producer = PrecheckProducer(cfg, store)
    producer.tick(ranked(store, cfg), running={(JELLY, 3)}, now=NOW)
    assert jobs(store) == [(KIND, JELLY, "4", "queued")], "the held issue does not use a slot"


def test_a_precheck_whose_issue_left_the_queue_is_skipped(store):
    cfg = config()
    seed(store, JELLY, 3)
    seed(store, JELLY, 4)
    producer = PrecheckProducer(cfg, store)
    producer.tick(ranked(store, cfg), now=NOW)
    store.forget_issue(JELLY, 3)
    producer.tick(ranked(store, cfg), running={(JELLY, 4)}, now=NOW + timedelta(seconds=5))
    assert jobs(store) == [(KIND, JELLY, "3", "skipped"), (KIND, JELLY, "4", "skipped")]
    row = store.background_job(1)
    assert row["outcome"] == "the issue left the queue before its issue-precheck ran"


def test_the_producer_is_off_with_the_lane_or_its_kind_off(store):
    seed(store, JELLY, 3)
    for cfg in (
        load_config(TRACKED_REPOS, env={"SCHEDULER_BACKGROUND": "off"}),
        config(SCHEDULER_BG_KINDS="box-noop"),
    ):
        producer = PrecheckProducer(cfg, store)
        assert producer.enabled is False
        assert producer.tick(ranked(store, cfg), now=NOW) == []
    assert jobs(store) == []


def test_top_n_zero_queues_nothing(store):
    cfg = config(SCHEDULER_PRECHECK_TOP_N="0")
    seed(store, JELLY, 3)
    assert PrecheckProducer(cfg, store).tick(ranked(store, cfg), now=NOW) == []


# --- the marker and the outcome -------------------------------------------------------


MARKER_LINE = (
    "<!-- fabro:precheck verdict=already_done body=abcdef012345 main=fff "
    "at=2026-10-30T20:00:00Z run=01RUN -->"
)


def test_the_marker_parses_from_the_first_line_only():
    marker = parse_marker(MARKER_LINE + "\n\n**Precheck: already done?**")
    assert marker == Marker("already_done", "abcdef012345", "fff", "2026-10-30T20:00:00Z", "01RUN")
    assert parse_marker("hello\n" + MARKER_LINE) is None
    assert parse_marker(None) is None
    older = MARKER_LINE.replace("already_done", "unclear").replace("01RUN", "00OLD")
    assert newest_marker([{"body": older}, {"body": "x"}, {"body": MARKER_LINE}]).run == "01RUN"


def test_the_outcome_is_the_verdict_only_for_the_runs_own_marker():
    marker = parse_marker(MARKER_LINE)
    assert outcome_for(marker, "01RUN") == "verdict=already_done"
    assert outcome_for(marker, "02OTHER") == "succeeded (no new verdict)"
    assert outcome_for(None, "01RUN") == "succeeded (no marker)"


# --- the lane records it --------------------------------------------------------------


def precheck_job(store: Store) -> int:
    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="3", inputs={"issue_number": 3}, priority=2,
        typical_box_minutes=10, now=NOW,
    )
    running(store, job_id, run_id="01RUN")
    return job_id


def test_a_succeeded_precheck_records_its_verdict(store):
    job_id = precheck_job(store)
    fabro = FakeFabro(runs={"01RUN": {"lifecycle": {"status": {"kind": "succeeded"}}}})
    asked = []

    def reader(repo, number):
        asked.append((repo, number))
        return parse_marker(MARKER_LINE)

    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth())
    lane_._read_precheck = reader
    ends = lane_.sync(NOW)
    assert asked == [(JELLY, 3)]
    assert [(end.state, end.outcome) for end in ends] == [("done", "verdict=already_done")]
    row = store.background_job(job_id)
    assert (row["state"], row["outcome"]) == ("done", "verdict=already_done")


def test_an_unreadable_verdict_leaves_the_plain_success(store):
    job_id = precheck_job(store)
    fabro = FakeFabro(runs={"01RUN": {"lifecycle": {"status": {"kind": "succeeded"}}}})

    def reader(repo, number):
        raise GitHubError("rate limited")

    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth())
    lane_._read_precheck = reader
    lane_.sync(NOW)
    assert store.background_job(job_id)["outcome"] == "succeeded"


def test_a_cancelled_precheck_is_queued_again_without_a_read(store):
    job_id = precheck_job(store)
    fabro = FakeFabro(
        runs={"01RUN": {"lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}}}}
    )
    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth())
    lane_._read_precheck = lambda repo, number: pytest.fail("no read for a cancel")
    lane_.sync(NOW)
    row = store.background_job(job_id)
    assert (row["state"], row["cancels"]) == ("queued", 1)


def test_the_lane_starts_a_precheck_on_the_issue_precheck_package(store):
    precheck = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="3", inputs={"issue_number": 3}, priority=2,
        typical_box_minutes=10, now=NOW,
    )
    fabro = FakeFabro()
    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth({"coders-a": IDLE}))
    starts = lane_.fill(NOW)
    assert [start.job_id for start in starts] == [precheck]
    sent = fabro.dispatched[0]
    assert sent["workflow"] == "issue-precheck"
    assert sent["inputs"] == {"issue_number": 3}
    assert sent["labels"] == {"lane": "background", "box": "coders-a", "job": KIND}
