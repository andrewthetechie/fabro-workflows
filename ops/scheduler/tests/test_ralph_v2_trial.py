"""The ralph-v2 trial's measures (ralph-v2 11, the experiment).

End to end, in the order the numbers travel: a fixture event stream -> `summary.summarize`
-> `Store.save_run_summary` -> the arms and the trial line `scorecard.by_workflow` computes.
Each fixture is one run's stream, cut to the events the summary reads and in the shape
`GET /runs/{id}/events` returns on fabro 0.362 (2026-11-24).

The nine streams the task names are: a clean v2 run, a reworked Task, a blocked Task, a run
whose Refuter missed, a run whose Refuter fell back, a mid-session failover, a run that
stopped before any review, a v1 run, and a v1 run from before `first_round` and `task_plans`
existed.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from fabro_scheduler import scorecard
from fabro_scheduler.prfacts import PrFacts
from fabro_scheduler.store import RunOutcome, Store
from fabro_scheduler.summary import FirstRound, RunSummary, StageStat, TaskPlans, TaskReview, summarize

REPO = "andrewthetechie/jelly-swipe"
T0 = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)  # a Monday, the week of the plan
WINDOW = (T0, T0 + timedelta(days=7))


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


# --- fixture event builders ---------------------------------------------------------


def submit(node: str, arguments: object, *, tool: str = "submit") -> dict:
    """A stage's `submit` call: where its result is (`properties.event.ToolCallStarted`)."""
    return {
        "event": "agent.tool.started",
        "node_id": node,
        "properties": {"event": {"ToolCallStarted": {"tool_name": tool, "arguments": arguments}}},
    }


def completed(node: str, updates: dict | None = None, *, ms: int = 0) -> dict:
    props: dict = {"status": "succeeded"}
    if ms:
        props["timing"] = {"wall_time_ms": ms}
    if updates is not None:
        props["context_updates"] = updates
    return {"event": "stage.completed", "node_id": node, "properties": props}


def started(node: str) -> dict:
    return {"event": "stage.started", "node_id": node, "properties": {}}


def failover(node: str) -> dict:
    return {
        "event": "agent.route.failover",
        "node_id": node,
        "properties": {
            "event": {
                "RouteFailover": {
                    "from": "box-a/coders-a",
                    "to": "zai/glm-5.3-flash",
                    "error": {"llm_kind": "quota_exhausted"},
                }
            }
        },
    }


def findings(errors: int, warnings: int = 0) -> list[dict]:
    return [{"severity": "error", "message": f"e{i}"} for i in range(errors)] + [
        {"severity": "warning", "message": f"w{i}"} for i in range(warnings)
    ]


def refute_arguments(not_met: int, defects: int = 0, tampering: int = 0) -> dict:
    return {
        "summary": "s",
        "criteria": [{"criterion": f"c{i}", "status": "met", "evidence": "a.py:1"} for i in range(2)]
        + [
            {"criterion": f"n{i}", "status": "not_met", "evidence": "b.py:2"}
            for i in range(not_met)
        ],
        "defects": [{"file": "a.py", "line": 1, "scenario": "s"} for _ in range(defects)],
        "hygiene": [{"ruling": "tampering"} for _ in range(tampering)]
        + [{"ruling": "legitimate"}],
    }


def quality_events(
    *, not_met: int = 0, defects: int = 0, tampering: int = 0, standards: int = 0, spec: int = 0
) -> list[dict]:
    """The issue-level hosted round: `standards`, then `spec`, then the Refuter (E14)."""
    return [
        started("standards"),
        submit("standards", {"summary": "s", "findings": findings(standards)}),
        completed("standards", ms=300_000),
        started("spec"),
        submit("spec", {"summary": "s", "findings": findings(spec)}),
        completed("spec", ms=300_000),
        started("refute"),
        submit("refute", refute_arguments(not_met, defects, tampering)),
        completed("refute", ms=600_000),
        completed(
            "refute_gate",
            {
                "refute_verdict": "fail" if (not_met or defects) else "pass",
                "refute_not_met": bool(not_met),
            },
        ),
    ]


