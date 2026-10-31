"""Task 12's Phase 3 and Phase 4 exit numbers (`measure.py`, /api/background/measure).

Every number is computed from seeded store rows, so each test states what the arithmetic is:
box minutes as a share of the day, endings per 100 issue runs against the baseline window,
tokens and credits per planner visit split by pack and by the box-plan trial's arm, the held
issues, the pre-review by arm (and the runs before the arm fix left out), the orientation, and
the flaky diagnoses. The empty case is a number too: it must be `null` or `0`, never a
misleading average.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from fabro_scheduler import measure
from fabro_scheduler.store import Store

# Late enough that the 14-day window starts after the baseline ends (the baseline is
# fixed at the 14 days before 2026-10-30T21:21Z); a window overlapping it would count
# the same runs twice.
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
LIVE = datetime.fromisoformat(measure.PRECHECK_LIVE_AT)
JELLY = "andrewthetechie/jelly-swipe"


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def rows(store: Store, *, days: int = 14) -> measure.Rows:
    gathered = store.measure_rows(
        since=NOW - timedelta(days=days),
        baseline_since=NOW - timedelta(days=days + measure.BASELINE_DAYS),
    )
    return measure.Rows(**gathered)


def run(store: Store, *, run_id: str, at: datetime, pool: str = "coders-a", issue: int = 3,
        workflow: str = "backlog", had_pack: int | None = 0,
        had_orientation: int | None = 0, target_kind: str = "issue") -> None:
    """One `run_history` row, written the way archiving writes it."""
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO run_history (run_id, coder_pool, repo, issue_number, dispatched_at,"
            " finished_at, kind, requeue_attempt, pr_lookup, workflow, target_kind, had_pack,"
            " had_orientation) VALUES (?, ?, ?, ?, ?, ?, 'succeeded', 0, 'none', ?, ?, ?, ?)",
            (run_id, pool, JELLY, issue, at.isoformat(), at.isoformat(), workflow,
             target_kind, had_pack, had_orientation),
        )


def summary(store: Store, *, run_id: str, terminal: str | None = "open_pr",
            arm: str | None = None, verdict: str | None = None,
            task_sources: dict | None = None) -> None:
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO run_summary (run_id, status, attempts, summarized_at, terminal_node,"
            " pre_review_arm, pre_review_verdict, task_sources) VALUES (?, 'ok', 1, ?, ?, ?, ?, ?)",
            (run_id, NOW.isoformat(), terminal, arm, verdict,
             json.dumps(task_sources or {})),
        )


def stage(store: Store, *, run_id: str, node: str, visits: int = 1, box_ms: int = 0,
          input_tokens: int | None = None, output_tokens: int | None = None,
          cache_read_tokens: int | None = None) -> None:
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO run_stage_stats (run_id, node_id, handler, visits, wall_ms, box_ms,"
            " timeouts, failures, input_tokens, output_tokens, cache_read_tokens)"
            " VALUES (?, ?, 'agent', ?, 0, ?, 0, '{}', ?, ?, ?)",
            (run_id, node, visits, box_ms, input_tokens, output_tokens, cache_read_tokens),
        )


def job(store: Store, *, kind: str, repo: str = JELLY, target: str = "", box: str = "coders-a",
        started: datetime | None = None, finished: datetime | None = None,
        state: str = "done", outcome: str | None = None, priority: int = 3,
        minutes: int = 5, issue_inputs: bool = False) -> int:
    job_id = store.enqueue_background_job(
        kind=kind, repo=repo, target=target,
        inputs={"issue_number": int(target)} if issue_inputs and target.isdigit() else {},
        priority=priority, typical_box_minutes=minutes, now=(started or NOW),
    )
    if state != "queued" and started is not None:
        store.start_background_job(job_id, run_id=f"R{job_id}", box=box, now=started)
    if state in ("done", "failed"):
        store.finish_background_job(job_id, state=state, outcome=outcome,
                                    now=finished or started or NOW)
    return job_id


def cache_issue(store: Store, number: int, *labels: str) -> None:
    from fabro_scheduler.github import Issue

    store.upsert_issue(Issue(JELLY, number, f"issue {number}", frozenset({"agent", *labels}), NOW))


# --- box use -----------------------------------------------------------------------


def test_box_use_adds_the_loop_and_the_lane_and_divides_by_the_day(store):
    inside = NOW - timedelta(days=1)
    run(store, run_id="R1", at=inside, pool="coders-a")
    stage(store, run_id="R1", node="coder", box_ms=6 * 60_000)  # 6 minutes on the box
    stage(store, run_id="R1", node="review", box_ms=0)
    run(store, run_id="R2", at=inside, pool="coders-b")
    stage(store, run_id="R2", node="coder", box_ms=2 * 60_000)
    # A background job holds coders-a for 4 minutes.
    job(store, kind="context-pack", target="3", started=inside, finished=inside + timedelta(minutes=4))
    document = measure.report(rows(store), days=7, now=NOW)
    boxes = document["phase_3"]["box_use"]["boxes"]
    assert boxes["coders-a"]["coder_box_minutes"] == 6.0
    assert boxes["coders-a"]["background_minutes"] == 4.0
    # 10 minutes in a 7-day window: 10 / (7 * 1440) = 0.099% -> 0.1 at one decimal.
    assert boxes["coders-a"]["busy_share_pct"] == 0.1
    assert boxes["coders-b"]["coder_box_minutes"] == 2.0
    assert boxes["coders-b"]["background_minutes"] == 0.0
    assert boxes["coders-a"]["phase_0_busy_share_pct"] == (32, 39)
    assert boxes["coders-b"]["phase_0_busy_share_pct"] == (44, 48)


def test_box_use_ignores_a_run_outside_the_window_and_a_job_with_no_span(store):
    run(store, run_id="OLD", at=NOW - timedelta(days=40))
    stage(store, run_id="OLD", node="coder", box_ms=6 * 60_000)
    job(store, kind="box-noop", started=None, finished=None, state="done")
    assert measure.report(rows(store), days=14, now=NOW)["phase_3"]["box_use"]["boxes"] == {}


# --- endings -----------------------------------------------------------------------


def test_endings_are_per_100_issue_runs_against_the_baseline_window(store):
    inside = NOW - timedelta(days=2)
    for i, ending in enumerate(("close_noop", "return_to_triage", "mark_stuck", "open_pr")):
        run(store, run_id=f"W{i}", at=inside, issue=10 + i)
        summary(store, run_id=f"W{i}", terminal=ending)
    baseline = LIVE - timedelta(days=2)
    for i, ending in enumerate(("close_noop", "open_pr")):
        run(store, run_id=f"B{i}", at=baseline, issue=20 + i)
        summary(store, run_id=f"B{i}", terminal=ending)
    document = measure.report(rows(store), days=14, now=NOW)["phase_3"]["endings_per_100"]
    assert document["window"]["n"] == 4
    assert document["window"]["counts"] == {"close_noop": 1, "return_to_triage": 1,
                                            "mark_stuck": 1}
    assert document["window"]["per_100"]["close_noop"] == 25.0
    assert document["window"]["per_100"]["mark_stuck"] == 25.0
    assert document["baseline"]["n"] == 2
    assert document["baseline"]["per_100"]["close_noop"] == 50.0
    assert document["baseline"]["per_100"]["mark_stuck"] == 0.0


def test_an_unsummarized_run_is_left_out_rather_than_counted_as_an_ending(store):
    inside = NOW - timedelta(days=1)
    run(store, run_id="R1", at=inside)
    run(store, run_id="R2", at=inside)
    summary(store, run_id="R1", terminal="close_noop")
    document = measure.report(rows(store), days=14, now=NOW)["phase_3"]["endings_per_100"]
    assert document["window"]["n"] == 1, "the run with no summary is not an ending"
    assert document["window"]["per_100"]["close_noop"] == 100.0


def test_a_pull_request_run_is_not_an_issue_run(store):
    inside = NOW - timedelta(days=1)
    run(store, run_id="P1", at=inside, target_kind="pr")
    summary(store, run_id="P1", terminal="close_noop")
    assert measure.report(rows(store), days=14, now=NOW)["phase_3"]["endings_per_100"][
        "window"]["n"] == 0


# --- planner visits and credits -----------------------------------------------------


def test_planner_visits_split_by_pack_and_by_the_plan_arm(store):
    peak = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)  # Tuesday, inside the peak window
    off_peak = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)
    # The plan arm is `(issue // 2) % 2`: #3 -> 1 (the box arm), #4 -> 0 (hosted).
    for run_id, issue, at, had_pack in (("R1", 3, peak, 0), ("R2", 3, off_peak, 1),
                                        ("R3", 4, peak, 0)):
        run(store, run_id=run_id, at=at, issue=issue, had_pack=had_pack)
        stage(store, run_id=run_id, node="decompose", visits=2, input_tokens=100_000,
              output_tokens=10_000, cache_read_tokens=50_000)
        stage(store, run_id=run_id, node="plan", input_tokens=200_000, output_tokens=20_000,
              cache_read_tokens=100_000)
    document = measure.report(rows(store), days=14, now=NOW)["phase_3"]["planner_visits"]
    by_key = {(g["workflow"], g["node"], g["had_pack"], g["plan_arm"]): g
              for g in document["groups"]}
    # One run of two visits, off-peak: (100k*6.9 + 50k*1.7 + 10k*24) / 10,000 = 101.5 credits,
    # halved -> 50.75 for the run, 25.4 per visit.
    with_pack = by_key[("backlog", "decompose", 1, 1)]
    assert with_pack["visits"] == 2
    assert with_pack["tokens_per_visit"] == {"input": 50_000.0, "cache_read": 25_000.0,
                                            "output": 5_000.0}
    assert with_pack["credits_per_visit"] == 25.4
    assert with_pack["priced_on"] == measure.PRICED_MODEL

    # The same tokens at peak are double, and a pack is a separate bucket: this is the
    # comparison Phase 3 is for.
    peak_decompose = by_key[("backlog", "decompose", 0, 1)]
    assert peak_decompose["visits"] == 2
    assert peak_decompose["credits_per_visit"] == 50.8, (
        "101.5 credits at peak over two visits, against 25.4 off-peak with a pack"
    )

    # `plan` in the box arm costs no credits, and the store's tokens are still reported there.
    box_plan = by_key[("backlog", "plan", 0, 1)]
    assert box_plan["credits_per_visit"] is None
    assert box_plan["priced_on"] is None
    assert box_plan["tokens_per_visit"]["input"] == 200_000.0
    hosted_plan = by_key[("backlog", "plan", 0, 0)]
    assert hosted_plan["priced_on"] == measure.PRICED_MODEL
    assert hosted_plan["credits_per_visit"] == 203.0, (
        "(200k*6.9 + 100k*1.7 + 20k*24) / 10,000 = 203 credits at peak, over one visit"
    )


def test_the_peak_factor_follows_the_dispatch_time():
    assert measure._peak("2026-11-03T07:00:00+00:00") is True, "Tuesday 07:00 UTC = 15:00 +08"
    assert measure._peak("2026-11-03T20:00:00+00:00") is False
    assert measure._peak("2026-11-07T07:00:00+00:00") is False, "Saturday"
    assert measure._peak("2026-11-03T05:59:00+00:00") is False, "just before the window"
    assert measure._peak("not a date") is False


def test_credits_at_the_overview_rates():
    assert measure.credits(10_000, 0, 0, peak=True) == 6.9
    assert measure.credits(10_000, 0, 0, peak=False) == 3.45
    assert measure.credits(0, 10_000, 0, peak=True) == 1.7
    assert measure.credits(0, 0, 10_000, peak=True) == 24.0


# --- held issues -------------------------------------------------------------------


def test_holds_report_what_is_held_and_what_the_verdicts_would_hold(store):
    cache_issue(store, 3, "precheck-already-done")
    cache_issue(store, 4, "precheck-blocked")
    cache_issue(store, 5)
    for target, verdict in (("3", "already_done"), ("4", "blocked"), ("5", "ready"),
                            ("6", "unclear")):
        job(store, kind="issue-precheck", target=target, outcome=f"verdict={verdict}",
            started=NOW - timedelta(days=1), finished=NOW - timedelta(days=1), issue_inputs=True)
    document = measure.report(
        rows(store), days=14, now=NOW, holds_on=True,
        hold_verdicts=("already_done", "contradiction", "unclear", "blocked"),
    )["phase_3"]["precheck_holds"]
    assert document["switch"] == "on"
    assert document["held_now"] == {"precheck-already-done": 1, "precheck-blocked": 1}
    assert document["held_now_total"] == 2
    assert document["verdicts_recorded"] == {
        "already_done": 1, "blocked": 1, "ready": 1, "unclear": 1,
    }
    assert document["holdable_verdicts_recorded"] == 3
    assert document["released_at_most"] == 1, "unclear was held on paper and is on no issue"


def test_with_the_switch_off_no_release_is_claimed(store):
    cache_issue(store, 3)
    job(store, kind="issue-precheck", target="3", outcome="verdict=already_done",
        started=NOW - timedelta(days=1), finished=NOW - timedelta(days=1), issue_inputs=True)
    document = measure.report(rows(store), days=14, now=NOW)["phase_3"]["precheck_holds"]
    assert document["switch"] == "off"
    assert document["held_now_total"] == 0
    assert document["released_at_most"] is None, "a hold never applied looks the same"


# --- Phase 4: the pre-review -------------------------------------------------------


def test_the_pre_review_is_measured_by_arm_and_only_after_the_arm_fix(store):
    after = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)  # inside the 14-day window
    before = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)  # before Part 0's fix
    run(store, run_id="ON", at=after, issue=3)
    summary(store, run_id="ON", arm="on", verdict="kept", task_sources={"plan": 2})
    stage(store, run_id="ON", node="standards")
    stage(store, run_id="ON", node="spec")
    stage(store, run_id="ON", node="rework_t1", visits=3)  # box rung: not hosted
    run(store, run_id="OFF", at=after, issue=1)
    summary(store, run_id="OFF", arm="off", verdict="unchanged", task_sources={"plan": 2})
    stage(store, run_id="OFF", node="quality")
    run(store, run_id="OLD", at=before, issue=5)
    summary(store, run_id="OLD", arm="on", verdict="kept", task_sources={"plan": 1})
    stage(store, run_id="OLD", node="standards")
    document = measure.report(rows(store), days=14, now=NOW)["phase_4"]["pre_review"]
    assert set(document["arms"]) == {"on", "off"}, "the run before the fix is left out"
    assert document["arms"]["on"] == {
        "runs": 1, "tasks": 2, "estimated_tasks": 0, "hosted_visits": 2,
        "hosted_visits_per_task": 1.0, "verdicts": {"kept": 1},
    }
    assert document["arms"]["off"]["hosted_visits"] == 1
    assert document["arms"]["off"]["verdicts"] == {"unchanged": 1}


def test_the_pre_review_boundary_is_the_operators_to_set(store):
    """The arm fix does nothing until it is deployed, so the window starts at the deploy."""
    run(store, run_id="R1", at=datetime(2026, 11, 10, 12, 0, tzinfo=UTC))
    summary(store, run_id="R1", arm="on", verdict="kept", task_sources={"plan": 1})
    document = measure.report(
        rows(store), days=14, now=NOW, pre_review_since="2026-11-11T00:00:00+00:00"
    )["phase_4"]["pre_review"]
    assert document["arms"] == {}
    assert document["since"] == "2026-11-11T00:00:00+00:00"


def test_a_run_with_no_task_tally_counts_as_one_task_and_says_so(store):
    run(store, run_id="R1", at=datetime(2026, 11, 10, 12, 0, tzinfo=UTC))
    summary(store, run_id="R1", arm="on", verdict="unchanged")
    document = measure.report(rows(store), days=14, now=NOW)["phase_4"]["pre_review"]
    assert document["arms"]["on"]["tasks"] == 1
    assert document["arms"]["on"]["estimated_tasks"] == 1


# --- Phase 4: the orientation and the flaky diagnoses --------------------------------


def test_orientation_splits_decompose_tokens_by_having_a_map(store):
    at = NOW - timedelta(days=1)
    run(store, run_id="YES", at=at, had_orientation=1)
    stage(store, run_id="YES", node="decompose", visits=2, input_tokens=10_000)
    run(store, run_id="NO", at=at, had_orientation=0)
    stage(store, run_id="NO", node="decompose", visits=1, input_tokens=40_000)
    stage(store, run_id="NO", node="plan", input_tokens=999_999)
    groups = measure.report(rows(store), days=14, now=NOW)["phase_4"]["orientation"]["groups"]
    assert groups["with"]["visits"] == 2
    assert groups["with"]["tokens_per_visit"]["input"] == 5_000.0
    assert groups["without"]["visits"] == 1
    assert groups["without"]["tokens_per_visit"]["input"] == 40_000.0


def test_flaky_diagnoses_and_the_runs_that_followed(store):
    diagnosed_at = NOW - timedelta(days=5)
    job(store, kind="flaky-investigation", target="7",
        started=diagnosed_at, finished=diagnosed_at, outcome="flaky cause=timing run=R7",
        issue_inputs=True)
    job(store, kind="flaky-investigation", target="8",
        started=diagnosed_at, finished=diagnosed_at, outcome="flaky cause=ordering run=R8",
        issue_inputs=True)
    job(store, kind="flaky-investigation", target="9",
        started=diagnosed_at, finished=diagnosed_at, outcome="succeeded (no marker)",
        issue_inputs=True)
    run(store, run_id="AFTER", at=NOW - timedelta(days=4), issue=7)
    summary(store, run_id="AFTER", terminal="open_pr")
    run(store, run_id="BEFORE", at=NOW - timedelta(days=6), issue=8)
    summary(store, run_id="BEFORE", terminal="close_noop")
    document = measure.report(rows(store), days=14, now=NOW)["phase_4"]["flaky"]
    assert document["diagnoses"] == 2, "a run that posted no marker is not a diagnosis"
    assert document["causes"] == {"ordering": 1, "timing": 1}
    assert document["runs_after_a_diagnosis"] == 1, "only the run dispatched after its diagnosis"
    assert document["endings_after_a_diagnosis"] == {"open_pr": 1}


# --- the empty case ---------------------------------------------------------------


def test_an_empty_store_reports_zeroes_and_nulls_never_a_misleading_average(store):
    document = measure.report(rows(store), days=14, now=NOW)
    assert document["phase_3"]["box_use"]["boxes"] == {}
    assert document["phase_3"]["endings_per_100"]["window"]["n"] == 0
    assert document["phase_3"]["endings_per_100"]["window"]["per_100"]["close_noop"] is None
    assert document["phase_3"]["planner_visits"]["groups"] == []
    assert document["phase_3"]["precheck_holds"]["held_now_total"] == 0
    assert document["phase_4"]["pre_review"]["arms"] == {}
    assert document["phase_4"]["orientation"]["groups"]["with"]["visits"] == 0
    assert document["phase_4"]["flaky"]["diagnoses"] == 0
    markdown = measure.as_markdown(document)
    assert "Window: **14 days**" in markdown
    assert "n=0" in markdown
    assert "Switch: **off**" in markdown


def test_the_window_says_its_dates_and_its_baseline(store):
    document = measure.report(rows(store), days=7, now=NOW)
    window = document["window"]
    assert window["days"] == 7
    assert window["from"] == (NOW - timedelta(days=7)).isoformat()
    assert window["baseline"]["to"] == measure.PRECHECK_LIVE_AT
    assert window["baseline"]["from"] == (
        LIVE - timedelta(days=measure.BASELINE_DAYS)
    ).isoformat()


# --- the HTTP surface ---------------------------------------------------------------


def test_the_measure_endpoint_serves_the_document_and_its_markdown(store):
    from fastapi.testclient import TestClient

    from fabro_scheduler.app import build_app
    from test_precheck import config

    run(store, run_id="R1", at=datetime(2026, 11, 10, 12, 0, tzinfo=UTC))
    summary(store, run_id="R1", terminal="close_noop", arm="on", verdict="kept",
            task_sources={"plan": 1})
    stage(store, run_id="R1", node="coder", box_ms=60_000)
    client = TestClient(build_app(config(), store))
    body = client.get("/api/background/measure?days=7").json()
    assert body["window"]["days"] == 7
    assert body["phase_3"]["endings_per_100"]["window"]["counts"]["close_noop"] == 1
    assert body["phase_4"]["pre_review"]["arms"]["on"]["verdicts"] == {"kept": 1}
    assert client.get("/api/background/measure").status_code == 200, "14 days by default"
    text = client.get("/api/background/measure?format=md")
    assert text.status_code == 200
    assert text.headers["content-type"].startswith("text/markdown")
    assert "# Background work" in text.text
    for bad in ("0", "121"):
        assert client.get(f"/api/background/measure?days={bad}").status_code == 400, bad
    # A non-integer is FastAPI's own 422, which names the parameter.
    assert client.get("/api/background/measure?days=seven").status_code == 422


def test_the_queue_page_links_the_measurement(store):
    from fastapi.testclient import TestClient

    from fabro_scheduler.app import build_app
    from test_precheck import config

    client = TestClient(build_app(config(), store))
    page = client.get("/").text
    assert "/api/background/measure" in page, "the operator must be able to find the numbers"
    assert "/api/background/calibration" in page
