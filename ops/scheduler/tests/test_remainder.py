"""The remainder promoter (ADR 0011 D6), against a faked GitHub.

What matters is which label writes happen, in which order, and which never happen:
a promotion that re-adds `agent` to an issue already on a box would dispatch it
twice, and a marker-less issue must never be touched.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from fabro_scheduler.config import RepoConfig
from fabro_scheduler.inventory import InventoryPoller
from fabro_scheduler.remainder import parse_marker, promote_remainders
from fabro_scheduler.store import Store

API = "https://api.github.com/repos/o/a"
ISSUES = f"{API}/issues"
MARKER = "<!-- fabro:remainder parent=1239 pr=1250 -->"


def held(number: int = 12, labels=("agent-remainder", "ai-generated"), body: str = MARKER):
    return {
        "number": number,
        "state": "open",
        "labels": [{"name": name} for name in labels],
        "body": body,
    }


def mock_held(*issues):
    return respx.get(ISSUES, params={"labels": "agent-remainder"}).mock(
        return_value=httpx.Response(200, json=list(issues))
    )


def mock_pull(state: str, merged: bool):
    return respx.get(f"{API}/pulls/1250").mock(
        return_value=httpx.Response(
            200,
            json={"number": 1250, "state": state, "merged_at": "2026-09-24T12:00:00Z" if merged else None},
        )
    )


def mock_writes():
    add = respx.post(f"{API}/issues/12/labels").mock(return_value=httpx.Response(200, json=[]))
    remove = respx.delete(url__regex=rf"{API}/issues/12/labels/.*").mock(
        return_value=httpx.Response(200, json=[])
    )
    return add, remove


def added(route) -> list[str]:
    import json

    return [json.loads(call.request.content)["labels"][0] for call in route.calls]


def removed(route) -> list[str]:
    return [str(call.request.url).rsplit("/", 1)[1] for call in route.calls]


# --- the marker ------------------------------------------------------------------------


def test_parse_marker_reads_parent_and_pr():
    assert parse_marker(MARKER + "\nThis issue continues #1239.") == (1239, 1250)


def test_parse_marker_is_none_without_the_marker():
    assert parse_marker("Remainder of #1239, see PR #1250") is None


# --- promotion -------------------------------------------------------------------------


@respx.mock
def test_a_merged_parent_promotes_priority_then_agent_then_drops_the_hold():
    mock_held(held())
    mock_pull("closed", merged=True)
    add, remove = mock_writes()

    assert promote_remainders("o/a", "ghp_test") == [(12, "promoted")]
    assert added(add) == ["priority", "agent"]
    assert removed(remove) == ["agent-remainder"]


@respx.mock
def test_an_open_parent_changes_nothing():
    mock_held(held())
    mock_pull("open", merged=False)
    add, remove = mock_writes()

    assert promote_remainders("o/a", "ghp_test") == []
    assert add.call_count == 0 and remove.call_count == 0


@respx.mock
def test_a_parent_closed_unmerged_parks_the_issue_as_stuck():
    mock_held(held())
    mock_pull("closed", merged=False)
    add, remove = mock_writes()

    assert promote_remainders("o/a", "ghp_test") == [(12, "orphaned")]
    assert added(add) == ["agent-stuck"]
    assert removed(remove) == ["agent-remainder"]


@respx.mock
def test_no_marker_means_no_pr_lookup_and_no_writes():
    mock_held(held(body="a remainder someone wrote by hand"))
    pull = mock_pull("closed", merged=True)
    add, remove = mock_writes()

    assert promote_remainders("o/a", "ghp_test") == []
    assert pull.call_count == 0 and add.call_count == 0 and remove.call_count == 0


@respx.mock
def test_a_half_finished_promotion_only_finishes_the_cleanup():
    # `agent` was added but the hold was not removed; the issue may already be on a
    # box. It must not be labelled again, and its PR is not even looked up.
    mock_held(held(labels=("agent-remainder", "agent-in-progress", "priority")))
    pull = mock_pull("closed", merged=True)
    add, remove = mock_writes()

    assert promote_remainders("o/a", "ghp_test") == [(12, "cleaned")]
    assert pull.call_count == 0 and add.call_count == 0
    assert removed(remove) == ["agent-remainder"]


@respx.mock
def test_a_failed_pr_lookup_skips_that_issue_without_raising():
    mock_held(held())
    respx.get(f"{API}/pulls/1250").mock(return_value=httpx.Response(502, json={"message": "bad"}))
    add, remove = mock_writes()

    assert promote_remainders("o/a", "ghp_test") == []
    assert add.call_count == 0 and remove.call_count == 0


# --- wiring into the inventory poll ----------------------------------------------------


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@respx.mock
def test_the_inventory_poll_runs_the_promoter(store):
    respx.get(ISSUES, params={"labels": "agent"}).mock(return_value=httpx.Response(304))
    mock_held(held())
    mock_pull("closed", merged=True)
    add, _ = mock_writes()

    InventoryPoller([RepoConfig("o/a", 0, "python")], store, "ghp_test").poll_repo(
        RepoConfig("o/a", 0, "python")
    )

    assert added(add) == ["priority", "agent"]


@respx.mock
def test_a_promoter_failure_does_not_mark_the_inventory_stale(store):
    respx.get(ISSUES, params={"labels": "agent"}).mock(return_value=httpx.Response(304))
    respx.get(ISSUES, params={"labels": "agent-remainder"}).mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )

    InventoryPoller([RepoConfig("o/a", 0, "python")], store, "ghp_test").poll_repo(
        RepoConfig("o/a", 0, "python")
    )

    state = store.fetch_state("o/a")
    assert state is not None and state.last_error is None