def gate_updates(
    *,
    task: str = "T1",
    source: str = "plan",
    round_: int = 1,
    verdict: str = "pass",
    errors: int = 0,
    warnings: int = 0,
    disputed: int = 0,
    withdrawn: int = 0,
    dropped: int = 0,
    refute_served: str = "box",
    review_served: str = "box",
    edges: int = 0,
    edges_covered: int = 0,
) -> dict:
    """The thirteen keys `task_review_gate` publishes on every path (C5 step 9)."""
    return {
        "task_review_task": task,
        "task_review_source": source,
        "task_review_round": round_,
        "task_review_verdict": verdict,
        "task_review_errors": errors,
        "task_review_warnings": warnings,
        "task_review_disputed": disputed,
        "task_review_withdrawn": withdrawn,
        "task_review_dropped": dropped,
        "task_review_refute_served": refute_served,
        "task_review_review_served": review_served,
        "task_review_edges": edges,
        "task_review_edges_covered": edges_covered,
    }


def review_round(
    *,
    refute_ms: int = 600_000,
    review_ms: int = 1_200_000,
    refute_tries: int = 1,
    review_tries: int = 1,
    updates: dict | None = None,
    fail: str | None = None,
) -> list[dict]:
    """One round of the Task review loop: prep, both reviewers, both checks, the gate."""
    return [
        started("task_review_prep"),
        completed("task_review_prep"),
        started("task_refute"),
        (failover("task_refute") if fail == "task_refute" else {}),
        completed("task_refute", ms=refute_ms),
        completed("task_refute_check", {"task_refute_next": "done", "task_refute_tries": refute_tries}, ms=15_000),
        started("task_review"),
        (failover("task_review") if fail == "task_review" else {}),
        completed("task_review", ms=review_ms),
        completed("task_review_check", {"task_review_next": "done", "task_review_tries": review_tries}, ms=15_000),
        completed("task_review_gate", updates if updates is not None else gate_updates()),
    ]


def clean_v2_events() -> list[dict]:
    """The clean v2 run: one planned Task that passes its first review, then the PR round."""
    return (
        [
            started("task_plan"),
            submit("task_plan", {"context": "a.py:1", "goals": []}),
            completed("task_plan", ms=480_000),
            # A rejected plan wrote no plan, so only the `go` visit is counted below.
            completed(
                "task_plan_gate",
                {"task_plan_route": "retry", "task_plan_type": "none",
                 "task_plan_goals": 0, "task_plan_edges": 0},
            ),
            completed(
                "task_plan_gate",
                {"task_plan_route": "go", "task_plan_type": "planned",
                 "task_plan_goals": 2, "task_plan_edges": 3},
            ),
            started("iterate_t1"),
            completed("iterate_t1", ms=1_044_000),
            completed("check", {"task_validation_mode": "scoped", "task_validation_ok": True}),
            completed(
                "record",
                {"iteration_passed": True, "review_due": True},
            ),
        ]
        + review_round(
            updates=gate_updates(
                verdict="pass", warnings=1, disputed=2, withdrawn=1, dropped=3,
                refute_served="box", review_served="fallback", edges=3, edges_covered=2,
            )
        )
        + quality_events(
            not_met=1, defects=2, tampering=1, standards=2, spec=1
        )
    )


def summarize_run(run_id: str, events: list[dict], *, workflow: str = "ralph-v2") -> RunSummary:
    return summarize(run_id, [], iter(events), workflow=workflow)


def with_stages(summary: RunSummary, *stats: StageStat) -> RunSummary:
    """Replace the summary's stage rows: the wall and box time the scorecard reads comes from
    the stage list, which a fixture's events alone cannot supply."""
    summary.stages = {stat.node_id: stat for stat in stats}
    return summary


# --- the fixture runs through the summary and the store -----------------------------


