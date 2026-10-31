"""The precheck's holds (more-local-inference D20).

A precheck verdict may keep an issue out of dispatch: `already_done`, `contradiction`, `unclear`
and `blocked` by default, each as a `precheck-<verdict>` label the scheduler adds when the lane
records the verdict. It ships **off**, and off means no label write at all. A label already on an
issue holds it whatever the switch says, because a label a human left on is still a hold. Only
`precheck-blocked` is ever removed by the scheduler, and only when its cited blocker closes.
Nothing here closes an issue, edits its body or reorders the queue.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from fabro_scheduler.background import BackgroundLane
from fabro_scheduler.config import ConfigError, load_config
from fabro_scheduler.github import Issue
from fabro_scheduler.lease import LeaseStore
from fabro_scheduler.precheck import (
    HOLD_VERDICTS,
    Marker,
    holding_labels,
    hold_label,
    parse_marker,
    release_blocked,
    verdict_for_label,
)
from fabro_scheduler.queue import build_queue, holding_labels as queue_holding
from fabro_scheduler.store import Store

from test_background import BOXES, NOW, TRACKED_REPOS, FakeFabro, FakeHealth, TRACKED_REPOS as REPOS
from test_precheck import config, seed

JELLY = "andrewthetechie/jelly-swipe"


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def cfg(**env: str):
    return load_config(TRACKED_REPOS, env={"SCHEDULER_BACKGROUND": "on", "SCHEDULER_BOX_HEALTH": BOXES, **env})


# --- the labels ---------------------------------------------------------------------


def test_a_verdict_maps_to_one_label_and_back():
    assert hold_label("already_done") == "precheck-already-done"
    assert hold_label("blocked") == "precheck-blocked"
    assert verdict_for_label("precheck-already-done") == "already_done"
    assert verdict_for_label("precheck-unverifiable") == "unverifiable"
    assert verdict_for_label("agent") is None
    assert verdict_for_label("precheck-") is None


def test_holding_labels_finds_every_precheck_label_and_nothing_else():
    labels = frozenset({"agent", "precheck-blocked", "scout", "precheck-unclear"})
    assert holding_labels(labels) == ["precheck-blocked", "precheck-unclear"]
    assert holding_labels(frozenset({"agent", "scout"})) == []


def test_the_default_hold_set_is_the_four_that_keep_an_issue_out():
    assert set(HOLD_VERDICTS) == {"already_done", "contradiction", "unclear", "blocked"}
    assert "unverifiable" in cfg(
        SCHEDULER_PRECHECK_HOLD_VERDICTS="unverifiable"
    ).precheck_hold_verdicts, "accepted"
    assert "unverifiable" not in cfg().precheck_hold_verdicts, "but not in the default"
    assert "ready" not in cfg().precheck_hold_verdicts, "ready is the absence of a hold"
    with pytest.raises(ConfigError) as exc:
        cfg(SCHEDULER_PRECHECK_HOLD_VERDICTS="ready")
    assert "absence of a hold" in str(exc.value), "a list naming ready would empty the queue"


def test_the_switch_is_off_by_default_and_reads_on_and_off():
    assert cfg().precheck_holds is False
    assert cfg(SCHEDULER_PRECHECK_HOLDS="on").precheck_holds is True
    assert cfg(SCHEDULER_PRECHECK_HOLDS="off").precheck_holds is False
    for bad in ("yes", "1", "ONN"):
        with pytest.raises(ConfigError) as exc:
            cfg(SCHEDULER_PRECHECK_HOLDS=bad)
        assert "SCHEDULER_PRECHECK_HOLDS" in str(exc.value)
    with pytest.raises(ConfigError) as exc:
        cfg(SCHEDULER_PRECHECK_HOLD_VERDICTS="already_done,vibes")
    assert "SCHEDULER_PRECHECK_HOLD_VERDICTS" in str(exc.value)


# --- applying a hold ----------------------------------------------------------------


def _precheck_job(store: Store, number: int = 3, *, outcome: str | None = None) -> int:
    job_id = store.enqueue_background_job(
        kind="issue-precheck", repo=JELLY, target=str(number),
        inputs={"issue_number": number}, priority=2, typical_box_minutes=3, now=NOW,
    )
    store.start_background_job(job_id, run_id="R1", box="coders-a", now=NOW)
    if outcome is not None:
        store.finish_background_job(job_id, state="done", outcome=outcome, now=NOW)
    return job_id


def _lane(store: Store, config_, writes: list[tuple[str, int, str]]):
    return BackgroundLane(
        config_, store, LeaseStore(store),
        FakeFabro(runs={"R1": {"lifecycle": {"status": {"kind": "succeeded"}}}}),
        health=FakeHealth(),
        precheck_reader=lambda repo, number: Marker(
            "blocked", "abcdef012345", "f" * 40, "2026-11-01T10:00:00Z", "R1", "40"
        ),
        hold_writer=lambda repo, number, label: writes.append((repo, number, label)),
        clock=lambda: NOW,
    )


def test_a_held_verdict_adds_its_label_when_the_switch_is_on(store):
    _precheck_job(store)
    writes: list[tuple[str, int, str]] = []
    _lane(store, cfg(SCHEDULER_PRECHECK_HOLDS="on"), writes).sync(NOW)
    assert writes == [(JELLY, 3, "precheck-blocked")]


def test_off_writes_no_label_at_all(store):
    """The switch is read where the label would be written, so an off lane makes no write."""
    _precheck_job(store)
    writes: list[tuple[str, int, str]] = []
    _lane(store, cfg(), writes).sync(NOW)
    assert writes == []
    assert store.background_job(1)["outcome"].startswith("verdict=blocked"), (
        "the verdict is still recorded: shadow mode is what the calibration reads"
    )


def test_a_verdict_outside_the_set_writes_no_label(store):
    writes: list[tuple[str, int, str]] = []
    lane = _lane(store, cfg(SCHEDULER_PRECHECK_HOLDS="on",
                            SCHEDULER_PRECHECK_HOLD_VERDICTS="already_done"), writes)
    lane._read_precheck = lambda repo, number: Marker(
        "unclear", "abcdef012345", "f" * 40, "2026-11-01T10:00:00Z", "R1"
    )
    _precheck_job(store)
    lane.sync(NOW)
    assert writes == []


def test_every_verdict_in_the_set_is_applied_and_ready_never_is(store):
    """All five holdable verdicts, with `unverifiable` named explicitly: it is accepted but not
    in the default set, which is the operator's choice to make."""
    for verdict in ("already_done", "contradiction", "unclear", "blocked", "unverifiable"):
        s = Store(":memory:")
        try:
            _precheck_job(s)
            writes: list[tuple[str, int, str]] = []
            lane = _lane(
                s,
                cfg(SCHEDULER_PRECHECK_HOLDS="on",
                    SCHEDULER_PRECHECK_HOLD_VERDICTS="already_done,contradiction,unclear,"
                                                    "blocked,unverifiable"),
                writes,
            )
            lane._read_precheck = lambda repo, number, v=verdict: Marker(
                v, "abcdef012345", "f" * 40, "2026-11-01T10:00:00Z", "R1", "40"
            )
            lane.sync(NOW)
            assert writes == [(JELLY, 3, f"precheck-{verdict.replace('_', '-')}")], verdict
        finally:
            s.close()


