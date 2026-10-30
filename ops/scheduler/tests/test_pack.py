"""The context pack's scheduler half (more-local-inference 08).

The producer queues one `context-pack` job per issue for the next issues in dispatch order,
only once the issue's precheck said `ready`, and never for an issue a coder loop holds. The
lane records a succeeded run's `pack run=<id>` outcome from the issue's marker. When an issue
run is archived, its `run_history` row records whether a pack and which precheck verdict
existed when it was dispatched (D21).
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from fabro_scheduler.github import Issue
from fabro_scheduler.lease import LeaseStore
from fabro_scheduler.pack import (
    KIND,
    PACK_TYPICAL_MINUTES,
    PackMarker,
    PackProducer,
    newest_marker,
    outcome_for,
    parse_marker,
)
from fabro_scheduler.precheck import KIND as PRECHECK_KIND
from fabro_scheduler.queue import build_queue
from fabro_scheduler.store import RunOutcome, Store

from test_background import NOW, FakeFabro, FakeHealth, IDLE, lane, running
from test_precheck import JELLY, config, jobs, seed


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def ranked(store: Store, cfg):
    return build_queue(cfg.schedulable_repos(), store, NOW, cfg.starvation_ceiling)


def checked(store: Store, number: int, outcome: str, *, at=NOW) -> int:
    """A finished precheck job for JELLY#number with `outcome`."""
    job_id = store.enqueue_background_job(
        kind=PRECHECK_KIND, repo=JELLY, target=str(number), inputs={"issue_number": number},
        priority=2, typical_box_minutes=10, now=at,
    )
    store.finish_background_job(job_id, state="done", outcome=outcome, now=at)
    return job_id


def packs(store: Store) -> list[tuple[str, str, str, str]]:
    return [row for row in jobs(store) if row[0] == KIND]


# --- the producer -------------------------------------------------------------------


def test_the_producer_maps_only_what_the_precheck_passed_once_each(store):
    cfg = config()
    for n in (3, 4, 5, 6):
        seed(store, JELLY, n)
    checked(store, 3, "verdict=ready")
    checked(store, 4, "verdict=already_done")
    checked(store, 5, "succeeded")
    producer = PackProducer(cfg, store)
    created = producer.tick(ranked(store, cfg), now=NOW)
    assert packs(store) == [(KIND, JELLY, "3", "queued")], "only #3 said ready; #6 has no precheck"
    row = store.background_job(created[0])
    assert (row["priority"], row["typical_box_minutes"], row["inputs_json"]) == (
        3,
        PACK_TYPICAL_MINUTES,
        '{"issue_number": 3}',
    )
    assert producer.tick(ranked(store, cfg), now=NOW) == [], "one job per issue"


def test_the_producer_never_maps_an_issue_a_coder_loop_holds(store):
    cfg = dataclasses.replace(config(), precheck_top_n=1)
    for n in (3, 4):
        seed(store, JELLY, n)
        checked(store, n, "verdict=ready")
    PackProducer(cfg, store).tick(ranked(store, cfg), running={(JELLY, 3)}, now=NOW)
    assert packs(store) == [(KIND, JELLY, "4", "queued")], "the held issue does not use a slot"


def test_a_pack_whose_issue_left_the_queue_is_skipped(store):
    cfg = config()
    seed(store, JELLY, 3)
    checked(store, 3, "verdict=ready")
    producer = PackProducer(cfg, store)
    producer.tick(ranked(store, cfg), now=NOW)
    producer.tick(ranked(store, cfg), running={(JELLY, 3)}, now=NOW + timedelta(seconds=5))
    assert packs(store) == [(KIND, JELLY, "3", "skipped")]


def test_the_producer_is_off_with_its_kind_off(store):
    seed(store, JELLY, 3)
    checked(store, 3, "verdict=ready")
    cfg = config(SCHEDULER_BG_KINDS="issue-precheck")
    producer = PackProducer(cfg, store)
    assert producer.enabled is False
    assert producer.tick(ranked(store, cfg), now=NOW) == []
    assert packs(store) == []


# --- the marker and the outcome -------------------------------------------------------


MARKER_LINE = "<!-- fabro:context-pack body=abcdef012345 main=fff at=2026-10-30T20:00:00Z run=01RUN -->"


