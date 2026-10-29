"""The scorecard computation (issue #22) over a store seeded through its real writes."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from fabro_scheduler import scorecard
from fabro_scheduler.prfacts import PrFacts
from fabro_scheduler.store import RunOutcome, Store
from fabro_scheduler.summary import RefuteVerdict, RunSummary, StageStat, TaskValidation
from fabro_scheduler.usage import ModelUsage

REPO = "andrewthetechie/jelly-swipe"
OTHER = "andrewthetechie/writers-app"
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # a Monday
WINDOW = (T0, T0 + timedelta(days=7))


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def run(store, run_id, *, hours=2.0, at=T0 + timedelta(hours=1), repo=REPO, pr=None,
        merged=None, kind="succeeded", reason="completed", workflow="backlog"):
    store.acquire_lease(
        coder_pool="coders-a", repo=repo, issue_number=1, run_id=run_id, dispatched_at=at,
        workflow=workflow,
    )
    store.archive_and_release_lease(
        run_id,
        RunOutcome(
            kind=kind, reason=reason, finished_at=at + timedelta(hours=hours),
            pr_lookup="found" if pr else "none", pr_number=pr,
            pr_url=f"https://github.com/{repo}/pull/{pr}" if pr else None, merged=merged,
            hosted_tokens=1000, hosted_cost_usd_micros=2_500_000,
        ),
    )


def facts(*, state="open", labels=("agent-authored",), reason=None, report="complete",
          operator=0, approved=False, handoff=False, created="2026-10-05T03:00:00Z",
          merged_at=None):
    return PrFacts(
        state=state, created_at=created, merged_at=merged_at, closed_at=merged_at,
        labels=tuple(labels), additions=1, deletions=1, commits=5, operator_commits=operator,
        report=report, reason=reason, queue_approved=approved, queue_handoff=handoff,
    )


def summary(run_id, stages, *, terminal="exit_node", failovers=None, refute=(),
            task_validations=(), unchecked=0, untested=0, final_routes=(),
            post_iterations=0, loop_exhausted=False, task_sources=None):
    return RunSummary(
        run_id=run_id,
        stages={s.node_id: s for s in stages},
        failovers=Counter(failovers or {}),
        path=["a>b"],
        terminal_node=terminal,
        refute=list(refute),
        events=10,
        task_validations=list(task_validations),
        task_validation_unchecked=unchecked,
        task_validation_untested=untested,
        final_routes=list(final_routes),
        post_iterations=post_iterations,
        loop_exhausted=loop_exhausted,
        task_sources=Counter(task_sources or {}),
    )


def visit(mode, minutes, *, ok=True, reason=""):
    return TaskValidation(mode=mode, reason_class=reason, ok=ok, ms=int(minutes * 60_000))


def compute(store):
    return scorecard.compute(store.scorecard_rows(*WINDOW), *WINDOW)


def test_merge_attribution_and_held_reasons(store):
    run(store, "R1", pr=1, merged=True)  # the merge phase merged it
    run(store, "R2", pr=2, merged=False)  # queued, then the merge queue merged it
    run(store, "R3", pr=3, merged=False)  # queued, handed off, merged by hand
    run(store, "R4", pr=4, merged=False)  # architecture, held, merged by hand
    run(store, "R5", pr=5, merged=False)  # held by the refuter, still open
    run(store, "R6", pr=6, merged=False)  # facts not read yet
    run(store, "R7", pr=5, merged=False)  # a second run on PR 5: one PR, not two
    store.save_pr_facts(REPO, 1, facts(state="merged", merged_at="2026-10-05T05:00:00Z"))
    store.save_pr_facts(REPO, 2, facts(state="merged", approved=True, report="queued",
                                       merged_at="2026-10-05T04:00:00Z"))
    store.save_pr_facts(REPO, 3, facts(state="merged", approved=True, handoff=True,
                                       report="queued", reason="CI is red",
                                       merged_at="2026-10-05T13:00:00Z"))
    store.save_pr_facts(REPO, 4, facts(state="merged", labels=("architecture",),
                                       reason="This PR implements an Architecture issue (label architecture).",
                                       operator=2, merged_at="2026-10-05T23:00:00Z"))
    store.save_pr_facts(REPO, 5, facts(reason="The refuter could not confirm the work is done.",
                                       report="blocked", operator=1))

    card = compute(store)
    assert card.runs == 7
    assert card.prs == 6
    assert card.merged_workflow == 2
    assert card.merged_operator == 2
    assert card.open == 1
    assert card.unknown == 1
    assert card.architecture_prs == 1 and card.architecture_merged_workflow == 0
    assert card.other_prs == 5 and card.other_merged_workflow == 2
    assert card.held == {"CI": 1, "architecture": 1, "refuter": 1, "not read yet": 1}
    assert card.operator_commit_prs == 2 and card.operator_commits == 3
    assert card.merge_rate == pytest.approx(2 / 6)
    assert card.box_hours == pytest.approx(14.0)
    assert card.box_hours_per_merge == pytest.approx(7.0)
    assert card.time_to_merge_hours["architecture"].count == 1
    assert card.time_to_merge_hours["architecture"].p50 == pytest.approx(20.0)
    other = card.time_to_merge_hours["other"]
    assert other.count == 3
    assert other.p50 == pytest.approx(2.0)  # 1, 2 and 10 hours
    assert other.p90 == pytest.approx(8.4)
    assert card.hosted_tokens == 7000
    assert card.hosted_cost_usd == pytest.approx(17.5)


@pytest.mark.parametrize(
    ("reason", "bucket"),
    [
        ("This PR implements an Architecture issue (label architecture). ADR 0012 requires a human to merge it.", "architecture"),
        ("The PR carries the do-not-merge label, so a human merges it.", "do-not-merge"),
        ("The refuter wrote an invalid result twice", "refuter"),
        ("Diff hygiene blocked: test_deleted", "diff hygiene"),
        ("The risk rating is missing or above 3; a human must merge this PR.", "risk"),
        ("A commit on this PR was not authored by the automation; a human must merge it.", "authorship"),
        ("There are unfixed findings; a human must merge this PR.", "unfixed findings"),
        ("Reviewers have requested changes on this PR.", "review threads"),
        ("Auto-merge is off for this repository.", "auto-merge off"),
        ("git push --force-with-lease was rejected: the PR branch moved", "branch moved"),
        ("CI failed twice after fixes", "CI"),
        ("The merge was blocked, but no reason was recorded.", "unspecified"),
        ("unspecified", "unspecified"),
        ("Something new entirely", "other"),
    ],
)
def test_reason_buckets(store, reason, bucket):
    run(store, "R1", pr=1, merged=False)
    store.save_pr_facts(REPO, 1, facts(reason=reason))
    assert compute(store).held == {bucket: 1}


def test_held_without_a_reason(store):
    run(store, "R1", pr=1)
    run(store, "R2", pr=2)
    run(store, "R3", pr=3)
    store.save_pr_facts(REPO, 1, facts(report=None))
    store.save_pr_facts(REPO, 2, facts(report="queued", approved=True))
    store.save_pr_facts(REPO, 3, facts(report="complete"))
    assert compute(store).held == {"no report": 1, "queued": 1, "no reason recorded": 1}


def test_lease_share_splits_box_and_idle_time(store):
    run(store, "R1", hours=10.0)
    hour = 3_600_000
    store.save_run_summary(summary("R1", [
        StageStat("coder", "agent", 2, 3 * hour, 3 * hour),
        StageStat("improve", "agent", 2, 2 * hour, 2 * hour),
        StageStat("spec", "agent", 1, hour // 2, 0),
        StageStat("validate", "command", 2, hour, 0),
        StageStat("review_merge.standards", "agent", 1, hour // 2, 0),
        StageStat("review_merge.review_fix", "agent", 1, hour, hour // 2),
        StageStat("human_rescue", "human", 1, hour, 0),
        StageStat("prep", "command", 1, hour // 2, 0),
    ]))
    run(store, "R2", hours=5.0)  # not summarized: excluded from the share

    card = compute(store)
    assert card.runs_summarized == 1
    assert card.lease_hours_summarized == pytest.approx(10.0)
    assert card.lease_hours == pytest.approx({
        "writing code": 3.5, "improve": 2.0, "hosted agents": 0.5, "validate": 1.0,
        "merge phase": 1.0, "human gates": 1.0, "other commands": 0.5, "overhead": 0.5,
    })
    assert sum(card.lease_share().values()) == pytest.approx(1.0)


def test_timeouts_failovers_and_runs_with_no_pr(store):
    run(store, "R1", hours=3.0)
    run(store, "R2", hours=1.0, kind="failed", reason="cancelled")
    run(store, "R3", hours=1.0)
    run(store, "R4", pr=9)
    store.save_run_summary(summary(
        "R1",
        [StageStat("improve", "agent", 3, 1, 1, timeouts=2)],
        terminal="human_rescue",
        failovers={("spec", "kimi/kimi-k3", "zai/glm-5.3", "access_denied"): 3},
    ))
    store.save_run_summary(summary(
        "R3",
        [StageStat("improve", "agent", 1, 1, 1, timeouts=1)],
        terminal="human_rescue",
        failovers={("spec", "kimi/kimi-k3", "zai/glm-5.3", "access_denied"): 1},
    ))
    store.save_summary_failure("R2", "missing", "404")

    card = compute(store)
    assert card.timeouts == {"improve": 3}
    assert card.failovers == [
        scorecard.FailoverLine("spec", "kimi/kimi-k3", "zai/glm-5.3", "access_denied", 2)
    ]
    assert card.no_pr == [
        scorecard.NoPrLine("human_rescue", 2, pytest.approx(4.0)),
        scorecard.NoPrLine("not summarized", 1, pytest.approx(1.0)),
    ]
    assert card.endings == {"no PR": 2, "cancelled": 1, "PR opened": 1}


def test_refuter_line(store):
    verdicts = {
        "R1": [RefuteVerdict("pass", False)],
        "R2": [RefuteVerdict("fail", True), RefuteVerdict("pass", False)],
        "R3": [RefuteVerdict("fail", True), RefuteVerdict("fail", True)],
        "R4": [RefuteVerdict("fail", False)],
        "R5": [RefuteVerdict("fail", None)],
        "R6": [RefuteVerdict("invalid", False)],
        "R7": [],
        "R8": [RefuteVerdict("fail", False), RefuteVerdict("pass", False)],  # a defect-only fix turn (#36)
    }
    for run_id, refute in verdicts.items():
        run(store, run_id)
        store.save_run_summary(summary(run_id, [], refute=refute))
    assert compute(store).refute == scorecard.RefuteLine(
        runs=7, first_pass=1, first_fail_not_met=2, first_fail_defects=0,
        first_fail_unverifiable_only=0, first_fail_other_unknown=2,
        first_fail_unknown=1, first_invalid=1, fix_turns=3, fix_cleared=2,
        first_ci_credited=0, first_ci_credited_runs=0,
    )


def test_refuter_line_three_buckets():
    # #40: a not_met-False fail is unverifiable-only when unverifiable_only is true,
    # defects when it is false, and other (unknown) when it is absent.
    line = scorecard._refute_line([
        '[{"verdict": "fail", "not_met": false, "unverifiable_only": true}]',
        '[{"verdict": "fail", "not_met": false, "unverifiable_only": false}]',
        '[{"verdict": "fail", "not_met": false}]',
    ])
    assert line.first_fail_unverifiable_only == 1
    assert line.first_fail_defects == 1
    assert line.first_fail_other_unknown == 1
    assert line.first_fail_unknown == 0


def test_refuter_line_old_row_lands_in_other():
    # A row stored before #40 carried only {verdict, not_met}; its fail with not_met
    # False has no unverifiable_only, so it lands in "other", never unverifiable-only.
    line = scorecard._refute_line(['[{"verdict": "fail", "not_met": false}]'])
    assert line.first_fail_other_unknown == 1
    assert line.first_fail_unverifiable_only == 0
    assert line.first_fail_defects == 0


def test_refuter_line_ci_credited_sums():
    # #40: first_ci_credited sums the per-run ci_credited counts, over first verdicts
    # that carry one (None or absent is skipped).
    line = scorecard._refute_line([
        '[{"verdict": "pass", "not_met": false, "unverifiable_only": false, "ci_credited": 3}]',
        '[{"verdict": "fail", "not_met": false, "unverifiable_only": false, "ci_credited": 2}]',
        '[{"verdict": "fail", "not_met": false, "unverifiable_only": true}]',
        '[{"verdict": "fail", "not_met": false, "unverifiable_only": false}]',
    ])
    assert line.first_ci_credited == 5
    assert line.first_ci_credited_runs == 2


def test_window_is_by_dispatch_time_and_repos_are_listed(store):
    run(store, "IN", at=T0 + timedelta(days=1), repo=OTHER, hours=4.0, pr=3, merged=True)
    run(store, "IN2", at=T0 + timedelta(days=2), hours=1.0)
    run(store, "BEFORE", at=T0 - timedelta(hours=1))
    run(store, "AFTER", at=T0 + timedelta(days=7))
    card = compute(store)
    assert card.runs == 2
    assert card.repos == [
        scorecard.RepoLine(OTHER, 1, pytest.approx(4.0), 1, 1),
        scorecard.RepoLine(REPO, 1, pytest.approx(1.0), 0, 0),
    ]


def test_empty_window(store):
    card = compute(store)
    assert card.runs == 0 and card.prs == 0
    assert card.merge_rate is None and card.box_hours_per_merge is None
    assert all(v == 0 for v in card.lease_share().values())
    assert "| PRs opened / merged by the workflow | 0 / 0 (n/a) |" in scorecard.to_markdown(card)


def test_markdown_carries_every_metric_row(store):
    run(store, "R1", pr=1, merged=True)
    store.save_pr_facts(REPO, 1, facts(state="merged", merged_at="2026-10-05T05:00:00Z"))
    text = scorecard.to_markdown(compute(store))
    for heading in (
        "PRs opened / merged by the workflow",
        "Non-architecture merged by the workflow",
        "Held PRs, by reason",
        "PRs with operator commits before merge",
        "Box-hours per merged PR",
        "Lease share: writing code / improve / idle",
        "Idle by phase",
        "Timeouts by node",
        "Provider failovers by node and reason",
        "Runs with no PR, by terminal node",
        "Refuter first verdicts",
        "Open to merge",
    ):
        assert f"| {heading}" in text
    assert "not summarized yet" in text  # R1 has no summary
    assert text.startswith("Factory scorecard, 2026-10-05 to 2026-10-11 (1 runs, 0 summarized)")


def test_json_is_serializable(store):
    import json

    run(store, "R1", pr=1, merged=True)
    out = scorecard.to_json(compute(store))
    json.dumps(out)
    assert out["merge_rate"] == 1.0
    assert out["start"] == T0.isoformat()


def test_week_windows_are_iso_weeks_oldest_first():
    now = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # a Wednesday
    windows = scorecard.week_windows(now, 3)
    assert [start.date().isoformat() for start, _ in windows] == [
        "2026-09-28", "2026-10-05", "2026-10-12",
    ]
    assert all(end - start == timedelta(days=7) for start, end in windows)


def test_tokens_by_model(store):
    run(store, "R1")
    run(store, "R2")
    run(store, "R3")  # summarized, model usage not read yet
    run(store, "OUT", at=T0 - timedelta(days=1))  # outside the window
    for run_id in ("R1", "R2", "R3", "OUT"):
        store.save_run_summary(summary(run_id, [StageStat("coder", "agent", visits=1)]))
    glm = lambda i, o, c: ModelUsage("zai", "glm-5.3", 2, i, o, 0, 0, c)
    box = ModelUsage("box-a", "deepseek", 3, 9000, 500, 400, 100, None)
    store.save_model_usage("R1", [box, glm(1000, 100, 2_000_000)])
    store.save_model_usage("R2", [glm(500, 50, 1_000_000)])
    store.save_model_usage("OUT", [glm(10**9, 0, 9)])
    card = compute(store)
    assert card.models_runs == 2
    by_name = {m.name: m for m in card.models}
    assert [m.name for m in card.models] == ["box-a/deepseek", "zai/glm-5.3"]
    assert by_name["box-a/deepseek"].local and not by_name["zai/glm-5.3"].local
    g = by_name["zai/glm-5.3"]
    assert (g.runs, g.stages, g.input_tokens, g.output_tokens, g.total_tokens) == (2, 4, 1500, 150, 1650)
    assert g.cost_usd == 3.0 and by_name["box-a/deepseek"].cost_usd is None
    assert abs(sum(m.share for m in card.models) - 1.0) < 1e-9
    text = scorecard.to_markdown(card)
    assert "Tokens by model (2 of 3 runs read)" in text
    assert "| zai/glm-5.3 | hosted | 2 | 4 | 1.50k | 150 | 0 | 1.65k | 14% | $3.00 |" in text
    assert "| box-a/deepseek | local | 1 | 3 | 9.00k | 500 | 500 | 10.0k | 86% | n/a |" in text


def test_token_formatting():
    assert [scorecard.tokens(n) for n in (0, 950, 12_345, 4_560_000, 1_200_000_000)] == [
        "0", "950", "12.3k", "4.56M", "1.20B"]


# --- task validation and final validation (ADR 0025, C9) -----------------------------


def test_task_validation_row_and_red_final_rate(store):
    run(store, "R1")
    run(store, "R2")
    run(store, "R3")  # not summarized: not counted anywhere
    store.save_run_summary(summary(
        "R1",
        [StageStat("validate", "command", 2, 0, 0), StageStat("open_pr_prep", "command", 1, 0, 0)],
        task_validations=[visit("scoped", 3.0), visit("scoped", 5.0, ok=False)],
        unchecked=4,
        final_routes=["pr"],
    ))
    store.save_run_summary(summary(
        "R2",
        [StageStat("validate", "command", 3, 0, 0), StageStat("open_pr_prep", "command", 2, 0, 0)],
        task_validations=[
            visit("full", 20.0, ok=False, reason="unowned_test"),
            visit("legacy", 24.0),
            visit("timeout", 30.0, ok=False),
        ],
        final_routes=["fix", "pr"],  # open_pr_prep was visited twice: the first counts
    ))
    card = compute(store)
    row = card.task_validation
    assert row.visits == 5
    assert row.by_mode == {"scoped": 2, "full": 1, "legacy": 1, "timeout": 1}
    assert row.mode_minutes["scoped"].p50 == pytest.approx(4.0)
    assert row.mode_minutes["full"].p50 == pytest.approx(20.0)
    assert row.all_minutes.p50 == pytest.approx(20.0)  # 3, 5, 20, 24, 30 minutes
    assert row.full_reasons == {"unowned_test": 1}
    assert row.red == {"scoped": 1, "full": 1, "timeout": 1}
    assert row.unchecked == 4  # 4 over 2 scoped visits
    final = card.final_validation
    assert final.reached == 2
    assert final.routes == {"pr": 1, "fix": 1}
    assert final.red_rate == pytest.approx(0.5)
    text = scorecard.to_markdown(card)
    assert "| Task validation: visits by mode" in text
    assert "| Task validation: full visits by reason | unowned_test 1 |" in text
    assert "| Task validation: unchecked files | 4 (2.0 per scoped visit) |" in text
    assert "| Final validation: verdicts / first route | 2 / pr 1, fix 1 (red 50%) |" in text


def test_task_validation_row_is_empty_without_summaries(store):
    run(store, "R1")
    card = compute(store)
    assert card.task_validation.visits == 0
    assert card.task_validation.all_minutes.p50 is None
    assert card.final_validation.reached == 0
    assert card.final_validation.red_rate is None
    text = scorecard.to_markdown(card)
    assert "| Task validation: visits by mode (p50/p90 min) | none; all 0 (n/a/n/a min) |" in text
    assert "| Task validation: red visits by mode | none |" in text


def test_repo_filter_narrows_every_series(store):
    run(store, "R1", repo=REPO, pr=1)
    run(store, "R2", repo=OTHER, pr=2)
    store.save_run_summary(summary(
        "R1", [StageStat("validate", "command", 1, 0, 0)],
        task_validations=[visit("scoped", 3.0)], final_routes=["pr"],
    ))
    store.save_run_summary(summary(
        "R2", [StageStat("validate", "command", 1, 0, 0)],
        task_validations=[visit("full", 30.0, ok=False, reason="no_config")],
        final_routes=["draft"],
    ))
    store.save_model_usage("R1", [ModelUsage("zai", "glm-5.3", 1, 1, 1, 0, 0, None)])
    store.save_model_usage("R2", [ModelUsage("box-a", "deepseek", 1, 5, 5, 0, 0, None)])
    card = scorecard.compute(store.scorecard_rows(*WINDOW), *WINDOW, repo=OTHER)
    assert card.runs == 1
    assert [line.repo for line in card.repos] == [OTHER]
    assert [m.name for m in card.models] == ["box-a/deepseek"]
    assert card.task_validation.by_mode == {"full": 1}
    assert card.task_validation.full_reasons == {"no_config": 1}
    assert card.final_validation.routes == {"draft": 1}


def test_a_run_with_no_recorded_route_is_not_a_red_final(store):
    # Before #35 (2026-10-13 22:13Z) open_pr_prep published no final_route. Such a run
    # reached Final validation but says nothing about its verdict, so it must not count
    # as red: a baseline window before #35 read "red 100%" (live, 2026-10-14).
    run(store, "R1")
    run(store, "R2")
    store.save_run_summary(summary(
        "R1", [StageStat("open_pr_prep", "command", 1, 0, 0)], final_routes=["pr"],
    ))
    store.save_run_summary(summary(
        "R2", [StageStat("open_pr_prep", "command", 1, 0, 0)], final_routes=[],
    ))
    final = compute(store).final_validation
    assert final.reached == 1
    assert final.unrouted == 1
    assert final.red_rate == pytest.approx(0.0)
    text = scorecard.to_markdown(compute(store))
    assert "| Final validation: verdicts / first route | 1 / pr 1 (red 0%); 1 more with no route recorded |" in text


def test_untested_files_are_counted_per_scoped_visit(store):
    run(store, "R1")
    store.save_run_summary(summary(
        "R1", [StageStat("validate", "command", 2, 0, 0)],
        task_validations=[visit("scoped", 3.0), visit("scoped", 4.0)],
        untested=3,
    ))
    card = compute(store)
    assert card.task_validation.untested == 3
    assert "| Task validation: untested files | 3 (1.5 per scoped visit) |" in scorecard.to_markdown(card)


# --- the trial's two arms (ADR 0029 C12) -------------------------------------------


def stage(node, visits=1, handler="command"):
    return StageStat(node, handler, visits=visits, wall_ms=60_000 * visits)


def test_a_window_splits_into_one_card_per_workflow_and_a_strata_table(store):
    run(store, "B1", pr=1, merged=True)
    run(store, "B2", repo=OTHER, pr=2, merged=False)
    run(store, "B3", merged=False)  # no PR
    run(store, "R1", pr=3, merged=True, workflow="ralph")
    run(store, "R2", repo=OTHER, pr=4, merged=False, workflow="ralph")
    store.save_pr_facts(REPO, 1, facts(state="merged", merged_at="2026-10-05T05:00:00Z"))
    store.save_pr_facts(OTHER, 2, facts())
    store.save_pr_facts(REPO, 3, facts(state="merged", merged_at="2026-10-05T05:00:00Z"))
    store.save_pr_facts(OTHER, 4, facts())

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)

    assert set(card.cards) == {"backlog", "ralph"}
    assert card.cards["backlog"].runs == 3
    assert card.cards["ralph"].runs == 2
    assert card.cards["backlog"].merged_workflow == 1
    assert card.cards["ralph"].merged_workflow == 1
    # A row per repo x pool, per arm, with each arm's own counts.
    assert {(row.repo, row.workflow): row.runs for row in card.strata} == {
        (REPO, "backlog"): 2,
        (OTHER, "backlog"): 1,
        (REPO, "ralph"): 1,
        (OTHER, "ralph"): 1,
    }
    cell = next(
        row for row in card.strata if row.repo == REPO and row.workflow == "backlog"
    )
    assert (cell.prs, cell.merged) == (1, 1)
    assert cell.box_hours == pytest.approx(4.0)
    assert cell.box_hours_per_merge == pytest.approx(4.0)


def test_the_doctors_follow_up_reviews_are_not_scorecard_runs(store):
    """F7 of the Phase 2 review: a doctored PR's review is archived in `run_history`, and
    counting it would give the PR a second "PR opened" and its merge to the issue arms."""
    run(store, "B1", pr=7, merged=False)
    store.acquire_lease(
        coder_pool="coders-b", repo=REPO, issue_number=7, run_id="REV1",
        dispatched_at=T0 + timedelta(hours=5), workflow="pr-review", target_kind="pr",
    )
    store.archive_and_release_lease(
        "REV1",
        RunOutcome(kind="succeeded", reason="completed",
                   finished_at=T0 + timedelta(hours=6), pr_lookup="found", pr_number=7,
                   merged=True),
    )
    store.save_pr_facts(REPO, 7, facts(state="merged", merged_at="2026-10-05T06:00:00Z"))

    rows = store.scorecard_rows(*WINDOW)
    assert [row["run_id"] for row in rows.history] == ["B1"]
    card = scorecard.by_workflow(rows, *WINDOW)
    assert set(card.cards) == {"backlog", "ralph"}
    assert card.cards["backlog"].runs == 1
    assert card.cards["backlog"].merged_workflow == 0

    reviews = store.doctor_review_rows(*WINDOW)
    assert [(row["run_id"], row["issue_number"], row["pr_state"]) for row in reviews] == [
        ("REV1", 7, "merged")
    ]


def test_a_window_with_no_ralph_run_keeps_the_column_at_zero(store):
    run(store, "B1", pr=1, merged=True)
    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)
    assert card.cards["ralph"].runs == 0
    assert card.ralph.runs == 0
    assert card.ralph.iterations.count == 0


def test_the_ralph_line_reads_its_own_stage_rows(store):
    """Iterations, Refute visits, sources, exhaustion, the post-PR budget and the
    queued hand-offs, from the arm's own rows (C12)."""
    run(store, "R1", pr=1, merged=True, workflow="ralph")
    run(store, "R2", merged=False, workflow="ralph")  # ran out, no PR
    store.save_run_summary(
        summary(
            "R1",
            [stage("iterate_t1", 3), stage("iterate_t2", 2), stage("refute", 2)],
            post_iterations=1,
            task_sources={"plan": 4, "ci": 1},
        )
    )
    store.save_run_summary(
        summary(
            "R2",
            [stage("iterate_t1", 1)],
            terminal="report_queued",
            loop_exhausted=True,
            post_iterations=1,
            task_sources={"plan": 2, "refute": 2},
        )
    )
    store.save_pr_facts(REPO, 1, facts(state="merged", merged_at="2026-10-05T05:00:00Z"))

    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)
    r = card.ralph

    assert r.runs == 2
    assert r.iterations.count == 2
    assert (r.iterations.p50, r.iterations.p90) == (3.0, pytest.approx(4.6))
    assert r.refute_visits.count == 2
    assert r.refute_visits.p50 == 1.0
    assert r.tasks_by_source == {"ci": 1, "plan": 6, "refute": 2}
    assert r.runs_exhausted == 1
    assert r.post_iterations == 2
    assert r.queued_handoffs == 1


def test_the_workflows_markdown_names_both_arms_and_the_strata(store):
    run(store, "R1", pr=1, merged=True, workflow="ralph")
    run(store, "B1", pr=2, merged=True)
    card = scorecard.by_workflow(store.scorecard_rows(*WINDOW), *WINDOW)
    text = scorecard.workflows_to_markdown(card)
    assert "|  | backlog | ralph |" in text
    assert "## Strata: repo x coder pool" in text
    assert "## ralph" in text
    payload = scorecard.workflows_to_json(card)
    assert set(payload["workflows"]) == {"backlog", "ralph"}
    assert payload["ralph"]["runs"] == 1
