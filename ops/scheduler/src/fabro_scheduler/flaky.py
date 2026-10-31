"""The flaky investigation (more-local-inference 10): the scheduler's half.

A local model reads what FlakeRadar holds about one queued flaky-test issue and the code at
`main`, and writes the likely cause and a proposed fix as the issue's one marker comment
(`.fabro/workflows/flaky-investigation/`). The `backlog` or `ralph` run that later takes the
issue copies it into `/tmp/fabro/background.md` (C9), so the hosted planners and the coder
start from a diagnosis instead of an empty page.

- **The producer** queues one `flaky-investigation` job for each of the next
  `SCHEDULER_PRECHECK_PER_REPO` issues of each repository, in dispatch order, that is labelled
  `flaky-test` and has **never** had one. The label is the whole selection: the scheduler's
  issue cache is already the `agent` collection (`github.py`), so a `flaky-test` issue in it is
  one the scout filed and no coder loop has taken yet — exactly the issue whose run will read
  the diagnosis. An issue that has left the queue (dispatched, closed, relabelled) gets its
  queued job skipped, as the precheck's and the pack's do.
- **The outcome** of a succeeded run is read back from the issue's marker:
  `flaky cause=<cause> run=<run_id>` when the marker is this run's own.

**One job per issue, ever.** The precheck's producer is deliberately the same (`precheck.py`:
"one precheck per issue for now"), and the reason is structural: the producer has no issue
body, only the cache row, and it must make no GitHub call (C7's producers read the queue and
the store). The package's own `stop` is what decides whether the *current* text of an issue
needs a diagnosis, so a changed issue is caught if a job ever runs again; the producer does not
create that job. `.scratch/more-local-inference/background-jobs-handoff.md` records this as an
open question rather than a promise.
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
from .github import GitHubError
from .precheck import fetch_comments, next_per_repo, posted_since, skip_departed
from .queue import QueueItem
from .store import Store

log = logging.getLogger(__name__)

KIND = "flaky-investigation"

# The issue label the scout files a flaky issue with (`docs/scout/00-overview-and-contracts.md`
# D10), beside `agent`, `scout` and `ai-generated`.
FLAKY_LABEL = "flaky-test"

# The whole run, dispatch to finish (C7's `typical_box_minutes`): sandbox start, clone, the code
# index, and one `investigate` stage the prompt bounds at about eight minutes and twenty-five
# tool calls, plus up to five bounded FlakeRadar reads in `claim`. **Estimated**, not yet
# measured: the precheck, whose shape this repeats, ran 0.9-2.5 minutes and the pack 2.9-5.8, so
# 8 is the sum of the same parts with a longer reading stage. The job rows measure it.
FLAKY_TYPICAL_MINUTES = 8

# C6's flaky-investigation marker, the comment's first line. `test=` is FlakeRadar's id of the
# test or of the job (C6 gives the marker one slot for it) and `unknown` when nothing resolved.
# `cause=` is the diagnosis's cause, so the outcome below reads off the marker's first line
# rather than out of the comment body.
MARKER = re.compile(
    r"^<!-- fabro:flaky-investigation test=(?P<test>\S+) cause=(?P<cause>\S+) "
    r"at=(?P<at>\S+) run=(?P<run>\S+) -->"
)

POSTED_PREFIX = "flaky cause="


@dataclass(frozen=True)
class FlakyMarker:
    test: str
    cause: str
    at: str
    run: str


def parse_marker(body: object) -> FlakyMarker | None:
    """The flaky-investigation marker on a comment's first line, or `None`."""
    if not isinstance(body, str):
        return None
    match = MARKER.match(body.split("\n", 1)[0])
    if match is None:
        return None
    return FlakyMarker(**match.groupdict())


def newest_marker(comments: Sequence[Mapping[str, Any]]) -> FlakyMarker | None:
    """The last flaky-investigation marker among `comments`, oldest first as GitHub lists them."""
    found = None
    for comment in comments:
        marker = parse_marker(comment.get("body"))
        if marker is not None:
            found = marker
    return found


def read_marker(
    repo: str, number: int, token: str, *, client: httpx.Client | None = None
) -> FlakyMarker | None:
    """The issue's newest flaky-investigation marker. Raises `GitHubError` on a failed read."""
    return newest_marker(fetch_comments(repo, number, token, client=client))


def outcome_for(
    marker: FlakyMarker | None, run_id: str, queued_at: str | None = None
) -> str:
    """The job outcome a finished, succeeded investigation earned.

    `flaky cause=<cause> run=<id>` when the marker is this job's: this run's own, or one an
    earlier attempt posted after the job was queued (`precheck.outcome_for` says why that
    happens). A run that stopped on a current diagnosis, or posted nothing, earned none.
    """
    if marker is None:
        return "succeeded (no marker)"
    if marker.run != run_id and not posted_since(marker.at, queued_at):
        return "succeeded (no new diagnosis)"
    return f"{POSTED_PREFIX}{marker.cause} run={marker.run}"


class FlakyProducer:
    """Queues `flaky-investigation` jobs for the next queued issues labelled `flaky-test`.

    Like the precheck's and the pack's producers, `tick` reads the ranked queue the dispatch
    loop already built and the store, and makes no GitHub or fabro call of its own. With the
    lane off, or `flaky-investigation` absent from `SCHEDULER_BG_KINDS`, it does nothing.
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
        """Skip what left the queue, then queue what is next. Returns the new job ids."""
        if not self.enabled:
            return []
        now = now or datetime.now(UTC)
        skip_departed(self._store, KIND, running, now)
        created: list[int] = []
        # The dedupe is in the query, not after the window: an issue that already has a job of
        # this kind, in any state, is not a candidate at all (the review's trap 3). And it runs
        # BEFORE the window, so a batch already covered does not consume it: with the filter
        # afterwards the window kept returning the same issues, and a queue that is not being
        # dispatched seeds nothing new (the calibration's stall, in the window this time).
        fresh = self._store.issues_without_job(KIND)
        candidates = [
            item
            for item in ranked
            if FLAKY_LABEL in item.issue.labels
            and (item.issue.repo, item.issue.number) in fresh
        ]
        for item in next_per_repo(candidates, running, self._config.precheck_per_repo):
            repo, number = item.issue.repo, item.issue.number
            job_id = self._store.enqueue_background_job(
                kind=KIND,
                repo=repo,
                target=str(number),
                inputs={"issue_number": number},
                priority=BACKGROUND_JOB_PRIORITY[KIND],
                typical_box_minutes=FLAKY_TYPICAL_MINUTES,
                now=now,
            )
            if job_id is not None:
                created.append(job_id)
                log.info("flaky: queued job %s for %s#%s", job_id, repo, number)
        return created
