"""Issue sharpening, the scheduler's half of the queue (issue-sharpening 04, ADR 0033).

An issue the precheck rates `unverifiable` has a criterion the Refuter cannot credit, so its
pull request would wait for a human. The factory rewrites the criteria itself, in one leased run
of the `issue-sharpen` package (task 05 dispatches it). This module decides **which** issues are
waiting for that run, and keeps them out of the queue until it is over.

**The hold (C7, D16).** An issue in the queue is *sharpen-held* when its newest `done` precheck
says `unverifiable` for its current text and no `sharpen` row of the issue names that text as the
text it was queued for (`body_hash`) or the text it produced (`applied_hash`). A held issue gets
the label `precheck-unverifiable` first and a `sharpen` row second, so a pass that stops halfway
leaves it held and never queued with a stale verdict. The hold is its own code path: it reads
neither `SCHEDULER_PRECHECK_HOLDS` nor `SCHEDULER_PRECHECK_HOLD_VERDICTS`, which no longer
accept `unverifiable` at all (`config.PRECHECK_HOLDABLE`).

**The release.** The label comes off when the row reaches a terminal state (task 05's release),
or here, when the issue's text changed while the row was still `queued`: the row ends
`superseded` and the issue is judged again by an ordinary precheck of its new text. A `running`
row is left to its run, whose `apply` stops `stale` if the text moved.

**The back-fill (D17).** Rows from before this series carry no hash, so at the first pass after a
scheduler start each queued issue whose newest `done` precheck said `unverifiable` is held
whatever text that precheck judged, once. The run's `claim` decides whether the verdict still
applies to the text the issue has now.

Every pass here **never raises**: a failed label write is logged and retried on the next
inventory poll, as the remainder and release passes are.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from .github import GitHubError
from .precheck import hold_label
from .store import SHARPEN_TERMINAL_STATES, Store

log = logging.getLogger(__name__)

# `precheck-unverifiable`: the verdict with `_` written as `-`, like every other hold label.
LABEL = hold_label("unverifiable")

# What a label write is: `(repo, issue number, label)`. The inventory binds the token and client.
LabelWrite = Callable[[str, int, str], object]


def parse_outcome(outcome: object) -> tuple[str, str]:
    """`(verdict, body hash)` of a precheck job outcome, `""` for each that is absent.

    The outcome is `verdict=<v> body=<hash>`; a job recorded before the hash was added has only
    `verdict=<v>`.
    """
    if not isinstance(outcome, str) or not outcome.startswith("verdict="):
        return "", ""
    words = dict(word.split("=", 1) for word in outcome.split() if "=" in word)
    return words.get("verdict", ""), words.get("body", "")


def judged_hash(job) -> str:
    """The text a precheck job judged: the marker's `body=`, else the hash it was queued for."""
    _, hashed = parse_outcome(job["outcome"])
    return hashed or str(job["target_hash"] or "")


def hold_reason(store: Store, repo: str, number: int) -> str | None:
    """The queue page's reason for a sharpen-held issue, or `None` when it is not one."""
    live = store.sharpen_live_row(repo, number)
    if live is None:
        return None
    return "being sharpened (" + str(live["state"]) + ")"


def reconcile_repo(
    repo: str,
    store: Store,
    add_label: LabelWrite,
    remove_label: LabelWrite,
    *,
    backfill: bool = False,
    now: datetime | None = None,
) -> list[tuple[int, str]]:
    """One pass over `repo`'s cached issues: supersede, release, hold. Returns `(number, action)`.

    Idempotent: a second pass over an unchanged world does nothing. `backfill` is the start-up
    pass (D17), which accepts a precheck that judged any text, or none.
    """
    now = now or datetime.now(UTC)
    done: list[tuple[int, str]] = []
    for issue in store.issues():
        if issue.repo != repo:
            continue
        action = _one(issue, store, add_label, remove_label, backfill, now)
        if action:
            done.append((issue.number, action))
    return done


def _one(issue, store: Store, add_label: LabelWrite, remove_label: LabelWrite,
         backfill: bool, now: datetime) -> str | None:
    repo, number = issue.repo, issue.number
    labelled = LABEL in issue.labels
    live = store.sharpen_live_row(repo, number)

    if live is not None:
        if live["state"] == "queued" and issue.body_hash and live["body_hash"] != issue.body_hash:
            store.sharpen_supersede(int(live["id"]), now)
            if labelled and not _write(remove_label, repo, number, "remove"):
                return "superseded; the label stays until the next pass"
            return "superseded: the text changed while it was queued"
        return None

    rows = store.sharpen_rows(repo, number)
    if labelled and rows and rows[0]["state"] in SHARPEN_TERMINAL_STATES:
        # The release already ran and the label write failed, or the row ended some other way:
        # a terminal row never holds an issue.
        if _write(remove_label, repo, number, "remove"):
            return "released: its sharpening is over"
        return None

    if not issue.body_hash or store.sharpen_names_hash(repo, number, issue.body_hash):
        return None
    if backfill and rows:
        # D17: the start-up pass holds only an issue with no sharpening at all behind it.
        return None
    job = store.newest_done_precheck(repo, number)
    if job is None or parse_outcome(job["outcome"])[0] != "unverifiable":
        return None
    judged = judged_hash(job)
    if not backfill and judged != issue.body_hash:
        # Only the start-up pass trusts a precheck that judged another text, or none (D17).
        return None
    if not labelled:
        if not _write(add_label, repo, number, "add"):
            return None
        store.add_cached_label(repo, number, LABEL)
    if store.sharpen_enqueue(repo, number, issue.body_hash, now) is None:
        return None
    return "held for sharpening" + (" (back-fill)" if backfill else "")


def _write(write: LabelWrite, repo: str, number: int, what: str) -> bool:
    try:
        write(repo, number, LABEL)
    except (GitHubError, ValueError) as exc:
        log.warning("sharpen: %s#%s: could not %s %s (%s)", repo, number, what, LABEL, exc)
        return False
    return True
