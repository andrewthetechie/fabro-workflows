"""The issue precheck (more-local-inference 07), in shadow mode.

A local model reads a queued issue and the code at `main` before the factory spends hosted
credits on it, and posts a verdict as the issue's one precheck marker comment
(`.fabro/workflows/issue-precheck/`). This module is the scheduler's half:

- **The producer** queues one `issue-precheck` job for each of the next
  `SCHEDULER_PRECHECK_PER_REPO` issues of each repository, in dispatch order, that has never
  had one (`next_per_repo`), and skips a
  queued job whose issue left the queue before its precheck ran (it was dispatched, closed or
  relabelled). How often that happens is part of what the shadow measures: a verdict that
  arrives after the run started is worth nothing.
- **The verdict** is read back from the issue's marker when the run ends, and recorded as the
  job's outcome (`verdict=<v>`), so it can be compared with how the issue's run ended.

**Shadow mode (operator decision, 2026-10-30):** nothing here holds, labels or reorders an
issue. Task 07's holds (D20) come only after the verdicts have been measured against real
outcomes. One precheck per issue for now: a body edited after its precheck is not re-checked
by the producer (the package itself would re-check a changed text if one were queued).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import BACKGROUND_JOB_PRIORITY, SchedulerConfig
from .github import GITHUB_API, GitHubError
from .prfacts import _get_all
from .queue import QueueItem
from .store import Store

log = logging.getLogger(__name__)

KIND = "issue-precheck"

# The whole run, dispatch to finish (C7's `typical_box_minutes`): sandbox start, clone, the
# code index, and one check stage. **Measured** over the first nine runs (2026-10-30, jobs
# 143-152): 0.9-1.8 minutes start to finish in the lane, median 1.2. It decides only which
# gaps a job may start in, and a job that overruns its gap costs one cancel of a few seconds,
# so 3 leaves margin without keeping it out of the many 5-minute gaps (the first guess was 10).
PRECHECK_TYPICAL_MINUTES = 3

# C6's precheck marker, the comment's first line. `blocker=<n>` is written only for a `blocked`
# verdict (2026-10-31, D20's release rule needs to know which issue to watch), and it is
# optional so a marker posted before it parses unchanged.
MARKER = re.compile(
    r"^<!-- fabro:precheck verdict=(?P<verdict>\S+) body=(?P<body>\S+) main=(?P<main>\S+) "
    r"at=(?P<at>\S+) run=(?P<run>\S+)(?: blocker=(?P<blocker>\d+))? -->"
)

# The label prefix a held issue carries, and the verdicts whose hold is applied by default
# (C6, D20). `precheck-already-done` is a verdict with `_` written as `-`, so a label is a
# legal GitHub label and the verdict is readable back from it.
HOLD_PREFIX = "precheck-"
HOLD_VERDICTS = ("already_done", "contradiction", "unclear", "blocked")

REQUEST_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Marker:
    verdict: str
    body: str
    main: str
    at: str
    run: str
    # The issue a `blocked` verdict waits for, as a string, or "" for every other verdict and
    # for a marker written before the field existed.
    blocker: str = ""


def parse_marker(body: object) -> Marker | None:
    """The precheck marker on a comment's first line, or `None`."""
    if not isinstance(body, str):
        return None
    match = MARKER.match(body.split("\n", 1)[0])
    if match is None:
        return None
    found = match.groupdict()
    # An absent optional group is None, not "": `Marker` says "" so every caller has one shape.
    found["blocker"] = found.get("blocker") or ""
    return Marker(**found)


def newest_marker(comments: Sequence[Mapping[str, Any]]) -> Marker | None:
    """The last precheck marker among `comments`, oldest first as GitHub lists them."""
    found = None
    for comment in comments:
        marker = parse_marker(comment.get("body"))
        if marker is not None:
            found = marker
    return found


def fetch_comments(
    repo: str, number: int, token: str, *, client: httpx.Client | None = None
) -> list[Mapping[str, Any]]:
    """Every comment on an issue, oldest first. Raises `GitHubError` on any failed read."""
    if not token or not token.strip():
        raise GitHubError("GITHUB_TOKEN is not set; the issue's markers cannot be read")
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        return _get_all(
            http, f"{GITHUB_API}/repos/{repo}/issues/{number}/comments?per_page=100", token, repo
        )
    except httpx.HTTPError as exc:
        raise GitHubError(f"{repo}#{number}: GitHub request failed: {exc}") from exc
    finally:
        if owned:
            http.close()


