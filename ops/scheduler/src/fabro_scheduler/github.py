"""The GitHub half: the read-only inventory, and the label writes dispatch needs.

One conditional `GET /repos/{owner}/{repo}/issues?labels=agent&state=open` per
repo per minute keeps the queue current. `add_label` / `remove_label` are the only
writes, and they exist for one thing: the durable handoff receipt a dispatch leaves
behind (`agent-in-progress`) and the queue label it takes away (`agent`). Nothing
here closes an issue, comments, or touches anything else — the worst thing a bug in
the read path can do is show the operator a stale queue.

Two facts drive the shape of the one request the inventory makes.

**A conditional request is free.** GitHub answers `If-None-Match` with `304 Not
Modified` and an empty body, and a `304` costs **zero** of the 5,000/hour
authenticated budget — `x-ratelimit-remaining` is byte-for-byte unchanged across
a `200`/`304` pair (verified live on 2026-09-18 for `andrewthetechie/jelly-swipe`).
So the `ETag` returned by a `200` is sent back **including its surrounding double
quotes, exactly as received**; stripping them turns the next call into an
unconditional `200` and silently burns quota.

**A `304` is not "no work".** It means "your cached copy is still current", and it
carries no issue list at all. `fetch_issues` therefore returns an explicit
`changed` flag, and the caller's rule is: on `changed=False`, touch nothing —
neither the cached issues nor their `first_seen`.

The collection ETag is what makes that safe here. It covers the *whole* filtered
collection (`?labels=agent&state=open`), so an issue that gains
`agent-in-progress` leaves the collection and changes the ETag. A `304` therefore
implies the membership and the labels of every cached item are unchanged, which is
what lets the caller skip re-filtering on every tick.

**A label write is not conditional and not free**, and it does not go through the
cache: it is a small mutation whose only safety property is that both operations
are idempotent, so a rollback or a retry can be repeated without harm.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# A hung GitHub call must never hold the poll loop for a whole minute: the
# interval is 60s and a request that outlives it delays every later repo's tick.
REQUEST_TIMEOUT_SECONDS = 15.0

# One page. Four repos with a handful of `agent` issues each fit in 100 with room
# to spare, and the ETag is per-URL — following a `Link: rel="next"` page would
# mean a second URL whose ETag nobody stores, and the 304 story would quietly fall
# apart. A full page is logged as a possible truncation instead.
PAGE_SIZE = 100

# The PR lookup's own timeout, deliberately shorter than this module's 15s
# default. It runs inside the 15-second release poll
# (`DEFAULT_RELEASE_INTERVAL_SECONDS`), and a call that can consume a whole tick
# delays the release of the OTHER coder instance's lease -- the scarcest resource
# the scheduler has. Five seconds is long enough for a healthy api.github.com and
# short enough that a sick one costs a third of one tick.
PR_LOOKUP_TIMEOUT_SECONDS = 5.0

REQUIRED_LABEL = "agent"

# `agent-stuck` is what `mark_stuck` leaves on an issue a human has to re-arm;
# `agent-in-progress` is the scheduler's own durable handoff receipt. A queue item
# is neither (CONTEXT.md, **Queue item**).
IN_PROGRESS_LABEL = "agent-in-progress"
STUCK_LABEL = "agent-stuck"

# An issue the operator (or the remainder promoter) wants dispatched next. Ranks
# after an Override and before every other tier (queue.py). It is NOT repo
# priority, the `repos.toml` integer; and it does not make an issue a queue item
# -- only REQUIRED_LABEL does that. ADR 0011.
PRIORITY_LABEL = "priority"

# A remainder issue that a `backlog` run filed for the tasks past its task budget,
# held out of the queue until its parent PR merges. `remainder.py` promotes it.
REMAINDER_LABEL = "agent-remainder"
# A Child issue a Split parent was divided into, waiting on an earlier Child
# (ADR 0015, C3/C5). It is created out of the queue and only the scheduler
# promotes it -- never `backlog`, which would implement it against a `main` that
# lacks its predecessor (the ADR 0011 D6 same-repo saturation race, again).
HELD_LABEL = "agent-held"
# A Split parent: an issue triage divided into Child issues. It is never worked
# and never labelled `agent`; the scheduler closes it when every child lands.
SPLIT_LABEL = "agent-split"
# A held child or a split parent is never a queue item, even if it also carries
# `agent`: a held child would be implemented against a `main` that lacks its
# predecessor, and a split parent would be implemented alongside its own children.
EXCLUDED_LABELS = frozenset({IN_PROGRESS_LABEL, STUCK_LABEL, HELD_LABEL, SPLIT_LABEL})


class GitHubError(RuntimeError):
    """A fetch that produced no usable issue list.

    Carries the HTTP status and the `x-ratelimit-remaining` header when there was
    one, because a `403` from a spent quota and a `403` from a token without
    `issues: read` look identical in the status line and are told apart by
    nothing else. Nothing in here ever holds the token.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        remaining: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.remaining = remaining


