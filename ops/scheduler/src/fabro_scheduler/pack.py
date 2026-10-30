"""The context pack (more-local-inference 08): the scheduler's half.

A local model maps the files, symbols, tests and commands a queued issue will touch, and
posts the map as the issue's one context-pack marker comment (`.fabro/workflows/context-pack/`).
`backlog`'s and `ralph`'s `claim` copy it into `background.md`, which `decompose` and `plan`
read, so the hosted planners explore less.

- **The producer** queues one `context-pack` job for each of the next
  `SCHEDULER_PRECHECK_TOP_N` issues in dispatch order whose precheck said `ready` (D10: the
  pack comes after the precheck, and an issue the precheck doubts is not worth mapping yet)
  and that has never had a pack job. It skips a queued job whose issue left the queue first,
  as the precheck producer does.
- **The outcome** of a succeeded run is read back from the issue's marker: `pack run=<run_id>`
  when the marker is this run's own. `Store.archive_and_release_lease` reads it to record
  `had_pack` on each issue run (D21).

One pack job per issue for now, as for the precheck: the package itself re-checks C6
*Staleness* when a job runs, but the producer does not queue a second job for an issue whose
text changed.
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
from .precheck import KIND as PRECHECK_KIND, fetch_comments, skip_departed
from .queue import QueueItem
from .store import Store

log = logging.getLogger(__name__)

KIND = "context-pack"

# The whole run, dispatch to finish (C7's `typical_box_minutes`): sandbox start, the code
# index, and one `gather` stage the prompt bounds at about five minutes and twenty tool calls.
# The first precheck, a stage of the same shape bounded at ten, held its box 1.6 minutes in
# all (2026-10-30). A first guess; the job rows measure the real number.
PACK_TYPICAL_MINUTES = 8

# The outcome a precheck job must have recorded before its issue gets a pack.
READY = "verdict=ready"

# C6's context-pack marker, the comment's first line.
MARKER = re.compile(
    r"^<!-- fabro:context-pack body=(?P<body>\S+) main=(?P<main>\S+) "
    r"at=(?P<at>\S+) run=(?P<run>\S+) -->"
)

# The outcome a succeeded run earns when the marker is its own. `run_history.pack_run` is the
# part after the prefix.
POSTED_PREFIX = "pack run="


@dataclass(frozen=True)
class PackMarker:
    body: str
    main: str
    at: str
    run: str


def parse_marker(body: object) -> PackMarker | None:
    """The context-pack marker on a comment's first line, or `None`."""
    if not isinstance(body, str):
        return None
    match = MARKER.match(body.split("\n", 1)[0])
    if match is None:
        return None
    return PackMarker(**match.groupdict())


def newest_marker(comments: Sequence[Mapping[str, Any]]) -> PackMarker | None:
    """The last context-pack marker among `comments`, oldest first as GitHub lists them."""
    found = None
    for comment in comments:
        marker = parse_marker(comment.get("body"))
        if marker is not None:
            found = marker
    return found


def read_marker(
    repo: str, number: int, token: str, *, client: httpx.Client | None = None
) -> PackMarker | None:
    """The issue's newest context-pack marker. Raises `GitHubError` on any failed read."""
    return newest_marker(fetch_comments(repo, number, token, client=client))


def outcome_for(marker: PackMarker | None, run_id: str) -> str:
    """The job outcome a finished, succeeded pack run earned.

    `pack run=<run_id>` only when the marker is this run's own: a run that stopped (the issue
    was closed, or its text already has a fresh pack) posts nothing, and an older marker is
    not its map.
    """
    if marker is None:
        return "succeeded (no marker)"
    if marker.run != run_id:
        return "succeeded (no new pack)"
    return f"{POSTED_PREFIX}{run_id}"


class PackProducer:
    """Queues `context-pack` jobs for the next issues in dispatch order that the precheck passed.

    Like the precheck producer, `tick` reads the ranked queue the dispatch loop already built
    and the store, and makes no GitHub or fabro call of its own.
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
        waiting = [item for item in ranked if (item.issue.repo, item.issue.number) not in running]
        for item in waiting[: self._config.precheck_top_n]:
            repo, target = item.issue.repo, str(item.issue.number)
            if self._store.latest_background_outcome(PRECHECK_KIND, repo, target) != READY:
                continue
            if self._store.background_job_exists(KIND, repo, target):
                continue
            job_id = self._store.enqueue_background_job(
                kind=KIND,
                repo=repo,
                target=target,
                inputs={"issue_number": item.issue.number},
                priority=BACKGROUND_JOB_PRIORITY[KIND],
                typical_box_minutes=PACK_TYPICAL_MINUTES,
                now=now,
            )
            if job_id is not None:
                created.append(job_id)
                log.info("context-pack: queued job %s for %s#%s", job_id, repo, target)
        return created
