"""The ralph-v2 trial's measures (ralph-v2 11, the experiment).

End to end, in the order the numbers travel: a fixture event stream -> `summary.summarize`
-> `Store.save_run_summary` -> the arms and the trial line `scorecard.by_workflow` computes.
Each fixture is one run's stream, cut to the events the summary reads and in the shape
`GET /runs/{id}/events` returns on fabro 0.362 (2026-11-24).

The nine streams the task names are: a clean v2 run, a reworked Task, a blocked Task, a run
with one reviewer missed, a fallback (E13: a reviewer's three box tries came back empty, so
its hosted `_fb` stage answered), a mid-session failover (E17: a provider failover inside a
reviewer's session), a run that stopped before any review, a v1 run, and a v1 run from before
`first_round` and `task_plans` existed. `nine_streams()` is the one table of the eight that
have events.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from fabro_scheduler import measure, scorecard
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


def failed(node: str, *, ms: int = 0) -> dict:
    """A stage visit that ended `stage.failed`: here a timeout, the visit a check node counts.
    It carries the attempt's timing, like `stage.completed` does."""
    props: dict = {
        "status": "failed",
        "failure": {"category": "timeout", "system_actor": "timeout"},
    }
    if ms:
        props["timing"] = {"wall_time_ms": ms}
    return {"event": "stage.failed", "node_id": node, "properties": props}


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


def fallback_round(
    who: str = "refute", *, updates: dict | None = None, fail_on: str | None = None
) -> list[dict]:
    """One round in which reviewer `who` (`refute` or `review`) timed out on all three of its
    box tries, in the order `task_<who>_check` runs. This is A3's counted road to the E13
    fallback, where a timeout or a failure is a try; a session that merely ends with no result
    is re-run by `stage-end` first, and is not this stream (C4).

    Each try is one visit that ends `stage.failed`; the check counts it and routes `retry`,
    and the third routes `fallback`, where the hosted `task_<who>_fb` answers. The check's visit
    after that publishes `done` with the count it had (3): a result does not add a try. The
    other reviewer passes on its first try, so its check publishes 0. A `_fb` stage is a
    reviewer like the other three.
    """
    other = "review" if who == "refute" else "refute"
    clean = [
        started(f"task_{other}"),
        completed(f"task_{other}", ms=600_000),
        completed(
            f"task_{other}_check",
            {f"task_{other}_next": "done", f"task_{other}_tries": 0},
            ms=15_000,
        ),
    ]
    events: list[dict | None] = [started("task_review_prep"), completed("task_review_prep")]
    if who == "review":
        events += clean
    for count in (1, 2, 3):
        events += [
            started(f"task_{who}"),
            failed(f"task_{who}", ms=1_800_000),  # the box timeout, with `max_retries=0` (P8)
            completed(
                f"task_{who}_check",
                {
                    f"task_{who}_next": "fallback" if count == 3 else "retry",
                    f"task_{who}_tries": count,
                },
                ms=15_000,
            ),
        ]
    events += [
        started(f"task_{who}_fb"),
        failover(f"task_{who}_fb") if fail_on == f"task_{who}_fb" else None,
        completed(f"task_{who}_fb", ms=900_000),
        completed(
            f"task_{who}_check",
            {f"task_{who}_next": "done", f"task_{who}_tries": 3},
            ms=15_000,
        ),
    ]
    if who == "refute":
        events += clean
    served = {f"{who}_served": "fallback"}
    events.append(
        completed("task_review_gate", updates if updates is not None else gate_updates(**served))
    )
    return [event for event in events if event is not None]


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


def nine_streams() -> dict[str, tuple[str, list[dict]]]:
    """The eight fixture streams that have events, each with the workflow it is a run of, in
    the order the task names them. The ninth, a v1 run from before the two columns existed,
    is the v1 stream read back with `first_round` and `task_plans` NULL.
    """
    return {
        "clean v2 run": ("ralph-v2", clean_v2_events()),
        "reworked Task": (
            "ralph-v2",
            review_round(
                updates=gate_updates(
                    round_=1, verdict="rework", errors=2, edges=2, edges_covered=1
                )
            )
            + review_round(
                updates=gate_updates(round_=2, verdict="pass", edges=2, edges_covered=2)
            ),
        ),
        "blocked Task": (
            "ralph-v2",
            review_round(updates=gate_updates(round_=4, verdict="blocked", errors=1)),
        ),
        "one reviewer missed": (
            "ralph-v2",
            review_round(updates=gate_updates(refute_served="missed", edges=0, edges_covered=0)),
        ),
        "a fallback": ("ralph-v2", fallback_round()),
        "mid-session failover": ("ralph-v2", review_round(fail="task_refute")),
        "stopped before any review": (
            "ralph-v2",
            [started("iterate_t1"), completed("iterate_t1", ms=600_000)],
        ),
        "v1 run": ("ralph", quality_events(not_met=1)),
    }


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


