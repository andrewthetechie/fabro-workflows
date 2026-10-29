"""The PR doctor: which blocked pull requests it may fix, and the job it queues.

A blocked factory PR gets **one** box attempt to fix what its merge phase's report says is
wrong, then **one** hosted `pr-review` (more-local-inference D13, D14, task 06). This module is
the part of that with no I/O: the reason table, the marker, and the eligibility rules. The
graph that does the fixing is `.fabro/workflows/pr-doctor/`; the run, the review lease and the
hand-off are `background.py`, `dispatch.py` and `reconcile.py`.

**The reason table is the whole gate.** Every block in `_shared/review-merge/` and `ralph`'s
copy writes a `**Reason:**` line, and the doctor can only fix some of them. A text that maps
to no prefix is *not* eligible: a doctor that guessed at "the run stopped after validate"
would spend a box, a hosted review and a human's attention on a PR whose real problem is an
infrastructure stop, and it would record that attempt forever (D13).

**One attempt, ever, and it is recorded in GitHub.** The marker comment `publish` posts is the
record (`C6`), and it also stops the doctor *before* it starts: a PR that already carries one
is never enqueued again, whatever happened to the run that wrote it. `jobs.background_job_exists`
is the same rule at the queue level, so a run that died before it could post a marker does not
come back for a second try either.

**A stale report is not eligible, and staleness is judged by time, not by SHA** (operator
decision 6). A report's `Reviewed HEAD:` is a sandbox-local commit fabro rewrites when it
pushes, so it is never the PR's head and GitHub has never heard of it (404 on all 19 live
blocked PRs on 2026-10-29). What is decidable is whether the PR's head commit was created
*after* the report was posted: if it was, someone pushed since the review and the report no
longer describes the code.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import BACKGROUND_JOB_PRIORITY, PR_DOCTOR_TYPICAL_MINUTES, SchedulerConfig
from .github import GitHubError, add_label, comment
from .prfacts import PrFacts
from .store import Store

log = logging.getLogger(__name__)

KIND = "pr-doctor"

# The one comment the hand-off posts (task 06 step 8). One string, so a test can assert it and
# an operator can grep for it.
HANDOFF_COMMENT = (
    "The PR doctor and one follow-up review did not get this merged; "
    "a human needs to take it from here."
)

# The hand-off when no review ran: `pushed=none`, or a `review=skip` marker (operator
# decision 2), or a review whose dispatch failed three times. The sentence above names a review
# that never ran, and a report that states a stopping point the run passed is what
# invariants-merge's *Reporting* rows exist to prevent -- a block reason nobody can verify is
# worse than one that is merely different from the task's quoted string.
HANDOFF_NO_REVIEW_COMMENT = (
    "The PR doctor's one attempt did not get this merged, and no follow-up review would "
    "pass; a human needs to take it from here."
)

# The label every hand-off restores (D13): the merge queue hands off any PR carrying it, so a
# PR the doctor could not merge stops being a queue candidate.
NEEDS_HUMAN_LABEL = "ai-review-needs-human"

# `retryable: ...` means the merge phase stopped on a transient infrastructure failure: it
# wants a re-review, not a fix, and a fix attempt would record the PR's one doctor run against
# a problem the code did not cause.
RETRYABLE_PREFIX = "retryable: "

# The reason table: a `**Reason:**` text prefix to the work the doctor would do. The five
# categories and their fix prompts are task 06's; `none` is every other text, which is not in
# this table at all.
FINDINGS = "findings"
REFUTER = "refuter"
CI = "ci"
HYGIENE = "hygiene"
REVIEW = "review"
CATEGORIES = (FINDINGS, REFUTER, CI, HYGIENE, REVIEW)

# Ordered: the first prefix that matches wins. Every entry is the exact opening text of one
# `fail '...'` in `_shared/review-merge/review-merge.fabro` (or its `ralph` copy), or of the
# text `refute_gate` writes to `refute_reason.txt`.
REASON_PREFIXES: tuple[tuple[str, str], ...] = (
    # fix_gate, line 951: the review's own fixer left findings unfixed.
    ("There are unfixed findings; a human must merge this PR.", FINDINGS),
    # fix_gate, the error-severity variant: an error finding the fix record does not account
    # for. Same work as the line above.
    (
        "The review reported an error-severity finding of its own that its record does not "
        "account for",
        FINDINGS,
    ),
    # refute_gate writes this to /tmp/fabro/review/refute_reason.txt, which fix_gate's
    # refuter check copies into the reason verbatim. `not_met` and `unverifiable` are both
    # fixable by code; the Refuter cannot prove them because it never runs a command.
    ("The refuter could not confirm the work is done: ", REFUTER),
    # validate: red after the second fix attempt.
    ("CI is still red after the maximum number of fix attempts.", CI),
    # watch_checks: red after both CI-fix tiers.
    ("CI is still failing after two fix attempts (", CI),
    # hygiene_reason.txt: the tamper/erosion block, samples included. A `config_changed`
    # block is the same prefix and is excluded separately below.
    ("Diff hygiene blocks the merge: ", HYGIENE),
    # fix_gate, line 943: the fixer returned needs_human or wrote no valid result. It is here
    # because the live reports under it list concrete leftovers (operator decision 1).
    ("The review did not end in a mergeable state.", REVIEW),
)


def classify(reason: str | None) -> str | None:
    """The category a `**Reason:**` text belongs to, or `None` when nothing maps.

    `None` is "the doctor does not know this text" and is not eligible. A `retryable:`
    prefix is not a category either: it is excluded before this is called, and it keeps its
    prefix so it cannot match a fixable text by accident.
    """
    if not reason:
        return None
    text = reason.strip()
    if text.startswith(RETRYABLE_PREFIX):
        return None
    for prefix, category in REASON_PREFIXES:
        if text.startswith(prefix):
            return category
    return None


def is_retryable(reason: str | None) -> bool:
    """Whether the block is a transient infrastructure stop (`retryable: ...`)."""
    return bool(reason) and reason.strip().startswith(RETRYABLE_PREFIX)


def is_config_change(reason: str | None) -> bool:
    """Whether a `Diff hygiene blocks the merge:` text names `config_changed`.

    A PR that touches `.fabro/hygiene.json`, `.fabro/test.toml`, `.fabro/ci.sh` or
    `.fabro/scout.toml` is merged by a human by design (ADR 0018 D7, ADR 0027 D14), because it
    can loosen the rules that judge it. The hygiene reason embeds the blocking counter names,
    so `config_changed=1` appears in the text itself.
    """
    return bool(reason) and "config_changed" in reason


# --- the marker -------------------------------------------------------------------------

MARKER_PREFIX = "<!-- fabro:pr-doctor"
MARKER_SUFFIX = "-->"
MARKER_FIELD = "{}="

# `fire` unless the marker says otherwise: a marker written before the `review=` field existed
# (or by hand) must not silently disable the review (operator decision 2).
REVIEW_FIRE = "fire"
REVIEW_SKIP = "skip"


@dataclass(frozen=True)
class Marker:
    """One parsed `<!-- fabro:pr-doctor ... -->` line (C6)."""

    head: str | None = None
    pushed: str | None = None  # "pending" | a sha | "none"
    review: str = REVIEW_FIRE
    run: str | None = None

    @property
    def wants_review(self) -> bool:
        return self.review != REVIEW_SKIP

    @property
    def pushed_nothing(self) -> bool:
        return self.pushed == "none"


def format_marker(*, head: str | None, pushed: str, review: str, run: str | None) -> str:
    """The marker line, exactly as C6 gives it."""
    return (
        f"{MARKER_PREFIX} head={head or ''} pushed={pushed} "
        f"review={review} run={run or ''} {MARKER_SUFFIX}"
    )


def parse_marker(line: str | None) -> Marker | None:
    """Parse a marker's first line. `None` for anything that is not one.

    Every field is optional in the reader: an absent `review=` reads as `fire` and an absent
    `pushed=` as `None`, so a marker that a human edited is still a marker -- which means the
    PR keeps its one attempt rather than getting a second.
    """
    if not line:
        return None
    text = line.strip()
    if not text.startswith(MARKER_PREFIX):
        return None
    fields: dict[str, str] = {}
    for part in text[len(MARKER_PREFIX) :].replace(MARKER_SUFFIX, " ").split():
        key, sep, value = part.partition("=")
        if sep and key and value:
            fields[key] = value
    review = fields.get("review", REVIEW_FIRE)
    if review not in (REVIEW_FIRE, REVIEW_SKIP):
        review = REVIEW_FIRE
    return Marker(
        head=fields.get("head") or None,
        pushed=fields.get("pushed") or None,
        review=review,
        run=fields.get("run") or None,
    )


# --- eligibility ------------------------------------------------------------------------


@dataclass(frozen=True)
class PrView:
    """The facts the eligibility rules need, from `PrFacts` or from a `pr_facts` row.

    One shape for both callers: the producer has a `PrFacts` from the collector, and the
    canary endpoint reads the stored row. Two implementations of the same rules would drift,
    and the drift would show up as a 4xx the operator cannot explain.
    """

    repo: str
    number: int
    state: str
    report: str | None
    reason: str | None
    labels: tuple[str, ...]
    marker: str | None
    report_at: str | None
    report_items: bool | None
    head_committed_at: str | None


def view_from_facts(repo: str, number: int, facts: PrFacts) -> PrView:
    return PrView(
        repo=repo,
        number=number,
        state=facts.state,
        report=facts.report,
        reason=facts.reason,
        labels=facts.labels,
        marker=facts.doctor_marker,
        report_at=facts.report_at,
        report_items=facts.report_items,
        head_committed_at=facts.head_committed_at,
    )


def view_from_row(row: object) -> PrView:
    """A `pr_facts` row as a `PrView`. `labels` is the row's JSON array.

    Takes a `sqlite3.Row` or a mapping: a Row supports index access and `.keys()` but not
    `.get`, so it is flattened first.
    """
    import json

    keys = row.keys() if hasattr(row, "keys") else ()  # type: ignore[union-attr]
    fields = {key: row[key] for key in keys}  # type: ignore[index]
    row = fields
    raw_labels = row.get("labels")
    try:
        labels = tuple(json.loads(raw_labels)) if isinstance(raw_labels, str) else ()
    except ValueError:
        labels = ()
    items = row.get("report_items")
    return PrView(
        repo=str(row.get("repo") or ""),
        number=int(row.get("pr_number") or 0),
        state=str(row.get("state") or ""),
        report=row.get("report") if isinstance(row.get("report"), str) else None,
        reason=row.get("reason") if isinstance(row.get("reason"), str) else None,
        labels=labels,
        marker=(
            row.get("doctor_marker") if isinstance(row.get("doctor_marker"), str) else None
        ),
        report_at=row.get("report_at") if isinstance(row.get("report_at"), str) else None,
        report_items=None if items is None else bool(items),
        head_committed_at=(
            row.get("head_committed_at")
            if isinstance(row.get("head_committed_at"), str)
            else None
        ),
    )


@dataclass(frozen=True)
class Verdict:
    """Whether the doctor may act on a PR, and either the work or the reason it may not."""

    eligible: bool
    category: str | None
    why: str


# The report kinds that mean "not merged, and the review had something to say". `queued`
# means the merge queue has it, `merged` means it is done, and both are ineligible.
BLOCKED_REPORTS = frozenset({"blocked", "needs-human"})


def ineligible(why: str) -> Verdict:
    return Verdict(eligible=False, category=None, why=why)


def eligibility(view: PrView, *, has_job: bool = False) -> Verdict:
    """The pure rule. Every exclusion has a test and a one-line reason.

    Order matters only for the reason the operator reads: the cheapest, most definitive
    facts come first, so a PR with a marker and a bad reason is reported as "already has a
    marker" rather than as something a human would chase.
    """
    if view.state != "open":
        return ineligible(f"the PR is {view.state}, not open")
    if view.marker:
        return ineligible("it already has a doctor marker, so its one attempt is spent")
    if has_job:
        return ineligible("a doctor job already exists for it")
    if view.report not in BLOCKED_REPORTS:
        return ineligible(f"its last report is {view.report or 'missing'}")
    if "do-not-merge" in view.labels:
        return ineligible("it carries do-not-merge")
    if not view.reason:
        return ineligible("its report carries no **Reason:** line")
    if is_retryable(view.reason):
        return ineligible("the reason is retryable: it wants a re-review, not a fix")
    category = classify(view.reason)
    if category is None:
        return ineligible("the reason is one the doctor has no fix for")
    if category == HYGIENE and is_config_change(view.reason):
        return ineligible(
            "the diff-hygiene block is config_changed, which a human merges by design"
        )
    if category == REVIEW and not view.report_items:
        return ineligible("the review's report lists no concrete leftover to fix")
    if is_stale(view):
        return ineligible("the head was committed after the report, so someone pushed since")
    return Verdict(eligible=True, category=category, why="eligible")


def is_stale(view: PrView) -> bool:
    """Whether the PR's head was committed after its last report was posted (decision 6).

    Judged by **time only**: the report's `Reviewed HEAD:` is a sandbox-local commit that
    fabro rewrites on push, so comparing SHAs would call every live report stale. fabro's last
    checkpoint commit arrives 8-22 s before the report on the live PRs, so a report that
    describes the current head passes with room to spare.

    An unparseable or absent timestamp is *not* stale: the rule exists to skip a PR someone
    pushed to, and refusing to run because GitHub omitted a date would hide every PR.
    """
    head = _timestamp(view.head_committed_at)
    report = _timestamp(view.report_at)
    if head is None or report is None:
        return False
    return head > report


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


# --- the producer -----------------------------------------------------------------------


class PrDoctorProducer:
    """Queues a `pr-doctor` job for each blocked PR the doctor may fix.

    Called once per `pr_facts` refresh, with the facts the collector just saved, so it makes
    **no** GitHub and **no** fabro call of its own. That is what makes "the switch off means
    off" true at this level rather than merely intended: with `SCHEDULER_BACKGROUND=off`, or
    with `pr-doctor` absent from `SCHEDULER_BG_KINDS`, `consider` returns before it reads
    anything, and `tick` does nothing at all.
    """

    def __init__(self, config: SchedulerConfig, store: Store) -> None:
        self._config = config
        self._store = store
        # Unmapped reasons, logged once per PR rather than on every refresh (decision 7).
        self._logged: set[tuple[str, int]] = set()

    @property
    def enabled(self) -> bool:
        """Whether this producer may enqueue anything (C7, D12)."""
        return self._config.background and KIND in self._config.bg_kinds

    def consider(
        self, repo: str, number: int, facts: PrFacts, now: datetime | None = None
    ) -> int | None:
        """Enqueue one job for `repo#number` when it is eligible. Returns the job id."""
        if not self.enabled:
            return None
        now = now or datetime.now(UTC)
        view = view_from_facts(repo, number, facts)
        verdict = eligibility(
            view, has_job=self._store.background_job_exists(KIND, repo, str(number))
        )
        if not verdict.eligible:
            if classify(view.reason) is None and not is_retryable(view.reason):
                key = (repo, number)
                if key not in self._logged:
                    self._logged.add(key)
                    log.info(
                        "pr-doctor: %s#%s is not eligible: %s", repo, number, verdict.why
                    )
            return None
        job_id = self._store.enqueue_background_job(
            kind=KIND,
            repo=repo,
            target=str(number),
            inputs={"pr_number": number},
            priority=BACKGROUND_JOB_PRIORITY[KIND],
            typical_box_minutes=PR_DOCTOR_TYPICAL_MINUTES,
            now=now,
        )
        if job_id is not None:
            log.info(
                "pr-doctor: queued job %s for %s#%s (%s)",
                job_id,
                repo,
                number,
                verdict.category,
            )
        return job_id


# --- the hand-off -----------------------------------------------------------------------


def hand_off(
    store: Store,
    job: Mapping[str, object],
    *,
    token: str,
    outcome: str,
    reviewed: bool,
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> bool:
    """Restore `ai-review-needs-human` and post one comment (task 06 step 8). Never raises.

    The label goes first. It is the signal the merge queue reads (`merge-queue`'s guard hands
    off anything that carries it), so a GitHub failure between the two writes leaves the PR
    visibly held for a human rather than half-announced. The job's outcome is recorded last,
    with the outcome the caller decided -- `handed_off`, or `handed_off_no_review` when no
    review ran.

    There is no retry path: the job is not enqueued again once it has had its attempt (D13),
    so a duplicate comment is not possible, and a failure here is logged for an operator
    rather than left in a queue that cannot move.
    """
    now = now or datetime.now(UTC)
    repo = str(_field(job, "repo") or "")
    try:
        number = int(str(_field(job, "target") or 0))
    except (TypeError, ValueError):
        number = 0
    body = HANDOFF_COMMENT if reviewed else HANDOFF_NO_REVIEW_COMMENT
    ok = True
    if repo and number > 0:
        try:
            add_label(repo, number, NEEDS_HUMAN_LABEL, token, client=client)
        except GitHubError as exc:
            ok = False
            log.error("pr-doctor: %s#%s: could not restore %s: %s", repo, number, NEEDS_HUMAN_LABEL, exc)
        try:
            comment(repo, number, body, token, client=client)
        except GitHubError as exc:
            ok = False
            log.error("pr-doctor: %s#%s: could not post the hand-off comment: %s", repo, number, exc)
        log.info("pr-doctor: %s#%s handed off (%s)", repo, number, outcome)
    else:
        ok = False
        log.error(
            "pr-doctor: job %s has no repo/target, so it cannot be handed off",
            _field(job, "job_id"),
        )
    store.set_background_outcome(
        int(str(_field(job, "job_id") or 0)), outcome=outcome, now=now
    )
    return ok


def _field(row: object, key: str) -> object:
    """One column from a `sqlite3.Row` or a mapping. Never raises for a missing key."""
    try:
        return row[key]  # type: ignore[index]
    except (IndexError, KeyError):
        return None