def read_marker(
    repo: str, number: int, token: str, *, client: httpx.Client | None = None
) -> Marker | None:
    """The issue's newest precheck marker. Raises `GitHubError` on any failed read."""
    return newest_marker(fetch_comments(repo, number, token, client=client))


def outcome_for(marker: Marker | None, run_id: str, queued_at: str | None = None) -> str:
    """The job outcome a finished, succeeded precheck run earned.

    `verdict=<v>` when the marker is this job's: this run's own, or one posted after the job
    was queued (`queued_at`) by an earlier attempt. That happens: the lane can cancel a run in
    `post`, after the comment went up (job 144, 2026-10-30), and the retry then stops on the
    marker it finds. A run that stopped on an older marker, or posted nothing, earned no
    verdict.
    """
    if marker is None:
        return "succeeded (no marker)"
    if marker.run != run_id and not posted_since(marker.at, queued_at):
        return "succeeded (no new verdict)"
    return f"verdict={marker.verdict}"


def posted_since(at: str, queued_at: str | None) -> bool:
    """Whether a marker's `at=` is at or after `queued_at`. Unparseable reads as no."""
    if not queued_at:
        return False
    try:
        return datetime.fromisoformat(at) >= datetime.fromisoformat(queued_at)
    except ValueError:
        return False


def next_per_repo(
    ranked: Sequence[QueueItem], running: Set[tuple[str, int]], per_repo: int
) -> list[QueueItem]:
    """The first `per_repo` issues of each repository in `ranked` that no coder loop holds.

    In dispatch order. Per repository, not the head of the whole queue: the diversity pass
    gives each box a different repository's head, so a lower-priority repository dispatches
    as often as the top one while its issues sit far down the ranked queue
    (`DEFAULT_PRECHECK_PER_REPO` has the night that showed it).
    """
    taken: dict[str, int] = {}
    chosen: list[QueueItem] = []
    for item in ranked:
        repo = item.issue.repo
        if (repo, item.issue.number) in running or taken.get(repo, 0) >= per_repo:
            continue
        taken[repo] = taken.get(repo, 0) + 1
        chosen.append(item)
    return chosen


class PrecheckProducer:
    """Queues `issue-precheck` jobs for the next issues in dispatch order.

    `tick` is called by the dispatch loop with the ranked queue it already built, so it makes
    **no** GitHub or fabro call of its own. With the lane off, or `issue-precheck` absent from
    `SCHEDULER_BG_KINDS`, it does nothing at all.
    """

    def __init__(self, config: SchedulerConfig, store: Store) -> None:
        self._config = config
        self._store = store

    @property
    def enabled(self) -> bool:
        return self._config.background and KIND in self._config.bg_kinds

    def tick(
        self,
        ranked: Sequence[QueueItem],
        running: Set[tuple[str, int]] = frozenset(),
        now: datetime | None = None,
    ) -> list[int]:
        """Skip what left the queue, then queue what is next. Returns the new job ids.

        `running` is every `(repo, issue)` a coder loop holds. A dispatched issue stays in the
        cache until the next inventory poll (up to a minute), so the ranked queue alone would
        still offer it.
        """
        if not self.enabled:
            return []
        now = now or datetime.now(UTC)
        skip_departed(self._store, KIND, running, now)
        created: list[int] = []
        for item in next_per_repo(ranked, running, self._config.precheck_per_repo):
            repo, number = item.issue.repo, item.issue.number
            if self._store.background_job_exists(KIND, repo, str(number)):
                continue
            job_id = self._store.enqueue_background_job(
                kind=KIND,
                repo=repo,
                target=str(number),
                inputs={"issue_number": number},
                priority=BACKGROUND_JOB_PRIORITY[KIND],
                typical_box_minutes=PRECHECK_TYPICAL_MINUTES,
                now=now,
            )
            if job_id is not None:
                created.append(job_id)
                log.info("precheck: queued job %s for %s#%s", job_id, repo, number)
        return created


