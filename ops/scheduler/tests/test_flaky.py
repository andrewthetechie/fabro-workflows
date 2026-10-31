"""The flaky investigation's scheduler half (more-local-inference 10).

The producer queues one `flaky-investigation` job for each queued issue the scout filed with
`flaky-test`, in dispatch order, and never twice: the `NOT EXISTS` in `issues_without_job` is
the dedupe, so a batch that is already done gives way to the next one. The lane records a
succeeded run's `flaky cause=<cause> run=<id>` outcome from the issue's marker. Nothing here
labels, closes or reorders an issue.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from fabro_scheduler.flaky import (
    FLAKY_LABEL,
    FLAKY_TYPICAL_MINUTES,
    KIND,
    FlakyMarker,
    FlakyProducer,
    newest_marker,
    outcome_for,
    parse_marker,
)
from fabro_scheduler.config import BACKGROUND_JOB_PRIORITY
from fabro_scheduler.github import Issue
from fabro_scheduler.lease import LeaseStore
from fabro_scheduler.queue import build_queue
from fabro_scheduler.store import Store

from fabro_scheduler.background import BackgroundLane
from test_background import (BOXES, IDLE, NOW, TRACKED_REPOS, FakeFabro, FakeHealth, lane,
                             running)
from test_precheck import config, jobs

JELLY = "andrewthetechie/jelly-swipe"
WRITERS = "andrewthetechie/writers-app"


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def seed(store: Store, repo: str, number: int, *labels: str) -> None:
    """One cached queue item. Every cached item carries `agent`: the cache IS that collection."""
    store.upsert_issue(
        Issue(repo, number, f"scout: flaky test {number}", frozenset({"agent", *labels}), NOW)
    )


def ranked(store: Store, cfg):
    return build_queue(cfg.schedulable_repos(), store, NOW, cfg.starvation_ceiling)


def flakies(store: Store) -> list[tuple[str, str, str, str]]:
    return [row for row in jobs(store) if row[0] == KIND]


# --- the marker and the outcome -----------------------------------------------------


def test_the_marker_parses_what_post_writes():
    marker = parse_marker(
        "<!-- fabro:flaky-investigation test=9354 cause=timing "
        "at=2026-11-01T10:00:00Z run=01M3ABC -->\n\n**Flaky investigation.**"
    )
    assert marker == FlakyMarker("9354", "timing", "2026-11-01T10:00:00Z", "01M3ABC")


def test_a_marker_of_another_kind_or_a_bad_shape_is_not_mistaken_for_one():
    for body in (
        None,
        "",
        "<!-- fabro:precheck verdict=ready body=abc main=def at=2026-11-01T10:00:00Z run=r -->",
        "<!-- fabro:flaky-investigation test=1 cause=timing -->",
        "not a marker at all",
    ):
        assert parse_marker(body) is None, body


def test_the_newest_marker_wins_and_a_comment_body_that_is_not_a_string_is_skipped():
    comments = [
        {"body": "<!-- fabro:flaky-investigation test=1 cause=timing at=2026-10-01T00:00:00Z run=a -->"},
        {"body": None},
        {"body": "<!-- fabro:flaky-investigation test=1 cause=other at=2026-11-01T00:00:00Z run=b -->"},
    ]
    assert newest_marker(comments).cause == "other"


def test_the_outcome_names_the_cause_and_the_run_that_posted_it():
    marker = FlakyMarker("9354", "timing", "2026-11-01T10:00:00Z", "R1")
    assert outcome_for(marker, "R1") == "flaky cause=timing run=R1"


def test_a_run_that_stopped_on_an_old_diagnosis_earned_none():
    marker = FlakyMarker("9354", "timing", "2026-10-01T10:00:00Z", "R0")
    assert outcome_for(marker, "R1") == "succeeded (no new diagnosis)"


def test_a_run_that_stopped_earned_none():
    assert outcome_for(None, "R1") == "succeeded (no marker)"


def test_a_diagnosis_an_earlier_attempt_posted_after_the_job_was_queued_counts():
    """The lane can cancel a run inside `post`, after the comment went up (job 144, 2026-10-30);
    the retry then stops on the marker it finds, and the job must not read as a miss."""
    marker = FlakyMarker("9354", "ordering", "2026-11-01T10:05:00Z", "R0")
    queued_at = "2026-11-01T10:00:00+00:00"
    assert outcome_for(marker, "R1", queued_at) == "flaky cause=ordering run=R0"


# --- the producer -------------------------------------------------------------------


def test_the_producer_queues_one_diagnosis_per_flaky_issue(store):
    cfg = dataclasses.replace(config(), precheck_per_repo=10)
    seed(store, JELLY, 3, FLAKY_LABEL)
    seed(store, JELLY, 4)  # an ordinary queue item
    seed(store, JELLY, 5, "bug", FLAKY_LABEL)
    created = FlakyProducer(cfg, store).tick(ranked(store, cfg), now=NOW)
    assert flakies(store) == [
        (KIND, JELLY, "3", "queued"),
        (KIND, JELLY, "5", "queued"),
    ], "only the `flaky-test` issues, and #4 is not one"
    row = store.background_job(created[0])
    assert (row["priority"], row["typical_box_minutes"], row["inputs_json"]) == (
        BACKGROUND_JOB_PRIORITY[KIND],
        FLAKY_TYPICAL_MINUTES,
        '{"issue_number": 3}',
    )
    assert BACKGROUND_JOB_PRIORITY[KIND] == 3
    assert FLAKY_TYPICAL_MINUTES <= 8, "a job that does not fit a gap never starts"


def test_the_dedupe_is_in_the_query_so_a_finished_batch_gives_way_to_the_next(store):
    """The calibration's stall, as a test: filtering after the window leaves a producer that
    sees the same covered set on every tick and queues nothing new."""
    cfg = dataclasses.replace(config(), precheck_per_repo=2)
    for n in (3, 4, 5, 6):
        seed(store, JELLY, n, FLAKY_LABEL)
    producer = FlakyProducer(cfg, store)
    first = producer.tick(ranked(store, cfg), now=NOW)
    assert [store.background_job(job)["target"] for job in first] == ["3", "4"]
    for job_id in first:
        store.finish_background_job(job_id, state="done", outcome="flaky cause=timing run=R",
                                    now=NOW)
    second = producer.tick(ranked(store, cfg), now=NOW)
    assert [store.background_job(job)["target"] for job in second] == ["5", "6"], (
        "the next batch, not the same two again"
    )
    assert producer.tick(ranked(store, cfg), now=NOW) == [], "and nothing once all four have one"


def test_a_cancelled_attempt_does_not_earn_a_second_job(store):
    cfg = dataclasses.replace(config(), precheck_per_repo=10)
    seed(store, JELLY, 3, FLAKY_LABEL)
    producer = FlakyProducer(cfg, store)
    (job_id,) = producer.tick(ranked(store, cfg), now=NOW)
    store.requeue_background_job(job_id, now=NOW)
    assert producer.tick(ranked(store, cfg), now=NOW) == [], (
        "the cancelled attempt is the same row, back on the queue"
    )
    assert len(flakies(store)) == 1


def test_an_issue_a_coder_loop_holds_gets_no_job(store):
    cfg = dataclasses.replace(config(), precheck_per_repo=10)
    seed(store, JELLY, 3, FLAKY_LABEL)
    seed(store, JELLY, 4, FLAKY_LABEL)
    created = FlakyProducer(cfg, store).tick(
        ranked(store, cfg), running={(JELLY, 3)}, now=NOW
    )
    assert [store.background_job(job)["target"] for job in created] == ["4"]


def test_a_queued_job_whose_issue_left_the_queue_is_skipped(store):
    cfg = dataclasses.replace(config(), precheck_per_repo=10)
    seed(store, JELLY, 3, FLAKY_LABEL)
    producer = FlakyProducer(cfg, store)
    (job_id,) = producer.tick(ranked(store, cfg), now=NOW)
    store.forget_issue(JELLY, 3)  # a coder loop took it, or a human relabelled it
    producer.tick(ranked(store, cfg), now=NOW)
    row = store.background_job(job_id)
    assert (row["state"], row["outcome"]) == ("skipped", f"the issue left the queue before its {KIND} ran")


def test_the_switch_is_read_at_the_tick_so_a_switched_off_lane_makes_no_call(store):
    cfg = dataclasses.replace(config(), precheck_per_repo=10, background=False)
    seed(store, JELLY, 3, FLAKY_LABEL)
    producer = FlakyProducer(cfg, store)
    assert producer.enabled is False
    assert producer.tick(ranked(store, cfg), now=NOW) == []
    assert flakies(store) == []


def test_the_kind_absent_from_bg_kinds_does_nothing(store):
    cfg = config(SCHEDULER_BG_KINDS="issue-precheck,context-pack")
    seed(store, JELLY, 3, FLAKY_LABEL)
    producer = FlakyProducer(cfg, store)
    assert producer.enabled is False
    assert producer.tick(ranked(store, cfg), now=NOW) == []
    assert flakies(store) == []


def test_a_flaky_issue_in_a_repo_with_no_queue_item_gets_no_job(store):
    """The cache is the queue: an issue the inventory dropped is not a candidate, so a stale
    flaky issue outside the queue cannot keep a job queued forever."""
    cfg = dataclasses.replace(config(), precheck_per_repo=10)
    seed(store, JELLY, 3, FLAKY_LABEL)
    seeded = ranked(store, cfg)
    store.forget_issue(JELLY, 3)
    assert FlakyProducer(cfg, store).tick(seeded, now=NOW) == []
    assert flakies(store) == []


# --- the lane's read-back ------------------------------------------------------------


def test_the_lane_records_the_cause_from_the_marker(store):
    """A finished run's outcome is read from `job["run_id"]`'s own marker on the issue, never
    from another run (the calibration's bug, 2026-10-31)."""
    seed(store, JELLY, 3, FLAKY_LABEL)
    store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="3", inputs={"issue_number": 3},
        priority=BACKGROUND_JOB_PRIORITY[KIND], typical_box_minutes=FLAKY_TYPICAL_MINUTES,
        now=NOW,
    )
    (job,) = store.queued_background_jobs()
    store.start_background_job(int(job["job_id"]), run_id="R1", box="coders-a", now=NOW)
    seen: list[tuple[str, int]] = []

    def reader(repo: str, number: int):
        seen.append((repo, number))
        return FlakyMarker("9354", "timing", "2026-11-01T10:00:00Z", "R1")

    lane_ = BackgroundLane(
        config(), store, LeaseStore(store),
        FakeFabro(runs={"R1": {"lifecycle": {"status": {"kind": "succeeded"}}}}),
        health=FakeHealth(), flaky_reader=reader, clock=lambda: NOW,
    )
    lane_.sync(NOW)
    row = store.background_job(int(job["job_id"]))
    assert row["outcome"] == "flaky cause=timing run=R1"
    assert seen == [(JELLY, 3)]
