"""The precheck calibration's scheduler half (pre-review-and-calibration, Part A).

The producer keeps a few `precheck-calibration` jobs queued, one per issue, from the runs the
store already labels: the issue's first finished run, newest first. The lane records a
succeeded run's verdict from the run's own `record` event, because the package posts nothing.
The comparison puts each verdict next to how its run ended. Nothing here reads GitHub for a
verdict, and nothing here can be mistaken for the live precheck's data.
"""

from __future__ import annotations

import dataclasses

import pytest

from fabro_scheduler.calibration import (
    AGREES,
    CALIBRATION_TYPICAL_MINUTES,
    KIND,
    CalibrationProducer,
    Recorded,
    outcome_class,
    outcome_for,
    parse_outcome,
    read_outcome,
    read_record,
    report,
)
from fabro_scheduler.lease import LeaseStore
from fabro_scheduler.pack import PackProducer
from fabro_scheduler.precheck import KIND as PRECHECK_KIND, skip_departed
from fabro_scheduler.queue import build_queue
from fabro_scheduler.store import RunOutcome, Store

from test_background import IDLE, NOW, FakeFabro, FakeHealth, lane
from test_precheck import JELLY, config, jobs, seed

DAY = "2026-10-01T12:00:00+00:00"


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def labelled(
    store: Store,
    run_id: str,
    number: int,
    *,
    dispatched: str,
    terminal_node: str | None,
    status: str = "ok",
    merged: int | None = None,
    pr_number: int | None = None,
    refute: str | None = None,
    target_kind: str = "issue",
) -> None:
    """One archived run and its summary, written as the collector would leave them."""
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO run_history (run_id, coder_pool, repo, issue_number, dispatched_at,"
            " finished_at, kind, pr_lookup, merged, pr_number, target_kind)"
            " VALUES (?, 'coders-a', ?, ?, ?, ?, 'succeeded', 'none', ?, ?, ?)",
            (run_id, JELLY, number, dispatched, dispatched, merged, pr_number, target_kind),
        )
        store._conn.execute(
            "INSERT INTO run_summary (run_id, status, summarized_at, terminal_node, refute)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, status, dispatched, terminal_node, refute),
        )


def calibration_jobs(store: Store) -> list[tuple[str, str, str, str]]:
    return [row for row in jobs(store) if row[0] == KIND]


def finished(store: Store, run_id: str, outcome: str, *, at=NOW, number: int = 5) -> int:
    """A done calibration job for `run_id`, as the lane would leave it."""
    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target=run_id, inputs={"issue_number": number, "at": "x"},
        priority=4, typical_box_minutes=CALIBRATION_TYPICAL_MINUTES, now=at,
    )
    store.finish_background_job(job_id, state="done", outcome=outcome, now=at)
    return job_id


# --- the run's own verdict ------------------------------------------------------------


def events(node: str, updates: dict | None, *, name: str = "stage.completed") -> dict:
    return {"event": name, "node_id": node, "properties": {"context_updates": updates}}


def test_the_verdict_comes_from_the_record_node(ck=None):
    stream = [
        events("claim", {"calibration_go": True}),
        events("gate", {"verdict": "ready"}),
        events("check", None, name="stage.started"),
        events("record", {
            "verdict": "already_done",
            "raw_verdict": "unclear",
            "rule": "it cited no evidence",
            "main_sha": "0123456789abcdef0123456789abcdef01234567",
            "body_changed": "true",
        }),
    ]
    assert read_record(stream) == Recorded(
        verdict="already_done", raw="unclear", rule="it cited no evidence",
        main="0123456789abcdef0123456789abcdef01234567", body_changed="true",
    )
    assert read_outcome(stream) == "verdict=already_done raw=unclear main=0123456789ab changed=1"


def test_a_run_with_no_record_earns_no_verdict():
    assert read_outcome([events("gate", {"verdict": "ready"})]) == "succeeded (no verdict)"
    assert read_outcome([]) == "succeeded (no verdict)"


def test_the_outcome_round_trips():
    assert parse_outcome("verdict=ready raw=ready main=abcdef012345 changed=0") == {
        "verdict": "ready", "raw": "ready", "main": "abcdef012345", "changed": "0",
    }
    for other in ("succeeded", "succeeded (no verdict)", "", None, "verdict=ready"):
        assert parse_outcome(other) is None, other