@pytest.mark.parametrize("who", ["refute", "review"])
def test_a_fallback_round_keeps_its_failed_tries_the_fallback_visit_and_the_last_tries(who):
    """The E13 fallback: each of the three failed box tries is a spent visit, and so is the
    hosted `_fb` visit that answered after them. The round's `ms` is all four plus the other
    reviewer's one -- 30 + 30 + 30 + 15 + 10 minutes -- and its `tries` is the **last** count
    each check published, 3 for the reviewer that fell back (four publishes: 1, 2, 3, 3) and 0
    for the one that passed first time. The fallback is a stage of the ladder, not a provider
    failover (E17), so `failover` stays False."""
    summary = summarize_run("R1", fallback_round(who))

    served = {"refute_served": "box", "review_served": "box"}
    served[f"{who}_served"] = "fallback"
    assert summary.task_reviews == [
        TaskReview(
            task="T1", source="plan", round=1, verdict="pass",
            errors=0, warnings=0, disputed=0, withdrawn=0, dropped=0,
            edges=0, edges_covered=0,
            tries=3,
            ms=6_900_000,
            failover=False,
            **served,
        )
    ]


@pytest.mark.parametrize(
    "stage", ["task_refute", "task_refute_fb", "task_review", "task_review_fb"]
)
def test_a_failover_on_any_of_the_four_reviewers_marks_the_round(stage):
    """E17 on the Task review loop: `failover` is an `agent.route.failover` on **any** of the
    four reviewer stages since the previous gate, the two fallback stages included."""
    if stage.endswith("_fb"):
        who = "refute" if stage.startswith("task_refute") else "review"
        events = fallback_round(who, fail_on=stage)
    else:
        events = review_round(fail=stage)

    assert summarize_run("R1", events).task_reviews[0].failover is True


def test_a_submit_tool_is_matched_on_its_tail_and_only_a_submit_is_a_result():
    """The rule is the baseline reader's: a tool whose name **ends in** `submit` carries the
    stage's result. A profile that names it `review_submit` is read the same way, and another
    tool's arguments are never a result, so a stage that completed after only that leaves its
    field None."""
    events = [
        submit("standards", {"findings": findings(3)}, tool="review_submit"),
        completed("standards"),
        submit("spec", {"findings": findings(5)}, tool="inputs"),
        completed("spec"),
    ]
    summary = summarize_run("R1", events)
    assert summary.first_round == FirstRound(standards_errors=3, spec_errors=None)
    assert summary.first_round.total() is None


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


# `run_task_reviews`' columns after `run_id`, in the table's order: what a stored round must
# give back.
REVIEW_COLUMNS = (
    "seq", "task", "source", "round", "verdict", "errors", "warnings", "disputed",
    "withdrawn", "dropped", "refute_served", "review_served", "edges", "edges_covered",
    "tries", "ms", "failover",
)


@pytest.mark.parametrize("name", list(nine_streams()))
def test_every_fixture_stream_is_stored_column_for_column(store, name):
    """For each stream, the rows it stores: `save_run_summary` writes the first round, the
    plans and every Task review round exactly as the summary held them, column for column,
    so a reader of the store sees what the summary said."""
    workflow, events = nine_streams()[name]
    summary = summarize_run(name, events, workflow=workflow)
    store.save_run_summary(summary)

    stored = store._conn.execute(
        "SELECT first_round, task_plans FROM run_summary WHERE run_id = ?", (name,)
    ).fetchone()
    assert json.loads(stored["first_round"]) == asdict(summary.first_round)
    assert json.loads(stored["task_plans"]) == {
        "types": dict(summary.task_plans.types),
        "goals": summary.task_plans.goals,
        "edges": summary.task_plans.edges,
    }
    rows = store._conn.execute(
        "SELECT * FROM run_task_reviews WHERE run_id = ? ORDER BY seq", (name,)
    ).fetchall()
    assert [tuple(row[column] for column in REVIEW_COLUMNS) for row in rows] == [
        (
            seq, review.task, review.source, review.round, review.verdict, review.errors,
            review.warnings, review.disputed, review.withdrawn, review.dropped,
            review.refute_served, review.review_served, review.edges, review.edges_covered,
            review.tries, review.ms, int(review.failover),
        )
        for seq, review in enumerate(summary.task_reviews, start=1)
    ]