def test_a_clean_v2_run_records_its_first_round_its_reviews_and_its_plans(store):
    """The clean run: five counts from the three submitters, one review row from the gate,
    and one accepted plan. The rejected plan visit is a `retry` and is not counted."""
    summary = summarize_run("V1", clean_v2_events())

    assert summary.first_round == FirstRound(
        refute_not_met=1, refute_defects=2, refute_tampering=1, standards_errors=2, spec_errors=1
    )
    assert summary.first_round.total() == 7
    assert summary.first_round.refuter_total() == 4
    assert summary.task_reviews == [
        TaskReview(
            task="T1", source="plan", round=1, verdict="pass",
            errors=0, warnings=1, disputed=2, withdrawn=1, dropped=3,
            refute_served="box", review_served="fallback",
            edges=3, edges_covered=2,
            # the two reviewers' wall time, not the check nodes': 10 + 20 minutes
            ms=1_800_000,
            # the last tries each check published: 1 + 1
            tries=2,
            failover=False,
        )
    ]
    assert summary.task_plans == TaskPlans(types=Counter({"planned": 1}), goals=2, edges=3)

    store.save_run_summary(summary)

    stored = store._conn.execute(
        "SELECT first_round, task_plans FROM run_summary WHERE run_id = 'V1'"
    ).fetchone()
    assert json.loads(stored["first_round"]) == {
        "refute_not_met": 1, "refute_defects": 2, "refute_tampering": 1,
        "standards_errors": 2, "spec_errors": 1,
    }
    assert json.loads(stored["task_plans"]) == {
        "types": {"planned": 1}, "goals": 2, "edges": 3,
    }
    row = store._conn.execute(
        "SELECT * FROM run_task_reviews WHERE run_id = 'V1' ORDER BY seq"
    ).fetchone()
    assert (row["seq"], row["task"], row["verdict"], row["errors"], row["warnings"]) == (
        1, "T1", "pass", 0, 1
    )
    assert (row["refute_served"], row["review_served"], row["tries"], row["ms"]) == (
        "box", "fallback", 2, 1_800_000
    )
    assert (row["edges"], row["edges_covered"], row["failover"]) == (3, 2, 0)


def test_the_last_submit_before_the_first_completion_is_the_result():
    """A reviewer submits once per visit, and the *first* completed visit is the round the
    measure reads: a later visit's submit, and a second completion, change nothing."""
    events = [
        submit("refute", refute_arguments(not_met=3)),
        submit("refute", refute_arguments(not_met=1)),  # the last submit wins
        completed("refute", ms=600_000),
        submit("refute", refute_arguments(not_met=9)),  # after the completion: ignored
        completed("refute", ms=600_000),
    ]
    summary = summarize_run("R1", events)
    # The second submit is the result: one `not_met`, not the first's three or the last's nine.
    assert summary.first_round.refute_not_met == 1
    assert summary.first_round.refute_defects == 0
    # The one `legitimate` ruling is not a tampering, and neither is an absent list.
    assert summary.first_round.refute_tampering == 0


def test_a_submit_that_is_not_an_object_is_no_result():
    """`arguments` arrives as a JSON string when the tool schema is malformed. That is not a
    result, so the field stays None and the round is not measured -- never 0."""
    events = [
        submit("refute", "not an object"),
        completed("refute", ms=600_000),
        submit("standards", {"findings": findings(2)}),
        completed("standards"),
    ]
    summary = summarize_run("R1", events)
    assert summary.first_round == FirstRound(standards_errors=2)
    assert summary.first_round.total() is None
    assert summary.first_round.refuter_total() is None


