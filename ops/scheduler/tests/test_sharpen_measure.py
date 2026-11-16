"""Measuring the dark loop (issue-sharpening 06): the measure section, the scorecard row and the
retro's `sharpen:<state>` classes, each on seeded store rows."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from fabro_scheduler import measure, retro, scorecard
from fabro_scheduler.github import Issue
from fabro_scheduler.prfacts import PrFacts
from fabro_scheduler.store import RunOutcome, Store

JELLY = "andrewthetechie/jelly-swipe"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
T0 = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)  # a Monday


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def issue_run(store: Store, run_id: str, number: int, at: datetime, *, merged: bool = False,
              pr: int | None = None) -> None:
    store.acquire_lease(coder_pool="coders-a", repo=JELLY, issue_number=number, run_id=run_id,
                        dispatched_at=at, workflow="ralph")
    store.archive_and_release_lease(run_id, RunOutcome(
        kind="succeeded", finished_at=at + timedelta(hours=1), merged=merged or None,
        pr_lookup="found" if pr else "none", pr_number=pr))


def sharpen_run(store: Store, run_id: str, number: int, at: datetime, *, minutes: float = 10) -> None:
    store.acquire_lease(coder_pool="coders-b", repo=JELLY, issue_number=number, run_id=run_id,
                        dispatched_at=at, workflow="issue-sharpen", target_kind="sharpen")
    store.archive_and_release_lease(run_id, RunOutcome(
        kind="succeeded", finished_at=at + timedelta(minutes=minutes)))


def row(store: Store, number: int, state: str, *, run_id: str | None, created: datetime,
        finished: datetime, rnd: int | None = 1, minutes: float | None = 10.0,
        transformations: str | None = None, recheck: str | None = None) -> int:
    rid = store.sharpen_enqueue(JELLY, number, "aaaaaaaaaaaa", created)
    store.sharpen_claim(rid, run_id or f"X{number}", created)
    store.sharpen_finish(rid, state=state, now=finished, applied_hash="bbbbbbbbbbbb" if state == "applied" else None,
                         recheck=recheck, round_=rnd, transformations=transformations, box_minutes=minutes)
    if run_id is None:
        with store._lock, store._conn:
            store._conn.execute("UPDATE sharpen SET run_id = NULL WHERE id = ?", (rid,))
    return rid


def stage(store: Store, run_id: str, node: str, *, visits: int, wall_ms: int) -> None:
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO run_stage_stats (run_id, node_id, handler, visits, wall_ms, box_ms, timeouts, failures)"
            " VALUES (?, ?, 'agent', ?, ?, 0, 0, '{}')", (run_id, node, visits, wall_ms))


def calibrated(store: Store, run_id: str, number: int, verdict: str, merged: bool) -> None:
    issue_run(store, run_id, number, NOW - timedelta(days=3), merged=merged)
    job = store.enqueue_background_job(kind="precheck-calibration", repo=JELLY, target=run_id, inputs={},
                                       priority=3, typical_box_minutes=3, now=NOW - timedelta(days=2))
    store.start_background_job(job, run_id=f"C{job}", box="coders-a", now=NOW - timedelta(days=2))
    store.finish_background_job(job, state="done", now=NOW - timedelta(days=2),
                                outcome=f"verdict={verdict} raw={verdict} main=abcdef012345 changed=0")


def document(store: Store, days: int = 14) -> dict:
    gathered = store.measure_rows(since=NOW - timedelta(days=days),
                                  baseline_since=NOW - timedelta(days=days + measure.BASELINE_DAYS))
    return measure.sharpening(measure.Rows(**gathered), days=days, now=NOW)


def seeded(store: Store) -> None:
    """Three issues sharpened in the window: #5 applied, #6 capped, #7 failed; #8 still queued."""
    day = NOW - timedelta(days=2)
    sharpen_run(store, "S5", 5, day)
    row(store, 5, "applied", run_id="S5", created=day - timedelta(hours=2), finished=day + timedelta(minutes=10),
        rnd=2, minutes=20.0, transformations="to_post_deploy=1,command_to_ci=2", recheck="ready")
    sharpen_run(store, "S6", 6, day)
    row(store, 6, "capped", run_id="S6", created=day - timedelta(hours=1), finished=day + timedelta(minutes=30),
        rnd=3, minutes=40.0, transformations="pr_text_to_code=1")
    sharpen_run(store, "S7", 7, day)
    row(store, 7, "failed", run_id="S7", created=day, finished=day + timedelta(minutes=60), rnd=None, minutes=60.0)
    store.sharpen_enqueue(JELLY, 8, "aaaaaaaaaaaa", NOW)
    store.upsert_issue(Issue(JELLY, 8, "issue 8", frozenset({"agent", measure.SHARPEN_HOLD_LABEL}), NOW, "aaaaaaaaaaaa"))
    stage(store, "S5", "draft", visits=2, wall_ms=240_000)
    stage(store, "S6", "draft", visits=3, wall_ms=540_000)
    stage(store, "S5", "comprehend", visits=2, wall_ms=120_000)
    # Issue runs: #5 ran after its row ended (one merged, one not) and once before it.
    issue_run(store, "I5a", 5, NOW - timedelta(days=9))
    issue_run(store, "I5b", 5, NOW - timedelta(days=1), merged=True)
    issue_run(store, "I5c", 5, NOW - timedelta(hours=5))
    issue_run(store, "I6", 6, NOW - timedelta(days=1), merged=True)
    issue_run(store, "I7", 7, NOW - timedelta(days=1))
    issue_run(store, "I9", 9, NOW - timedelta(days=1), merged=True)  # no sharpening: not counted