def test_body_changed_reads_false_for_anything_but_true_or_unknown():
    base = Recorded(verdict="ready", raw="ready", main="abc", body_changed="false")
    assert outcome_for(base).endswith("changed=0")
    assert outcome_for(dataclasses.replace(base, body_changed="")).endswith("changed=0")
    assert outcome_for(dataclasses.replace(base, body_changed="TRUE")).endswith("changed=1")
    # `unknown`: edited after `at`, but no entry reaches back, so the current text was read.
    unknown = outcome_for(dataclasses.replace(base, body_changed="unknown"))
    assert unknown.endswith("changed=?")
    assert parse_outcome(unknown)["changed"] == "?"


# --- the producer ---------------------------------------------------------------------


def test_the_producer_takes_the_first_run_of_each_issue_newest_first(store):
    seed(store, JELLY, 5)
    # Issue 5 ran twice: the first run is the one the calibration measures.
    labelled(store, "R5old", 5, dispatched="2026-10-01T09:00:00+00:00", terminal_node="close_noop")
    labelled(store, "R5new", 5, dispatched="2026-10-09T09:00:00+00:00", terminal_node="close_noop")
    labelled(store, "R6", 6, dispatched="2026-10-08T09:00:00+00:00", terminal_node="mark_stuck")
    # Not calibrable: no summary (the events could not be read), a launch failure, a cancel,
    # and a run dispatched before the window.
    labelled(store, "R7", 7, dispatched="2026-10-07T09:00:00+00:00", terminal_node=None)
    labelled(store, "R8", 8, dispatched="2026-10-06T09:00:00+00:00",
             terminal_node="launch_failed")
    labelled(store, "R9", 9, dispatched="2026-10-05T09:00:00+00:00", terminal_node="cancelled")
    labelled(store, "R10", 10, dispatched="2026-01-01T09:00:00+00:00",
             terminal_node="close_noop")
    # A PR run is not an issue run.
    labelled(store, "R11", 11, dispatched="2026-10-04T09:00:00+00:00", terminal_node="close_noop",
             target_kind="pr")

    created = CalibrationProducer(config(), store).tick(NOW)

    assert len(created) == 2
    rows = [(row["repo"], row["target"], row["state"]) for row in
            store._conn.execute(
                "SELECT repo, target, state FROM background_jobs WHERE kind = ? ORDER BY job_id",
                (KIND,),
            ).fetchall()]
    assert rows == [(JELLY, "R6", "queued"), (JELLY, "R5old", "queued")], (
        "newest first, and issue 5's first run rather than its newer one"
    )
    job = store.background_job(created[0])
    assert (job["priority"], job["typical_box_minutes"]) == (4, CALIBRATION_TYPICAL_MINUTES)
    assert job["inputs_json"] == (
        '{"at": "2026-10-08T09:00:00+00:00", "issue_number": 6}'
    )


def test_the_producer_keeps_only_its_cap_queued(store):
    for n in range(20, 26):
        seed(store, JELLY, n)
        labelled(store, f"R{n}", n, dispatched=f"2026-10-0{n - 15}T09:00:00+00:00",
                 terminal_node="close_noop")
    producer = CalibrationProducer(
        dataclasses.replace(config(), calibration_queued=2), store
    )
    assert len(producer.tick(NOW)) == 2
    assert producer.tick(NOW) == [], "the cap is already reached"
    assert len(calibration_jobs(store)) == 2


def test_the_producer_moves_on_once_its_batch_has_finished(store):
    """The dedupe is in the query: a finished batch must not come back and stall the producer."""
    for n in range(20, 25):
        seed(store, JELLY, n)
        labelled(store, f"R{n}", n, dispatched=f"2026-10-0{n - 15}T09:00:00+00:00",
                 terminal_node="close_noop")
    producer = CalibrationProducer(
        dataclasses.replace(config(), calibration_queued=2), store
    )
    first = producer.tick(NOW)
    assert [store.background_job(j)["target"] for j in first] == ["R24", "R23"]
    for job_id in first:
        store.finish_background_job(job_id, state="done", outcome="succeeded", now=NOW)
    second = producer.tick(NOW)
    assert [store.background_job(j)["target"] for j in second] == ["R22", "R21"], (
        "the next two runs, not nothing"
    )
    store.finish_background_job(second[0], state="failed", outcome="no", now=NOW)
    assert [store.background_job(j)["target"] for j in producer.tick(NOW)] == ["R20"]
    assert producer.tick(NOW) == [], "every run has its one job"