def test_a_failed_write_does_not_fail_the_tick(store):
    from fabro_scheduler.github import GitHubError

    _precheck_job(store)
    lane = BackgroundLane(
        cfg(SCHEDULER_PRECHECK_HOLDS="on"), store, LeaseStore(store),
        FakeFabro(runs={"R1": {"lifecycle": {"status": {"kind": "succeeded"}}}}),
        health=FakeHealth(),
        precheck_reader=lambda repo, number: Marker(
            "blocked", "abcdef012345", "f" * 40, "2026-11-01T10:00:00Z", "R1", "40"
        ),
        hold_writer=lambda repo, number, label: (_ for _ in ()).throw(GitHubError("502")),
        clock=lambda: NOW,
    )
    ended = lane.sync(NOW)
    assert ended[0].state == "done"
    assert ended[0].outcome == "verdict=blocked"


# --- honouring a hold ---------------------------------------------------------------


def test_a_held_issue_is_out_of_the_queue_with_the_switch_off(store):
    seed(store, JELLY, 3)
    seed(store, JELLY, 4)
    store.upsert_issue(Issue(JELLY, 5, "held", frozenset({"agent", "precheck-already-done"}), NOW))
    config_ = cfg()  # the switch is off
    ranked = build_queue(config_.schedulable_repos(), store, NOW, config_.starvation_ceiling)
    assert [item.issue.number for item in ranked] == [3, 4]
    assert [issue.number for issue in store.held_issues()] == [5]
    assert queue_holding(store.get_issue(JELLY, 5)) == ["precheck-already-done"]


