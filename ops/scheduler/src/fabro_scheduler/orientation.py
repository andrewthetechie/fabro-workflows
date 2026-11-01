"""Repository orientation (more-local-inference 10): the scheduler's half.

Once a day per repository — and sooner when `main` has moved a lot — a local model writes a map
of that repository at `main` into the body of one pinned issue (`workflows/repo-orientation/`,
D22). `backlog`'s and `ralph`'s `prep` copy the body to `/tmp/fabro/orientation.md`, and
`decompose` and `plan` read it as an optional input, so the hosted planners explore less: that
exploration was most of what they spent in the week the z.ai quota ran out.

- **The producer** queues one `repo-orientation` job per schedulable repository when its newest
  job of this kind is more than a day old or it has never had one, and never while one is
  queued or running. It reads the store only — the package's own `stop` is what decides
  whether `main` has actually moved, so the producer needs no GitHub call and no issue body.
- **The outcome** of a succeeded run is read back from the orientation issue: `orientation
  main=<sha12> chars=<n>` when the marker is this run's own, `orientation unchanged` when the
  run stopped on a map that was still current.

`run_history.had_orientation` records whether a done one existed when a run was dispatched
(`store._background_at_dispatch`), so Phase 4 can compare credits per `decompose` visit with and
without a map — the same shape as `had_pack` (D21).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .config import BACKGROUND_JOB_PRIORITY, SchedulerConfig
from .precheck import posted_since
from .store import Store

log = logging.getLogger(__name__)

KIND = "repo-orientation"

# The issue label the map lives under, and the title the run that creates it gives it (D22).
LABEL = "fabro-orientation"
TITLE = "fabro: repository orientation"

# The whole run, dispatch to finish (C7's `typical_box_minutes`): sandbox start, clone, the code
# index and the repository outline, and one `map` stage the prompt bounds at about eight minutes
# and twenty-five tool calls. **Estimated**, not yet measured: the same parts as the context pack
# (2.9-5.8 minutes measured), with a longer writing stage. The job rows measure it. It is not
# task 10's 30: a job that does not fit a gap never starts.
ORIENTATION_TYPICAL_MINUTES = 8

# How stale the newest job must be before the producer queues another. A day, both because the
# map describes a `main` that moves daily and because a box gap per repository per night is
# cheap; the package's `stop` skips the nights when nothing changed.
REFRESH = timedelta(hours=24)

# C6's orientation marker: the first line of the pinned issue's body.
MARKER = re.compile(
    r"^<!-- fabro:orientation main=(?P<main>\S+) at=(?P<at>\S+) run=(?P<run>\S+) -->"
)

POSTED_PREFIX = "orientation main="


@dataclass(frozen=True)
class OrientationMarker:
    main: str
    at: str
    run: str
    # The body's length, so the outcome can say how much a planning run will pay for. Not part
    # of the marker: it is a property of the comment the marker is in.
    chars: int = 0


def parse_marker(body: object) -> OrientationMarker | None:
    """The orientation marker on a body's first line, or `None`."""
    if not isinstance(body, str):
        return None
    match = MARKER.match(body.split("\n", 1)[0])
    if match is None:
        return None
    return OrientationMarker(chars=len(body), **match.groupdict())


def newest_marker(bodies: Sequence[Mapping[str, Any]]) -> OrientationMarker | None:
    """The marker of the first issue in `bodies` that carries one (a repository has one)."""
    for issue in bodies:
        marker = parse_marker(issue.get("body"))
        if marker is not None:
            return marker
    return None


def read_marker(
    repo: str, token: str, *, client: httpx.Client | None = None
) -> OrientationMarker | None:
    """The repository's orientation marker. Raises `GitHubError` on a failed read.

    Read through the issue list by label, not by number: the reader in `backlog`'s and
    `ralph`'s `prep` selects the same way, so a re-created issue is found by both.
    """
    from .github import GITHUB_API, GitHubError
    from .prfacts import _get_all

    if not token or not token.strip():
        raise GitHubError("GITHUB_TOKEN is not set; the orientation issue cannot be read")
    owned = client is None
    http = client or httpx.Client(timeout=10.0)
    try:
        issues = _get_all(
            http,
            f"{GITHUB_API}/repos/{repo}/issues?labels={LABEL}&state=open&per_page=50",
            token,
            repo,
        )
    except httpx.HTTPError as exc:
        raise GitHubError(f"{repo}: GitHub request failed: {exc}") from exc
    finally:
        if owned:
            http.close()
    # Pull requests come back from the issues endpoint as well; a map is never a pull request.
    return newest_marker([issue for issue in issues if "pull_request" not in issue])


def outcome_for(
    marker: OrientationMarker | None, run_id: str, queued_at: str | None = None
) -> str | None:
    """The job outcome a finished, succeeded orientation run earned.

    `orientation main=<sha12> chars=<n>` when the marker is this job's: this run's own, or one
    an earlier attempt posted after the job was queued (`precheck.outcome_for` says why that
    happens). Another run's marker is `orientation unchanged`, which is the run's `stop`: the
    map it found was of the commit it checked out, or under a day old and under ten commits
    behind.

    None when no map was found. A run can only stop on a map that exists, so a succeeded run
    with none in sight posted one the read did not see yet: GitHub's labelled-issue list lags a
    new issue by seconds, and three of the five first maps read as `unchanged` that way
    (2026-10-31, jobs 255, 257 and 259). The caller keeps the plain `succeeded`.
    """
    if marker is None:
        return None
    if marker.run != run_id and not posted_since(marker.at, queued_at):
        return "orientation unchanged"
    return f"{POSTED_PREFIX}{marker.main[:12]} chars={marker.chars}"


class OrientationProducer:
    """Queues one `repo-orientation` job per repository that has not had one today.

    `tick` reads the store only: with the lane off, or `repo-orientation` absent from
    `SCHEDULER_BG_KINDS`, it does nothing at all and makes no call.
    """

    def __init__(self, config: SchedulerConfig, store: Store) -> None:
        self._config = config
        self._store = store

    @property
    def enabled(self) -> bool:
        return self._config.background and KIND in self._config.bg_kinds

    def tick(self, now: datetime | None = None) -> list[int]:
        """Queue one job for every repository that is due. Returns the new job ids."""
        if not self.enabled:
            return []
        now = now or datetime.now(UTC)
        due = self._store.repos_due_for_job(
            KIND,
            [row.name for row in self._config.schedulable_repos()],
            since=(now - REFRESH).isoformat(),
        )
        created: list[int] = []
        for row in self._config.schedulable_repos():
            if row.name not in due:
                continue
            job_id = self._store.enqueue_background_job(
                kind=KIND,
                repo=row.name,
                target="",
                inputs={},
                priority=BACKGROUND_JOB_PRIORITY[KIND],
                typical_box_minutes=ORIENTATION_TYPICAL_MINUTES,
                now=now,
            )
            if job_id is not None:
                created.append(job_id)
                log.info("orientation: queued job %s for %s", job_id, row.name)
        return created