def test_an_issue_whose_first_run_was_calibrated_never_offers_its_second(store):
    seed(store, JELLY, 5)
    labelled(store, "R5old", 5, dispatched="2026-10-01T09:00:00+00:00", terminal_node="close_noop")
    labelled(store, "R5new", 5, dispatched="2026-10-09T09:00:00+00:00", terminal_node="close_noop")
    producer = CalibrationProducer(config(), store)
    first = producer.tick(NOW)
    store.finish_background_job(first[0], state="done", outcome="succeeded", now=NOW)
    assert producer.tick(NOW) == []
    assert [row[2] for row in calibration_jobs(store)] == ["R5old"]


def test_the_producer_never_queues_the_same_run_twice(store):
    seed(store, JELLY, 5)
    labelled(store, "R5", 5, dispatched="2026-10-01T09:00:00+00:00", terminal_node="close_noop")
    producer = CalibrationProducer(config(), store)
    first = producer.tick(NOW)
    store.finish_background_job(first[0], state="failed", outcome="no", now=NOW)
    assert producer.tick(NOW) == [], "the run already had its one job"
    assert len(calibration_jobs(store)) == 1


def test_the_kind_switch_turns_the_producer_off(store):
    seed(store, JELLY, 5)
    labelled(store, "R5", 5, dispatched="2026-10-01T09:00:00+00:00", terminal_node="close_noop")
    for name, cfg in (
        ("lane off", dataclasses.replace(config(), background=False)),
        ("kind off", config(SCHEDULER_BG_KINDS="issue-precheck")),
    ):
        producer = CalibrationProducer(cfg, store)
        assert producer.enabled is False, name
        assert producer.tick(NOW) == [], name
    assert calibration_jobs(store) == []


def test_the_since_date_limits_the_window(store):
    seed(store, JELLY, 5)
    labelled(store, "R5", 5, dispatched="2026-09-26T09:00:00+00:00", terminal_node="close_noop")
    labelled(store, "R6", 6, dispatched="2026-09-24T09:00:00+00:00", terminal_node="close_noop")
    CalibrationProducer(config(), store).tick(NOW)
    assert [row[2] for row in calibration_jobs(store)] == ["R5"], (
        "the run dispatched inside the window, and only that one"
    )


# --- the lane -------------------------------------------------------------------------


def test_a_succeeded_calibration_run_records_the_verdict_from_the_run(store):
    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="C1", inputs={"issue_number": 5, "at": DAY},
        priority=4, typical_box_minutes=CALIBRATION_TYPICAL_MINUTES, now=NOW,
    )
    assert store.start_background_job(job_id, run_id="CALRUN", box="coders-a", now=NOW)
    fabro = FakeFabro(runs={"CALRUN": {"lifecycle": {"status": {"kind": "succeeded"}}}})
    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth())
    read: list[str] = []
    lane_._read_calibration = lambda run_id: (
        read.append(run_id) or "verdict=ready raw=ready main=0123456789ab changed=0"
    )
    lane_._read_precheck = lambda repo, number: pytest.fail("a calibration is not a precheck")
    ends = lane_.sync(NOW)
    assert [(end.state, end.outcome) for end in ends] == [
        ("done", "verdict=ready raw=ready main=0123456789ab changed=0")
    ]
    assert store.background_job(job_id)["outcome"] == (
        "verdict=ready raw=ready main=0123456789ab changed=0"
    )
    assert read == ["CALRUN"], "the calibration's own run, not the run it calibrates (C1)"


def test_a_reader_that_cannot_read_leaves_plain_succeeded(store):
    from fabro_scheduler.fabro import FabroError

    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="C1", inputs={"issue_number": 5, "at": DAY},
        priority=4, typical_box_minutes=CALIBRATION_TYPICAL_MINUTES, now=NOW,
    )
    store.start_background_job(job_id, run_id="CALRUN", box="coders-a", now=NOW)
    fabro = FakeFabro(runs={"CALRUN": {"lifecycle": {"status": {"kind": "succeeded"}}}})
    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth())
    lane_._read_calibration = lambda run_id: (_ for _ in ()).throw(FabroError("404"))
    assert [(end.state, end.outcome) for end in lane_.sync(NOW)] == [("done", "succeeded")]


