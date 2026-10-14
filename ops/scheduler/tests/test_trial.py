"""The plan trial's split by arm (#25) over a store seeded through its real writes."""

from __future__ import annotations

from collections import Counter

import pytest

from fabro_scheduler import trial
from fabro_scheduler.summary import RunSummary, StageStat

from test_scorecard import OTHER, REPO, WINDOW, facts, run, store  # noqa: F401  (fixture)

MIN = 60_000


def summarize(store, run_id, stages, *, first_edit=(), dispositions=None):
    store.save_run_summary(
        RunSummary(
            run_id=run_id,
            stages={s.node_id: s for s in stages},
            failovers=Counter(),
            path=[],
            terminal_node="exit_node",
            refute=[],
            events=10,
            first_edit_ms=list(first_edit),
            dispositions=Counter(dispositions or {}),
        )
    )


def results(store):
    return {r.arm: r for r in trial.compute(store.scorecard_rows(*WINDOW), *WINDOW)}


def test_a_run_is_in_the_arm_of_the_planner_that_ran(store):
    run(store, "P1", pr=1, merged=True)
    run(store, "P2", pr=2, merged=False, repo=OTHER)
    run(store, "I1", pr=3, merged=False)
    run(store, "F1", pr=4, merged=True)  # fast lane: no planner, in neither arm
    run(store, "U1", pr=5, merged=True)  # not summarized yet: in neither arm
    store.save_pr_facts(REPO, 1, facts(state="merged", merged_at="2026-10-05T05:00:00Z"))
    summarize(store, "P1", [
        StageStat("plan", "agent", 2, 6 * MIN, 0, input_tokens=100_000,
                  output_tokens=5_000, cache_read_tokens=400_000),
        StageStat("coder", "agent", 2, 40 * MIN, 40 * MIN),
        StageStat("rework_t1", "agent", 1, 10 * MIN, 10 * MIN),
        StageStat("validate", "command", 3, 3 * MIN, 0, failures=Counter({"deterministic": 1})),
    ], first_edit=[60_000, 180_000], dispositions={"ready": 2})
    summarize(store, "P2", [
        StageStat("plan", "agent", 1, 4 * MIN, 0, timeouts=1, input_tokens=50_000,
                  output_tokens=3_000, cache_read_tokens=100_000),
        StageStat("coder", "agent", 1, 20 * MIN, 20 * MIN, timeouts=1),
        StageStat("rework_t3", "agent", 1, 10 * MIN, 0),
    ], first_edit=[120_000], dispositions={"ready": 1, "split": 1})
    summarize(store, "I1", [
        StageStat("improve", "agent", 3, 30 * MIN, 30 * MIN),
        StageStat("coder", "agent", 3, 60 * MIN, 60 * MIN),
    ], first_edit=[300_000])
    summarize(store, "F1", [StageStat("coder", "agent", 1, 5 * MIN, 5 * MIN)])

    by_arm = results(store)
    plan, improve = by_arm["plan"], by_arm["improve"]

    assert plan.card.runs == 2 and plan.card.prs == 2 and plan.card.merged_workflow == 1
    assert improve.card.runs == 1 and improve.card.prs == 1 and improve.card.merged_workflow == 0

    line = plan.line
    assert line.runs == 2 and line.tasks == 3
    assert line.planner_visits == 3
    assert line.planner_minutes == pytest.approx(10.0)
    assert line.planner_minutes_per_run.p50 == pytest.approx(5.0)
    assert line.planner_box_minutes == 0
    assert (line.planner_input_tokens, line.planner_cache_read_tokens,
            line.planner_output_tokens, line.planner_token_runs) == (150_000, 500_000, 8_000, 2)
    assert line.coder_box_minutes_per_task == pytest.approx(20.0)
    assert line.coding_minutes_per_task == pytest.approx(80 / 3)
    assert line.rework_per_task == pytest.approx(2 / 3)
    assert line.hosted_rework_per_task == pytest.approx(1 / 3)
    assert line.validate_failures_per_task == pytest.approx(1 / 3)
    assert line.coder_timeouts == 1
    assert line.first_edit_minutes.count == 3 and line.first_edit_minutes.p50 == pytest.approx(2.0)
    assert line.dispositions == {"ready": 3, "split": 1}
    assert plan.card.timeouts == {"plan": 1, "coder": 1}

    assert improve.line.planner_box_minutes == pytest.approx(30.0)
    assert improve.line.coder_box_minutes_per_task == pytest.approx(20.0)


def test_planner_tokens_ignore_rows_summarized_before_the_trial(store):
    run(store, "I1")
    summarize(store, "I1", [StageStat("improve", "agent", 1, MIN, MIN, input_tokens=10)])
    store._conn.execute("UPDATE run_stage_stats SET input_tokens = NULL")
    line = results(store)["improve"].line
    assert line.planner_token_runs == 0 and line.planner_input_tokens == 0


def test_an_empty_window_renders(store):
    by_arm = trial.compute(store.scorecard_rows(*WINDOW), *WINDOW)
    assert [r.arm for r in by_arm] == ["plan", "improve"]
    table = trial.to_markdown(by_arm, *WINDOW)
    assert "| Runs / tasks coded | 0 / 0 | 0 / 0 |" in table
    assert "n/a" in table
    assert trial.to_json(by_arm)[0]["arm"] == "plan"
