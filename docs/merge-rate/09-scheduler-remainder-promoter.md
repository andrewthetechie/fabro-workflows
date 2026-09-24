# Scheduler: promote a remainder issue once its parent PR merges

## Tracer-Bullet Outcome
Once a minute per repository, the scheduler looks at every open issue labelled
`agent-remainder` and at the pull request named in its body.

- If that PR has merged, whether auto-merged or merged by hand, the issue gains
  `priority` and `agent` and loses `agent-remainder`. It joins the queue and is worked
  next.
- If that PR was closed without merging, the issue gains `agent-stuck` and loses
  `agent-remainder`, so it waits for a human.
- While the PR is open, nothing changes.

No human step is needed on either merge path.

## User Story
As the operator, I want a run's leftover tasks picked up automatically as the next issue,
but only after the PR they build on is on `main`, so that a large issue finishes over
several bounded PRs without me relabelling anything, and never starts from a `main` that
lacks its parent's work.

## Description
All work is in `ops/scheduler/` in fabro-workflows:

1. `src/fabro_scheduler/github.py`: a `REMAINDER_LABEL` constant, a `RemainderIssue`
   dataclass, and two read functions, `fetch_remainders` and `fetch_pull_state`.
2. `src/fabro_scheduler/remainder.py` (new): the marker parser and
   `promote_remainders`.
3. `src/fabro_scheduler/inventory.py`: call the promoter after each repository poll,
   without ever letting it fail or stale the inventory.
4. `tests/test_remainder.py` (new).

## Context Pack
- Source decisions: overview decision 12, and contracts C1 (marker) and C2 (labels).
  ADR 0011 D6, amended 2026-09-24.
- The marker a remainder issue's body starts with (written by task 10):
  `<!-- fabro:remainder parent=<N> pr=<P> -->`, for example
  `<!-- fabro:remainder parent=1239 pr=1250 -->`.
- Why the hold exists: the scheduler runs two issues of the same repository at once
  when the queue holds only that repository (commit `bb44dfe`, "saturate every box").
  A remainder labelled `agent` at filing time could start on the other box, from a
  `main` without its parent PR.
- Why the write order matters: `priority` first, then `agent`, then remove
  `agent-remainder`. If a pass fails half-way, the next pass sees `agent` (or
  `agent-in-progress`, if it was dispatched in between) and only removes
  `agent-remainder`. It never re-adds `agent` to an issue that is already on a box,
  which would dispatch it twice.