def test_a_calibration_job_starts_its_own_package(store):
    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="C1",
        inputs={"issue_number": 5, "at": "2026-10-01T12:00:00+00:00"},
        priority=4, typical_box_minutes=CALIBRATION_TYPICAL_MINUTES, now=NOW,
    )
    fabro = FakeFabro()
    lane_ = lane(config(), store, LeaseStore(store), fabro, FakeHealth({"coders-a": IDLE}))
    assert [start.job_id for start in lane_.fill(NOW)] == [job_id]
    sent = fabro.dispatched[0]
    assert sent["workflow"] == "precheck-calibration"
    assert sent["inputs"] == {"issue_number": 5, "at": "2026-10-01T12:00:00+00:00"}


# --- the comparison -------------------------------------------------------------------


@pytest.mark.parametrize("merged,terminal,pr,expected", [
    (1, "close_noop", 12, "merged"),  # a merged run is merged, whatever node it ended on
    (0, "close_noop", None, "noop"),
    (0, "return_to_triage", None, "returned"),
    (0, "mark_stuck", None, "stuck"),
    (0, "mark_needs_human", 12, "stuck"),
    (0, "human_rescue", None, "stuck"),
    (0, "demote_lane", None, "demoted"),
    (0, "open_pr", 12, "held"),
    (0, "report_blocked", 12, "held"),
    (0, "integrate", None, "failed"),  # no PR and no ending: the run died there
    (None, None, None, "other"),
])
def test_the_outcome_classes(merged, terminal, pr, expected):
    assert outcome_class(merged=merged, terminal_node=terminal, pr_number=pr,
                         pr_reason="unspecified") == expected


def test_a_held_pr_says_when_the_hold_is_not_about_the_issue():
    arch = ("This PR implements an Architecture issue (label architecture). ADR 0012 requires "
            "a human to merge it.")
    assert outcome_class(merged=0, terminal_node="open_pr", pr_number=12,
                         pr_reason=arch) == "held_architecture"
    assert outcome_class(merged=0, terminal_node="open_pr", pr_number=12,
                         pr_reason=None) == "held_unread"
    assert outcome_class(merged=0, terminal_node="open_pr", pr_number=12,
                         pr_reason="") == "held"


def test_the_report_puts_each_verdict_next_to_its_ending(store):
    seed(store, JELLY, 5)
    # four calibrated runs: two said already_done and ended noop, one said ready and got
    # stuck, one said unverifiable and came back as a question.
    for run_id, number, terminal, merged, pr, verdict in (
        ("A1", 5, "close_noop", None, None, "already_done"),
        ("A2", 6, "close_noop", None, None, "already_done"),
        ("A3", 7, "mark_stuck", None, None, "ready"),
        ("A4", 8, "return_to_triage", None, None, "unverifiable"),
    ):
        labelled(store, run_id, number, dispatched=DAY, terminal_node=terminal, merged=merged,
                 pr_number=pr,
                 refute='[{"verdict": "pass", "not_met": false, "unverifiable_only": true, '
                        '"ci_credited": null}]' if run_id == "A4" else "[]")
        finished(store, run_id, f"verdict={verdict} raw={verdict} main=abcdef012345 changed=0",
                 number=number)
    # A job that ended without a verdict is listed and left out of the counts.
    finished(store, "A5", "succeeded (no verdict)", number=9)

    doc = report(store.calibration_rows())

    assert doc["counts"] == {"jobs": 5, "with_verdict": 4}
    assert doc["matrix"] == {
        "already_done": {"noop": 2},
        "ready": {"stuck": 1},
        "unverifiable": {"returned": 1},
    }
    by_verdict = {row["verdict"]: row for row in doc["verdicts"]}
    assert by_verdict["already_done"]["agrees"] == 2
    assert by_verdict["already_done"]["comparable"] == 2
    assert "agrees" not in by_verdict["ready"], "a ready verdict predicts nothing"
    assert by_verdict["unverifiable"]["refuter_unverifiable"] == 1
    assert doc["ready_unproductive"] == 1
    assert [row["outcome_class"] for row in doc["rows"]] == [
        "other", "returned", "stuck", "noop", "noop",
    ]
    no_verdict = doc["rows"][0]
    assert (no_verdict["run_id"], no_verdict["verdict"]) == ("A5", None)