def test_the_numbers_of_the_dark_loop(store):
    seeded(store)
    d = document(store)
    assert d["held_now"] == 1
    assert d["live"] == {"queued": 1}
    assert d["finished"] == 3
    assert d["by_state"] == {"applied": 1, "capped": 1, "failed": 1}
    assert d["rounds"] == {"2": 1, "3": 1}
    assert d["transformations"] == {"command_to_ci": 2, "pr_text_to_code": 1, "to_post_deploy": 1}
    assert d["recheck_after_applied"] == {"ready": 1}
    assert d["box_minutes"] == {"n": 3, "p50": 40.0, "p90": 60.0, "max": 60.0, "total": 120.0}
    assert d["hold_to_release_minutes"]["n"] == 3
    assert d["hold_to_release_minutes"]["p50"] == 90.0, "capped: held 1h, ended 30m into its run"
    assert d["stage_seconds"]["draft"] == {"n": 2, "p50": 120.0, "p90": 180.0, "max": 180.0}
    assert d["stage_seconds"]["comprehend"]["n"] == 1


def test_the_merge_rate_joins_the_runs_that_came_after_each_row(store):
    seeded(store)
    merge = document(store)["merge"]
    assert merge["applied"] == {"issues": 1, "runs": 2, "merged": 1, "merged_per_100": 50.0}, \
        "#5's two runs after the apply (I5b, I5c); the run before it (I5a) is not counted"
    assert merge["released_unapplied"] == {"issues": 2, "runs": 2, "merged": 1, "merged_per_100": 50.0}, \
        "the do-nothing comparison: #6 (merged) and #7 (not)"


def test_a_run_dispatched_exactly_when_the_row_ended_counts_and_one_second_before_does_not(store):
    ended = NOW - timedelta(days=2)
    sharpen_run(store, "S1", 1, ended - timedelta(minutes=10))
    row(store, 1, "applied", run_id="S1", created=ended - timedelta(hours=1), finished=ended)
    issue_run(store, "AT", 1, ended)
    issue_run(store, "BEFORE", 1, ended - timedelta(seconds=1))
    assert document(store)["merge"]["applied"]["runs"] == 1


def test_the_calibration_rates_are_beside_the_applied_rate(store):
    for n in range(4):
        calibrated(store, f"CU{n}", 20 + n, "unverifiable", merged=n == 0)
    for n in range(2):
        calibrated(store, f"CR{n}", 30 + n, "ready", merged=True)
    cal = document(store)["merge"]["calibration"]
    assert cal["unverifiable"] == {"runs": 4, "merged": 1, "merged_per_100": 25.0}
    assert cal["ready"] == {"runs": 2, "merged": 2, "merged_per_100": 100.0}


def test_the_window_leaves_out_rows_that_ended_before_it(store):
    old = NOW - timedelta(days=40)
    row(store, 5, "applied", run_id="OLD", created=old, finished=old + timedelta(minutes=5))
    d = document(store)
    assert d["finished"] == 0 and d["by_state"] == {}


def test_an_empty_window_is_null_and_zero_never_a_misleading_average(store):
    d = document(store)
    assert (d["held_now"], d["finished"], d["by_state"]) == (0, 0, {})
    assert d["box_minutes"] == {"n": 0, "p50": None, "p90": None, "max": None, "total": 0}
    assert d["merge"]["applied"] == {"issues": 0, "runs": 0, "merged": 0, "merged_per_100": None}
    assert d["merge"]["calibration"]["ready"]["merged_per_100"] is None
    assert d["stage_seconds"] == {}


def test_the_section_is_in_the_report_and_the_markdown(store):
    seeded(store)
    gathered = store.measure_rows(since=NOW - timedelta(days=14), baseline_since=NOW - timedelta(days=28))
    report = measure.report(measure.Rows(**gathered), days=14, now=NOW)
    assert report["sharpening"]["by_state"] == {"applied": 1, "capped": 1, "failed": 1}
    text = measure.as_markdown(report)
    assert "## Issue sharpening" in text
    assert "| applied | 1 | 2 | 1 | 50 |" in text
    assert "| `draft` stage, seconds |" in text
    assert "precheck `unverifiable` (calibration)" in text


# --- the scorecard row ---------------------------------------------------------------


def window_rows(store: Store):
    return store.scorecard_rows(T0, T0 + timedelta(days=7))