def test_a_reworked_task_is_two_rows_with_the_fallbacks_tries_and_the_rounds_ms():
    """The reworked Task (E9-E10): round 1 rejects, round 2 passes. The second round's
    reviewers were tried three and two times -- the fallback ladder -- and its Refuter fell
    back, which is the round's `refute_served` and its window's `ms`."""
    events = (
        review_round(
            updates=gate_updates(
                round_=1, verdict="rework", errors=2, warnings=1, edges=2, edges_covered=1
            )
        )
        + review_round(
            refute_ms=900_000,
            review_ms=600_000,
            refute_tries=3,
            review_tries=2,
            updates=gate_updates(
                round_=2, verdict="pass", edges=2, edges_covered=2, refute_served="fallback"
            ),
        )
    )
    summary = summarize_run("R1", events)

    assert [(review.round, review.verdict, review.errors) for review in summary.task_reviews] == [
        (1, "rework", 2),
        (2, "pass", 0),
    ]
    first, second = summary.task_reviews
    assert first.tries == 2 and first.ms == 1_800_000
    assert second.tries == 5 and second.ms == 1_500_000
    assert second.refute_served == "fallback" and second.failover is False


def test_a_blocked_task_is_a_blocked_row():
    """E10: an error left after the fourth review blocks the Task: one row, verdict
    `blocked`, and the count the gate published."""
    events = review_round(
        updates=gate_updates(round_=4, verdict="blocked", errors=1, warnings=2)
    )
    summary = summarize_run("R1", events)
    assert [review.verdict for review in summary.task_reviews] == ["blocked"]
    assert summary.task_reviews[0].errors == 1
    assert summary.task_reviews[0].round == 4


def test_a_missed_reviewer_is_a_miss_and_only_both_missed_is_a_missed_verdict():
    """E13: a reviewer that leaves no valid result is retried, then recorded `missed`; the
    Task passes. `missed` as a verdict is both reviewers, which is the round the Task passes
    without being read (C5 step 6)."""
    events = review_round(
        updates=gate_updates(round_=1, verdict="pass", refute_served="missed", review_served="box")
    ) + review_round(
        updates=gate_updates(
            task="T2", round_=1, verdict="missed",
            refute_served="missed", review_served="missed",
        )
    )
    summary = summarize_run("R1", events)
    assert [(review.refute_served, review.review_served, review.verdict)
            for review in summary.task_reviews] == [
        ("missed", "box", "pass"),
        ("missed", "missed", "missed"),
    ]


def test_a_mid_session_failover_marks_the_round_and_is_a_failover_of_the_run():
    """A reviewer that ran on the fallback model is an incident (E17, C11), so the round says
    so; the run's own failover counter is the same event."""
    summary = summarize_run("R1", review_round(fail="task_review"))
    assert summary.task_reviews[0].failover is True
    assert summary.failovers == {
        ("task_review", "box-a/coders-a", "zai/glm-5.3-flash", "quota_exhausted"): 1
    }
    # A round with no failover is not accidentally true (see the clean run), and the flag
    # does not leak into the next round.
    two = summarize_run("R2", review_round(fail="task_review") + review_round())
    assert [review.failover for review in two.task_reviews] == [True, False]


def test_a_run_that_stopped_before_any_review_has_no_first_round_fields_and_no_rows():
    """A run that ended in the task loop or on the box lenses reaches no hosted reviewer and
    no Task review gate: every field stays None, and nothing is invented as 0."""
    events = [
        started("task_plan"),
        completed("task_plan_gate",
                  {"task_plan_route": "go", "task_plan_type": "planned",
                   "task_plan_goals": 1, "task_plan_edges": 0}),
        started("iterate_t1"),
        completed("iterate_t1", ms=600_000),
        completed("check", {"task_validation_mode": "full", "task_validation_ok": False}),
        completed("record", {"iteration_passed": False, "review_due": False}),
    ]
    summary = summarize_run("R1", events)
    assert summary.first_round == FirstRound()
    assert summary.first_round.total() is None
    assert summary.task_reviews == []
    assert summary.task_plans == TaskPlans(types=Counter({"planned": 1}), goals=1, edges=0)


def test_a_v1_run_reads_its_first_round_and_no_task_rows():
    """`ralph` runs the same three hosted reviewers, so its quality measure is the same five
    counts; it has no Task review gate, so those two facts stay empty."""
    summary = summarize_run("R1", quality_events(not_met=2, defects=1, standards=1), workflow="ralph")
    assert summary.first_round == FirstRound(
        refute_not_met=2, refute_defects=1, refute_tampering=0, standards_errors=1, spec_errors=0
    )
    assert summary.task_reviews == []
    assert summary.task_plans == TaskPlans()