def test_a_row_from_before_the_columns_is_refilled_by_a_re_summarize(store):
    """The ninth stream: a v1 run's row as the collector wrote it before the two columns
    existed. NULL to every reader, and a second summary of the same events writes both."""
    workflow, events = nine_streams()["v1 run"]
    store.save_run_summary(summarize_run("R1", events, workflow=workflow))
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE run_summary SET first_round = NULL, task_plans = NULL WHERE run_id = 'R1'"
        )
        before = store._conn.execute(
            "SELECT first_round, task_plans FROM run_summary WHERE run_id = 'R1'"
        ).fetchone()
    assert (before["first_round"], before["task_plans"]) == (None, None)

    store.save_run_summary(summarize_run("R1", events, workflow=workflow))

    after = store._conn.execute(
        "SELECT first_round, task_plans FROM run_summary WHERE run_id = 'R1'"
    ).fetchone()
    assert json.loads(after["first_round"]) == {
        "refute_not_met": 1, "refute_defects": 0, "refute_tampering": 0,
        "standards_errors": 0, "spec_errors": 0,
    }
    assert json.loads(after["task_plans"]) == {"types": {}, "goals": 0, "edges": 0}


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


def test_a_merge_with_an_operator_commit_is_not_a_zero_touch_merge(store):
    """Measure 1 is `merged_by_workflow` **and** `operator_commits` 0 (the experiment). A PR
    the merge phase merged after the operator had committed is a merge for the split table and
    is not one for the trial: it stays in `runs`, and leaves both the numerator and the
    guardrail's denominator."""
    run(store, "V1", workflow="ralph-v2", pr=11, merged=True)
    run(store, "V2", workflow="ralph-v2", pr=12, merged=True)
    store.save_pr_facts(REPO, 11, fact(state="merged", merged_at="2026-11-23T05:00:00Z"))
    store.save_pr_facts(
        REPO, 12, fact(state="merged", operator=2, merged_at="2026-11-23T05:00:00Z")
    )

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)

    assert card.cards["ralph-v2"].merged_workflow == 2, "the split table counts both"
    v2 = card.arms[1]
    assert (v2.runs, v2.zero_touch, v2.merge_rate) == (2, 1, pytest.approx(0.5))
    assert v2.lease_hours_per_merge == pytest.approx(4.0)  # two 2-hour leases over the one


def test_a_run_that_ran_out_is_counted_and_a_partial_round_is_the_refuters_alone(store):
    """Two ways a run sits beside the quality mean instead of in it (the experiment).

    A run whose loop ran out reaches no hosted reviewer, the Refuter being last (E14): it is
    one of the arm's `exhausted_runs`, and in no mean. A run whose Refuter completed but whose
    `standards` and `spec` did not is out of the five-count mean and in the Refuter's three
    alone: 2 + 1 + 0 for it and 1 + 0 + 0 for the clean run, so n 2, mean 2 and sample sd
    sqrt((1 + 1) / 1).
    """
    for run_id, events in (
        ("V1", [completed("pick", {"phase": "pre", "loop_exhausted": True})]),
        (
            "V2",
            [
                submit("refute", refute_arguments(not_met=2, defects=1)),
                completed("refute", ms=600_000),
            ],
        ),
        ("V3", quality_events(not_met=1)),
    ):
        run(store, run_id, workflow="ralph-v2")
        store.save_run_summary(summarize_run(run_id, events))

    v2 = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW).arms[1]

    assert (v2.runs, v2.exhausted_runs) == (3, 1)
    assert (v2.quality_n, v2.quality_mean) == (1, pytest.approx(1.0))
    assert (v2.refuter_n, v2.refuter_mean) == (2, pytest.approx(2.0))
    assert v2.refuter_sd == pytest.approx(math.sqrt(2.0))


