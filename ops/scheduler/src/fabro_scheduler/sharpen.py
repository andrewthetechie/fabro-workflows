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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import SchedulerConfig
from .github import GitHubError
from .precheck import hold_label
from .queue import QueueItem, rank
from .store import SHARPEN_TERMINAL_STATES, Store

log = logging.getLogger(__name__)

# `precheck-unverifiable`: the verdict with `_` written as `-`, like every other hold label.
LABEL = hold_label("unverifiable")

# The terminal states that released an issue without applying a rewrite: the do-nothing comparison
# the measure section and the scorecard both draw (D6). `superseded` and `not_needed` are neither.
UNAPPLIED_STATES = ("capped", "churn", "cannot_sharpen", "failed", "stale")

# D12: a run that fails, times out or is lost is retried up to two more times, at least 30 minutes
# apart, so three attempts in all. A model's own refusal (`cannot_sharpen`, `capped`, `churn`) is
# an ending, never a fault, and is never retried.
MAX_ATTEMPTS = 3
RETRY_SPACING = timedelta(minutes=30)
# A dispatch that never produced a run (fabro refused it, or the version could not be built).
DISPATCH_RETRY = timedelta(minutes=5)

# The outcomes a succeeded run's `record` can carry that end the row (C11). `failed` is not one:
# it makes the run end failed, which is a fault to retry.
RECORDED_ENDINGS = ("applied", "capped", "churn", "stale", "cannot_sharpen", "not_needed")

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


@dataclass(frozen=True)
class Recorded:
    """The `record` stage's context updates (C11): how a sharpening run ended."""

    outcome: str
    rule: str = ""
    round: int = 0
    body_hash: str = ""
    applied_hash: str = ""
    recheck: str = ""
    changes: int = 0
    transformations: str = ""


def read_record(events: Iterable[Mapping[str, Any]]) -> Recorded | None:
    """The last `record` stage's context updates, or `None` when the run has none.

    The same shape as `calibration.read_record`: `record` is the package's last node, so its
    `stage.completed` is the last word. Consumed once and never held.
    """
    found: Recorded | None = None
    for event in events:
        if (event.get("event") or event.get("type")) != "stage.completed":
            continue
        props = event.get("properties")
        if not isinstance(props, Mapping) or event.get("node_id") != "record":
            continue
        updates = props.get("context_updates")
        if not isinstance(updates, Mapping):
            continue
        outcome = updates.get("sharpen_outcome")
        if not isinstance(outcome, str) or not outcome:
            continue
        found = Recorded(
            outcome=outcome,
            rule=str(updates.get("sharpen_rule") or ""),
            round=_int(updates.get("round")),
            body_hash=str(updates.get("body_hash") or ""),
            applied_hash=str(updates.get("applied_hash") or ""),
            recheck=str(updates.get("recheck") or ""),
            changes=_int(updates.get("changes")),
            transformations=str(updates.get("transformations") or ""),
        )
    return found


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def settle(
    store: Store,
    row,
    *,
    kind: str,
    reason: str | None,
    recorded: Recorded | None,
    remove_label: LabelWrite,
    now: datetime,
    box_minutes: float | None = None,
) -> str:
    """Write what a finished run did to its `sharpen` row and the hold. Returns the new state.

    `kind` and `reason` are the run's fabro status (`succeeded`, `failed`/`terminated`, ...), or
    `lost` when fabro no longer has it. Succeeded with an ending in the record: the row ends there.
    A fabro restart or an operator's cancel puts the row back without counting the attempt. Every
    other failure counts, and the third ends the row `failed` (D12). The label comes off on every
    terminal state, and a failed write is the inventory pass's to retry.
    """
    repo, number = str(row["repo"]), int(row["issue_number"])
    if kind == "succeeded" and recorded is not None and recorded.outcome in RECORDED_ENDINGS:
        store.sharpen_finish(
            int(row["id"]), state=recorded.outcome, now=now,
            applied_hash=recorded.applied_hash, recheck=recorded.recheck, round_=recorded.round,
            rule=recorded.rule, transformations=recorded.transformations, box_minutes=box_minutes,
        )
        _write(remove_label, repo, number, "remove")
        return recorded.outcome
    if reason in ("terminated", "cancelled"):
        store.sharpen_requeue(int(row["id"]))
        return "queued"
    why = (
        f"{kind}/{reason}" if reason else kind
    ) + ("" if recorded is None else f" (record: {recorded.outcome})")
    if int(row["attempts"] or 0) >= MAX_ATTEMPTS:
        store.sharpen_finish(
            int(row["id"]), state="failed", now=now, rule=why, box_minutes=box_minutes
        )
        _write(remove_label, repo, number, "remove")
        return "failed"
    store.sharpen_retry(int(row["id"]), next_eligible_at=now + RETRY_SPACING, rule=why)
    return "queued"


def queued_in_order(
    store: Store, config: SchedulerConfig, now: datetime, *, only_due: bool = True
) -> list[tuple[Any, Any]]:
    """The `queued` rows in dispatch order, as `(row, repo config)`.

    The order is the coder queue's own (`queue.rank`: repo priority, then issue number, so the
    issue that would be worked next is sharpened first, D3), over the issues the rows name. A
    row whose issue left the cache, or whose repo is no longer schedulable, is left out: nothing
    can run it. `only_due` leaves out a row still waiting for its `next_eligible_at` (D12).
    """
    items: list[QueueItem] = []
    found: dict[tuple[str, int], tuple[Any, Any]] = {}
    for row in store.sharpen_live_rows():
        if row["state"] != "queued":
            continue
        due = row["next_eligible_at"]
        if only_due and due and datetime.fromisoformat(due) > now:
            continue
        issue = store.get_issue(str(row["repo"]), int(row["issue_number"]))
        repo_row = config.repo_named(str(row["repo"]))
        if issue is None or repo_row is None or not repo_row.enabled:
            continue
        found[(issue.repo, issue.number)] = (row, repo_row)
        items.append(QueueItem(
            issue=issue, repo_priority=repo_row.priority, override_rank=None,
            waited=now - issue.first_seen,
        ))
    return [found[(item.issue.repo, item.issue.number)]
            for item in rank(items, config.starvation_ceiling)]
