"""The scorecard collector's bookkeeping, and the event pager it streams through."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from fabro_scheduler.collector import RETRY_AFTER, SETTLE, ScorecardCollector
from fabro_scheduler.fabro import FabroError, iter_events
from fabro_scheduler.github import GitHubError
from fabro_scheduler.prfacts import PrFacts
from fabro_scheduler.store import SUMMARY_MAX_ATTEMPTS, RunOutcome, Store

REPO = "andrewthetechie/jelly-swipe"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
FABRO_API = "http://fabro.test/api/v1"


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def released(store, run_id, *, finished=NOW - timedelta(hours=1), pr=None):
    store.acquire_lease(
        coder_pool="coders-a", repo=REPO, issue_number=1, run_id=run_id,
        dispatched_at=finished - timedelta(hours=1),
    )
    store.archive_and_release_lease(
        run_id,
        RunOutcome(kind="succeeded", finished_at=finished,
                   pr_lookup="found" if pr else "none", pr_number=pr),
    )


USAGE = {"by_model": [
    {"model": {"provider": "zai", "model_id": "glm-5.3"}, "stages": 3,
     "usage": {"tokens": {"input": 900, "output": 80, "reasoning": 20, "cache_read": 0,
                          "cache_write": 0}, "cost": {"usd_micros": 1500, "source": "catalog"}}},
    {"model": {"provider": "box-a", "model_id": "deepseek"}, "stages": 2,
     "usage": {"tokens": {"input": 5000, "output": 400, "reasoning": 0, "cache_read": 100,
                          "cache_write": 0}}},
]}


class FakeFabro:
    def __init__(self, fail=None, usage_fail=None, usage=None):
        self.fail = fail or {}  # run_id -> status code
        self.usage_fail = usage_fail or {}  # run_id -> status code, for run_usage
        self.usage = USAGE if usage is None else usage
        self.streamed = []
        self.usage_read = []

    def run_usage(self, run_id):
        self.usage_read.append(run_id)
        if run_id in self.usage_fail:
            raise FabroError("boom", status_code=self.usage_fail[run_id])
        return self.usage

    def summary_stages(self, run_id):
        if run_id in self.fail:
            raise FabroError("boom", status_code=self.fail[run_id])
        return [{"node_id": "coder", "handler": "agent", "wall_time_ms": 1000,
                 "provider_used": {"model": "coders-a"}}]

    def iter_events(self, run_id):
        self.streamed.append(run_id)
        yield {"event": "stage.started", "node_id": "coder"}


def facts(state="open"):
    return PrFacts(state=state, created_at=None, merged_at=None, closed_at=None, labels=(),
                   additions=0, deletions=0, commits=0, operator_commits=0, report=None,
                   reason=None, queue_approved=False, queue_handoff=False)


def collector(store, fabro, *, reader=None, runs=2, prs=10, now=NOW):
    return ScorecardCollector(
        store, fabro, "", "andrews-ai-agent", runs_per_tick=runs, prs_per_tick=prs,
        pr_reader=reader or (lambda repo, n: facts()), now=lambda: now,
    )


def summary_row(store, run_id):
    return store._conn.execute("SELECT * FROM run_summary WHERE run_id = ?", (run_id,)).fetchone()


def test_summarizes_newest_first_a_few_per_tick(store):
    for i, hours in enumerate((5, 3, 1)):
        released(store, f"R{i}", finished=NOW - timedelta(hours=hours))
    fabro = FakeFabro()
    report = collector(store, fabro, runs=2).tick()
    assert report.summarized == 2
    assert fabro.streamed == ["R2", "R1"]
    assert summary_row(store, "R2")["terminal_node"] == "coder"
    stages = store._conn.execute("SELECT * FROM run_stage_stats WHERE run_id = 'R2'").fetchall()
    assert [(s["node_id"], s["box_ms"]) for s in stages] == [("coder", 1000)]
    assert collector(store, fabro).tick().summarized == 1
    assert collector(store, fabro).tick().summarized == 0


def test_a_run_waits_out_the_settle_time(store):
    released(store, "FRESH", finished=NOW - SETTLE + timedelta(seconds=10))
    assert collector(store, FakeFabro()).tick().summarized == 0


def test_a_404_is_missing_and_never_retried(store):
    released(store, "GONE")
    fabro = FakeFabro(fail={"GONE": 404})
    assert collector(store, fabro).tick().missing == 1
    assert summary_row(store, "GONE")["status"] == "missing"
    later = NOW + RETRY_AFTER * 10
    assert collector(store, fabro, now=later).tick().missing == 0


def test_an_error_is_retried_after_the_backoff_up_to_the_cap(store):
    released(store, "FLAKY")
    fabro = FakeFabro(fail={"FLAKY": 500})
    now = NOW
    assert collector(store, fabro, now=now).tick().failed == 1
    assert collector(store, fabro, now=now + timedelta(minutes=5)).tick().failed == 0
    for _ in range(SUMMARY_MAX_ATTEMPTS - 1):
        now += RETRY_AFTER + timedelta(minutes=1)
        assert collector(store, fabro, now=now).tick().failed == 1
    assert summary_row(store, "FLAKY")["attempts"] == SUMMARY_MAX_ATTEMPTS
    now += RETRY_AFTER + timedelta(minutes=1)
    assert collector(store, fabro, now=now).tick().failed == 0


def test_a_retried_error_that_succeeds_becomes_ok(store):
    released(store, "FLAKY")
    fabro = FakeFabro(fail={"FLAKY": 502})
    collector(store, fabro).tick()
    fabro.fail.clear()
    collector(store, fabro, now=NOW + RETRY_AFTER + timedelta(minutes=1)).tick()
    assert summary_row(store, "FLAKY")["status"] == "ok"
    assert summary_row(store, "FLAKY")["error"] is None


def test_prs_are_read_until_final(store):
    released(store, "R1", pr=11)
    released(store, "R2", pr=12)
    states = {11: "open", 12: "merged"}
    reads = []

    def reader(repo, number):
        reads.append(number)
        return facts(states[number])

    assert collector(store, FakeFabro(), reader=reader).tick().prs == 2
    # Within the refresh interval nothing is read again.
    assert collector(store, FakeFabro(), reader=reader, now=NOW + timedelta(minutes=5)).tick().prs == 0
    # After it, only the open PR is.
    reads.clear()
    collector(store, FakeFabro(), reader=reader, now=NOW + timedelta(hours=1)).tick()
    assert reads == [11]


def test_a_rate_limited_pr_read_stops_the_pass(store):
    released(store, "R1", pr=11)
    released(store, "R2", pr=12)
    calls = []

    def reader(repo, number):
        calls.append(number)
        raise GitHubError("rate limited", status_code=403, remaining="0")

    report = collector(store, FakeFabro(), reader=reader).tick()
    assert report.pr_failures == 1
    assert len(calls) == 1


def test_without_github_no_pr_is_read(store):
    released(store, "R1", pr=11)
    report = ScorecardCollector(store, FakeFabro(), " ", "x", now=lambda: NOW).tick()
    assert report.prs == 0 and report.summarized == 1


@respx.mock
def test_iter_events_pages_with_since_seq():
    route = respx.get(f"{FABRO_API}/runs/R1/events")
    route.side_effect = [
        httpx.Response(200, json={"data": [{"seq": 1}, {"seq": 2}], "meta": {"has_more": True}}),
        httpx.Response(200, json={"data": [{"seq": 3}], "meta": {"has_more": False}}),
    ]
    assert [e["seq"] for e in iter_events(FABRO_API, "tok", "R1")] == [1, 2, 3]
    assert route.calls[0].request.url.params["since_seq"] == "1"
    assert route.calls[1].request.url.params["since_seq"] == "3"
    assert route.calls[0].request.url.params["limit"] == "1000"


@respx.mock
def test_iter_events_stops_on_an_empty_page_and_raises_on_404():
    respx.get(f"{FABRO_API}/runs/R1/events").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"has_more": True}})
    )
    assert list(iter_events(FABRO_API, "tok", "R1")) == []
    respx.get(f"{FABRO_API}/runs/R2/events").mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(FabroError) as raised:
        list(iter_events(FABRO_API, "tok", "R2"))
    assert raised.value.status_code == 404


@respx.mock
def test_iter_events_never_loops_on_a_seq_that_does_not_advance():
    respx.get(f"{FABRO_API}/runs/R1/events").mock(
        return_value=httpx.Response(200, json={"data": [{"seq": 0}], "meta": {"has_more": True}})
    )
    assert len(list(iter_events(FABRO_API, "tok", "R1"))) == 1


def model_rows_of(store, run_id):
    return store._conn.execute(
        "SELECT * FROM run_model_usage WHERE run_id = ? ORDER BY provider", (run_id,)
    ).fetchall()


def test_model_usage_is_read_once_after_the_summary(store):
    released(store, "R1")
    fabro = FakeFabro()
    report = collector(store, fabro).tick()
    assert report.summarized == 1 and report.models == 1
    rows = model_rows_of(store, "R1")
    assert [(r["provider"], r["model_id"], r["stages"]) for r in rows] == [
        ("box-a", "deepseek", 2), ("zai", "glm-5.3", 3)]
    zai = rows[1]
    assert (zai["input_tokens"], zai["output_tokens"], zai["cost_usd_micros"]) == (900, 100, 1500)
    assert rows[0]["cost_usd_micros"] is None and rows[0]["cache_read_tokens"] == 100
    assert summary_row(store, "R1")["model_usage"] == "ok"
    assert collector(store, fabro).tick().models == 0
    assert fabro.usage_read == ["R1"]


def test_model_usage_backfills_runs_summarized_before_it_existed(store):
    for i, hours in enumerate((5, 3, 1)):
        released(store, f"R{i}", finished=NOW - timedelta(hours=hours))
    fabro = FakeFabro()
    collector(store, fabro, runs=3).tick()
    store._conn.execute("UPDATE run_summary SET model_usage = NULL, model_usage_at = NULL")
    store._conn.execute("DELETE FROM run_model_usage")
    fabro.usage_read.clear()
    c = ScorecardCollector(store, fabro, "", "andrews-ai-agent", runs_per_tick=3,
                           models_per_tick=2, pr_reader=lambda r, n: facts(), now=lambda: NOW)
    assert c.tick().models == 2
    assert fabro.usage_read == ["R2", "R1"]  # newest first
    assert c.tick().models == 1


def test_model_usage_is_not_read_for_an_unsummarized_run(store):
    released(store, "GONE")
    fabro = FakeFabro(fail={"GONE": 404})
    assert collector(store, fabro).tick().models == 0
    assert fabro.usage_read == []


def test_empty_model_usage_is_none_and_a_404_is_missing(store):
    released(store, "EMPTY", finished=NOW - timedelta(hours=2))
    released(store, "PRUNED", finished=NOW - timedelta(hours=1))
    fabro = FakeFabro(usage={"by_model": []}, usage_fail={"PRUNED": 404})
    report = collector(store, fabro).tick()
    assert report.models == 1 and report.model_failures == 0
    assert summary_row(store, "EMPTY")["model_usage"] == "none"
    assert summary_row(store, "PRUNED")["model_usage"] == "missing"
    assert collector(store, fabro, now=NOW + RETRY_AFTER * 10).tick().models == 0


def test_a_model_usage_error_is_retried_after_the_backoff(store):
    released(store, "FLAKY")
    fabro = FakeFabro(usage_fail={"FLAKY": 500})
    assert collector(store, fabro).tick().model_failures == 1
    assert summary_row(store, "FLAKY")["model_usage"] == "error"
    assert collector(store, fabro, now=NOW + timedelta(minutes=5)).tick().model_failures == 0
    fabro.usage_fail.clear()
    assert collector(store, fabro, now=NOW + RETRY_AFTER + timedelta(minutes=1)).tick().models == 1
    assert summary_row(store, "FLAKY")["model_usage"] == "ok"