def test_the_scorecard_row_counts_applied_and_unapplied_and_the_applied_issues_prs(store):
    day = T0 + timedelta(days=1)
    sharpen_run(store, "S5", 5, day)
    row(store, 5, "applied", run_id="S5", created=day, finished=day + timedelta(minutes=10), recheck="ready")
    sharpen_run(store, "S6", 6, day)
    row(store, 6, "capped", run_id="S6", created=day, finished=day + timedelta(minutes=20))
    sharpen_run(store, "S7", 7, day)
    row(store, 7, "not_needed", run_id="S7", created=day, finished=day + timedelta(minutes=5))
    issue_run(store, "I5", 5, day + timedelta(hours=3), merged=True, pr=50)
    issue_run(store, "I5x", 5, day - timedelta(hours=3), pr=49)  # before the rewrite: not counted
    issue_run(store, "I6", 6, day + timedelta(hours=3), merged=True, pr=60)
    store.save_pr_facts(JELLY, 50, PrFacts(state="merged", created_at="2026-11-17T03:00:00Z",
                                           merged_at="2026-11-17T05:00:00Z", closed_at=None, labels=(),
                                           additions=1, deletions=1, commits=1, operator_commits=0,
                                           report="complete", reason=None, queue_approved=False,
                                           queue_handoff=False))
    card = scorecard.compute(window_rows(store), T0, T0 + timedelta(days=7))
    assert (card.sharpen_applied, card.sharpen_unapplied) == (1, 1), "not_needed is neither"
    assert (card.sharpen_prs, card.sharpen_merged) == (1, 1)
    text = scorecard.to_markdown(card)
    assert "| Sharpened | applied 1, released unapplied 1; applied issues' PRs merged 1 of 1 |" in text
    assert scorecard.to_json(card)["sharpen_applied"] == 1


def test_the_scorecard_row_is_zero_before_the_first_sharpening(store):
    card = scorecard.compute(window_rows(store), T0, T0 + timedelta(days=7))
    assert "| Sharpened | applied 0, released unapplied 0; applied issues' PRs merged 0 of 0 |" in scorecard.to_markdown(card)


def test_a_repo_filter_narrows_the_sharpened_row_too(store):
    day = T0 + timedelta(days=1)
    sharpen_run(store, "S5", 5, day)
    row(store, 5, "applied", run_id="S5", created=day, finished=day + timedelta(minutes=10))
    assert scorecard.compute(window_rows(store), T0, T0 + timedelta(days=7), repo=JELLY).sharpen_applied == 1
    assert scorecard.compute(window_rows(store), T0, T0 + timedelta(days=7), repo="o/other").sharpen_applied == 0


# --- the retro ------------------------------------------------------------------------


def classify(store: Store) -> retro.Classified:
    result = retro.classify(window_rows(store))
    for key in result.classes:
        assert retro.KEY_PATTERN.match(key), key
    return result


def test_each_unapplied_state_files_its_own_class_and_applied_files_none(store):
    day = T0 + timedelta(days=1)
    for n, state in enumerate(("applied", "not_needed", "superseded", "capped", "churn",
                               "cannot_sharpen", "failed", "stale"), start=10):
        row(store, n, state, run_id=f"S{n}", created=day, finished=day + timedelta(minutes=n), minutes=float(n))
    classes = classify(store).classes
    assert sorted(classes) == ["sharpen:cannot_sharpen", "sharpen:capped", "sharpen:churn",
                               "sharpen:failed", "sharpen:stale"]
    assert all(stat.kind == "sharpen" for stat in classes.values())


def test_sharpen_capped_crosses_the_threshold_on_rows_and_files_with_the_evidence(store):
    day = T0 + timedelta(days=1)
    for n in (1, 2, 3):
        row(store, n, "capped", run_id=f"RUN{n}", created=day, finished=day + timedelta(minutes=30), minutes=30.0)
    stat = classify(store).classes["sharpen:capped"]
    assert (len(stat.runs), stat.events, round(stat.box_hours, 1)) == (3, 3, 1.5)
    assert retro.over_threshold(stat, min_runs=retro.DEFAULT_MIN_RUNS, min_box_hours=retro.DEFAULT_MIN_BOX_HOURS)
    body = retro.issue_body(stat, None, date(2026, 11, 16))
    assert body.startswith("<!-- fabro:retro-class sharpen:capped -->")
    assert "GET :32280/api/background/measure" in body
    assert all(f"`RUN{n}`" in body for n in (1, 2, 3))
    assert retro.MARKER.findall(body) == ["sharpen:capped"], "the next week's retro finds the class again"


def test_two_rows_do_not_cross_the_threshold_but_four_box_hours_do(store):
    day = T0 + timedelta(days=1)
    row(store, 1, "failed", run_id="R1", created=day, finished=day, minutes=150.0)
    row(store, 2, "failed", run_id="R2", created=day, finished=day, minutes=100.0)
    stat = classify(store).classes["sharpen:failed"]
    assert retro.over_threshold(stat, min_runs=3, min_box_hours=4.0), "4.2 box-hours is half a coder day"
    assert not retro.over_threshold(stat, min_runs=3, min_box_hours=5.0)


def test_a_row_that_never_reached_a_run_is_not_filed(store):
    day = T0 + timedelta(days=1)
    row(store, 1, "failed", run_id=None, created=day, finished=day)
    assert classify(store).classes == {}
