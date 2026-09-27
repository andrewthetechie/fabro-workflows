"""Split-parent/child bookkeeping: promote held children, close split parents
(ADR 0015, C5).

Triage divides a large issue -- the Split parent, labelled `agent-split` -- into
up to three ordered Child issues. A Child with no predecessor is created `agent`
and joins the queue immediately; every later Child is created `agent-held`, out
of the queue, because a `main` that lacks its predecessor's work is the wrong
tree to implement it against. The first line of a Child's body is

    <!-- fabro:split-child parent=<P> after=<N> -->

where `after` (0 for the first child) is the **issue number** of the predecessor
this child waits on. This module, called once a minute per repo from the
inventory poll:

- **promote_held**: for each held child, read its predecessor `after`:
  - closed with `state_reason: completed`: add `priority`, then `agent`, then
    remove `agent-held` -- the child joins the queue, ranked next.
  - closed any other way: add `agent-stuck`, remove `agent-held` -- the work it
    continues never landed, so a human decides.
  - open: leave it alone. `after=0`, or a missing marker: log and leave alone.
- **close_parents**: for each open split parent, read its sub-issues:
  - every child closed: comment `All child issues landed.` and close the parent
    with `state_reason: completed`.
  - any child closed another way: add `agent-stuck` (once).
  - an empty sub-issue list: log and leave alone.

**Order matters, kept from `remainder.py`.** `priority` is added before `agent`,
and `agent` before `agent-held` is removed, so a half-finished promotion leaves
an issue that carries `agent` and is never re-labelled or re-added. `agent-stuck`
is only ever added to a parent if it is not already there, so the closer does not
re-report the same stalled parent every minute.
"""

from __future__ import annotations

import logging
import re

import httpx

from .github import (
    HELD_LABEL,
    IN_PROGRESS_LABEL,
    PRIORITY_LABEL,
    REQUIRED_LABEL,
    SPLIT_LABEL,
    STUCK_LABEL,
    GitHubError,
    add_label,
    close_issue,
    comment,
    fetch_issue_state,
    fetch_labelled,
    fetch_sub_issues,
    remove_label,
)

log = logging.getLogger(__name__)

MARKER = re.compile(r"<!-- fabro:split-child parent=(\d+) after=(\d+) -->")

# Any of these means the promotion already happened (or a human took over): only
# the `agent-held` cleanup is left to do.
_PAST_PROMOTION = frozenset({REQUIRED_LABEL, IN_PROGRESS_LABEL, STUCK_LABEL})


def parse_marker(body: str) -> tuple[int, int] | None:
    """`(parent, after)` from a child body, or `None` if there is no marker."""
    match = MARKER.search(body)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def promote_held(
    repo: str, token: str, *, client: httpx.Client | None = None
) -> list[tuple[int, str]]:
    """Promote or park every held child issue in `repo` (ADR 0015 C5).

    Returns `(issue_number, action)` for each issue it changed, where `action` is
    `"promoted"`, `"orphaned"` or `"cleaned"`. Raises `GitHubError` only when the
    list of held issues itself cannot be read; a failure on one issue is logged
    and the next issue is still processed.
    """
    changed: list[tuple[int, str]] = []
    for held in fetch_labelled(repo, HELD_LABEL, token, client=client):
        try:
            action = _promote_one(repo, held.number, held.labels, held.body, token, client)
        except GitHubError as exc:
            log.warning("split: %s#%s not promoted this pass: %s", repo, held.number, exc)
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
        remove_label(repo, number, HELD_LABEL, token, client=client)
        return "cleaned"

    marker = parse_marker(body)
    if marker is None:
        log.warning(
            "split: %s#%s carries %s but has no fabro:split-child marker; left alone",
            repo, number, HELD_LABEL,
        )
        return None
    parent, after = marker
    if after == 0:
        log.info(
            "split: %s#%s has after=0; not held behind a predecessor", repo, number
        )
        return None

    state, state_reason = fetch_issue_state(repo, after, token, client=client)
    if state == "closed":
        if state_reason == "completed":
            add_label(repo, number, PRIORITY_LABEL, token, client=client)
            add_label(repo, number, REQUIRED_LABEL, token, client=client)
            remove_label(repo, number, HELD_LABEL, token, client=client)
            log.info(
                "split: %s#%s promoted (parent #%s, pred #%s completed)",
                repo, number, parent, after,
            )
            return "promoted"
        add_label(repo, number, STUCK_LABEL, token, client=client)
        remove_label(repo, number, HELD_LABEL, token, client=client)
        log.warning(
            "split: %s#%s parked as %s: pred #%s closed without completing",
            repo, number, STUCK_LABEL, after,
        )
        return "orphaned"
    return None


def close_parents(
    repo: str, token: str, *, client: httpx.Client | None = None
) -> list[tuple[int, str]]:
    """Close finished split parents, and park stalled ones (ADR 0015 C5).

    Returns `(issue_number, action)` for each parent it changed, where `action` is
    `"closed"` or `"stuck"`. Raises `GitHubError` only when the list of split
    parents itself cannot be read; a failure on one parent is logged and the next
    parent is still processed.
    """
    changed: list[tuple[int, str]] = []
    for parent in fetch_labelled(repo, SPLIT_LABEL, token, client=client):
        try:
            action = _close_one(repo, parent.number, parent.labels, token, client)
        except GitHubError as exc:
            log.warning(
                "split: %s#%s parent not processed this pass: %s", repo, parent.number, exc
            )
            continue
        if action is not None:
            changed.append((parent.number, action))
    return changed


def _close_one(
    repo: str, number: int, labels: frozenset[str], token: str, client: httpx.Client | None
) -> str | None:
    children = fetch_sub_issues(repo, number, token, client=client)
    if not children:
        log.info(
            "split: %s#%s split parent has no sub-issues; left alone", repo, number
        )
        return None

    # Careful with state_reason: GitHub returns None when an issue is closed with
    # no explicit reason, and closed-by-merge is not possible for an issue. Any
    # closed child that is not "completed" means the sequence of work stopped, so
    # the parent is parked rather than closed as finished.
    failed = [
        n
        for n, state, reason in children
        if state == "closed" and reason != "completed"
    ]
    if failed:
        if STUCK_LABEL not in labels:
            add_label(repo, number, STUCK_LABEL, token, client=client)
            log.warning(
                "split: %s#%s marked %s: sub-issue(s) %s closed without completing",
                repo, number, STUCK_LABEL, sorted(failed),
            )
            return "stuck"
        return None

    if all(state == "closed" and reason == "completed" for _, state, reason in children):
        comment(repo, number, "All child issues landed.", token, client=client)
        close_issue(repo, number, token, reason="completed", client=client)
        log.info(
            "split: %s#%s closed completed (all %d children landed)",
            repo, number, len(children),
        )
        return "closed"
    return None