@dataclass(frozen=True)
class Issue:
    """One open, `agent`-labelled issue that is neither in progress nor stuck.

    `first_seen` is when **this scheduler** first observed the issue, not GitHub's
    `created_at`. It is what the starvation ceiling is measured from, and the
    store never resets it — otherwise an issue could be refreshed forever and
    never reach the ceiling.
    """

    repo: str  # "owner/repo"
    number: int
    title: str
    labels: frozenset[str]
    first_seen: datetime


@dataclass(frozen=True)
class PullRequest:
    """The PR on a run branch, as the history record needs it.

    `merged` is a point-in-time snapshot: it is whether the PR had merged when
    this was read, not a promise about what it becomes later.
    """

    number: int
    url: str
    merged: bool


def is_eligible(payload: Mapping[str, Any]) -> bool:
    """Whether one object from `GET /issues` is a queue item.

    `GET /issues` returns **pull requests as well as issues** — a PR object
    carries a `pull_request` key and an issue does not, which is the only
    reliable discriminator (a PR's `number` and `title` look exactly like an
    issue's). `state` and the label rules are re-checked here even though the
    query already filters them, because the query is not the contract: the
    request asks for `labels=agent&state=open` and this function is what the
    queue actually depends on.
    """
    if payload.get("state") != "open":
        return False
    if "pull_request" in payload:
        return False

    labels = _label_names(payload)
    if REQUIRED_LABEL not in labels:
        return False
    if labels & EXCLUDED_LABELS:
        return False
    return open_blockers(payload) == 0


def open_blockers(payload: Mapping[str, Any]) -> int:
    """How many of the issue's GitHub `blocked_by` dependencies are still open.

    `issue_dependencies_summary.blocked_by` counts the open ones only
    (`total_blocked_by` counts all of them), and it rides on every object of the
    `GET /issues` list, so this costs no extra request. An issue with an open
    blocker is not a queue item: `backlog` would implement it against a `main`
    that lacks the blocker's work. On 2026-10-02 run 01M3XAQJBC36XX9AVY65Z23TP9
    took a coder box for womens-fantasy-sports#1279 while its blocker #1278 was
    still open, and the decomposer could only report `needs_human_review`.

    The summary is part of the response body, so a blocker closing changes the
    collection's ETag and the next poll sees the issue again. A missing or
    malformed summary counts as no blockers, which is the behaviour before this
    check existed.
    """
    summary = payload.get("issue_dependencies_summary")
    if not isinstance(summary, Mapping):
        return 0
    count = summary.get("blocked_by")
    if isinstance(count, bool) or not isinstance(count, int):
        return 0
    return max(count, 0)


