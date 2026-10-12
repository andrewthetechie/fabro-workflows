"""What became of a factory PR, read from GitHub for the scorecard (issue #22).

`run_history` records what a run's ending WAS at release (ADR 0008). The scorecard
also needs what became of the PR afterwards, and three facts no release row holds:

* **Who merged it.** `mergedBy` cannot answer: the workflow and the operator merge
  as the same account. The release row's `merged` says the merge phase merged it
  before the lease was released. Since ADR 0024 a PR can also be merged later by the
  `merge-queue` workflow, and those PRs carry the merge phase's approval marker
  (`<!-- fabro:merge-queue approved ... -->`) with no hand-off marker after it.
* **Operator commits.** A commit whose author is none of the automation's identities
  (`SCHEDULER_AUTOMATION_AUTHOR`, a comma-separated list of GitHub logins and email
  addresses; see `is_automation_commit`).
* **Why it was held.** The merge phase's last report comment carries a
  `**Reason:**` line when it did not merge (`render.jq` in `_shared/review-merge`).

Three GETs per PR (the pull, its commits, its issue comments), each paged. The
collector fetches a PR until it is merged or closed and then never again, so the
steady cost is the handful of PRs that are open.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .github import (
    GITHUB_API,
    REQUEST_TIMEOUT_SECONDS,
    GitHubError,
    _error_message,
    _next_link_url,
)

# The headings `render.jq` opens a report comment with, after an optional marker
# line. A comment that does not start with one is a human's, or another tool's.
REPORT_HEADINGS = ("## Merged automatically", "## AI PR review")

APPROVED_MARKER = re.compile(r"^<!-- fabro:merge-queue approved head=")
HANDOFF_MARKER = re.compile(r"^<!-- fabro:merge-queue handoff head=")
REASON_LINE = re.compile(r"^\*\*Reason:\*\*\s*(.*)$", re.MULTILINE)

# Pages are 100 items; a PR with more than 1,000 commits (one per stage, so
# possible on a long run) or comments is truncated rather than fetched forever.
MAX_PAGES = 10


@dataclass(frozen=True)
class PrFacts:
    state: str  # "open" | "merged" | "closed"
    created_at: str | None
    merged_at: str | None
    closed_at: str | None
    labels: tuple[str, ...]
    additions: int
    deletions: int
    commits: int
    operator_commits: int
    report: str | None  # "merged" | "queued" | "complete" | "blocked" | "needs-human"
    reason: str | None  # the last report's **Reason:** text, when it has one
    queue_approved: bool
    queue_handoff: bool


def fetch_pr_facts(
    repo: str,
    number: int,
    token: str,
    automation_author: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> PrFacts:
    """Read one PR's facts. Raises `GitHubError` for any non-`200` answer."""
    if not token or not token.strip():
        raise GitHubError("GITHUB_TOKEN is not set; PR facts cannot be read")
    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        pull = _get_one(http, f"{GITHUB_API}/repos/{repo}/pulls/{number}", token, repo)
        commits = _get_all(
            http, f"{GITHUB_API}/repos/{repo}/pulls/{number}/commits?per_page=100", token, repo
        )
        comments = _get_all(
            http, f"{GITHUB_API}/repos/{repo}/issues/{number}/comments?per_page=100", token, repo
        )
    except httpx.HTTPError as exc:
        raise GitHubError(f"{repo}#{number}: GitHub request failed: {exc}") from exc
    finally:
        if owned:
            http.close()
    return parse_pr_facts(pull, commits, comments, automation_author)