def test_a_backlog_run_records_no_first_round(store):
    """`backlog` reviews through the imported `review_merge.*` nodes and is not an arm, so
    the column stays NULL: not measured, which is not the same as a round of five zeros."""
    summary = summarize_run("R1", quality_events(), workflow="backlog")
    assert summary.first_round is None
    store.save_run_summary(summary)
    stored = store._conn.execute(
        "SELECT first_round FROM run_summary WHERE run_id = 'R1'"
    ).fetchone()
    assert stored["first_round"] is None
    # And the scorecard's reader treats a row from before the columns the same way.
    assert scorecard._first_round(stored["first_round"]) is None
    assert scorecard._first_round('{"refute_not_met": 0}').total() is None


# --- the arms, the trial line and the stop rules ------------------------------------


def run(store, run_id, *, workflow, hours=2.0, at=T0 + timedelta(hours=1), pr=None, merged=None):
    """One released run, written the way the scheduler's release writes it."""
    store.acquire_lease(
        coder_pool="coders-a", repo=REPO, issue_number=1, run_id=run_id, dispatched_at=at,
        workflow=workflow,
    )
    store.archive_and_release_lease(
        run_id,
        RunOutcome(
            kind="succeeded", reason="completed", finished_at=at + timedelta(hours=hours),
            pr_lookup="found" if pr else "none", pr_number=pr,
            pr_url=f"https://github.com/{REPO}/pull/{pr}" if pr else None, merged=merged,
        ),
    )


def fact(*, state="open", operator=0, merged_at=None):
    return PrFacts(
        state=state, created_at="2026-11-23T03:00:00Z", merged_at=merged_at, closed_at=merged_at,
        labels=("agent-authored",), additions=1, deletions=1, commits=5,
        operator_commits=operator, report="complete", reason=None,
        queue_approved=False, queue_handoff=False,
    )


