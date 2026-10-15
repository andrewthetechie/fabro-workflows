"""The weekly retro's failure classes (issue #33, ADR 0026 C1/C2).

Pure code over rows seeded through the store's real writes, exactly as
`tests/test_scorecard.py` does: the fixtures below are copied from there, so the
two modules agree about what a window's rows look like.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

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