def skip_departed(store: Store, kind: str, running: Set[tuple[str, int]], now: datetime) -> None:
    """Skip every queued `kind` job whose issue is no longer waiting: it will never be useful.

    Shared by the precheck and the context pack, whose jobs are both about an issue before
    its run starts.
    """
    waiting = {
        (issue.repo, str(issue.number))
        for issue in store.issues()
        if (issue.repo, issue.number) not in running
    }
    for job in store.queued_background_jobs():
        if job["kind"] != kind:
            continue
        if (job["repo"], job["target"]) in waiting:
            continue
        store.finish_background_job(
            int(job["job_id"]),
            state="skipped",
            outcome=f"the issue left the queue before its {kind} ran",
            now=now,
        )
        log.info(
            "%s: job %s skipped, %s#%s left the queue first",
            kind,
            job["job_id"],
            job["repo"],
            job["target"],
        )


# --- the holds (D20) -----------------------------------------------------------------


def hold_label(verdict: str) -> str:
    """The label a held issue carries: `precheck-` + the verdict with `_` written as `-`."""
    return f"{HOLD_PREFIX}{verdict.replace('_', '-')}"


def verdict_for_label(label: str) -> str | None:
    """The verdict a `precheck-*` label names, or None for a label that names none."""
    if not label.startswith(HOLD_PREFIX):
        return None
    verdict = label[len(HOLD_PREFIX):].replace("-", "_")
    return verdict or None


def holding_labels(labels: object) -> list[str]:
    """Every `precheck-*` label among `labels`, sorted. `[]` when there is none.

    Whatever the switch says, an issue that carries one of these is held: a human who left a
    label on is still holding the issue, and the scheduler never removes a label it did not add
    (only `blocked`, when its blocker closes). The switch decides only whether a *new* verdict
    adds one.
    """
    if not isinstance(labels, (set, frozenset, list, tuple)):
        return []
    return sorted(
        name for name in labels
        if isinstance(name, str) and name.startswith(HOLD_PREFIX) and len(name) > len(HOLD_PREFIX)
    )


def release_blocked(
    repo: str, token: str, *, client: httpx.Client | None = None
) -> list[tuple[int, str]]:
    """Drop `precheck-blocked` from every held issue whose cited blocker has closed.

    The inventory's pass, run once per poll after the other promotions (D20). Only that label is
    ever removed: `precheck-already-done`, `precheck-contradiction` and `precheck-unclear` are a
    human's to release, because nothing observable says they have stopped applying.

    **Never raises.** A failed read or write is logged and retried on the next poll, exactly as
    the remainder and held-child passes are: the inventory itself is fine.
    """
    from .github import fetch_label_all_states, fetch_issue_state, remove_label

    out: list[tuple[int, str]] = []
    blocked = hold_label("blocked")
    try:
        held = fetch_label_all_states(repo, blocked, token, client=client)
    except GitHubError as exc:
        log.warning("precheck: %s: cannot list %s issues (%s)", repo, blocked, exc)
        return out
    for issue in held:
        if issue.state != "open":
            continue
        try:
            marker = read_marker(repo, issue.number, token, client=client)
        except GitHubError as exc:
            log.warning("precheck: %s#%s: cannot read its marker (%s)", repo, issue.number, exc)
            continue
        if marker is None or marker.verdict != "blocked":
            continue
        if not marker.blocker:
            # A marker from before the field existed: the release is a human's, and the run that
            # would have written it is long gone.
            continue
        try:
            state, _ = fetch_issue_state(repo, int(marker.blocker), token, client=client)
        except (GitHubError, ValueError) as exc:
            log.warning(
                "precheck: %s#%s: cannot read blocker #%s (%s)",
                repo, issue.number, marker.blocker, exc,
            )
            continue
        if state != "closed":
            continue
        try:
            remove_label(repo, issue.number, blocked, token, client=client)
        except GitHubError as exc:
            log.warning("precheck: %s#%s: cannot remove %s (%s)", repo, issue.number, blocked, exc)
            continue
        log.info(
            "precheck: %s#%s released: its blocker #%s is closed", repo, issue.number,
            marker.blocker,
        )
        out.append((issue.number, f"released: blocker #{marker.blocker} closed"))
    return out