def test_a_blocked_verdict_reports_its_endings_without_claiming_agreement(store):
    # `blocked` says the issue should not have been dispatched at all, so any ending is
    # consistent with holding it: the report shows the mix and claims no agreement.
    assert "blocked" not in AGREES
    labelled(store, "B1", 5, dispatched=DAY, terminal_node="close_noop")
    finished(store, "B1", "verdict=blocked raw=blocked main=abcdef012345 changed=0")
    row = report(store.calibration_rows())["verdicts"][0]
    assert row == {"verdict": "blocked", "runs": 1, "by_class": {"noop": 1}}


def test_the_report_is_empty_with_no_jobs(store):
    assert report(store.calibration_rows()) == {
        "rows": [], "matrix": {}, "verdicts": [], "ready_unproductive": 0,
        "counts": {"jobs": 0, "with_verdict": 0},
    }


# --- live and calibration data stay apart ---------------------------------------------


def test_a_calibration_verdict_never_becomes_the_verdict_a_run_was_dispatched_with(store):
    finished(store, "CALRUN", "verdict=already_done raw=already_done main=abcdef012345 changed=0")
    assert store.acquire_lease(
        coder_pool="coders-a", repo=JELLY, issue_number=5, run_id="R5",
        dispatched_at=NOW,
    )
    store.archive_and_release_lease("R5", RunOutcome(kind="succeeded"), at=NOW)
    row = store._conn.execute(
        "SELECT precheck_verdict, had_pack FROM run_history WHERE run_id = 'R5'"
    ).fetchone()
    assert tuple(row) == (None, 0), (
        "a calibration job's verdict is not the precheck's, so it must not land here"
    )


def test_a_calibration_verdict_never_makes_the_pack_producer_queue(store):
    seed(store, JELLY, 5)
    finished(store, "CALRUN", "verdict=ready raw=ready main=abcdef012345 changed=0")
    cfg = config()
    ranked = build_queue(cfg.schedulable_repos(), store, NOW, cfg.starvation_ceiling)
    assert PackProducer(cfg, store).tick(ranked, set(), now=NOW) == []
    assert [row for row in jobs(store) if row[0] == "context-pack"] == []


def test_skip_departed_leaves_calibration_jobs_alone(store):
    # A calibration job is about an issue whose run has FINISHED: it is not in the queue by
    # design, so the precheck's and pack's "the issue left the queue" sweep must never see it.
    # `skip_departed` only ever runs for those two kinds, and it filters on its own kind.
    seed(store, JELLY, 5)
    pre = store.enqueue_background_job(
        kind=PRECHECK_KIND, repo=JELLY, target="6", inputs={"issue_number": 6},
        priority=2, typical_box_minutes=3, now=NOW,
    )
    queued_cal = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target="CALRUN", inputs={"issue_number": 7, "at": DAY},
        priority=4, typical_box_minutes=3, now=NOW,
    )
    for kind in (PRECHECK_KIND, "context-pack"):
        skip_departed(store, kind, set(), NOW)
    assert store.background_job(pre)["state"] == "skipped"
    assert store.background_job(queued_cal)["state"] == "queued"


# --- the HTTP surface -----------------------------------------------------------------


def test_the_calibration_endpoint_renders_the_report(tmp_path):
    from fastapi.testclient import TestClient

    from fabro_scheduler.app import build_app

    store = Store(tmp_path / "scheduler.db")
    try:
        cfg = config()
        finished(store, "A1", "verdict=already_done raw=already_done main=abcdef012345 changed=1",
                 number=5)
        from fabro_scheduler.calibration import CALIBRATION_TYPICAL_MINUTES as T

        assert T == 3
        client = TestClient(build_app(cfg, store, fabro_client=FakeFabro()))
        body = client.get("/api/background/calibration").json()
        assert body["counts"] == {"jobs": 1, "with_verdict": 1}
        assert body["rows"][0]["verdict"] == "already_done"
        assert body["rows"][0]["body_changed"] is True
        # The job row was never joined to a run, so its class is `other`.
        assert body["rows"][0]["outcome_class"] == "other"
    finally:
        store.close()
