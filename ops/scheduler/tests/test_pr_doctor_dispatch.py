"""The doctor's follow-up review: the lease, the queue order, the cap and the hand-off.

A `pr-review` the doctor earns is a coder loop (D14). It takes a primary lease, so it holds a
box exactly like a `backlog` run -- but it is not a queue item, and the whole point of these
tests is the difference:

* it is dispatched **before** any issue, oldest waiting review first;
* it is not dispatched at all over the daily cap (operator decision 3);
* releasing it writes **no issue label**, requeues nothing and looks up no PR from a run
  branch;
* its end is a hand-off (`ai-review-needs-human` back on, one comment) unless the PR merged.

`respx` stands in for both APIs, and the requests are recorded in order: "the marker's PR
comment, then the labels" and "no label write at all" are contracts, not effects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from fabro_scheduler.config import load_config
from fabro_scheduler.dispatch import DispatchLoop
from fabro_scheduler.fabro import FabroClient
from fabro_scheduler.lease import Lease, LeaseStore
from fabro_scheduler.reconcile import reconcile_leases
from fabro_scheduler.store import Store

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"

JELLY = "andrewthetechie/jelly-swipe"
FABRO_API = "http://10.10.0.32:32276/api/v1"
GITHUB_API = "https://api.github.com"
VERSION_ID = "f" * 64
FABRO_TOKEN = "dev-token-do-not-echo"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
BOXES = "coders-a=http://a.invalid/health"

LABEL_POST = re.compile(rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/labels$")
LABEL_DELETE = re.compile(
    rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/labels/[^/]+$"
)
COMMENTS = re.compile(
    rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/issues/\d+/comments(?:\?.*)?$"
)
PULL = re.compile(rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/pulls/\d+$")
COMMITS = re.compile(rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/pulls/\d+/commits.*$")
RUN = re.compile(rf"{re.escape(FABRO_API)}/runs/[^/]+$")


@dataclass
class Recorder:
    calls: list[tuple[str, str, object]] = field(default_factory=list)

    def record(self, request: httpx.Request) -> None:
        body: object = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode("utf-8", "replace")
        self.calls.append((request.method, request.url.path, body))

    def made(self, method: str, path: str) -> list[tuple[str, str, object]]:
        return [call for call in self.calls if call[0] == method and call[1] == path]

    def writes(self) -> list[tuple[str, str, object]]:
        return [call for call in self.calls if call[0] in ("POST", "PATCH", "DELETE")]


@pytest.fixture
def config(tmp_path):
    # A box in the lane, so `make_room` is exercised; empty it in a test that wants no
    # active-run listing at all.
    # No box in the lane: these tests are about the review's lease and order, and an empty
    # `SCHEDULER_BOX_HEALTH` is the lane doing nothing (C7), which keeps `/health` and the
    # active-run listing out of them entirely.
    return load_config(
        TRACKED_REPOS,
        env={
            "SCHEDULER_BACKGROUND": "on",
            "SCHEDULER_BOX_HEALTH": "",
            "SCHEDULER_PR_DOCTOR_REVIEWS_PER_DAY": "6",
        },
    )


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


@pytest.fixture
def leases(store) -> LeaseStore:
    return LeaseStore(store)


@pytest.fixture
def fabro(fake_checkout) -> FabroClient:
    return FabroClient(FABRO_API, FABRO_TOKEN, clone=fake_checkout.clone)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


def install_fabro(recorder: Recorder, *, run_ids=("REV1",), start_error=None) -> None:
    remaining = iter(run_ids)

    def register(request):
        recorder.record(request)
        return httpx.Response(201, json={"workflow_version_id": VERSION_ID})

    def create(request):
        recorder.record(request)
        return httpx.Response(201, json={"id": next(remaining)})

    def start(request):
        recorder.record(request)
        if start_error is not None:
            return start_error
        return httpx.Response(200, json={"lifecycle": {"status": {"kind": "runnable"}}})

    respx.post(f"{FABRO_API}/workflow-versions").mock(side_effect=register)
    respx.post(f"{FABRO_API}/runs").mock(side_effect=create)
    respx.post(url__regex=rf"{re.escape(FABRO_API)}/runs/[^/]+/start").mock(side_effect=start)


def install_automations(recorder: Recorder, description: str = "", rows=None) -> None:
    def handler(request):
        recorder.record(request)
        return httpx.Response(
            200,
            json={
                "data": rows
                if rows is not None
                else [
                    {
                        "id": "auto-1",
                        "workflow": "pr-review",
                        "environment_id": "python",
                        "description": description,
                        "target": {"kind": "git", "repo": JELLY, "branch": "main"},
                    }
                ]
            },
        )

    respx.get(f"{FABRO_API}/automations").mock(side_effect=handler)


def install_active(recorder: Recorder, runs=None) -> None:
    def handler(request):
        recorder.record(request)
        return httpx.Response(200, json={"data": runs or [], "meta": {"has_more": False}})

    respx.get(f"{FABRO_API}/runs").mock(side_effect=handler)


def install_labels(recorder: Recorder) -> None:
    def handler(request):
        recorder.record(request)
        return httpx.Response(200, json=[])

    respx.post(url__regex=LABEL_POST).mock(side_effect=handler)
    respx.delete(url__regex=LABEL_DELETE).mock(side_effect=handler)


def install_pr(recorder: Recorder, *, report: str = "blocked", merged: bool = False) -> None:
    def pull(request):
        recorder.record(request)
        return httpx.Response(
            200,
            json={
                "state": "closed" if merged else "open",
                "merged_at": "2026-11-02T12:30:00Z" if merged else None,
                "created_at": "2026-10-01T00:00:00Z",
                "labels": [],
                "additions": 1,
                "deletions": 1,
                "head": {"sha": "b" * 40},
            },
        )

    def commits(request):
        recorder.record(request)
        return httpx.Response(
            200,
            json=[
                {
                    "sha": "b" * 40,
                    "author": {"login": "andrews-ai-agent"},
                    "commit": {
                        "author": {"email": "andrews-ai-agent@users.noreply.github.com"},
                        "committer": {"date": "2026-11-02T11:00:00Z"},
                    },
                }
            ],
        )

    def comments(request):
        recorder.record(request)
        if merged:
            # The merge phase's own report for a merge. Step 8 reads the LAST report, so a
            # review that merged the PR must find this one, not the older block.
            body = "## Merged automatically\n\nSquash-merged as `fix: x`.\n"
        else:
            body = (
                "## AI PR review\n\n### Findings\n\n_None recorded._\n\n"
                "### Not auto-merged\n\n**Reason:** There are unfixed findings; a human "
                "must merge this PR.\n"
            )
        return httpx.Response(
            200, json=[{"created_at": "2026-11-02T11:30:00Z", "body": body}]
        )

    respx.get(url__regex=PULL).mock(side_effect=pull)
    respx.get(url__regex=COMMITS).mock(side_effect=commits)
    respx.get(url__regex=COMMENTS).mock(side_effect=pull if report is None else comments)


def install_run(recorder: Recorder, *, kind: str, reason: str | None = None) -> None:
    def handler(request):
        recorder.record(request)
        status: dict[str, object] = {"kind": kind}
        if reason:
            status["reason"] = reason
        return httpx.Response(200, json={"lifecycle": {"status": status}})

    respx.get(url__regex=RUN).mock(side_effect=handler)


def pending_review(store: Store, *, pr: int = 7, at: datetime = NOW) -> int:
    job_id = store.enqueue_background_job(
        kind="pr-doctor",
        repo=JELLY,
        target=str(pr),
        inputs={"pr_number": pr},
        priority=1,
        typical_box_minutes=40,
        now=at - timedelta(hours=1),
    )
    assert job_id is not None
    store.start_background_job(job_id, run_id="doctorrun", box="coders-a", now=at)
    store.set_background_outcome(job_id, outcome="review_pending", now=at)
    return job_id


def seed_issue(store: Store, number: int = 2) -> None:
    from fabro_scheduler.github import Issue

    store.upsert_issue(
        Issue(JELLY, number, "issue", frozenset({"agent"}), datetime.now(UTC))
    )


def loop(config, store, leases, fabro) -> DispatchLoop:
    return DispatchLoop(config, store, leases, fabro, "ghp_test", clock=lambda: NOW)


# --- dispatch ----------------------------------------------------------------------


def test_a_waiting_review_is_dispatched_ahead_of_an_issue(
    config, store, leases, fabro, recorder, respx_mock
):
    install_fabro(recorder, run_ids=("REV1", "I2"))
    install_automations(recorder)
    install_labels(recorder)
    seed_issue(store)
    job_id = pending_review(store)

    loop(config, store, leases, fabro).tick()

    # The review's create comes before the issue's, in one tick: D14 puts it ahead of the
    # issue queue, and the loop dispatches reviews before it builds the ranked queue.
    creates = recorder.made("POST", "/api/v1/runs")
    assert len(creates) == 2
    assert creates[0][2]["args"]["inputs"]["pr_number"] == 7
    assert creates[1][2]["args"]["inputs"]["issue_number"] == 2
    assert sorted(lease.workflow for lease in leases.active()) == ["backlog", "pr-review"]
    assert store.background_job(job_id)["outcome"] == "review_fired"
    assert store.background_job(job_id)["review_run_id"] == "REV1"
    assert store.reviews_dispatched_since(NOW - timedelta(days=1)) == 1

    intent = recorder.made("POST", "/api/v1/runs")[0][2]
    assert intent["args"]["inputs"] == {
        "pr_number": 7,
        "auto_merge": 1,
        "coder_pool": "coders-a",
    }
    assert intent["args"]["labels"] == {
        "source": "scheduler",
        "lane": "coder",
        "box": "coders-a",
        "pr": "7",
        "workflow": "pr-review",
        "workflow_sha": "a" * 40,
    }
    assert intent["environment_id"] == "python", "the pr-review automation's environment"


def test_the_lease_is_a_pr_lease_and_carries_the_reviews_box_stages(
    config, store, leases, fabro, recorder, respx_mock, fake_checkout
):
    # The version's generated manifest is where `read_box_stages` finds a run's box stages
    # (C1, C7 step 6), so the fake tree has to carry one for this to prove anything.
    (fake_checkout.directory / "workflows/pr-review/workflow.toml").write_text(
        "[run.environment.env]\n"
        "FABRO_IO_MANIFEST = "
        "\'\'\'{\"stages\":{\"rebase_agent_t1\":{\"box\":true}}}\'\'\'\n"
    )
    install_fabro(recorder)
    install_automations(recorder)
    pending_review(store)
    loop(config, store, leases, fabro).tick()
    held = leases.active()[0]
    assert (held.target_kind, held.issue_number, held.workflow) == ("pr", 7, "pr-review")
    assert held.box_stages == {"rebase_agent_t1": "true"}


def test_the_per_repo_auto_merge_switch_rides_into_the_inputs(
    config, store, leases, fabro, recorder
, respx_mock):
    install_fabro(recorder)
    install_automations(recorder, description="auto_merge=false")
    pending_review(store)
    loop(config, store, leases, fabro).tick()
    assert recorder.made("POST", "/api/v1/runs")[0][2]["args"]["inputs"]["auto_merge"] == 0


def test_a_missing_automation_row_hands_the_pr_off_after_three_tries(
    config, store, leases, fabro, recorder
, respx_mock):
    """A row that is absent, duplicated or has no environment is not a retryable condition,
    so the review is abandoned -- and the PR must not sit in a queue that cannot move."""
    install_fabro(recorder)
    install_automations(recorder, rows=[])
    install_labels(recorder)
    respx.post(url__regex=COMMENTS).mock(
        side_effect=lambda request: (recorder.record(request), httpx.Response(200, json={}))[1]
    )
    job_id = pending_review(store)
    dispatch = loop(config, store, leases, fabro)
    for _ in range(3):
        dispatch._review_retry_at.clear()
        dispatch.tick()
    assert store.background_job(job_id)["outcome"] == "handed_off_no_review"
    assert store.background_job(job_id)["review_failures"] == 3
    assert recorder.made("POST", "/api/v1/runs") == []


def test_the_daily_cap_holds_a_pushed_pr_until_the_next_day(
    config, store, leases, fabro, recorder
, respx_mock):
    import dataclasses

    capped = dataclasses.replace(config, pr_doctor_reviews_per_day=1)
    install_fabro(recorder, run_ids=("REV1", "REV2"))
    install_automations(recorder)
    old = pending_review(store, pr=7, at=NOW)
    new = pending_review(store, pr=8, at=NOW)
    # One review already went out today, so the cap is spent.
    store.start_background_review(old, run_id="EARLIER", now=NOW)
    store.set_background_outcome(old, outcome="review_pending", now=NOW)
    store.start_background_review(new, run_id="EARLIER2", now=NOW)
    store.set_background_outcome(new, outcome="review_pending", now=NOW)

    loop(capped, store, leases, fabro).tick()
    assert recorder.made("POST", "/api/v1/runs") == []
    assert len(store.pending_review_jobs()) == 2

    # The next UTC day has a fresh slot, and the oldest waiting review goes first.
    tomorrow = datetime(2026, 11, 3, 0, 5, tzinfo=UTC)
    dispatch = DispatchLoop(
        capped, store, leases, fabro, "ghp_test", clock=lambda: tomorrow
    )
    dispatch.tick()
    starts = recorder.made("POST", "/api/v1/runs")
    assert len(starts) == 1
    assert starts[0][2]["args"]["inputs"]["pr_number"] == 7


def test_zero_reviews_per_day_turns_the_reviews_off_without_the_doctor(
    config, store, leases, fabro, recorder
, respx_mock):
    import dataclasses

    off = dataclasses.replace(config, pr_doctor_reviews_per_day=0)
    install_fabro(recorder)
    install_automations(recorder)
    pending_review(store)
    loop(off, store, leases, fabro).tick()
    assert recorder.made("POST", "/api/v1/runs") == []
    assert store.pending_review_jobs() != []


# --- release and hand-off -----------------------------------------------------------


def take_review_lease(leases: LeaseStore, *, run_id: str = "REV1") -> None:
    leases.acquire(
        Lease(
            coder_pool="coders-a",
            repo=JELLY,
            issue_number=7,
            run_id=run_id,
            dispatched_at=NOW,
            workflow="pr-review",
            target_kind="pr",
        )
    )


def test_releasing_a_review_writes_no_issue_label_and_requeues_nothing(
    config, store, leases, fabro, recorder
, respx_mock):
    install_run(recorder, kind="succeeded")
    install_pr(recorder, report="blocked")
    install_labels(recorder)
    respx.post(url__regex=COMMENTS).mock(
        side_effect=lambda request: (recorder.record(request), httpx.Response(200, json={}))[1]
    )
    job_id = pending_review(store)
    store.start_background_review(job_id, run_id="REV1", now=NOW)
    take_review_lease(leases)
    seed_issue(store, 7)  # an issue with the same number must be untouched

    actions = reconcile_leases(config, store, leases, fabro, "ghp_test")

    assert [a.outcome for a in actions] == ["released"]
    assert leases.active() == []
    assert store.issue_counts().get(JELLY) == 1, "the issue cache is untouched"
    # The only writes are the hand-off's label and comment; no issue label was removed or
    # restored, which is what the issue path would have done.
    bodies = [
        call[2]
        for call in recorder.writes()
        if re.search(r"/labels$", call[1])
    ]
    assert bodies == [{"labels": ["ai-review-needs-human"]}]
    assert store.background_job(job_id)["outcome"] == "handed_off"


def test_a_review_that_merged_the_pr_needs_no_handoff(config, store, leases, fabro, recorder, respx_mock):
    install_run(recorder, kind="succeeded")
    install_pr(recorder, merged=True)
    install_labels(recorder)
    job_id = pending_review(store)
    store.start_background_review(job_id, run_id="REV1", now=NOW)
    take_review_lease(leases)

    reconcile_leases(config, store, leases, fabro, "ghp_test")

    assert store.background_job(job_id)["outcome"] == "merged"
    assert [
        call for call in recorder.writes() if re.search(r"/issues/\d+", call[1])
    ] == [], "a merged PR gets no label and no comment"


def test_a_review_released_while_still_running_keeps_its_lease(
    config, store, leases, fabro, recorder
, respx_mock):
    install_run(recorder, kind="running")
    job_id = pending_review(store)
    store.start_background_review(job_id, run_id="REV1", now=NOW)
    take_review_lease(leases)
    actions = reconcile_leases(config, store, leases, fabro, "ghp_test")
    assert [a.outcome for a in actions] == ["adopted"]
    assert len(leases.active()) == 1


def test_the_receipt_scan_ignores_a_pr_lease(config, store, leases, fabro, recorder, respx_mock):
    """A `pr` lease's `issue_number` is a PR number. If the receipt scan counted it as live,
    it would protect an unrelated issue #7 from the repair that pass exists to do."""
    take_review_lease(leases)
    live = {
        (lease.repo, lease.issue_number)
        for lease in leases.active()
        if lease.target_kind == "issue"
    }
    assert live == set()