- Repo facts:
  - Task 08 added, in `github.py`, directly after `STUCK_LABEL = "agent-stuck"`:
    `PRIORITY_LABEL = "priority"`. This task depends on it.
  - Existing constants and helpers in `github.py` that the new code uses, verbatim
    signatures:
    ```python
    GITHUB_API = "https://api.github.com"
    REQUEST_TIMEOUT_SECONDS = 15.0
    PAGE_SIZE = 100
    PR_LOOKUP_TIMEOUT_SECONDS = 5.0
    REQUIRED_LABEL = "agent"
    IN_PROGRESS_LABEL = "agent-in-progress"
    STUCK_LABEL = "agent-stuck"

    class GitHubError(RuntimeError): ...   # GitHubError(message, *, status_code=None, remaining=None)
    def _label_names(payload: Mapping[str, Any]) -> frozenset[str]: ...
    def _error_message(repo: str, response: httpx.Response, remaining: str | None) -> str: ...
    def add_label(repo: str, number: int, label: str, token: str, *, client: httpx.Client | None = None, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None: ...
        # POST /repos/{repo}/issues/{n}/labels with json {"labels": [label]}; idempotent; raises GitHubError on non-2xx
    def remove_label(repo: str, number: int, label: str, token: str, *, client: httpx.Client | None = None, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None: ...
        # DELETE /repos/{repo}/issues/{n}/labels/{label}; a 404 counts as success
    ```
  - The pattern to copy for an unconditional per-label fetch is `fetch_in_progress`,
    verbatim:
    ```python
def fetch_in_progress(
    repo: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[int]:
    """Numbers of open issues in `repo` carrying `agent-in-progress`.

    Draft 09's recovery GitHub pass scans these: an issue wearing the scheduler's
    receipt with no live lease is a receipt left behind by a crash between
    labelling and recording, and this is how the pass finds it. Deliberately not
    the ETag-cached `fetch_issues` — this runs once at startup, not on a poll,
    and it asks for a *different* label collection (`agent-in-progress` rather
    than `agent`), so none of that machinery carries over.

    Raises `GitHubError` for anything that is not a `200`; the recovery pass then
    leaves that repo alone rather than un-label issues on a guess.
    """
    if not token or not token.strip():
        raise GitHubError(
            "GITHUB_TOKEN is not set; the GitHub inventory cannot be refreshed"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"labels": IN_PROGRESS_LABEL, "state": "open", "per_page": PAGE_SIZE}

    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.get(
            f"{GITHUB_API}/repos/{repo}/issues", params=params, headers=headers
        )
    except httpx.HTTPError as exc:
        raise GitHubError(f"{repo}: GitHub request failed: {exc}") from exc
    finally:
        if owned:
            http.close()

    if response.status_code != 200:
        remaining = response.headers.get("x-ratelimit-remaining")
        raise GitHubError(
            _error_message(repo, response, remaining),
            status_code=response.status_code,
            remaining=remaining,
        )

    payload = response.json()
    if not isinstance(payload, list):
        raise GitHubError(
            f"{repo}: expected a list of issues, got {type(payload).__name__}"
        )
    numbers = []
    for item in payload:
        if (
            isinstance(item, Mapping)
            and isinstance(item.get("number"), int)
            and IN_PROGRESS_LABEL in _label_names(item)
        ):
            numbers.append(item["number"])
    log.info(
        "github: %s %d agent-in-progress issue(s)",
        repo,
        len(numbers),
    )
    return numbers


def fetch_pull_for_branch(
    repo: str,
    branch: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = PR_LOOKUP_TIMEOUT_SECONDS,
) -> PullRequest | None:
    """The pull request whose head is `branch`, or `None` if there is none.

    `repo` is `"owner/repo"`; `branch` is a bare ref such as
    `"fabro/run/01M2ZQG1AGP23KERJF6FKHHMCR"`. The `head` filter wants the
    `owner:ref` form, so the owner is taken from `repo` and prefixed here rather
    than by the caller.

    **`state=all` is load-bearing.** The endpoint defaults to `state=open`, and
    a backlog run's PR has usually already merged by the time its lease is
    released -- so the default returns nothing for exactly the runs worth
    recording. Verified live on 2026-09-20: with `state=all` this filter returns
    PR 388; without it, zero results.

    Raises `GitHubError` for anything that is not a `200`, so the caller can tell
    "there is no PR" (`None`) from "GitHub could not answer" (the raise). The two
    are opposite conclusions for an operator and must never collapse into one.
    """
    if not token or not token.strip():
        raise GitHubError(
            "GITHUB_TOKEN is not set; the GitHub inventory cannot be refreshed"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owner = repo.split("/", 1)[0]
    params = {"head": f"{owner}:{branch}", "state": "all", "per_page": 1}

    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.get(
            f"{GITHUB_API}/repos/{repo}/pulls", params=params, headers=headers
        )
    except httpx.HTTPError as exc:
        raise GitHubError(f"{repo}: GitHub request failed: {exc}") from exc
    finally:
        if owned:
            http.close()

    if response.status_code != 200:
        remaining = response.headers.get("x-ratelimit-remaining")
        raise GitHubError(
            _error_message(repo, response, remaining),
            status_code=response.status_code,
            remaining=remaining,
        )

    payload = response.json()
    if not isinstance(payload, list):
        raise GitHubError(
            f"{repo}: expected a list of pulls, got {type(payload).__name__}"
        )
    if not payload:
        return None
    item = payload[0]
    if not isinstance(item, Mapping) or not isinstance(item.get("number"), int):
        return None
    return PullRequest(
        number=item["number"],
        url=str(item.get("html_url") or ""),
        merged=item.get("merged_at") is not None,
    )
    ```
  - `InventoryPoller.poll_repo` today, verbatim (`inventory.py`):
    ```python
    def poll_repo(self, repo: RepoConfig) -> None:
        """One conditional request for one repo, and whatever it implies.

        **Never raises.** A single repo's bad day — a malformed payload, a bug in
        the parse, a full disk — must not stop the loop for the other three, and it
        must not stop this one forever either. The failure is recorded against the
        repo and the page shows it as stale, which is louder than a poller thread
        that died without saying so while `/health` kept answering `ok`.
        """
        try:
            self._poll(repo)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.exception("inventory: %s: unexpected failure", repo.name)
            self._note_failure(repo.name, f"unexpected error: {exc}")
    ```
    `inventory.py` imports `from .github import REQUEST_TIMEOUT_SECONDS, GitHubError, fetch_issues`.
    `self._client` is an `httpx.Client` (or `None` before `start()`), and `self._token`
    is the GitHub token.
  - Tests use pytest and `respx`. `respx.get(url, params={...})` matches when the request
    contains those query parameters. Existing inventory tests build a repo as
    `RepoConfig("o/a", 0, "python")` and a store as `Store(tmp_path / "scheduler.db")`.
    `store.fetch_state(repo)` returns an object with `.last_error`, which is `None` when
    the last poll succeeded.