def test_the_marker_parses_from_the_first_line_only():
    marker = parse_marker(MARKER_LINE + "\n\n**Context pack.**")
    assert marker == PackMarker("abcdef012345", "fff", "2026-10-30T20:00:00Z", "01RUN")
    assert parse_marker("hello\n" + MARKER_LINE) is None
    assert parse_marker("<!-- fabro:precheck verdict=ready body=a main=b at=c run=d -->") is None
    older = MARKER_LINE.replace("01RUN", "00OLD")
    assert newest_marker([{"body": older}, {"body": "x"}, {"body": MARKER_LINE}]).run == "01RUN"


def test_the_outcome_names_the_run_only_for_its_own_marker():
    marker = parse_marker(MARKER_LINE)
    assert outcome_for(marker, "01RUN") == "pack run=01RUN"
    assert outcome_for(marker, "02OTHER") == "succeeded (no new pack)"
    assert outcome_for(None, "01RUN") == "succeeded (no marker)"
    assert outcome_for(marker, "02RETRY", "2026-10-30T19:00:00+00:00") == "pack run=01RUN", (
        "an earlier attempt's pack, posted after the job was queued, is the job's"
    )


# --- the lane -----------------------------------------------------------------------


def test_a_succeeded_pack_records_its_run(store):
    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="3", inputs={"issue_number": 3}, priority=3,
        typical_box_minutes=8, now=NOW,
    )
    running(store, job_id, run_id="01RUN")
    fabro = FakeFabro(runs={"01RUN": {"lifecycle": {"status": {"kind": "succeeded"}}}})
    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth())
    lane_._read_pack = lambda repo, number: parse_marker(MARKER_LINE)
    lane_._read_precheck = lambda repo, number: pytest.fail("a pack is not a precheck")
    ends = lane_.sync(NOW)
    assert [(end.state, end.outcome) for end in ends] == [("done", "pack run=01RUN")]


def test_the_lane_starts_a_pack_on_the_context_pack_package(store):
    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="3", inputs={"issue_number": 3}, priority=3,
        typical_box_minutes=8, now=NOW,
    )
    fabro = FakeFabro()
    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth({"coders-a": IDLE}))
    assert [start.job_id for start in lane_.fill(NOW)] == [job_id]
    sent = fabro.dispatched[0]
    assert sent["workflow"] == "context-pack"
    assert sent["inputs"] == {"issue_number": 3}
    assert sent["labels"] == {"lane": "background", "box": "coders-a", "job": KIND}


# --- the measurement record (D21) -----------------------------------------------------


def archived(store: Store, number: int, *, dispatched=NOW, target_kind="issue"):
    assert store.acquire_lease(
        coder_pool="coders-a", repo=JELLY, issue_number=number, run_id=f"R{number}",
        dispatched_at=dispatched, target_kind=target_kind,
    )
    store.archive_and_release_lease(f"R{number}", RunOutcome(kind="succeeded"), at=NOW)
    return store._conn.execute(
        "SELECT had_pack, pack_run, precheck_verdict FROM run_history WHERE run_id = ?",
        (f"R{number}",),
    ).fetchone()


def finished_pack(store: Store, number: int, outcome: str, *, at) -> None:
    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target=str(number), inputs={"issue_number": number},
        priority=3, typical_box_minutes=8, now=at,
    )
    store.finish_background_job(job_id, state="done", outcome=outcome, now=at)


def test_a_run_records_the_pack_and_verdict_it_was_dispatched_with(store):
    earlier = NOW - timedelta(hours=1)
    checked(store, 3, "verdict=ready", at=earlier)
    finished_pack(store, 3, "pack run=01PACK", at=earlier)
    assert tuple(archived(store, 3)) == (1, "01PACK", "ready")


def test_a_run_with_no_pack_records_zero(store):
    earlier = NOW - timedelta(hours=1)
    checked(store, 3, "verdict=unclear", at=earlier)
    finished_pack(store, 3, "succeeded (no new pack)", at=earlier)
    assert tuple(archived(store, 3)) == (0, None, "unclear")
    assert tuple(archived(store, 4)) == (0, None, None)


def test_a_pack_finished_after_dispatch_does_not_count(store):
    dispatched = NOW - timedelta(hours=1)
    finished_pack(store, 3, "pack run=LATE", at=dispatched + timedelta(minutes=5))
    assert tuple(archived(store, 3, dispatched=dispatched)) == (0, None, None)


def test_a_pr_run_records_nothing(store):
    finished_pack(store, 3, "pack run=01PACK", at=NOW - timedelta(hours=1))
    assert tuple(archived(store, 3, target_kind="pr")) == (None, None, None)