def test_the_arm_lines_and_the_trial_line_on_hand_computed_numbers(store):
    """Two arms of six runs each, and the arithmetic of both co-primary measures.

    v1: one zero-touch merge of six runs (1/6), twelve lease-hours, so twelve lease-hours a
        merge; its first rounds sum to 4, 6, 6, 8, 5, 7 (mean 6, sample sd sqrt(10/5)).
    v2: five zero-touch merges of six (5/6), the same twelve lease-hours, so 2.4 a merge;
        its first rounds sum to 1, 2, 3, 2, 2, 2 (mean 2, sample sd sqrt(2/5)).
    merge: 5/6 - 1/6 = 2/3, se sqrt(1/6*5/6/6 + 5/6*1/6/6) = sqrt(2 * 5/216) = 0.2152, and
        2 se is 0.4303 < 0.6667, so v2 is *better*.
    quality: 2 - 6 = -4, se sqrt((10/5)/6 + (2/5)/6) = 0.6325, and 4 > 1.2649 with fewer
        findings better, so v2 is *better* again.
    guardrail: 2.4 / 12 = 0.2, under the trial's 2x stop and its +50% loss line.
    None of the three stop rules can fire on six runs (they need 40 and 60).
    """
    for i, value in enumerate([4, 6, 6, 8, 5, 7], start=1):
        # R1 is the one the merge phase merged; the rest were left for a human.
        run(store, f"R{i}", workflow="ralph", pr=i, merged=i == 1)
        store.save_run_summary(
            summarize_run(
                f"R{i}",
                quality_events(not_met=value - 2, standards=1, spec=1),
                workflow="ralph",
            )
        )
    for i, value in enumerate([1, 2, 3, 2, 2, 2], start=11):
        # V11..V15 merged by the run; V16's PR is still open.
        run(store, f"V{i}", workflow="ralph-v2", pr=i, merged=i != 16)
        store.save_run_summary(summarize_run(f"V{i}", quality_events(not_met=value)))
    # v1's merge: R1's PR merged by the run, no operator commit. R3's was merged by hand.
    store.save_pr_facts(REPO, 1, fact(state="merged", merged_at="2026-11-23T05:00:00Z"))
    store.save_pr_facts(REPO, 3, fact(state="merged", operator=2, merged_at="2026-11-23T05:00:00Z"))
    # v2's: five merges by the run, and V16's still open.
    for number in (11, 12, 13, 14, 15):
        store.save_pr_facts(REPO, number, fact(state="merged", merged_at="2026-11-23T05:00:00Z"))

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)

    v1, v2 = card.arms
    assert [arm.workflow for arm in card.arms] == ["ralph", "ralph-v2"]
    assert (v1.runs, v1.zero_touch, v1.merge_rate) == (6, 1, pytest.approx(1 / 6))
    assert v1.merge_se == pytest.approx(math.sqrt((1 / 6) * (5 / 6) / 6))
    assert (v2.runs, v2.zero_touch, v2.merge_rate) == (6, 5, pytest.approx(5 / 6))
    assert v1.quality_n == 6
    assert v1.quality_mean == pytest.approx(6.0)
    assert v1.quality_sd == pytest.approx(math.sqrt(10 / 5))
    assert v2.quality_mean == pytest.approx(2.0)
    assert v2.quality_sd == pytest.approx(math.sqrt(2 / 5))
    # The Refuter's three: v1's are its not_met counts, which are value - 2 (mean 4).
    assert (v1.refuter_n, v1.refuter_mean) == (6, pytest.approx(4.0))
    assert (v2.refuter_n, v2.refuter_mean) == (6, pytest.approx(2.0))
    assert (v1.lease_hours, v2.lease_hours) == (pytest.approx(12.0), pytest.approx(12.0))
    assert v1.lease_hours_per_merge == pytest.approx(12.0)
    assert v2.lease_hours_per_merge == pytest.approx(2.4)

    trial = card.trial
    assert trial.merge_diff == pytest.approx(5 / 6 - 1 / 6)
    assert trial.merge_se == pytest.approx(math.sqrt((1 / 6) * (5 / 6) / 6 * 2))
    assert trial.merge_verdict == "better"
    assert trial.quality_diff == pytest.approx(-4.0)
    assert trial.quality_se == pytest.approx(math.sqrt((2 / 6) + (0.4 / 6)))
    assert trial.quality_verdict == "better"
    assert trial.guardrail == pytest.approx(2.4 / 12.0)
    assert (trial.lease_stop, trial.merge_stop, trial.miss_stop) == (False, False, False)

    # The two sections say the same thing in markdown, after the strata table.
    text = scorecard.workflows_to_markdown(card)
    assert text.index("## Strata: repo x coder pool") < text.index("## ralph-v2 trial")
    assert "| merge, v2 - v1 | +0.67 ± 0.22: better |" in text
    assert "| quality, v2 - v1 | -4.00 ± 0.63: better |" in text
    assert "| guardrail, v2 / v1 | 0.20x (the trial is lost above 2.00x) |" in text
    payload = scorecard.workflows_to_json(card)
    json.dumps(payload)  # the endpoint's shape: the three new keys must serialize
    assert payload["trial"]["merge_verdict"] == "better"
    assert [arm["workflow"] for arm in payload["arms"]] == ["ralph", "ralph-v2"]
    assert payload["task_reviews"]["reviews"] == 0


def test_the_trial_line_needs_two_runs_in_each_arm(store):
    """One run an arm is not a comparison: the differences and the guardrail stay None, and
    an arm's own numbers are still there."""
    run(store, "R1", workflow="ralph", pr=1)
    run(store, "V1", workflow="ralph-v2", pr=11)
    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)
    assert [arm.runs for arm in card.arms] == [1, 1]
    assert card.arms[0].merge_rate == 0.0  # a PR with no facts is not a zero-touch merge yet
    assert card.trial == scorecard.TrialLine()


