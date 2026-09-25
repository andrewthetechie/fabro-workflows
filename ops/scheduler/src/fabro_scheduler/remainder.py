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