def parse_pr_facts(
    pull: Mapping[str, Any],
    commits: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    automation_author: str,
) -> PrFacts:
    """The pure half of `fetch_pr_facts`: GitHub payloads in, `PrFacts` out."""
    merged_at = _str_or_none(pull.get("merged_at"))
    if merged_at is not None:
        state = "merged"
    elif pull.get("state") == "closed":
        state = "closed"
    else:
        state = "open"

    labels = tuple(
        sorted(
            label["name"]
            for label in pull.get("labels") or []
            if isinstance(label, Mapping) and isinstance(label.get("name"), str)
        )
    )

    operator = sum(1 for commit in commits if not is_automation_commit(commit, automation_author))

    report: str | None = None
    reason: str | None = None
    approved = False
    handoff = False
    for item in comments:  # oldest first, so the last report wins
        body = item.get("body")
        if not isinstance(body, str):
            continue
        first = body.split("\n", 1)[0]
        if APPROVED_MARKER.match(first):
            approved, handoff = True, False
        elif HANDOFF_MARKER.match(first):
            handoff = True
        kind = report_kind(body)
        if kind is not None:
            report = kind
            found = REASON_LINE.search(body)
            reason = found.group(1).strip() if found else None

    return PrFacts(
        state=state,
        created_at=_str_or_none(pull.get("created_at")),
        merged_at=merged_at,
        closed_at=_str_or_none(pull.get("closed_at")),
        labels=labels,
        additions=_int(pull.get("additions")),
        deletions=_int(pull.get("deletions")),
        commits=len(commits),
        operator_commits=operator,
        report=report,
        reason=reason,
        queue_approved=approved,
        queue_handoff=handoff,
    )


def report_kind(body: str) -> str | None:
    """Which `render.jq` report a comment is, or None when it is not one."""
    lines = body.split("\n")
    if lines and lines[0].startswith("<!-- fabro:"):
        lines = lines[1:]
    head = lines[0].strip() if lines else ""
    if not head.startswith(REPORT_HEADINGS):
        return None
    if head.startswith("## Merged automatically"):
        return "merged"
    if "human needed" in head:
        return "needs-human"
    if "queued to merge" in head:
        return "queued"
    if "### Not auto-merged" in body:
        return "blocked"
    return "complete"


def is_automation_commit(commit: Mapping[str, Any], automation_author: str) -> bool:
    """True when the commit's author is one of the automation's identities.

    `automation_author` is a comma-separated list. An entry with an `@` is a whole
    email address; any other entry is a GitHub login, matched on `author.login`
    when GitHub linked the commit to an account, and otherwise on the local part of
    the commit's author email (`<login>@users.noreply.github.com`, or the
    `<id>+<login>@` form). Everything compares case-insensitively. Runs before the
    run identity was set committed as `Fabro <noreply@fabro.sh>`, which is why the
    default carries both.
    """
    wanted = [part.strip().casefold() for part in automation_author.split(",") if part.strip()]
    if not wanted:
        return True  # unconfigured: count no commit as the operator's
    logins = {w for w in wanted if "@" not in w}
    emails = {w for w in wanted if "@" in w}
    author = commit.get("author")
    if isinstance(author, Mapping) and isinstance(author.get("login"), str):
        if author["login"].casefold() in logins:
            return True
    inner = commit.get("commit")
    if isinstance(inner, Mapping):
        meta = inner.get("author")
        if isinstance(meta, Mapping) and isinstance(meta.get("email"), str):
            email = meta["email"].casefold()
            if email in emails:
                return True
            local = email.split("@", 1)[0]
            if local in logins or local.split("+", 1)[-1] in logins:
                return True
    return False


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_one(http: httpx.Client, url: str, token: str, repo: str) -> Mapping[str, Any]:
    response = http.get(url, headers=_headers(token))
    if response.status_code != 200:
        remaining = response.headers.get("x-ratelimit-remaining")
        raise GitHubError(
            _error_message(repo, response, remaining),
            status_code=response.status_code,
            remaining=remaining,
        )
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise GitHubError(f"{repo}: expected an object, got {type(payload).__name__}")
    return payload


def _get_all(http: httpx.Client, url: str, token: str, repo: str) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    next_url: str | None = url
    for _ in range(MAX_PAGES):
        if next_url is None:
            break
        response = http.get(next_url, headers=_headers(token))
        if response.status_code != 200:
            remaining = response.headers.get("x-ratelimit-remaining")
            raise GitHubError(
                _error_message(repo, response, remaining),
                status_code=response.status_code,
                remaining=remaining,
            )
        payload = response.json()
        if not isinstance(payload, list):
            raise GitHubError(f"{repo}: expected a list, got {type(payload).__name__}")
        items.extend(item for item in payload if isinstance(item, Mapping))
        next_url = _next_link_url(response)
    return items


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