def test_the_fixture_runs_read_as_one_arm(store):
    """The nine fixture streams as one window: six v2 runs and two v1 ones. Only the clean run
    reached a hosted reviewer, so the quality mean is over one run; the review loop counts
    every round, one per fixture, and the round-1 rows are where the edge cases are read."""
    fixtures = {
        "VC": clean_v2_events(),
        "VR": review_round(
            updates=gate_updates(round_=1, verdict="rework", errors=2, edges=2, edges_covered=1)
        )
        + review_round(updates=gate_updates(round_=2, verdict="pass", edges=2, edges_covered=2)),
        "VB": review_round(updates=gate_updates(round_=4, verdict="blocked", errors=1)),
        "VM": review_round(
            updates=gate_updates(refute_served="missed", edges=0, edges_covered=0)
        ),
        "VF": review_round(fail="task_refute"),
        "VS": [started("iterate_t1"), completed("iterate_t1", ms=600_000)],
    }
    for run_id, events in fixtures.items():
        run(store, run_id, workflow="ralph-v2", at=T0 + timedelta(hours=len(run_id)))
        store.save_run_summary(summarize_run(run_id, events))
    for i, value in enumerate([1, 3], start=1):
        run(store, f"R{i}", workflow="ralph", at=T0 + timedelta(hours=9 + i), pr=i, merged=True)
        store.save_run_summary(
            summarize_run(f"R{i}", quality_events(not_met=value), workflow="ralph")
        )
        store.save_pr_facts(
            REPO, i, fact(state="merged", merged_at="2026-11-23T05:00:00Z")
        )

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)

    v1, v2 = card.arms
    assert (v1.runs, v1.zero_touch, v1.quality_n) == (2, 2, 2)
    assert (v2.runs, v2.zero_touch) == (6, 0)
    assert v2.quality_n == 1, "only the clean run reached the hosted reviewers"
    assert v2.quality_mean == pytest.approx(7.0)
    assert v2.quality_sd is None, "one measured run has no spread"

    reviews = card.task_reviews
    assert reviews.reviews == 6, "one row per fixture round: the reworked Task has two"
    assert reviews.verdicts == {"pass": 4, "rework": 1, "blocked": 1}
    assert reviews.blocked == 1
    assert reviews.misses == 1
    # Round 1 rejected one of its four rows, round 2 none, and round 4 is the blocked Task's.
    assert reviews.rejection_rate == {1: pytest.approx(0.25), 2: 0.0, 4: 1.0}
    assert (reviews.edges_planned, reviews.edges_covered) == (3, 3)
    assert reviews.plan_types == {"planned": 1}
    assert card.trial.merge_diff == pytest.approx(-1.0)
    assert card.trial.merge_verdict == "worse", "v2 merged none of its six, v1 both of its two"


def test_the_arm_line_leaves_out_a_run_whose_first_round_is_not_measured(store):
    """A v1 run summarized before the two columns existed reads NULL, which is "not
    measured": it counts in the arm's runs and in its lease-hours, and in neither mean."""
    for i, value in enumerate([2, 4], start=1):
        run(store, f"R{i}", workflow="ralph")
        store.save_run_summary(summarize_run(f"R{i}", quality_events(not_met=value)))
    run(store, "R3", workflow="ralph")
    store.save_run_summary(summarize_run("R3", quality_events(not_met=9)))
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE run_summary SET first_round = NULL, task_plans = NULL WHERE run_id = 'R3'"
        )

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)

    v1 = card.arms[0]
    assert v1.runs == 3
    assert (v1.quality_n, v1.quality_mean) == (2, pytest.approx(3.0))
    assert v1.quality_sd == pytest.approx(math.sqrt(2.0))  # |2 - 3| and |4 - 3| over n-1
    # The 9 of the unmeasured run is not in the mean, and would have moved it to 5.0.
    assert v1.refuter_n == 2


