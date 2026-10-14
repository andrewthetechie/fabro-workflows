"""`/metrics`, `GET /api/scorecard` and `/api/scorecard/weeks` over `TestClient`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fabro_scheduler.app import _window, build_app
from fabro_scheduler.config import load_config
from fabro_scheduler.store import RunOutcome, Store

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"
REPO = "andrewthetechie/jelly-swipe"


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@pytest.fixture
def client(store) -> TestClient:
    return TestClient(build_app(load_config(TRACKED_REPOS, env={}), store))


def seed(store, run_id, at, *, pr=None, merged=None):
    store.acquire_lease(coder_pool="coders-a", repo=REPO, issue_number=1, run_id=run_id,
                        dispatched_at=at)
    store.archive_and_release_lease(
        run_id,
        RunOutcome(kind="succeeded", finished_at=at + timedelta(hours=2),
                   pr_lookup="found" if pr else "none", pr_number=pr, merged=merged),
    )


def test_metrics_page_renders_with_the_three_tab_nav(client, store):
    seed(store, "R1", datetime.now(UTC) - timedelta(days=1), pr=5, merged=True)
    page = client.get("/metrics")
    assert page.status_code == 200
    html = page.text
    assert "Factory metrics" in html
    assert 'href="/"' in html and 'href="/history"' in html
    assert ">Metrics</span>" in html  # the current tab is text, not a link
    assert html.count("<svg") == 5  # four trends and the endings; the rest need summaries
    assert "Share of PRs merged by the workflow" in html
    assert "not running" in html  # no fabro client, so no collector


def test_queue_and_history_link_to_metrics(client):
    assert 'href="/metrics"' in client.get("/history").text


def test_metrics_page_with_nothing_stored(client):
    page = client.get("/metrics?from=2020-01-01&to=2020-01-07&weeks=2")
    assert page.status_code == 200
    assert "Nothing in this window." in page.text
    assert "No runs in this window." in page.text


def test_api_scorecard_json_for_a_window(client, store):
    seed(store, "IN", datetime(2026, 10, 6, 9, tzinfo=UTC), pr=5, merged=True)
    seed(store, "OUT", datetime(2026, 10, 9, 9, tzinfo=UTC))
    body = client.get("/api/scorecard?from=2026-10-06&to=2026-10-08").json()
    assert body["start"] == "2026-10-06T00:00:00+00:00"
    assert body["end"] == "2026-10-09T00:00:00+00:00"
    assert body["runs"] == 1
    assert body["prs"] == 1 and body["merged_workflow"] == 1
    assert body["merge_rate"] == 1.0


def test_api_scorecard_markdown(client, store):
    seed(store, "IN", datetime(2026, 10, 6, 9, tzinfo=UTC), pr=5, merged=True)
    response = client.get("/api/scorecard?from=2026-10-06&to=2026-10-06&format=md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("Factory scorecard, 2026-10-06 to 2026-10-06 (1 runs")
    assert "| PRs opened / merged by the workflow | 1 / 1 (100%) |" in response.text


def test_api_scorecard_arms(client):
    """The plan trial (#25): both arms, `plan` first, as JSON and as the side-by-side table."""
    body = client.get("/api/scorecard/arms?from=2026-10-06&to=2026-10-08").json()
    assert [arm["arm"] for arm in body] == ["plan", "improve"]
    assert body[0]["line"]["runs"] == 0
    response = client.get("/api/scorecard/arms?from=2026-10-06&to=2026-10-08&format=md")
    assert response.headers["content-type"].startswith("text/markdown")
    assert "| Metric | plan (hosted glm-5.3) | improve (coder box) |" in response.text


def test_api_scorecard_weeks(client):
    weeks = client.get("/api/scorecard/weeks?weeks=3").json()
    assert len(weeks) == 3
    starts = [datetime.fromisoformat(w["start"]) for w in weeks]
    assert all(s.weekday() == 0 for s in starts)
    assert starts == sorted(starts)
    assert len(client.get("/api/scorecard/weeks?weeks=bogus").json()) == 12


def test_window_parsing_never_raises():
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = _window(None, None, None)
    assert end == today + timedelta(days=1) and end - start == timedelta(days=7)
    start, end = _window("2026-10-01", "2026-10-03", None)
    assert (start.day, end.day) == (1, 4)
    start, end = _window(None, "2026-10-03", "14")
    assert end - start == timedelta(days=14)
    for junk in (("nope", "nope", "x"), ("2026-10-05", "2026-10-01", None), (None, None, "-3")):
        start, end = _window(*junk)
        assert start < end
