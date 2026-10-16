"""The weekly retro's failure classes (issue #33, ADR 0026 C1/C2).

Pure code over rows seeded through the store's real writes, exactly as
`tests/test_scorecard.py` does: the fixtures below are copied from there, so the
two modules agree about what a window's rows look like.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest

from fabro_scheduler import retro
from fabro_scheduler.prfacts import PrFacts
from fabro_scheduler.store import RunOutcome, Store
from fabro_scheduler.summary import RunSummary, StageStat

REPO = "andrewthetechie/jelly-swipe"
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # a Monday
WINDOW = (T0, T0 + timedelta(days=7))


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def run(store, run_id, *, hours=2.0, at=T0 + timedelta(hours=1), repo=REPO, pr=None,
        merged=None, kind="succeeded", reason="completed", category=None, pr_lookup=None):
    store.acquire_lease(
        coder_pool="coders-a", repo=repo, issue_number=1, run_id=run_id, dispatched_at=at
    )
    store.archive_and_release_lease(
        run_id,
        RunOutcome(
            kind=kind, reason=reason, category=category,
            finished_at=at + timedelta(hours=hours),
            pr_lookup=pr_lookup if pr_lookup is not None else ("found" if pr else "none"),
            pr_number=pr,
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


def summary(run_id, stages, *, terminal="exit_node", failovers=None):
    return RunSummary(
        run_id=run_id,
        stages={s.node_id: s for s in stages},
        failovers=Counter(failovers or {}),
        path=["a>b"],
        terminal_node=terminal,
        refute=[],
        events=10,
    )


def classify(store) -> retro.Classified:
    """`retro.classify` for the window, with every key checked against its grammar."""
    result = retro.classify(store.scorecard_rows(*WINDOW))
    for key in result.classes:
        assert retro.KEY_PATTERN.match(key), key
    return result


def stat(key, *, runs=1, boxes=0.0, kind="timeout") -> retro.ClassStat:
    """A `ClassStat` with `runs` runs whose leases total `boxes` box-hours."""
    each = int(boxes * 3_600_000 / runs) if runs else 0
    refs = tuple(retro.RunRef(f"R{i}", "failed", each) for i in range(runs))
    return retro.ClassStat(key=key, kind=kind, runs=refs, events=runs)


def test_slug():
    assert retro.slug("diff hygiene") == "diff-hygiene"
    assert retro.slug("kimi/kimi-k3") == "kimi/kimi-k3"
    assert retro.slug("  ") == "unknown"
    assert retro.slug("CI") == "ci"


def test_ending_classes(store):
    run(store, "DET", kind="failed", reason="workflow_error", category="deterministic")
    run(store, "NOPR")
    run(store, "CLOSE")
    run(store, "DEMOTE")
    run(store, "CANCEL", kind="failed", reason="cancelled")
    run(store, "TERM", kind="failed", reason="terminated")
    run(store, "LOST", kind="lost", reason="fabro 404")
    run(store, "LOOKUP", pr_lookup="failed")
    store.save_run_summary(summary("DET", [], terminal="improve"))
    store.save_run_summary(summary("NOPR", [], terminal="mark_stuck"))
    store.save_run_summary(summary("CLOSE", [], terminal="close_noop"))
    store.save_run_summary(summary("DEMOTE", [], terminal="demote_lane"))
    store.save_run_summary(summary("CANCEL", [], terminal="improve"))
    store.save_run_summary(summary("TERM", [], terminal="improve"))
    store.save_summary_failure("LOST", "missing", "404")
    store.save_run_summary(summary("LOOKUP", [], terminal="improve"))

    classified = classify(store)

    assert set(classified.classes) == {
        "ending:improve:deterministic",
        "ending:mark_stuck:no-pr",
        "ending:improve:terminated",
        "ending:unknown:lost",
    }
    assert classified.unsummarized == 0
    assert classified.classes["ending:unknown:lost"].runs[0].run_id == "LOST"


def test_held_classes(store):
    run(store, "REF", pr=1, merged=False)
    run(store, "ARCH", pr=2, merged=False)
    run(store, "QUEUED", pr=3, merged=False)
    run(store, "MERGED", pr=4, merged=True)
    run(store, "TWO-A", pr=5, merged=False)
    run(store, "TWO-B", pr=5, merged=False)
    for run_id in ("REF", "ARCH", "QUEUED", "MERGED", "TWO-A", "TWO-B"):
        store.save_run_summary(summary(run_id, []))
    store.save_pr_facts(
        REPO, 1, facts(reason="The refuter could not confirm the work is done.", report="blocked")
    )
    store.save_pr_facts(
        REPO, 2, facts(labels=("architecture",),
                       reason="This PR implements an Architecture issue (label architecture).")
    )
    store.save_pr_facts(REPO, 3, facts(report="queued", approved=True))
    store.save_pr_facts(REPO, 4, facts(state="merged", merged_at="2026-10-05T05:00:00Z"))
    store.save_pr_facts(REPO, 5, facts(reason="Something new entirely"))

    classified = classify(store)

    assert set(classified.classes) == {"held:refuter", "held:other"}
    refuter = classified.classes["held:refuter"]
    assert [r.run_id for r in refuter.runs] == ["REF"]
    assert refuter.events == 1
    other = classified.classes["held:other"]
    assert [r.run_id for r in other.runs] == ["TWO-A", "TWO-B"]
    assert other.events == 1  # one PR, two runs
    assert other.samples == ("Something new entirely",)


def test_timeout_and_failover_classes(store):
    run(store, "R1", hours=3.0)
    run(store, "R2", hours=1.0)
    store.save_run_summary(summary(
        "R1",
        [StageStat("improve", "agent", 3, 1, 1, timeouts=2)],
        failovers={("spec", "kimi/kimi-k3", "zai/glm-5.3", "access_denied"): 3},
    ))
    store.save_run_summary(summary(
        "R2",
        [StageStat("improve", "agent", 1, 1, 1, timeouts=1)],
        failovers={("spec", "kimi/kimi-k3", "zai/glm-5.3", "access_denied"): 1},
    ))

    classified = classify(store)

    timeout = classified.classes["timeout:improve"]
    assert timeout.kind == "timeout"
    assert timeout.events == 3
    assert [r.run_id for r in timeout.runs] == ["R1", "R2"]  # longest lease first
    failover = classified.classes["failover:spec:kimi/kimi-k3:access_denied"]
    assert failover.kind == "failover"
    assert failover.events == 4
    assert [r.run_id for r in failover.runs] == ["R1", "R2"]


def test_an_unsummarized_run_is_in_no_class(store):
    run(store, "SUM", pr=1, merged=False)
    run(store, "NOSUM", kind="failed", reason="workflow_error", category="deterministic")
    run(store, "ERR")
    store.save_run_summary(summary("SUM", []))
    store.save_summary_failure("ERR", "error", "boom")
    store.save_pr_facts(
        REPO, 1, facts(reason="The refuter could not confirm the work is done.")
    )

    classified = classify(store)

    assert classified.unsummarized == 2
    assert set(classified.classes) == {"held:refuter"}


def test_one_run_in_two_classes_counts_its_full_lease_in_each(store):
    # ADR 0026 D12: box-hours are per class and never added across classes, so the
    # same five leased hours appear in both the timeout class and the held class.
    run(store, "R1", hours=5.0, pr=1, merged=False)
    store.save_run_summary(summary("R1", [StageStat("improve", "agent", 1, 0, 0, timeouts=1)]))
    store.save_pr_facts(
        REPO, 1, facts(reason="The refuter could not confirm the work is done.")
    )

    classified = classify(store)

    assert classified.classes["timeout:improve"].box_hours == pytest.approx(5.0)
    assert classified.classes["held:refuter"].box_hours == pytest.approx(5.0)


def test_over_threshold():
    assert retro.over_threshold(
        stat("timeout:a", runs=3, boxes=0.5), min_runs=3, min_box_hours=4.0
    )
    assert retro.over_threshold(
        stat("timeout:a", runs=2, boxes=4.0), min_runs=3, min_box_hours=4.0
    )
    assert not retro.over_threshold(
        stat("timeout:a", runs=2, boxes=3.9), min_runs=3, min_box_hours=4.0
    )


def test_ranked_orders_by_box_hours_then_key():
    a = stat("timeout:a", runs=1, boxes=2.0)
    b = stat("timeout:b", runs=1, boxes=2.0)
    biggest = stat("timeout:c", runs=1, boxes=3.0)
    assert [s.key for s in retro.ranked([a, b, biggest])] == [
        "timeout:c",
        "timeout:a",
        "timeout:b",
    ]


# --- the plan, the guard and the text (task 03, C3-C7) ----------------------------

from dataclasses import replace as _replace  # noqa: E402

from fabro_scheduler import scorecard  # noqa: E402
from fabro_scheduler.github import TrackedIssue  # noqa: E402
from fabro_scheduler.store import ScorecardRows  # noqa: E402

WEEK = date(2026, 10, 5)
# The four configured repositories, from ops/scheduler/repos.toml.
REPOS = (
    "andrewthetechie/womens-fantasy-sports",
    "andrewthetechie/writers-app",
    "andrewthetechie/lawncare-saas",
    "andrewthetechie/jelly-swipe",
)


def tracked(number, *, state="open", state_reason=None, body="", closed_at=None):
    return TrackedIssue(
        number=number,
        state=state,
        state_reason=state_reason,
        closed_at=closed_at,
        html_url=f"https://github.com/andrewthetechie/fabro-workflows/issues/{number}",
        body=body,
    )


def marked(key, **kwargs):
    return tracked(body=f"<!-- fabro:retro-class {key} -->", **kwargs)


def cls(*stats):
    return retro.Classified(classes={s.key: s for s in stats}, unsummarized=0)


def a_file(key, *, runs=6, boxes=9.5, kind="timeout", samples=()):
    return retro.Action(
        kind="file",
        key=key,
        stat=stat(key, runs=runs, boxes=boxes, kind=kind),
        issue=None,
        title=f"t-{key}",
        body=f"b-{key}",
    )


def a_comment(key, issue):
    return retro.Action(
        kind="comment", key=key, stat=stat(key, runs=3, boxes=2.0),
        issue=issue, title=None, body=f"b-{key}",
    )


def test_known_classes_maps_every_marker_highest_number_first():
    issues = [
        marked("timeout:improve", number=7),
        marked("held:ci", number=19, state="closed", state_reason="completed",
               closed_at="2026-10-10T00:00:00Z"),
        marked("held:ci", number=41, state="closed", state_reason="not_planned"),
        tracked(99, body="no marker here"),
    ]
    found = retro.known_classes(issues)
    assert set(found) == {"timeout:improve", "held:ci"}
    assert [issue.number for issue in found["held:ci"]] == [41, 19]
    assert found["timeout:improve"][0].number == 7


def test_known_classes_reads_two_markers_on_one_issue():
    issue = tracked(5, body="<!-- fabro:retro-class timeout:improve -->\n"
                            "<!-- fabro:retro-class held:diff-hygiene -->")
    found = retro.known_classes([issue])
    assert found["timeout:improve"] == [issue]
    assert found["held:diff-hygiene"] == [issue]


def test_guard_matches_and_allows():
    assert retro.guard("see andrewthetechie/lawncare-saas", REPOS)
    assert retro.guard("LAWNCARE-SAAS", REPOS)
    assert retro.guard("the lawncare-saas repository", REPOS)
    assert retro.guard("https://github.com/andrewthetechie/writers-app/pull/3", REPOS)
    assert retro.guard("a cross-repository reference wfs#2080", REPOS)


@pytest.mark.parametrize(
    "text",
    [
        "#41",
        "(#41)",
        "see #41 for the details",
        "https://github.com/andrewthetechie/fabro-workflows/issues/41",
        "lawncare-saasy",
        "`#41`",
    ],
)
def test_guard_does_not_match_this_repository_or_near_misses(text):
    assert retro.guard(text, REPOS) is None


def test_safe_sample_rewrites_hashes_and_mentions_and_cuts_long_text():
    assert retro.safe_sample("closes #1326 now") == "closes issue 1326 now"
    assert retro.safe_sample("thanks @andrew") == "thanks @\u200bandrew"
    long = "word " * 80
    safe = retro.safe_sample(long)
    assert len(safe) <= 161
    assert safe.endswith("word…")
    assert long.startswith(safe[:-1])
    # One token longer than the limit has no space to cut at: a hard cut.
    assert len(retro.safe_sample("x" * 300)) == 161


def test_safe_sample_keeps_a_cross_repository_reference_for_the_guard():
    # Rewritten to `wfsissue 2080` it would pass the guard and publish `wfs`.
    safe = retro.safe_sample("see wfs#2080 and (#12)")
    assert safe == "see wfs#2080 and (issue 12)"
    assert retro.guard(safe, REPOS)


def test_safe_sample_never_halves_a_repository_name():
    reason = "a" * 150 + " lawncare-saas is the blocker"
    safe = retro.safe_sample(reason)
    assert "lawncare" not in safe
    assert retro.guard(safe, REPOS) is None


def test_issue_body_starts_with_a_marker_that_reads_the_key_back():
    for key in ("held:diff-hygiene", "timeout:improve",
                "failover:spec:kimi/kimi-k3:access_denied", "ending:unknown:lost"):
        stat = retro.ClassStat(
            key=key, kind=key.split(":")[0],
            runs=(retro.RunRef("R1", "failed", 3_600_000),), events=1,
        )
        body = retro.issue_body(stat, None, WEEK)
        assert body.splitlines()[0] == f"<!-- fabro:retro-class {key} -->"
        assert retro.MARKER.findall(body) == [key]


def test_issue_body_previous_week_column_is_zero_and_names_no_repository():
    stat = retro.ClassStat(
        key="held:other", kind="held",
        runs=(retro.RunRef("R1", "PR opened", 3_600_000),), events=1,
        samples=("the reason mentions no repository",),
    )
    body = retro.issue_body(stat, None, WEEK)
    assert "| Runs | 1 | 0 |" in body
    assert "| Events | 1 | 0 |" in body
    assert "| Box-hours | 1.0 | 0.0 |" in body
    assert "**Sample reasons:**" in body
    assert retro.guard(body, REPOS) is None
    for repo in REPOS:
        assert repo not in body
        assert repo.split("/")[1] not in body


def test_a_regression_body_names_the_closed_issue_and_its_date():
    stat = retro.ClassStat(
        key="timeout:improve", kind="timeout",
        runs=(retro.RunRef("R1", "failed", 3_600_000),), events=1,
    )
    closed = tracked(19, state="closed", state_reason="completed",
                     closed_at="2026-10-10T00:00:00Z")
    body = retro.issue_body(stat, None, WEEK, regression=closed)
    assert "Regression of #19, closed as completed on 2026-10-10." in body


def test_a_regression_body_without_a_close_date_says_so():
    stat = retro.ClassStat(
        key="timeout:improve", kind="timeout",
        runs=(retro.RunRef("R1", "failed", 3_600_000),), events=1,
    )
    body = retro.issue_body(stat, None, WEEK, regression=tracked(19, state="closed"))
    assert "Regression of #19, closed as completed on an unknown date." in body


def test_plan_comments_on_the_open_issue_even_with_a_closed_one_present():
    one = stat("timeout:improve", runs=4, boxes=4.0)
    known = retro.known_classes([
        marked("timeout:improve", number=19, state="closed", state_reason="completed"),
        marked("timeout:improve", number=41),
    ])
    actions = retro.plan(
        cls(one), cls(), known, week_start=WEEK, repos=REPOS,
        min_runs=3, min_box_hours=4.0, cap=3,
    )
    assert [a.kind for a in actions] == ["comment"]
    assert actions[0].issue == 41


def test_plan_files_a_regression_of_a_completed_issue():
    one = stat("timeout:improve", runs=4, boxes=4.0)
    known = retro.known_classes([
        marked("timeout:improve", number=19, state="closed", state_reason="completed",
               closed_at="2026-10-02T00:00:00Z"),
    ])
    actions = retro.plan(cls(one), cls(), known, week_start=WEEK, repos=REPOS,
                         min_runs=3, min_box_hours=4.0, cap=3)
    assert [a.kind for a in actions] == ["regression"]
    assert actions[0].issue == 19
    assert actions[0].title.startswith("retro (regression of #19): ")


@pytest.mark.parametrize("closed_at", ["2026-10-05T00:00:00Z", "2026-10-10T18:00:00Z",
                                       "2026-10-12T09:00:00Z"])
def test_plan_files_no_regression_of_an_issue_fixed_inside_the_week(closed_at):
    # The week's runs include the ones from before the fix; they are no recurrence.
    one = stat("timeout:improve", runs=4, boxes=4.0)
    known = retro.known_classes([
        marked("timeout:improve", number=19, state="closed", state_reason="completed",
               closed_at=closed_at),
    ])
    actions = retro.plan(cls(one), cls(), known, week_start=WEEK, repos=REPOS,
                         min_runs=3, min_box_hours=4.0, cap=3)
    assert [a.kind for a in actions] == ["recent"]
    assert actions[0].issue == 19
    assert actions[0].body is None


@pytest.mark.parametrize("reason", ["not_planned", "duplicate"])
def test_plan_suppresses_a_rejected_class(reason):
    one = stat("timeout:improve", runs=4, boxes=4.0)
    known = retro.known_classes([
        marked("timeout:improve", number=19, state="closed", state_reason=reason),
    ])
    actions = retro.plan(cls(one), cls(), known, week_start=WEEK, repos=REPOS,
                         min_runs=3, min_box_hours=4.0, cap=3)
    assert [a.kind for a in actions] == ["suppressed"]
    assert actions[0].body is None


def test_plan_files_a_class_nothing_knows():
    one = stat("timeout:improve", runs=4, boxes=4.0)
    actions = retro.plan(cls(one), cls(), {}, week_start=WEEK, repos=REPOS,
                         min_runs=3, min_box_hours=4.0, cap=3)
    assert [a.kind for a in actions] == ["file"]
    assert actions[0].issue is None
    assert actions[0].body is not None
    assert actions[0].body.splitlines()[0] == "<!-- fabro:retro-class timeout:improve -->"


def test_plan_caps_files_by_box_hours():
    stats = [stat(f"timeout:n{i}", runs=4, boxes=float(i)) for i in range(1, 6)]
    actions = retro.plan(cls(*stats), cls(), {}, week_start=WEEK, repos=REPOS,
                         min_runs=3, min_box_hours=0.0, cap=3)
    assert [a.kind for a in actions] == ["file", "file", "file", "capped", "capped"]
    assert [a.key for a in actions] == [
        "timeout:n5", "timeout:n4", "timeout:n3", "timeout:n2", "timeout:n1",
    ]


def test_plan_ignores_a_class_under_the_threshold():
    under = stat("timeout:improve", runs=2, boxes=3.9)
    actions = retro.plan(cls(under), cls(), {}, week_start=WEEK, repos=REPOS,
                         min_runs=3, min_box_hours=4.0, cap=3)
    assert actions == []


def test_a_withheld_class_gives_its_cap_slot_to_the_next():
    # cap 1: the biggest class names a configured repository in its sample, so its
    # write is withheld and the second class gets the week's one new-issue slot.
    first = retro.ClassStat(
        key="held:other", kind="held",
        runs=(retro.RunRef("R1", "PR opened", 10 * 3_600_000),), events=1,
        samples=("blocked until jelly-swipe lands",),
    )
    second = stat("timeout:improve", runs=4, boxes=5.0)
    actions = retro.plan(cls(first, second), cls(), {}, week_start=WEEK, repos=REPOS,
                         min_runs=3, min_box_hours=4.0, cap=1)
    assert [a.kind for a in actions] == ["withheld", "file"]
    assert "jelly-swipe" in actions[0].why
    assert actions[1].key == "timeout:improve"


# --- the Discord message (C6) ------------------------------------------------------


def card(**over) -> scorecard.Scorecard:
    empty = scorecard.compute(
        ScorecardRows([], [], [], [], [], []), T0, T0 + timedelta(days=7)
    )
    return _replace(empty, **over)


def test_discord_text_dry_run_prefix_and_would_wording():
    text = retro.discord_text(
        card(), card(), [a_file("timeout:improve")], week_start=WEEK, dry_run=True,
        still_leased=0, unsummarized=0,
    )
    assert text.startswith(
        "[DRY RUN] Factory retro, week of 2026-10-05 to 2026-10-11 (backlog runs only)"
    )
    assert "Would file: timeout:improve (6 runs, 9.5 box-h)" in text
    assert "Filed:" not in text


def test_discord_text_real_run_shows_the_issue_number():
    text = retro.discord_text(
        card(), card(), [a_file("timeout:improve")], week_start=WEEK, dry_run=False,
        still_leased=0, unsummarized=0, issued={0: 41},
    )
    assert "Filed: #41 timeout:improve (6 runs, 9.5 box-h)" in text
    assert not text.startswith("[DRY RUN]")


def test_discord_text_regression_line_names_both_issues_and_the_key():
    action = retro.Action(
        "regression", "held:unspecified", stat("held:unspecified", runs=3, boxes=2.0),
        issue=19, title="t", body="b",
    )
    real = retro.discord_text(
        card(), card(), [action], week_start=WEEK, dry_run=False, still_leased=0,
        unsummarized=0, issued={0: 44},
    )
    assert "Regression: #44 of #19 held:unspecified (3 runs, 2.0 box-h)" in real
    dry = retro.discord_text(
        card(), card(), [action], week_start=WEEK, dry_run=True, still_leased=0,
        unsummarized=0,
    )
    assert "Would file regression: held:unspecified of #19 (3 runs, 2.0 box-h)" in dry


def test_discord_text_signed_deltas():
    current = card(runs=45, prs=10, merged_workflow=3, box_hours_per_merge=6.1)
    previous = card(runs=48, prs=8, merged_workflow=2, box_hours_per_merge=6.9)
    text = retro.discord_text(
        current, previous, [], week_start=WEEK, dry_run=False, still_leased=0, unsummarized=0
    )
    assert "Runs 45 (-3)" in text
    assert "PRs 10 (+2)" in text
    assert "merged by workflow 3 (30%, +5pp)" in text
    assert "box-h per merge 6.1 (-0.8)" in text


def test_discord_text_n_a_when_a_side_is_missing():
    text = retro.discord_text(
        card(), card(), [], week_start=WEEK, dry_run=False, still_leased=0, unsummarized=0
    )
    assert "merged by workflow 0 (n/a, n/a)" in text
    assert "box-h per merge n/a (n/a)" in text


def test_discord_text_leaves_out_an_empty_list():
    text = retro.discord_text(
        card(), card(), [a_comment("timeout:improve", 41)], week_start=WEEK, dry_run=False,
        still_leased=0, unsummarized=0,
    )
    assert "Commented: #41 timeout:improve" in text
    assert "Filed:" not in text
    assert "Regression:" not in text
    assert "Over threshold, not filed" not in text
    assert "Suppressed" not in text


def test_discord_text_shortens_a_long_list_to_fit():
    actions = [a_file(f"timeout:node{i}") for i in range(200)]
    text = retro.discord_text(
        card(), card(), actions, week_start=WEEK, dry_run=False, still_leased=0,
        unsummarized=0,
    )
    assert len(text) <= 2000
    assert re.search(r"…and \d+ more", text)


def test_discord_text_reports_a_failure_and_the_writes_not_made():
    actions = [a_file("timeout:improve"), a_file("held:ci")]
    text = retro.discord_text(
        card(), card(), actions, week_start=WEEK, dry_run=False, still_leased=0,
        unsummarized=0, failure="HTTP 500", done={0}, issued={0: 41},
    )
    assert text.splitlines()[0] == "retro failed: HTTP 500"
    assert "Filed: #41 timeout:improve" in text
    assert "Not written: held:ci" in text


def test_discord_text_counts_the_buckets_and_what_was_not_counted():
    current = card(
        runs=10,
        held={"architecture": 9, "refuter": 5, "CI": 3, "unspecified": 2, "risk": 1,
              "other": 1},
        timeouts={"improve": 4, "review_merge.review_fix": 2},
        failovers=[scorecard.FailoverLine("spec", "a/b", "c/d", "quota", 12)],
    )
    text = retro.discord_text(
        current, card(), [], week_start=WEEK, dry_run=False, still_leased=2,
        unsummarized=1, failover_runs=12,
    )
    assert "Held: refuter 5, CI 3, unspecified 2, risk 1, other 1" in text
    assert "architecture 9" not in text
    assert "Timeouts: improve 4, review_merge.review_fix 2" in text
    assert "Failovers: 12 runs" in text
    assert (
        "Not counted: 2 runs still leased, 1 not summarized. "
        "Box-hours are per class; never add them." in text
    )


def test_discord_text_failovers_come_from_the_distinct_run_count_not_the_lines():
    # One run on two failover lines is one run; the card's lines cannot say so.
    current = card(failovers=[
        scorecard.FailoverLine("spec", "a/b", "c/d", "quota", 1),
        scorecard.FailoverLine("coder", "a/b", "c/d", "quota", 1),
    ])
    text = retro.discord_text(
        current, card(), [], week_start=WEEK, dry_run=False, still_leased=0,
        unsummarized=0, failover_runs=1,
    )
    assert "Failovers: 1 runs" in text


def test_discord_text_lists_a_class_fixed_inside_the_week():
    action = retro.Action("recent", "timeout:improve", stat("timeout:improve", runs=4,
                          boxes=4.0), issue=19, title=None, body=None)
    text = retro.discord_text(
        card(), card(), [action], week_start=WEEK, dry_run=False, still_leased=0,
        unsummarized=0,
    )
    assert "Closed as completed during the week, not re-filed: #19 timeout:improve" in text


def test_discord_text_shortens_a_long_not_written_list_instead_of_failing():
    # The writes are made before this message is built; it must never raise.
    actions = [a_comment(f"timeout:node{i:03d}", 100 + i) for i in range(300)]
    text = retro.discord_text(
        card(), card(), actions, week_start=WEEK, dry_run=False, still_leased=0,
        unsummarized=0, failure="HTTP 500", done=set(),
    )
    assert len(text) <= 2000
    assert text.splitlines()[0] == "retro failed: HTTP 500"
    assert re.search(r"Not written: .*…and \d+ more", text)
