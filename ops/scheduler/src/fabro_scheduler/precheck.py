"""The issue precheck (more-local-inference 07), in shadow mode.

A local model reads a queued issue and the code at `main` before the factory spends hosted
credits on it, and posts a verdict as the issue's one precheck marker comment
(`.fabro/workflows/issue-precheck/`). This module is the scheduler's half:

- **The producer** queues one `issue-precheck` job for each of the next
  `SCHEDULER_PRECHECK_TOP_N` issues in dispatch order that has never had one, and skips a
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

# C6's precheck marker, the comment's first line.
MARKER = re.compile(
    r"^<!-- fabro:precheck verdict=(?P<verdict>\S+) body=(?P<body>\S+) main=(?P<main>\S+) "
    r"at=(?P<at>\S+) run=(?P<run>\S+) -->"
)

REQUEST_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Marker:
    verdict: str
    body: str
    main: str
    at: str
    run: str


def parse_marker(body: object) -> Marker | None:
    """The precheck marker on a comment's first line, or `None`."""
    if not isinstance(body, str):
        return None
    match = MARKER.match(body.split("\n", 1)[0])
    if match is None:
        return None
    return Marker(**match.groupdict())


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
        waiting = [item for item in ranked if (item.issue.repo, item.issue.number) not in running]
        for item in waiting[: self._config.precheck_top_n]:
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
