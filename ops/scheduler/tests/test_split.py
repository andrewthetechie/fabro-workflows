"""The split-parent/child promoter and closer (ADR 0015 C5), against a faked GitHub.

What matters is which label writes happen, in which order, and which never happen:
a held child promoted before its predecessor lands would be implemented against a
`main` that lacks the predecessor's work, and a marker-less or `after=0` child
must never be touched.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from fabro_scheduler.config import RepoConfig
from fabro_scheduler.inventory import InventoryPoller
from fabro_scheduler.split import close_parents, parse_marker, promote_held
from fabro_scheduler.store import Store

API = "https://api.github.com/repos/o/a"
ISSUES = f"{API}/issues"
BODY = "<!-- fabro:split-child parent=10 after=5 -->\nPart of #10."
BODY_ZERO = "<!-- fabro:split-child parent=10 after=0 -->\nFirst child."


def child(number: int = 30, labels=("agent-held", "ai-generated"), body: str = BODY):
    return {
        "number": number,
        "state": "open",
        "labels": [{"name": name} for name in labels],
        "body": body,
    }


def parent_item(number: int = 40, labels=("agent-split", "ai-generated"), body: str = ""):
    return {
        "number": number,
        "state": "open",
        "labels": [{"name": name} for name in labels],
        "body": body,
    }


def mock_labelled(label: str, *issues):
    return respx.get(ISSUES, params={"labels": label}).mock(
        return_value=httpx.Response(200, json=list(issues))
    )


def mock_issue(number: int, state: str, reason: str | None):
    return respx.get(f"{API}/issues/{number}").mock(
        return_value=httpx.Response(
            200, json={"number": number, "state": state, "state_reason": reason}
        )
    )


def sub_issue(number: int, state: str, reason: str | None):
    return {"sub_issue": {"number": number, "state": state, "state_reason": reason}}


def mock_sub_issues(number: int, *subs):
    return respx.get(f"{API}/issues/{number}/sub_issues").mock(
        return_value=httpx.Response(200, json=list(subs))
    )


def mock_writes(number: int):
    add = respx.post(f"{API}/issues/{number}/labels").mock(
        return_value=httpx.Response(200, json=[])
    )
    remove = respx.delete(url__regex=rf"{API}/issues/{number}/labels/.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    return add, remove


def added(route) -> list[str]:
    return [json.loads(call.request.content)["labels"][0] for call in route.calls]


def removed(route) -> list[str]:
    return [str(call.request.url).rsplit("/", 1)[1] for call in route.calls]


# --- the marker ------------------------------------------------------------------------


def test_parse_marker_reads_parent_and_after():
    assert parse_marker(BODY + "\n") == (10, 5)


def test_parse_marker_is_none_without_the_marker():
    assert parse_marker("a child issue someone wrote by hand") is None


# --- held-child promotion --------------------------------------------------------------


@respx.mock
def test_a_completed_predecessor_promotes_priority_then_agent_then_drops_the_hold():
    mock_labelled("agent-held", child())
    mock_issue(5, "closed", "completed")
    add, remove = mock_writes(30)

    assert promote_held("o/a", "ghp_test") == [(30, "promoted")]
    assert added(add) == ["priority", "agent"]
    assert removed(remove) == ["agent-held"]


@respx.mock
def test_a_predecessor_closed_not_planned_parks_the_child_as_stuck():
    mock_labelled("agent-held", child())
    mock_issue(5, "closed", "not_planned")
    add, remove = mock_writes(30)

    assert promote_held("o/a", "ghp_test") == [(30, "orphaned")]
    assert added(add) == ["agent-stuck"]
    assert removed(remove) == ["agent-held"]


@respx.mock
def test_an_open_predecessor_changes_nothing():
    mock_labelled("agent-held", child())
    mock_issue(5, "open", None)
    add, remove = mock_writes(30)

    assert promote_held("o/a", "ghp_test") == []
    assert add.call_count == 0 and remove.call_count == 0


@respx.mock
def test_after_zero_is_left_alone():
    mock_labelled("agent-held", child(body=BODY_ZERO))
    pred = mock_issue(5, "closed", "completed")
    add, remove = mock_writes(30)

    assert promote_held("o/a", "ghp_test") == []
    assert pred.call_count == 0 and add.call_count == 0 and remove.call_count == 0


@respx.mock
def test_no_marker_means_no_lookup_and_no_writes():
    mock_labelled("agent-held", child(body="a child written by hand"))
    pred = mock_issue(5, "closed", "completed")
    add, remove = mock_writes(30)

    assert promote_held("o/a", "ghp_test") == []
    assert pred.call_count == 0 and add.call_count == 0 and remove.call_count == 0


@respx.mock
def test_a_half_finished_promotion_only_finishes_the_cleanup():
    # `agent` was added but the hold was not removed; the child already carries
    # the promotion. It must not be labelled again, and its predecessor is not
    # even looked up.
    mock_labelled("agent-held", child(labels=("agent-held", "agent", "priority")))
    pred = mock_issue(5, "closed", "completed")
    add, remove = mock_writes(30)

    assert promote_held("o/a", "ghp_test") == [(30, "cleaned")]
    assert pred.call_count == 0 and add.call_count == 0
    assert removed(remove) == ["agent-held"]


@respx.mock
def test_a_5xx_on_one_child_skips_it_and_still_processes_the_next():
    mock_labelled(
        "agent-held",
        child(number=30, body="<!-- fabro:split-child parent=10 after=5 -->\n"),
        child(number=31, body="<!-- fabro:split-child parent=10 after=6 -->\n"),
    )
    respx.get(f"{API}/issues/5").mock(return_value=httpx.Response(502, json={"message": "bad"}))
    mock_issue(6, "closed", "completed")
    add, remove = mock_writes(31)

    assert promote_held("o/a", "ghp_test") == [(31, "promoted")]
    assert added(add) == ["priority", "agent"]
    assert removed(remove) == ["agent-held"]


# --- split-parent closing --------------------------------------------------------------


@respx.mock
def test_all_children_completed_comments_then_closes():
    mock_labelled("agent-split", parent_item())
    mock_sub_issues(40, sub_issue(41, "closed", "completed"), sub_issue(42, "closed", "completed"))
    comment = respx.post(f"{API}/issues/40/comments").mock(
        return_value=httpx.Response(201, json={})
    )
    close = respx.patch(f"{API}/issues/40").mock(return_value=httpx.Response(200, json={}))

    assert close_parents("o/a", "ghp_test") == [(40, "closed")]
    assert comment.call_count == 1 and close.call_count == 1
    assert json.loads(comment.calls[0].request.content)["body"] == "All child issues landed."
    assert json.loads(close.calls[0].request.content) == {
        "state": "closed",
        "state_reason": "completed",
    }


@respx.mock
def test_a_not_planned_child_marks_the_parent_stuck_once():
    first = mock_labelled("agent-split", parent_item(labels=("agent-split",)))
    mock_sub_issues(40, sub_issue(41, "closed", "completed"), sub_issue(42, "closed", "not_planned"))
    add, remove = mock_writes(40)
    respx.post(f"{API}/issues/40/comments").mock(return_value=httpx.Response(201, json={}))
    respx.patch(f"{API}/issues/40").mock(return_value=httpx.Response(200, json={}))

    assert close_parents("o/a", "ghp_test") == [(40, "stuck")]
    assert added(add) == ["agent-stuck"]
    assert remove.call_count == 0

    # A second pass sees the parent already wearing agent-stuck: no further writes.
    respx.get(ISSUES, params={"labels": "agent-split"}).mock(
        return_value=httpx.Response(
            200, json=[parent_item(labels=("agent-split", "agent-stuck"))]
        )
    )
    assert close_parents("o/a", "ghp_test") == []
    assert add.call_count == 1  # nothing new added on the second pass


@respx.mock
def test_an_open_child_means_the_parent_is_left_alone():
    mock_labelled("agent-split", parent_item())
    mock_sub_issues(40, sub_issue(41, "closed", "completed"), sub_issue(42, "open", None))
    comment = respx.post(f"{API}/issues/40/comments").mock(
        return_value=httpx.Response(201, json={})
    )
    close = respx.patch(f"{API}/issues/40").mock(return_value=httpx.Response(200, json={}))

    assert close_parents("o/a", "ghp_test") == []
    assert comment.call_count == 0 and close.call_count == 0


@respx.mock
def test_an_empty_sub_issue_list_is_left_alone():
    mock_labelled("agent-split", parent_item())
    mock_sub_issues(40)
    comment = respx.post(f"{API}/issues/40/comments").mock(
        return_value=httpx.Response(201, json={})
    )
    close = respx.patch(f"{API}/issues/40").mock(return_value=httpx.Response(200, json={}))

    assert close_parents("o/a", "ghp_test") == []
    assert comment.call_count == 0 and close.call_count == 0


@respx.mock
def test_a_5xx_on_the_sub_issue_list_closes_nothing():
    mock_labelled("agent-split", parent_item())
    respx.get(f"{API}/issues/40/sub_issues").mock(
        return_value=httpx.Response(502, json={"message": "bad"})
    )
    add, remove = mock_writes(40)
    comment = respx.post(f"{API}/issues/40/comments").mock(
        return_value=httpx.Response(201, json={})
    )
    close = respx.patch(f"{API}/issues/40").mock(return_value=httpx.Response(200, json={}))

    assert close_parents("o/a", "ghp_test") == []
    assert add.call_count == 0 and remove.call_count == 0
    assert comment.call_count == 0 and close.call_count == 0


# --- wiring into the inventory poll ----------------------------------------------------


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@respx.mock
def test_the_inventory_poll_runs_the_held_promoter(store):
    respx.get(ISSUES, params={"labels": "agent"}).mock(return_value=httpx.Response(304))
    respx.get(ISSUES, params={"labels": "agent-remainder"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    mock_labelled("agent-held", child())
    mock_issue(5, "closed", "completed")
    add, _ = mock_writes(30)
    respx.get(ISSUES, params={"labels": "agent-split"}).mock(
        return_value=httpx.Response(200, json=[])
    )

    InventoryPoller([RepoConfig("o/a", 0, "python")], store, "ghp_test").poll_repo(
        RepoConfig("o/a", 0, "python")
    )

    assert added(add) == ["priority", "agent"]


@respx.mock
def test_a_promotion_failure_does_not_mark_the_inventory_stale(store):
    respx.get(ISSUES, params={"labels": "agent"}).mock(return_value=httpx.Response(304))
    respx.get(ISSUES, params={"labels": "agent-remainder"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(ISSUES, params={"labels": "agent-held"}).mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    respx.get(ISSUES, params={"labels": "agent-split"}).mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )

    InventoryPoller([RepoConfig("o/a", 0, "python")], store, "ghp_test").poll_repo(
        RepoConfig("o/a", 0, "python")
    )

    state = store.fetch_state("o/a")
    assert state is not None and state.last_error is None