def test_the_fixture_runs_read_as_one_arm(store):
    """The fixture streams as one window: every v2 stream, seven runs, beside two v1 ones, so
    each one is read through the arm lines too. Only the clean run reached a hosted reviewer, so
    the quality mean is over one run; the review loop counts every round, one per fixture and
    two for the reworked Task; and the round-1 rows are where the edge cases are read."""
    for index, (run_id, (workflow, events)) in enumerate(nine_streams().items(), start=1):
        if workflow != "ralph-v2":
            continue
        run(store, run_id, workflow="ralph-v2", at=T0 + timedelta(hours=index))
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
    assert (v2.runs, v2.zero_touch) == (7, 0)
    assert v2.quality_n == 1, "only the clean run reached the hosted reviewers"
    assert v2.quality_mean == pytest.approx(7.0)
    assert v2.quality_sd is None, "one measured run has no spread"

    reviews = card.task_reviews
    assert reviews.reviews == 7, "one row per fixture round: the reworked Task has two"
    assert reviews.verdicts == {"pass": 5, "rework": 1, "blocked": 1}
    assert reviews.blocked == 1
    # One missed reviewer is one miss. A fallback answered, so it is none (E13).
    assert reviews.misses == 1
    assert reviews.refute_served == {"box": 5, "fallback": 1, "missed": 1}
    assert reviews.review_served == {"box": 6, "fallback": 1, "missed": 0}
    # Round 1 rejected one of its five rows, round 2 none, and round 4 is the blocked Task's.
    assert reviews.rejection_rate == {1: pytest.approx(0.2), 2: 0.0, 4: 1.0}
    # A round's reviewer minutes, smallest first: 25, 30 x5 and the fallback's 115. p50 is the
    # fourth of seven, 30; p90 is 30 + 0.4 x (115 - 30) = 64.
    assert (reviews.ms.p50, reviews.ms.p90) == (pytest.approx(30.0), pytest.approx(64.0))
    assert (reviews.edges_planned, reviews.edges_covered) == (3, 3)
    assert reviews.plan_types == {"planned": 1}
    assert card.trial.merge_diff == pytest.approx(-1.0)
    assert card.trial.merge_verdict == "worse", "v2 merged none of its seven, v1 both of its two"


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
    Two rounds are four reviewers, so two misses are 50% and the rule fires."""
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


@pytest.mark.parametrize(
    ("one", "both", "misses", "stops"),
    [
        pytest.param(2, 1, 4, False, id="4 misses in 20 rounds: a tenth, which is not above"),
        pytest.param(3, 1, 5, True, id="5 misses in 20 rounds: 12.5%"),
    ],
)
def test_the_miss_stop_rule_is_over_reviewers_two_to_a_round(store, one, both, misses, stops):
    """The third rule's rate is the one `GET /api/box-health` reports: a review is one reviewer,
    two to a round (step 4), so 20 rounds are 40 reviewers: 4 misses is a tenth, which is not
    above, and 5 is 12.5%. A round with one missed reviewer is one miss; a round with both is
    two (E13). `one` rounds here miss one reviewer and `both` miss both, so `one + 2 x both`
    misses.

    Divided by rounds instead, the same 4 misses read 20% and stopped the trial where C11,
    reading 10%, would not.
    """
    events: list[dict] = []
    for index in range(one):
        events += review_round(updates=gate_updates(task=f"one{index}", refute_served="missed"))
    for index in range(both):
        events += review_round(
            updates=gate_updates(
                task=f"both{index}", verdict="missed",
                refute_served="missed", review_served="missed",
            )
        )
    for index in range(20 - one - both):
        events += review_round(updates=gate_updates(task=f"clean{index}"))
    run(store, "V1", workflow="ralph-v2")
    store.save_run_summary(summarize_run("V1", events))

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)

    assert (card.task_reviews.reviews, card.task_reviews.misses) == (20, misses)
    assert card.trial.miss_stop is stops
    text = scorecard.workflows_to_markdown(card)
    assert f"| stop rules | lease no, merge no, misses {'yes' if stops else 'no'} |" in text
    # The same rows through the other reader: one rate, to the four places the endpoint rounds.
    gathered = store.box_health_rows(since=T0)
    health = measure.box_health(
        gathered["history"],
        gathered["stages"],
        gathered["failovers"],
        reviews=gathered["reviews"],
        hours=24,
        now=T0 + timedelta(days=1),
    )
    assert (health["reviews"], health["reviews_missed"], health["miss_rate"]) == (
        40, misses, round(misses / 40, 4)
    )


def arm_of(store, workflow: str, *, runs: int, merged: int, hours: float, first: int) -> None:
    """`runs` released runs of one arm, each holding its lease `hours`; the first `merged` of
    them had a PR the merge phase merged with no operator commit."""
    for index in range(runs):
        number = first + index
        run(
            store,
            f"{workflow}-{index}",
            workflow=workflow,
            hours=hours,
            pr=number,
            merged=True if index < merged else None,
        )
        if index < merged:
            store.save_pr_facts(
                REPO, number, fact(state="merged", merged_at="2026-11-23T05:00:00Z")
            )


def test_the_lease_and_merge_stop_rules_fire_past_their_lines_and_their_counts(store):
    """*Stop early*'s first two weekly checks, on hand-computed numbers.

    v1: 60 runs, all 60 merged by the run, 2 lease-hours each: 120 lease-hours, 2.0 a merge.
    v2: 60 runs, 42 merged by the run, 10 lease-hours each: 600 lease-hours, 600 / 42 = 14.29
        a merge.
    merge: 42/60 - 60/60 = -0.30, which is 30 points below (the line is 15), with 60 v2 runs
      (the count is 60): it fires. se = sqrt(0.7 x 0.3 / 60 + 1 x 0 / 60) = 0.059, and 0.30 is
      above 2 x 0.059, so the verdict is `worse` as well.
    lease: (600 / 42) / 2.0 = 7.14, above 2 (the line), with 60 v2 runs (the count is 40): it
      fires.
    """
    arm_of(store, "ralph", runs=60, merged=60, hours=2.0, first=1)
    arm_of(store, "ralph-v2", runs=60, merged=42, hours=10.0, first=1000)

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)

    v1, v2 = card.arms
    assert (v1.runs, v1.zero_touch, v1.merge_rate) == (60, 60, pytest.approx(1.0))
    assert (v2.runs, v2.zero_touch, v2.merge_rate) == (60, 42, pytest.approx(0.7))
    assert (v1.lease_hours, v2.lease_hours) == (pytest.approx(120.0), pytest.approx(600.0))
    assert v2.lease_hours_per_merge == pytest.approx(600 / 42)
    trial = card.trial
    assert trial.merge_diff == pytest.approx(-0.30)
    assert trial.merge_se == pytest.approx(math.sqrt(0.7 * 0.3 / 60))
    assert trial.merge_verdict == "worse"
    assert trial.guardrail == pytest.approx(600 / 42 / 2.0)
    assert (trial.lease_stop, trial.merge_stop, trial.miss_stop) == (True, True, False)
    text = scorecard.workflows_to_markdown(card)
    assert "| merge, v2 - v1 | -0.30 ± 0.06: worse |" in text
    assert "| guardrail, v2 / v1 | 7.14x (the trial is lost above 2.00x) |" in text
    assert "| stop rules | lease yes, merge yes, misses no |" in text


@pytest.mark.parametrize(
    ("v2", "stops", "verdict", "guardrail", "why"),
    [
        pytest.param(
            dict(runs=39, merged=39, hours=10.0),
            (False, False),
            "unclear",
            10.0,
            "the lease ratio is 10 with 39 v2 runs: one short of the 40 the rule needs",
            id="39 v2 runs",
        ),
        pytest.param(
            dict(runs=40, merged=40, hours=2.0),
            (False, False),
            "unclear",
            2.0,
            "the lease ratio is exactly 2 with 40 v2 runs: the rule is above 2",
            id="a ratio of exactly 2",
        ),
        pytest.param(
            dict(runs=59, merged=0, hours=1.0),
            (False, False),
            "worse",
            None,
            "100 points below with 59 v2 runs: one short of the 60 the rule needs",
            id="59 v2 runs",
        ),
        pytest.param(
            dict(runs=100, merged=86, hours=1.5),
            (False, False),
            "worse",
            150 / 86,
            "14 points below, one short of the 15 the rule names, and a ratio of 1.74 with"
            " 100 v2 runs: worse, and under both lines",
            id="under both lines",
        ),
    ],
)
def test_a_stop_rule_holds_below_its_line_or_short_of_its_count(
    store, v2, stops, verdict, guardrail, why
):
    """A rule fires only when its line **and** its count are met. v1 is two runs, both merged
    by the run at 1 lease-hour each, so v1's merge rate is 1.0 with no spread and its
    lease-hours a merge 1.0: an equal v2 is `unclear` (the difference is 0), a lower one
    `worse`. A `worse` verdict is not a stop: the rule has its own line."""
    arm_of(store, "ralph", runs=2, merged=2, hours=1.0, first=1)
    arm_of(store, "ralph-v2", first=1000, **v2)

    trial = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW).trial

    assert (trial.lease_stop, trial.merge_stop) == stops, why
    assert trial.merge_verdict == verdict
    assert trial.guardrail == (None if guardrail is None else pytest.approx(guardrail))


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


def test_the_box_minutes_are_per_run_of_the_whole_arm_summarized_or_not(store):
    """`per run` divides by every v2 run of the window, summarized or not, so a run released
    but not yet summarized lowers the average instead of vanishing from it."""
    run(store, "V1", workflow="ralph-v2")
    store.save_run_summary(
        with_stages(summarize_run("V1", []), StageStat("task_plan", "agent", 1, 900_000, 900_000))
    )
    run(store, "V2", workflow="ralph-v2")  # released, not summarized yet

    row = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW).task_reviews

    assert row.box_minutes["task_plan"] == pytest.approx(7.5)  # 15 minutes over two runs
    assert row.box_minutes["task_review"] == 0.0


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
