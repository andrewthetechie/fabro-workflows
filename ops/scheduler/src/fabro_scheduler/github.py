"""The GitHub half of the inventory: open `agent`-labelled issues, cached by ETag.

Read-only, and deliberately so. Nothing in this module writes a label, a comment
or a state; the label writes that dispatch needs are draft 08's business. The
worst thing a bug here can do is show the operator a stale queue.

Two facts drive the shape of the one request this makes.

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
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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

REQUIRED_LABEL = "agent"

# `agent-stuck` is what `mark_stuck` leaves on an issue a human has to re-arm;
# `agent-in-progress` is the scheduler's own durable handoff receipt. A queue item
# is neither (CONTEXT.md, **Queue item**).
EXCLUDED_LABELS = frozenset({"agent-in-progress", "agent-stuck"})


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
    return not labels & EXCLUDED_LABELS


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