def test_every_precheck_label_holds_an_issue(store):
    for i, label in enumerate(("precheck-already-done", "precheck-contradiction",
                               "precheck-unclear", "precheck-blocked",
                               "precheck-unverifiable", "precheck-something-new")):
        store.upsert_issue(Issue(JELLY, 10 + i, "held", frozenset({"agent", label}), NOW))
    config_ = cfg(SCHEDULER_PRECHECK_HOLDS="off")
    assert build_queue(config_.schedulable_repos(), store, NOW, config_.starvation_ceiling) == []
    assert len(store.held_issues()) == 6


# --- releasing `blocked` ------------------------------------------------------------


class FakeGitHub:
    """The four GitHub calls `release_blocked` makes, and a record of its writes."""

    def __init__(self, *, held: list, markers: dict, blockers: dict, fail: set | None = None):
        self.held = held
        self.markers = markers
        self.blockers = blockers
        self.fail = fail or set()
        self.removed: list[tuple[str, int, str]] = []
        self.listed: list[str] = []

    def install(self, monkeypatch):
        import fabro_scheduler.github as gh
        import fabro_scheduler.precheck as pc

        class Tracked:
            def __init__(self, number, state="open"):
                self.number = number
                self.state = state

        monkeypatch.setattr(gh, "fetch_label_all_states",
                            lambda repo, label, token, client=None: [
                                Tracked(n) for n in (self.held.get(label, [])
                                                     if isinstance(self.held, dict) else self.held)
                            ])
        monkeypatch.setattr(pc, "read_marker",
                            lambda repo, number, token, client=None: self.markers.get(number))
        monkeypatch.setattr(gh, "fetch_issue_state",
                            lambda repo, number, token, client=None: (
                                self.blockers[str(number)], None))
        monkeypatch.setattr(gh, "remove_label",
                            lambda repo, number, label, token, client=None: (
                                self.removed.append((repo, number, label))))
        return self


def test_blocked_is_released_when_its_blocker_closes(monkeypatch):
    fake = FakeGitHub(
        held=[7],
        markers={7: Marker("blocked", "abcdef012345", "f" * 40, "2026-11-01T10:00:00Z", "R", "40")},
        blockers={"40": "closed"},
    ).install(monkeypatch)
    actions = release_blocked(JELLY, "token")
    assert fake.removed == [(JELLY, 7, "precheck-blocked")]
    assert actions == [(7, "released: blocker #40 closed")]


def test_blocked_stays_while_its_blocker_is_open(monkeypatch):
    fake = FakeGitHub(
        held=[7],
        markers={7: Marker("blocked", "abcdef012345", "f" * 40, "2026-11-01T10:00:00Z", "R", "40")},
        blockers={"40": "open"},
    ).install(monkeypatch)
    assert release_blocked(JELLY, "token") == []
    assert fake.removed == []


def test_a_marker_from_before_the_blocker_field_is_left_to_a_human(monkeypatch):
    fake = FakeGitHub(
        held=[7],
        markers={7: Marker("blocked", "abcdef012345", "f" * 40, "2026-11-01T10:00:00Z", "R")},
        blockers={},
    ).install(monkeypatch)
    assert release_blocked(JELLY, "token") == []
    assert fake.removed == []


def test_only_the_blocked_label_is_ever_removed(monkeypatch):
    """A human's `already_done` hold is theirs: nothing observable says it stopped applying."""
    fake = FakeGitHub(held=[], markers={}, blockers={}).install(monkeypatch)
    release_blocked(JELLY, "token")
    assert fake.removed == []
    import fabro_scheduler.precheck as pc
    source = open(pc.__file__).read()
    assert "remove_label(repo, issue.number, blocked" in source, "the one removal is `blocked`"
    assert "hold_label(\"already_done\")" not in source


def test_a_listing_failure_is_logged_and_retried(monkeypatch):
    import fabro_scheduler.github as gh

    def boom(*a, **k):
        raise gh.GitHubError("502")

    monkeypatch.setattr(gh, "fetch_label_all_states", boom)
    assert release_blocked(JELLY, "token") == []