def fetch_issues(
    repo: str,
    token: str,
    etag: str | None,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[list[Issue], str | None, bool]:
    """Fetch the queue items for one repo.

    Returns `(issues, new_etag, changed)`. `changed` is `False` on a `304`, and
    `issues` is then **empty** — which must never be read as "this repo has no
    work". The caller keeps its cached items and its `first_seen` values.

    Raises `GitHubError` for anything else that is not a `200`: the caller keeps
    the last known items and marks the repo stale, rather than emptying it.
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
    if etag:
        # Verbatim, quotes and all. See the module docstring.
        headers["If-None-Match"] = etag

    params = {"labels": REQUIRED_LABEL, "state": "open", "per_page": PAGE_SIZE}

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

    remaining = response.headers.get("x-ratelimit-remaining")

    if response.status_code == 304:
        log.info(
            "github: %s 304 not modified ratelimit-remaining=%s",
            repo,
            remaining if remaining is not None else "?",
        )
        # Keep the ETag **we sent**, not the one echoed back. A 304 means the
        # representation identified by the ETag we sent is still current, so that
        # string remains the right one to send next time — and GitHub does not
        # echo it byte-for-byte: a `W/"abc"` we send comes back as `"abc"`, the
        # strong form. (Verified live on 2026-09-18: both forms return 304, so
        # this is about not churning the stored value, not about it working.)
        return [], etag or response.headers.get("etag"), False

    if response.status_code != 200:
        raise GitHubError(
            _error_message(repo, response, remaining),
            status_code=response.status_code,
            remaining=remaining,
        )

    payload = response.json()
    if not isinstance(payload, list):
        raise GitHubError(f"{repo}: expected a list of issues, got {type(payload).__name__}")

    seen_at = datetime.now(UTC)
    issues = [_to_issue(repo, item, seen_at) for item in payload if is_eligible(item)]
    for item in payload:
        # An `agent` issue missing from the queue page needs a reason somewhere.
        if isinstance(item, Mapping) and open_blockers(item) > 0:
            log.info(
                "github: %s#%s held out of the queue: %d open blocker(s)",
                repo,
                item.get("number"),
                open_blockers(item),
            )

    new_etag = response.headers.get("etag") or None
    log.info(
        "github: %s 200 %d object(s), %d eligible etag=%s ratelimit-remaining=%s",
        repo,
        len(payload),
        len(issues),
        new_etag or "none",
        remaining if remaining is not None else "?",
    )
    if len(payload) >= PAGE_SIZE:
        # Not an error, but a silent cap is exactly the shape of bug that looks
        # like "the queue is shorter than it is".
        log.warning(
            "github: %s returned a full page of %d objects; the list may be truncated",
            repo,
            len(payload),
        )

    return issues, new_etag, True


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


@dataclass(frozen=True)
class RemainderIssue:
    """One open issue carrying `agent-remainder`, as the promoter needs it.

    `body` is the raw issue body (`""` when GitHub sends `null`); the promoter, not
    this module, parses the marker out of it.
    """

    number: int
    labels: frozenset[str]
    body: str


def fetch_labelled(
    repo: str,
    label: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[RemainderIssue]:
    """Open issues in `repo` carrying `label` (ADR 0011 D6, ADR 0015 C5).

    Unconditional, like `fetch_in_progress`: it asks for a different label
    collection than the ETag-cached inventory, and it runs once a minute per repo,
    which is 240 requests an hour for four repos -- well inside the 5,000/hour
    budget. Pull requests are dropped (`GET /issues` returns them too), and so is
    any object that does not actually carry the label. The raw body travels with
    the issue because the promoter that consumes this parses a marker out of it.

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
    params = {"labels": label, "state": "open", "per_page": PAGE_SIZE}

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
        if label not in labels:
            continue
        body = item.get("body")
        found.append(
            RemainderIssue(
                number=number, labels=labels, body=body if isinstance(body, str) else ""
            )
        )
    return found


def fetch_remainders(
    repo: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[RemainderIssue]:
    """Open issues in `repo` carrying `agent-remainder` (ADR 0011 D6).

    A thin wrapper over `fetch_labelled` kept so `remainder.py` and its tests do
    not change. Raises `GitHubError` for anything that is not a `200`.
    """
    return fetch_labelled(repo, REMAINDER_LABEL, token, client=client, timeout=timeout)


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


def fetch_issue_state(
    repo: str,
    number: int,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[str, str | None]:
    """`(state, state_reason)` for one issue (ADR 0015 C5).

    `GET /repos/{owner}/{repo}/issues/{number}`. `state` is `"open"` or
    `"closed"`; `state_reason` is `"completed"`, `"not_planned"`, `"reopened"`
    or `None`. The held-child promoter reads the predecessor through this, and the
    `state_reason` is what distinguishes "the work landed" from "it was dropped".
    Raises `GitHubError` for anything that is not a `200` -- including a `404` for
    a number that is not an issue -- so the caller can tell "not done yet" from
    "GitHub could not answer".
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
            f"{GITHUB_API}/repos/{repo}/issues/{number}", headers=headers
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
        raise GitHubError(
            f"{repo}: expected an issue object, got {type(payload).__name__}"
        )
    state = payload.get("state") or ""
    reason = payload.get("state_reason")
    return state, reason if isinstance(reason, str) else None


def fetch_sub_issues(
    repo: str,
    number: int,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> list[tuple[int, str, str | None]]:
    """`[(number, state, state_reason)]` for one issue's sub-issues (ADR 0015 C5).

    `GET /repos/{owner}/{repo}/issues/{number}/sub_issues`, paged through every
    `rel="next"` Link. Each result object **is** the sub-issue -- a plain issue
    object with `number`, `state` and `state_reason` at the top level, with no
    wrapper key (checked against `cli/cli#14529` on 2026-09-27). The
    split-parent closer uses this to tell "every child landed" from "one stopped
    early". Raises `GitHubError` for anything that is not a `200`.
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
    results: list[tuple[int, str, str | None]] = []
    url: str | None = f"{GITHUB_API}/repos/{repo}/issues/{number}/sub_issues"
    try:
        while url:
            response = http.get(url, headers=headers)
            if response.status_code != 200:
                remaining = response.headers.get("x-ratelimit-remaining")
                raise GitHubError(
                    _error_message(f"{repo}#{number}", response, remaining),
                    status_code=response.status_code,
                    remaining=remaining,
                )
            payload = response.json()
            if not isinstance(payload, list):
                raise GitHubError(
                    f"{repo}#{number}: expected a list of sub_issues, got {type(payload).__name__}"
                )
            for item in payload:
                if not isinstance(item, Mapping):
                    continue
                n = item.get("number")
                if not isinstance(n, int) or isinstance(n, bool):
                    continue
                state = item.get("state") or ""
                reason = item.get("state_reason")
                results.append((n, state, reason if isinstance(reason, str) else None))
            url = _next_link_url(response)
    except httpx.HTTPError as exc:
        raise GitHubError(f"{repo}#{number}: GitHub request failed: {exc}") from exc
    finally:
        if owned:
            http.close()
    return results


def comment(
    repo: str,
    number: int,
    body: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> None:
    """`POST /repos/{owner}/{repo}/issues/{n}/comments` -- post one comment.

    Idempotent in the sense that a retry duplicates the comment rather than
    corrupting state, but the split-parent closer calls it exactly once, right
    before closing, because a stray duplicate is harmless but a parent closed
    without the observation is the failure mode. Raises `GitHubError` for
    anything that is not a `2xx`.
    """
    path = f"/repos/{repo}/issues/{number}/comments"
    response = _write(
        "POST", path, token, json={"body": body}, client=client, timeout=timeout
    )
    if not 200 <= response.status_code < 300:
        raise _write_error(path, "comment", response)
    log.info("github: %s#%s comment posted", repo, number)


def close_issue(
    repo: str,
    number: int,
    token: str,
    *,
    reason: str = "completed",
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> None:
    """`PATCH /repos/{owner}/{repo}/issues/{n}` -- close with a `state_reason`.

    The split-parent closer closes a parent as `completed` once every child has
    landed. Idempotent: a second close is a `200` no-op. Raises `GitHubError` for
    anything that is not a `2xx`.
    """
    path = f"/repos/{repo}/issues/{number}"
    response = _write(
        "PATCH",
        path,
        token,
        json={"state": "closed", "state_reason": reason},
        client=client,
        timeout=timeout,
    )
    if not 200 <= response.status_code < 300:
        raise _write_error(path, f"close ({reason})", response)
    log.info("github: %s#%s closed (%s)", repo, number, reason)


def _next_link_url(response: httpx.Response) -> str | None:
    """The bare `rel="next"` URL from a Link header, or `None`."""
    link = response.headers.get("link")
    if not link:
        return None
    for part in link.split(","):
        seg = part.split(";")
        if len(seg) < 2:
            continue
        if 'rel="next"' not in seg[1] and "rel='next'" not in seg[1]:
            continue
        url = seg[0].strip()
        if url.startswith("<") and url.endswith(">"):
            return url[1:-1]
    return None


def add_label(
    repo: str,
    number: int,
    label: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> None:
    """`POST /repos/{owner}/{repo}/issues/{n}/labels` — add one label.

    Idempotent: the response is the issue's full label list, `200` whether or not
    the label was already there, so a retry after a partial failure is safe. Raises
    `GitHubError` for anything else, because a dispatch that could not set its own
    receipt must not proceed.
    """
    path = f"/repos/{repo}/issues/{number}/labels"
    response = _write(
        "POST", path, token, json={"labels": [label]}, client=client, timeout=timeout
    )
    if not 200 <= response.status_code < 300:
        raise _write_error(path, label, response)
    log.info("github: %s#%s +%s", repo, number, label)


def remove_label(
    repo: str,
    number: int,
    label: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> None:
    """`DELETE /repos/{owner}/{repo}/issues/{n}/labels/{label}` — remove one.

    **A `404` is success**, not failure. GitHub answers `404` when the label is not
    on the issue at all, and the caller's intent is "this label must not be there"
    — which is equally true either way. Treating it as an error would make the
    retry-after-partial-failure path (roll back the receipt, restore the queue
    label) report failures for work that is already done.
    """
    path = f"/repos/{repo}/issues/{number}/labels/{quote(label, safe='')}"
    response = _write("DELETE", path, token, client=client, timeout=timeout)
    if response.status_code == 404:
        log.info("github: %s#%s -%s (not present)", repo, number, label)
        return
    if not 200 <= response.status_code < 300:
        raise _write_error(path, label, response)
    log.info("github: %s#%s -%s", repo, number, label)


def _write(
    method: str,
    path: str,
    token: str,
    *,
    json: Mapping[str, object] | None = None,
    client: httpx.Client | None,
    timeout: float,
) -> httpx.Response:
    """One mutating request, un-checked. The caller decides what counts as success."""
    if not token or not token.strip():
        raise GitHubError(
            "GITHUB_TOKEN is not set; the scheduler cannot write issue labels"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if json is not None:
        headers["Content-Type"] = "application/json"

    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        return http.request(
            method, f"{GITHUB_API}{path}", headers=headers, json=json, timeout=timeout
        )
    except httpx.HTTPError as exc:
        raise GitHubError(f"{method} {path} failed: {exc}") from exc
    finally:
        if owned:
            http.close()


def _write_error(path: str, label: str, response: httpx.Response) -> GitHubError:
    """A failed label write, in the same shape `fetch_issues` reports failures.

    `path`, not the whole URL, and never the token: this message is logged and can
    reach an HTTP response body. `x-ratelimit-remaining` is carried for the same
    reason it is on the read path — a spent quota and a token without
    `issues: write` are both `403` and are told apart by nothing else.
    """
    remaining = response.headers.get("x-ratelimit-remaining")
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping) and isinstance(body.get("message"), str):
        detail = f": {body['message']}"

    hint = ""
    if response.status_code == 403:
        hint = (
            " (rate limit exhausted)"
            if remaining == "0"
            else " (not a rate limit — check the token has `issues: write` on this repo)"
        )

    return GitHubError(
        f"{path} [{label}] -> HTTP {response.status_code}{detail}{hint} "
        f"[x-ratelimit-remaining={remaining if remaining is not None else '?'}]",
        status_code=response.status_code,
        remaining=remaining,
    )


def _label_names(payload: Mapping[str, Any]) -> frozenset[str]:
    labels = payload.get("labels")
    # A list or tuple, not merely a `Sequence`: a bare string is a Sequence, and
    # iterating it would read `"agent"` as five one-character labels.
    if not isinstance(labels, (list, tuple)):
        return frozenset()
    names = set()
    for label in labels:
        if isinstance(label, Mapping) and isinstance(label.get("name"), str):
            names.add(label["name"])
        elif isinstance(label, str):
            names.add(label)
    return frozenset(names)


def _to_issue(repo: str, payload: Mapping[str, Any], seen_at: datetime) -> Issue:
    number = payload.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise GitHubError(f"{repo}: issue object without a numeric `number`")
    title = payload.get("title")
    return Issue(
        repo=repo,
        number=number,
        title=title if isinstance(title, str) else "",
        labels=_label_names(payload),
        first_seen=seen_at,
    )


def _error_message(repo: str, response: httpx.Response, remaining: str | None) -> str:
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping) and isinstance(body.get("message"), str):
        detail = f": {body['message']}"

    hint = ""
    if response.status_code == 403:
        # The whole point of carrying this number: `remaining=0` is a spent
        # quota, anything else is a permission problem with the token.
        if remaining == "0":
            hint = " (rate limit exhausted)"
        else:
            hint = (
                " (not a rate limit — check the token has `issues: read` on this repo; "
                f"x-ratelimit-remaining={remaining})"
            )

    return (
        f"{repo}: GitHub returned {response.status_code}{detail}{hint} "
        f"[x-ratelimit-remaining={remaining if remaining is not None else '?'}]"
    )