def test_the_miss_stop_rule_reads_the_windows_task_reviews(store):
    """The experiment's third stop rule: more than one Task review in ten lost its reviewer.
    Five rounds are ten reviewers, so two misses are 20% and the rule fires."""
    for i in (1, 2):
        run(store, f"V{i}", workflow="ralph-v2")
        store.save_run_summary(
            summarize_run(
                f"V{i}",
                review_round(updates=gate_updates(task=f"T{i}", refute_served="missed")),
            )
        )
    run(store, "R1", workflow="ralph")
    run(store, "R2", workflow="ralph")

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)

    assert (card.task_reviews.reviews, card.task_reviews.misses) == (2, 2)
    assert card.trial.miss_stop is True
    assert "| stop rules | lease no, merge no, misses yes |" in scorecard.workflows_to_markdown(card)


def test_the_task_review_line_reads_the_loop(store):
    """The loop's own numbers: rejections by round, blocked Tasks, disputes, where each
    reviewer answered, the planner's edge cases against the first review's covered ones, and
    what each new stage cost on the box."""
    run(store, "V1", workflow="ralph-v2", hours=4.0)
    store.save_run_summary(
        with_stages(
            summarize_run(
                "V1",
                review_round(
                    updates=gate_updates(
                        round_=1, verdict="rework", errors=2, warnings=1, disputed=1,
                        refute_served="missed", edges=2, edges_covered=1
                    )
                )
                + review_round(
                    updates=gate_updates(
                        round_=2, verdict="blocked", errors=1, withdrawn=1,
                        review_served="fallback", edges=2, edges_covered=2
                    )
                )
                + [
                    completed(
                        "task_plan_gate",
                        {"task_plan_route": "go", "task_plan_type": "fallback",
                         "task_plan_goals": 3, "task_plan_edges": 2},
                    )
                ],
            ),
            StageStat("task_plan", "agent", 1, 480_000, 480_000),
            StageStat("task_refute", "agent", 2, 1_500_000, 1_500_000),
            StageStat("task_review", "agent", 2, 1_800_000, 1_200_000),
            StageStat("iterate_t1", "agent", 1, 1_044_000, 1_044_000),
        )
    )

    row = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW).task_reviews

    assert (row.reviews, row.verdicts, row.blocked) == (2, {"rework": 1, "blocked": 1}, 1)
    assert row.rejection_rate == {1: 1.0, 2: 1.0}
    assert (row.disputes, row.withdrawals, row.misses) == (1, 1, 1)
    assert row.refute_served == {"box": 1, "fallback": 0, "missed": 1}
    assert row.review_served == {"box": 1, "fallback": 1, "missed": 0}
    assert row.ms.p50 == pytest.approx(30.0)  # 10 + 20 minutes a round
    assert (row.edges_planned, row.edges_covered) == (2, 1)  # round 1 only: same Task, same edges
    assert row.box_minutes == {
        "task_plan": pytest.approx(8.0),
        "task_refute": pytest.approx(25.0),
        "task_review": pytest.approx(20.0),
        "iterate_t1": pytest.approx(17.4),
    }
    assert row.plan_types == {"fallback": 1}


def test_re_summarizing_a_run_replaces_its_rows(store):
    """`save_run_summary` replaces a run's summary in one transaction, so a second summary of
    the same run leaves its rows and nothing of the first one's."""
    run(store, "V1", workflow="ralph-v2")
    store.save_run_summary(summarize_run("V1", review_round() + review_round(updates=gate_updates(round_=2))))
    assert store._conn.execute("SELECT COUNT(*) AS n FROM run_task_reviews").fetchone()["n"] == 2

    store.save_run_summary(
        summarize_run(
            "V1",
            review_round(updates=gate_updates(task="T9", verdict="rework", errors=1)),
        )
    )

    rows = store._conn.execute("SELECT * FROM run_task_reviews ORDER BY seq").fetchall()
    assert [(row["seq"], row["task"], row["verdict"]) for row in rows] == [(1, "T9", "rework")]