- Non-goals: do not change `fetch_issues`, the ETag cache, `rank()`, or the dispatcher.
  No new database table. No web-UI change: a promoted issue shows `priority` in the queue
  page's labels column.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/github.py        (edit)
  ops/scheduler/src/fabro_scheduler/remainder.py     (new)
  ops/scheduler/src/fabro_scheduler/inventory.py     (edit)
  ops/scheduler/tests/test_remainder.py              (new)
  ```
- **`github.py`, edit 1:** directly after `PRIORITY_LABEL = "priority"` (from task 08), add:
  ```python

  # A remainder issue that a `backlog` run filed for the tasks past its task budget,
  # held out of the queue until its parent PR merges. `remainder.py` promotes it.
  REMAINDER_LABEL = "agent-remainder"
  ```
- **`github.py`, edit 2:** directly before `def add_label(`, add:
  ```python
@dataclass(frozen=True)
class RemainderIssue:
    """One open issue carrying `agent-remainder`, as the promoter needs it.

    `body` is the raw issue body (`""` when GitHub sends `null`); the promoter, not
    this module, parses the marker out of it.
    """

    number: int
    labels: frozenset[str]
    body: str


def fetch_remainders(
    repo: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[RemainderIssue]:
    """Open issues in `repo` carrying `agent-remainder` (ADR 0011 D6).

    Unconditional, like `fetch_in_progress`: it asks for a different label
    collection than the ETag-cached inventory, and it runs once a minute per repo,
    which is 240 requests an hour for four repos -- well inside the 5,000/hour
    budget. Pull requests are dropped (`GET /issues` returns them too), and so is
    any object that does not actually carry the label.

    Raises `GitHubError` for anything that is not a `200`.
    """
    if not token or not token.strip():
        raise GitHubError(
            "GITHUB_TOKEN is not set; the GitHub inventory cannot be refreshed"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"labels": REMAINDER_LABEL, "state": "open", "per_page": PAGE_SIZE}

    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.get(
            f"{GITHUB_API}/repos/{repo}/issues", params=params, headers=headers
        )
    except httpx.HTTPError as exc:
        raise GitHubError(f"{repo}: GitHub request failed: {exc}") from exc
    finally:
        if owned:
            http.close()

    if response.status_code != 200:
        remaining = response.headers.get("x-ratelimit-remaining")
        raise GitHubError(
            _error_message(repo, response, remaining),
            status_code=response.status_code,
            remaining=remaining,
        )

    payload = response.json()
    if not isinstance(payload, list):
        raise GitHubError(
            f"{repo}: expected a list of issues, got {type(payload).__name__}"
        )
    found = []
    for item in payload:
        if not isinstance(item, Mapping) or "pull_request" in item:
            continue
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        labels = _label_names(item)
        if REMAINDER_LABEL not in labels:
            continue
        body = item.get("body")
        found.append(
            RemainderIssue(
                number=number, labels=labels, body=body if isinstance(body, str) else ""
            )
        )
    return found


def fetch_pull_state(
    repo: str,
    number: int,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = PR_LOOKUP_TIMEOUT_SECONDS,
) -> str:
    """`"merged"`, `"closed"` (closed without merging) or `"open"` for one PR.

    `GET /repos/{owner}/{repo}/pulls/{number}`. `merged_at` decides merged, because a
    merged PR's `state` is also `"closed"`. Raises `GitHubError` for anything that
    is not a `200` -- including a `404` for a number that is not a PR -- so the caller
    can tell "not merged yet" from "GitHub could not answer".
    """
    if not token or not token.strip():
        raise GitHubError(
            "GITHUB_TOKEN is not set; the GitHub inventory cannot be refreshed"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{number}", headers=headers
        )
    except httpx.HTTPError as exc:
        raise GitHubError(f"{repo}: GitHub request failed: {exc}") from exc
    finally:
        if owned:
            http.close()

    if response.status_code != 200:
        remaining = response.headers.get("x-ratelimit-remaining")
        raise GitHubError(
            _error_message(repo, response, remaining),
            status_code=response.status_code,
            remaining=remaining,
        )
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise GitHubError(f"{repo}: expected a pull object, got {type(payload).__name__}")
    if payload.get("merged_at") is not None:
        return "merged"
    return "closed" if payload.get("state") == "closed" else "open"


  ```
- **`remainder.py` (new), full content:**
  ```python
"""Remainder promotion: a held remainder issue becomes the next queue item once its
parent PR merges (ADR 0011 D6).

A `backlog` run that spends its task budget files the tasks it did not start as ONE
remainder issue, labelled `agent-remainder` and deliberately NOT `agent`, so the queue
cannot see it while `main` still lacks the parent PR. The first line of its body is

    <!-- fabro:remainder parent=<N> pr=<P> -->

and this module, called once a minute per repo from the inventory poll, reads each
held issue's PR `P`:

- merged (by the run or by a human): add `priority`, then `agent`, then remove
  `agent-remainder`. The issue joins the queue and `priority` ranks it next.
- closed without merging: add `agent-stuck`, remove `agent-remainder`. The work it
  continues never landed, so a human decides.
- open: leave it alone.

**Order matters.** `agent` is added before `agent-remainder` is removed, so a failure
in between leaves an issue that carries both. The next pass sees `agent` and only
finishes the cleanup; it never re-adds `agent`. That is what stops a half-finished
promotion from re-queueing an issue that has since been dispatched and wears
`agent-in-progress` instead. An issue with no parseable marker is logged and never
touched.
"""

from __future__ import annotations

import logging
import re

import httpx

from .github import (
    IN_PROGRESS_LABEL,
    PRIORITY_LABEL,
    REMAINDER_LABEL,
    REQUIRED_LABEL,
    STUCK_LABEL,
    GitHubError,
    add_label,
    fetch_pull_state,
    fetch_remainders,
    remove_label,
)

log = logging.getLogger(__name__)

MARKER = re.compile(r"<!-- fabro:remainder parent=(\d+) pr=(\d+) -->")

# Any of these means the promotion already happened (or a human took over): only
# the `agent-remainder` cleanup is left to do.
_PAST_PROMOTION = frozenset({REQUIRED_LABEL, IN_PROGRESS_LABEL, STUCK_LABEL})


def parse_marker(body: str) -> tuple[int, int] | None:
    """`(parent_issue, pr)` from a remainder body, or `None` if there is no marker."""
    match = MARKER.search(body)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def promote_remainders(
    repo: str, token: str, *, client: httpx.Client | None = None
) -> list[tuple[int, str]]:
    """Promote or park every held remainder issue in `repo`.

    Returns `(issue_number, action)` for each issue it changed, where `action` is
    `"promoted"`, `"orphaned"` or `"cleaned"`. Raises `GitHubError` only when the
    list of held issues itself cannot be read; a failure on one issue is logged and
    the next issue is still processed.
    """
    changed: list[tuple[int, str]] = []
    for held in fetch_remainders(repo, token, client=client):
        try:
            action = _promote_one(repo, held.number, held.labels, held.body, token, client)
        except GitHubError as exc:
            log.warning("remainder: %s#%s not processed this pass: %s", repo, held.number, exc)
            continue
        if action is not None:
            changed.append((held.number, action))
    return changed


def _promote_one(
    repo: str,
    number: int,
    labels: frozenset[str],
    body: str,
    token: str,
    client: httpx.Client | None,
) -> str | None:
    if labels & _PAST_PROMOTION:
        remove_label(repo, number, REMAINDER_LABEL, token, client=client)
        return "cleaned"

    marker = parse_marker(body)
    if marker is None:
        log.warning(
            "remainder: %s#%s carries %s but has no fabro:remainder marker; left alone",
            repo, number, REMAINDER_LABEL,
        )
        return None
    parent, pr = marker

    state = fetch_pull_state(repo, pr, token, client=client)
    if state == "merged":
        add_label(repo, number, PRIORITY_LABEL, token, client=client)
        add_label(repo, number, REQUIRED_LABEL, token, client=client)
        remove_label(repo, number, REMAINDER_LABEL, token, client=client)
        log.info("remainder: %s#%s promoted (parent #%s, PR #%s merged)", repo, number, parent, pr)
        return "promoted"
    if state == "closed":
        add_label(repo, number, STUCK_LABEL, token, client=client)
        remove_label(repo, number, REMAINDER_LABEL, token, client=client)
        log.warning(
            "remainder: %s#%s parked as %s: PR #%s closed without merging",
            repo, number, STUCK_LABEL, pr,
        )
        return "orphaned"
    return None
  ```
- **`inventory.py`:**
  - Directly after the line
    `from .github import REQUEST_TIMEOUT_SECONDS, GitHubError, fetch_issues`, add
    `from .remainder import promote_remainders`.
  - Replace `poll_repo` (shown above) with:
    ```python
    def poll_repo(self, repo: RepoConfig) -> None:
        """One conditional request for one repo, and whatever it implies.

        **Never raises.** A single repo's bad day — a malformed payload, a bug in
        the parse, a full disk — must not stop the loop for the other three, and it
        must not stop this one forever either. The failure is recorded against the
        repo and the page shows it as stale, which is louder than a poller thread
        that died without saying so while `/health` kept answering `ok`.
        """
        try:
            self._poll(repo)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.exception("inventory: %s: unexpected failure", repo.name)
            self._note_failure(repo.name, f"unexpected error: {exc}")
        self._promote(repo)

    def _promote(self, repo: RepoConfig) -> None:
        """Promote held remainder issues (ADR 0011 D6). **Never raises.**

        Deliberately outside `_poll`'s error handling: a promoter failure is logged,
        and it must not mark the repo's inventory stale, because the inventory itself
        is fine. The promotion is retried on the next pass anyway.
        """
        try:
            for number, action in promote_remainders(
                repo.name, self._token, client=self._client
            ):
                log.info("inventory: %s#%s remainder %s", repo.name, number, action)
        except Exception:  # noqa: BLE001 - see the docstring
            log.exception("inventory: %s: remainder promotion failed", repo.name)
    ```
- **`tests/test_remainder.py` (new), full content:**
  ```python
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
  ```
- Verified external contracts:
  - `GET /repos/{owner}/{repo}/issues?labels=<l>&state=open&per_page=100` returns a
    list of issue objects, including pull requests (which carry a `pull_request` key),
    each with `number`, `labels` (a list of `{"name": …}`) and `body` (a string or
    `null`). This is the same endpoint `fetch_issues` and `fetch_in_progress` use.
  - `GET /repos/{owner}/{repo}/pulls/{number}` returns an object with `state`
    (`"open"` or `"closed"`) and `merged_at` (a timestamp, or `null` when not merged).
    A merged PR has `state: "closed"` **and** a non-null `merged_at`, which is why
    `merged_at` is tested first.
- Behavior rules: see the docstring of `remainder.py`. One pass per repository per
  inventory tick (60 s).
- Error and security rules: nothing logs the token. A failure to list held issues is
  logged by `InventoryPoller._promote` and is **not** recorded as an inventory failure. A
  failure on one issue skips that issue for this pass only.

## Acceptance Criteria
- [ ] The four files match the content above.
- [ ] `cd ops/scheduler && uv run pytest -q` passes with 10 more tests than after task 08
      (404 in total if tasks 08 and 09 are the only scheduler changes).
- [ ] A marker-less `agent-remainder` issue causes no PR request and no label write
      (test `test_no_marker_means_no_pr_lookup_and_no_writes`).

## Test Expectations
- Framework: pytest with `respx`. Command: `cd ops/scheduler && uv run pytest -q tests/test_remainder.py`,
  then the full suite.
- Literal expectations (from the tests above):
  - merged: returns `[(12, "promoted")]`; labels added `["priority", "agent"]`, in that
    order; removed `["agent-remainder"]`.
  - open: returns `[]`; no writes.
  - closed unmerged: returns `[(12, "orphaned")]`; added `["agent-stuck"]`; removed
    `["agent-remainder"]`.
  - no marker: returns `[]`; no PR lookup, no writes.
  - already has `agent-in-progress`: returns `[(12, "cleaned")]`; no PR lookup, no adds,
    removed `["agent-remainder"]`.
  - PR lookup 502: returns `[]`; no writes; nothing raised.
  - the poller runs the promoter after a `304`; a promoter `500` leaves
    `last_error is None`.
- Run against a scratch copy of `ops/scheduler` with task 08 applied (2026-09-24): `404 passed`.

## Dependencies
- Blocked by: 08 (Scheduler: a `priority` label puts an issue next in the queue)
- Why blocked: this task imports `PRIORITY_LABEL` from `github.py`, which 08 adds, and
  "worked next" is true only because 08's `rank()` honours that label.
- Blocks: 10 (the `backlog` side that files remainder issues). **Deploy this before 10
  reaches `main`**, or remainder issues are filed with nothing to promote them.

## Labels
`enhancement`, `scheduler`, `priority:high`

## Estimate
Medium

## Risk
3 - This is the first scheduler code that adds `agent` to an issue on its own. The
write order and the "already past promotion" guard are what prevent a double dispatch,
and both are tested. Deploy with
`rsync -a --delete --exclude .venv --exclude __pycache__ --exclude .pytest_cache ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/`
and `ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d --build scheduler'` (operator).

## Validator Stopping Point
`cd ops/scheduler && uv run pytest -q` passes.
