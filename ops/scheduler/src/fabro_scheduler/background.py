"""The background lane: box-only jobs in a coder loop's gaps (more-local-inference C7).

The coder boxes are idle most of the day: a run holds its box for its whole lease but
uses it only for its box stages. This module fills the gaps between those stages with
**background runs** — short runs of box-only packages, whose every stage resolves to
the box's own `coders-?-bg` model row, so they spend no hosted credits.

**The coder loop always wins.** Three layers keep a background run off a busy box
(D17), and two of them are here:

1. `evict` runs first on every tick: a box whose loop is *in* a box stage has its
   background run cancelled.
2. `fill` starts a run only on a box whose loop is outside a box stage — read **fresh**
   from fabro at decision time, never from the probe's 30 s cache — and whose
   strix-infer has been idle long enough.
3. The background package's own `fabro-io box-guard` hook closes the window the first
   two cannot see (a loop stage that begins after the scheduler's decision).

**Nothing here takes a lease.** A background job is its own queue entry in
`background_jobs`; it is not in `leases` and never in `run_history`, which is what keeps
background usage out of the coder accounting (C8). The one shared resource is the run
cap, and `make_room` gives a coder-loop dispatch the slot it needs.

**Every decision is a pure function of state the caller hands in**, so a test drives a
tick by hand with a fake fabro and a fake health client, exactly as
`tests/test_dispatch.py` drives the primary loop.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import BACKGROUND_JOB_PRIORITY, SchedulerConfig, predicted_gap_minutes
from .doctor import KIND as DOCTOR_KIND, hand_off, parse_marker
from .fabro import FabroClient, FabroError, RunNotStarted, status_kind, status_reason
from .github import GitHubError
from .lease import Lease, LeaseStore
from .prfacts import PrFacts
from .store import Store

log = logging.getLogger(__name__)

# The package each job kind runs. Later tasks add their own kinds here and to
# `config.BACKGROUND_JOB_PRIORITY`.
JOB_WORKFLOWS = {"box-noop": "box-noop", "pr-doctor": "pr-doctor"}


# How long a `box-noop` run is expected to hold its box, whole run, dispatch to finish
# (C7's `typical_box_minutes`). Measured 2026-10-29 over 135 uncancelled runs: median 3.5 min,
# max 10.1. At 10 the lane filled only gaps a coder rarely ends early, so claims almost never
# cancelled one and task 12's exit sample stalled; 5 fills the shorter gaps too.
BOX_NOOP_TYPICAL_MINUTES = 5

# A job that has lost its box three times needs a gap twice its own length before the
# lane picks it again (D9).
CANCEL_BACKOFF = 3

# `/health` reads are short: this is a LAN box, and a slow answer must not hold a tick.
HEALTH_TIMEOUT_SECONDS = 5.0

# The run statuses fabro reports as finished. A background run in any other status is
# still holding its box.
TERMINAL_KINDS = frozenset({"succeeded", "failed", "dead"})


def plan_arm_true(issue: int) -> bool:
    """The box-plan trial's arm: `(issue_number // 2) % 2 == 1` (docs/plan-box-trial/).

    The same arithmetic `ops/fabro-io/src/boxclaim.rs` hard-codes and checker rule R13
    pins, so a `"plan-arm"` stage is judged identically by scheduler and hook.
    """
    return (issue // 2) % 2 == 1


def running_node(stages: list[dict]) -> str | None:
    """The `node_id` of the stage fabro reports as `running`, or `None` between stages."""
    for stage in stages:
        if stage.get("status") == "running":
            node = stage.get("node_id")
            return node if isinstance(node, str) else None
    return None


def is_box_stage(box_stages: Mapping[str, str], issue: int, node: str | None) -> bool:
    """Whether `node` of this run runs on the box, from the generated manifest (C1)."""
    if node is None:
        return False
    mode = box_stages.get(node)
    if mode == "true":
        return True
    if mode == "plan-arm":
        return plan_arm_true(issue)
    return False


@dataclass(frozen=True)
class Health:
    """One strix-infer `/health` reading."""

    busy: bool
    queued: int
    idle_seconds: float

    @property
    def idle(self) -> bool:
        return not self.busy and self.queued == 0

    @classmethod
    def parse(cls, body: object) -> Health | None:
        """A `/health` body, or `None` when it does not carry both idle fields."""
        if not isinstance(body, Mapping):
            return None
        busy = body.get("busy")
        queued = body.get("queued")
        idle = body.get("idle_seconds", 0.0)
        if not isinstance(busy, bool) or not isinstance(queued, int):
            return None
        return cls(busy=busy, queued=queued, idle_seconds=float(idle or 0.0))


class BoxHealth:
    """The boxes' `/health` reader. Any failure reads as "not idle" (C7 step 8)."""

    def __init__(
        self,
        urls: Mapping[str, str],
        *,
        client: httpx.Client | None = None,
        timeout: float = HEALTH_TIMEOUT_SECONDS,
    ) -> None:
        self._urls = dict(urls)
        self._client = client
        self._timeout = timeout

    def read(self, box: str) -> Health | None:
        url = self._urls.get(box)
        if url is None:
            return None
        try:
            if self._client is not None:
                response = self._client.get(url, timeout=self._timeout)
            else:
                response = httpx.get(url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            log.info("background: %s /health unreadable (%s); reading it as busy", box, exc)
            return None
        if response.status_code != 200:
            log.info("background: %s /health answered %d", box, response.status_code)
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        health = Health.parse(body)
        if health is None:
            log.info("background: %s /health carried no busy/queued fields", box)
        return health


@dataclass(frozen=True)
class Loop:
    """What the coder loop on one box is doing, read fresh (C7 step 7)."""

    lease: Lease
    node: str | None
    on_box: bool


@dataclass(frozen=True)
class Eviction:
    """One background run cancelled so a coder loop could have its box."""

    box: str
    run_id: str
    job_id: int
    kind: str
    reason: str


@dataclass(frozen=True)
class JobStart:
    """One background run the lane started."""

    job_id: int
    kind: str
    repo: str
    box: str
    run_id: str
    gap_minutes: float | None


@dataclass(frozen=True)
class JobEnd:
    """One background job the completion poll moved."""

    job_id: int
    kind: str
    state: str  # "done" | "failed" | "queued"
    outcome: str | None


@dataclass(frozen=True)
class LaneTick:
    """One pass of the lane: what finished, what it evicted, then what it started."""

    ends: tuple[JobEnd, ...] = ()
    evictions: tuple[Eviction, ...] = ()
    starts: tuple[JobStart, ...] = ()


@dataclass(frozen=True)
class BoxView:
    """Why a box is, or is not, filling — the page's *Background* strip (C7 step 14)."""

    box: str
    running: Mapping[str, object] | None
    blocked_by: str | None
    health: Health | None


def job_row(row) -> dict[str, object]:
    """One `background_jobs` row as the JSON the page and `/api/background` render."""
    return {
        "job_id": int(row["job_id"]),
        "kind": row["kind"],
        "repo": row["repo"],
        "target": row["target"],
        "priority": int(row["priority"]),
        "typical_box_minutes": int(row["typical_box_minutes"]),
        "state": row["state"],
        "run_id": row["run_id"],
        "box": row["box"],
        "cancels": int(row["cancels"]),
        "created_at": row["created_at"],
        "last_started_at": row["last_started_at"],
        "last_finished_at": row["last_finished_at"],
        "outcome": row["outcome"],
    }


class BackgroundLane:
    """Evict, fill, and finish the box-only jobs (C7).

    Every public method takes `now`, so a test drives time instead of waiting for it,
    and every network call goes through an injected client.
    """

    def __init__(
        self,
        config: SchedulerConfig,
        store: Store,
        leases: LeaseStore,
        fabro: FabroClient | None,
        *,
        health: BoxHealth | None = None,
        pr_reader: Callable[[str, int], PrFacts] | None = None,
        github_token: str = "",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._store = store
        self._leases = leases
        self._fabro = fabro
        self._health = health if health is not None else BoxHealth(config.box_health)
        # The PR reader the doctor's end-of-run decision needs (task 06 step 5). Injected so a
        # test drives the whole branch without a network, and so the lane holds no token.
        self._read_pr = pr_reader
        self._github_token = github_token
        self._clock = clock
        # The last `/health` reading per box, so the page reads a cache instead of the
        # network. Refreshed by every `tick`, at most one interval old.
        self._readings: dict[str, Health] = {}
        # Today's evictions, for the strip's counter. Process-local on purpose: it is a
        # "what has happened since midnight" readout, not a record.
        self._evictions: list[tuple[datetime, Eviction]] = []

    # --- the tick -------------------------------------------------------------

    def tick(self, now: datetime | None = None) -> LaneTick:
        """Evict first, then fill (D17). Called after the primary dispatch loop."""
        now = now or self._clock()
        if not self._config.box_health and not self._store.running_background_jobs():
            # No box in the lane and nothing running: nothing to read, nothing to decide, and
            # no calls. A run that IS running is still finished, because "no box in the lane"
            # means "start no new work", not "forget the work already started" (D12), and a
            # PR doctor's end of run is what decides its PR's fate (task 06 step 5).
            return LaneTick()
        # Finish first: a run that ended frees its box and its slot for this same tick.
        # This runs whatever the switch says — `off` means no new background run starts,
        # not that the ones already running are forgotten (D12).
        ends = self.sync(now)
        # `/health` is read only when something may act on it: the switch is on, or a
        # background run is still finishing. Off with nothing running is the scheduler as
        # it was before the lane existed: no box call at all (task 05, *Done when*).
        if self._config.background or self._store.running_background_jobs():
            self._refresh_health()
        evictions = self.evict(now)
        starts = self.fill(now) if self._config.background else ()
        if ends or evictions or starts:
            log.info(
                "background: finished %d, evicted %d, started %d",
                len(ends),
                len(evictions),
                len(starts),
            )
        return LaneTick(ends=tuple(ends), evictions=tuple(evictions), starts=tuple(starts))

    def _reading(self, box: str) -> Health | None:
        """The box's `/health`, from the last tick's cache or read now.

        The cache is what the page renders, so a page load never waits on a box; a
        `fill` driven by hand (a test, or a tick that skipped the refresh) reads directly
        rather than treating a missing cache entry as "not idle".
        """
        reading = self._readings.get(box)
        if reading is not None:
            return reading
        reading = self._health.read(box)
        if reading is not None:
            self._readings[box] = reading
        return reading

    def _refresh_health(self) -> None:
        for box in self._config.box_health:
            reading = self._health.read(box)
            if reading is not None:
                self._readings[box] = reading

    # --- eviction (D17 layer 2) -----------------------------------------------

    def evict(self, now: datetime | None = None) -> list[Eviction]:
        """Cancel every background run on a box whose loop is *in* a box stage."""
        now = now or self._clock()
        evicted: list[Eviction] = []
        running = self._store.running_background_jobs()
        if not running:
            return evicted
        for box in self._config.box_health:
            on_box = [job for job in running if job["box"] == box]
            if not on_box:
                continue
            loop = self._loop_on(box)
            if loop is None or not loop.on_box:
                continue
            for job in on_box:
                run_id = job["run_id"]
                if not isinstance(run_id, str) or not run_id:
                    continue
                if self._cancel(run_id, "the coder loop is in a box stage"):
                    eviction = Eviction(
                        box=box,
                        run_id=run_id,
                        job_id=int(job["job_id"]),
                        kind=str(job["kind"]),
                        reason="loop in box stage",
                    )
                    evicted.append(eviction)
                    self._evictions.append((now, eviction))
        return evicted

    # --- filling --------------------------------------------------------------

    def fill(self, now: datetime | None = None) -> list[JobStart]:
        """Start at most one job per box, when all six C7 conditions hold."""
        now = now or self._clock()
        if not self._config.background:
            return []
        if self._fabro is None:
            return []
        free_slots = self._free_slots()
        starts: list[JobStart] = []
        for box in self._config.box_health:
            if free_slots < self._config.bg_slot_reserve:
                return starts
            if box in self._store.drained_pools():
                continue
            if any(job["box"] == box for job in self._store.running_background_jobs()):
                continue
            loop = self._loop_on(box)
            blocked, gap = self._gap_minutes(box, loop)
            if blocked:
                continue
            reading = self._reading(box)
            if reading is None or not reading.idle:
                continue
            if reading.idle_seconds < self._config.bg_gap_seconds:
                continue
            job = self._pick(gap)
            if job is None:
                continue
            started = self._start(job, box, gap, now)
            if started is not None:
                starts.append(started)
                free_slots -= 1
        return starts

    def _free_slots(self) -> int:
        """fabro's free run slots: `max_concurrent_runs` minus every active run (C7.5).

        Returns 0 when fabro cannot be asked: a failing API is not evidence of room.
        """
        if self._fabro is None:
            return 0
        try:
            active = self._fabro.active_runs()
        except FabroError as exc:
            log.info("background: cannot list active runs (%s); assuming no free slot", exc)
            return 0
        return max(0, self._config.max_concurrent_runs - len(active))

    def _gap_minutes(
        self, box: str, loop: Loop | None
    ) -> tuple[bool, float | None]:
        """`(blocked, predicted remaining gap in minutes)` for `box`.

        `gap` is `None` when there is no coder loop at all: the whole box is free.
        `blocked` means the box does not qualify this tick — the loop is between stages,
        is in a box stage, or its box stages are unknown. A loop with no stage in
        `running` does not qualify (D7), because the gap after it has not started yet.
        """
        if loop is None:
            return False, None
        if loop.node is None:
            return True, None
        if loop.on_box:
            return True, None
        if not loop.lease.box_stages:
            # The manifest for this run's version is unknown, so no stage can be shown
            # *not* to be a box stage. Fail closed: a background run inside a coder box
            # stage is the one outcome the lane exists to prevent.
            return True, None
        return False, predicted_gap_minutes(loop.lease.workflow, loop.node)

    def _pick(self, gap: float | None):
        """The next job that fits `gap`: lowest priority, then oldest (C7, D9)."""
        for job in self._store.queued_background_jobs():
            need = int(job["typical_box_minutes"])
            if int(job["cancels"]) >= CANCEL_BACKOFF:
                need *= 2
            if gap is None or need <= gap:
                return job
        return None

    def _start(self, job, box: str, gap: float | None, now: datetime) -> JobStart | None:
        kind = str(job["kind"])
        repo = str(job["repo"])
        workflow = JOB_WORKFLOWS.get(kind)
        row = self._config.repo_named(repo)
        if workflow is None:
            log.warning("background: job %s has unknown kind %r; skipping it", job["job_id"], kind)
            self._store.finish_background_job(
                int(job["job_id"]), state="skipped", outcome=f"unknown kind {kind}", now=now
            )
            return None
        if row is None or not row.enabled:
            log.info(
                "background: job %s is for %s, which is no longer a schedulable repo; skipping it",
                job["job_id"],
                repo,
            )
            self._store.finish_background_job(
                int(job["job_id"]), state="skipped", outcome=f"{repo} is not schedulable", now=now
            )
            return None
        try:
            inputs = json.loads(job["inputs_json"] or "{}")
        except ValueError:
            inputs = {}
        try:
            result = self._fabro.dispatch_job(
                repo=repo,
                environment_id=row.environment_id,
                inputs=inputs if isinstance(inputs, dict) else {},
                labels={"lane": "background", "box": box, "job": kind},
                coder_pool=f"{box}-bg",
                workflow=workflow,
            )
        except (FabroError, RunNotStarted) as exc:
            log.warning("background: job %s could not be dispatched: %s", job["job_id"], exc)
            self._store.finish_background_job(
                int(job["job_id"]), state="failed", outcome=str(exc), now=now
            )
            return None
        if not self._store.start_background_job(
            int(job["job_id"]), run_id=result.run_id, box=box, now=now
        ):
            # Another process took it between the read and here. The run exists and is
            # labelled, so it will do its work; the job row is simply not ours.
            log.warning(
                "background: job %s was taken by another writer; run %s is unrecorded",
                job["job_id"],
                result.run_id,
            )
            return None
        log.info(
            "background: started %s job %s on %s -> run %s (gap %s)",
            kind,
            job["job_id"],
            box,
            result.run_id,
            "unlimited" if gap is None else f"{gap:g}m",
        )
        return JobStart(
            job_id=int(job["job_id"]),
            kind=kind,
            repo=repo,
            box=box,
            run_id=result.run_id,
            gap_minutes=gap,
        )

    # --- completion (C7 step 11) ----------------------------------------------

    def sync(self, now: datetime | None = None) -> list[JobEnd]:
        """Finish, requeue or fail every `running` job from its fabro run status."""
        now = now or self._clock()
        if self._fabro is None:
            return []
        ended: list[JobEnd] = []
        for job in self._store.running_background_jobs():
            run_id = job["run_id"]
            if not isinstance(run_id, str) or not run_id:
                continue
            try:
                run = self._fabro.get_run(run_id)
            except FabroError as exc:
                if getattr(exc, "status_code", None) == 404:
                    # fabro has never heard of it, or it has been pruned: the job cannot
                    # be finished by waiting, so it is recorded as failed for a human.
                    self._store.finish_background_job(
                        int(job["job_id"]), state="failed", outcome="run is missing", now=now
                    )
                    ended.append(
                        JobEnd(int(job["job_id"]), str(job["kind"]), "failed", "run is missing")
                    )
                else:
                    log.info("background: run %s unreadable (%s); leaving the job running", run_id, exc)
                continue
            kind = status_kind(run)
            if kind not in TERMINAL_KINDS:
                continue
            reason = status_reason(run)
            if str(job["kind"]) == DOCTOR_KIND:
                doctor_end = self._finish_doctor(job, kind, reason, now)
                if doctor_end is not None:
                    ended.append(doctor_end)
                continue
            end = self._finish_plain(job, kind, reason, now)
            ended.append(end)
        for end in ended:
            log.info(
                "background: job %s (%s) -> %s%s",
                end.job_id,
                end.kind,
                end.state,
                f" ({end.outcome})" if end.outcome else "",
            )
        return ended

    def _finish_plain(self, job, kind: str, reason: str | None, now: datetime) -> JobEnd:
        """Finish a background job that only has its fabro run to go on.

        A cancel or a fabro restart puts the job back on the queue (D9); a success records it
        `done`; any other ending is a failure for a human to read.
        """
        job_id = int(job["job_id"])
        if kind == "succeeded":
            self._store.finish_background_job(
                job_id, state="done", outcome="succeeded", now=now
            )
            return JobEnd(job_id, str(job["kind"]), "done", "succeeded")
        if reason in ("cancelled", "terminated"):
            counted = reason == "cancelled"
            self._store.requeue_background_job(job_id, now=now, count_cancel=counted)
            return JobEnd(job_id, str(job["kind"]), "queued", reason)
        outcome = f"{kind}/{reason}" if reason else str(kind)
        self._store.finish_background_job(
            job_id, state="failed", outcome=outcome, now=now
        )
        return JobEnd(job_id, str(job["kind"]), "failed", outcome)

    def _finish_doctor(
        self, job, kind: str, reason: str | None, now: datetime
    ) -> JobEnd | None:
        """Task 06 step 5: read the PR's marker and decide what the attempt earned.

        This runs whatever the switch says. It is the end of an attempt that already started,
        and leaving it undone would strand a PR with its labels removed and neither a review
        nor a hand-off (the brief's second "do not repeat" rule).

        - no marker: nothing reached GitHub, so the ordinary rule applies (a cancel requeues,
          anything else is a failure for a human);
        - `review=skip`, or `pushed=none`: hand off with no review,
          `handed_off_no_review` -- the doctor's own contract says a review cannot pass;
        - `pushed=<sha>`: queue the follow-up review;
        - `pushed=pending`: the run died inside `publish`. If the PR's head is no longer the
          marker's `head=`, the push landed and the review runs; otherwise nothing was pushed
          and the PR is handed off.

        A read that fails leaves the job `running` so the next tick retries. The box is not
        held: the run is over, and `fill` skips only the box whose job is `running`, which
        costs at most one tick's use of one box rather than losing the PR.
        """
        job_id = int(job["job_id"])
        repo = str(job["repo"])
        try:
            number = int(str(job["target"] or 0))
        except (TypeError, ValueError):
            number = 0
        facts: PrFacts | None = None
        if number > 0 and self._read_pr is not None:
            try:
                facts = self._read_pr(repo, number)
            except GitHubError as exc:
                log.info(
                    "background: pr-doctor job %s: cannot read %s#%s (%s); leaving it running",
                    job_id,
                    repo,
                    number,
                    exc,
                )
                return None
        marker = parse_marker(facts.doctor_marker) if facts is not None else None
        if marker is None:
            return self._finish_plain(job, kind, reason, now)
        if marker.pushed_nothing or not marker.wants_review:
            log.info(
                "background: pr-doctor job %s: %s#%s pushed nothing or cannot pass a review; "
                "handing off without one",
                job_id,
                repo,
                number,
            )
            return self._hand_off_job(job, reviewed=False, outcome="handed_off_no_review", now=now)
        if marker.pushed and marker.pushed != "pending":
            return self._queue_review(job, now)
        head = facts.head_sha if facts is not None else None
        if head and marker.head and head != marker.head:
            log.info(
                "background: pr-doctor job %s: %s#%s died inside publish but its head moved "
                "(%s -> %s), so the push landed",
                job_id,
                repo,
                number,
                (marker.head or "")[:12],
                head[:12],
            )
            return self._queue_review(job, now)
        log.info(
            "background: pr-doctor job %s: %s#%s died inside publish with its head unmoved; "
            "handing off without a review",
            job_id,
            repo,
            number,
        )
        return self._hand_off_job(job, reviewed=False, outcome="handed_off_no_review", now=now)

    def _queue_review(self, job, now: datetime) -> JobEnd:
        """Put the doctor's follow-up review in the queue (task 06 step 7)."""
        job_id = int(job["job_id"])
        self._store.set_background_outcome(job_id, outcome="review_pending", now=now)
        log.info(
            "background: pr-doctor job %s queued its follow-up review for %s#%s",
            job_id,
            job["repo"],
            job["target"],
        )
        return JobEnd(job_id, str(job["kind"]), "done", "review_pending")

    def _hand_off_job(self, job, *, reviewed: bool, outcome: str, now: datetime) -> JobEnd:
        """Restore the needs-human label and say so on the PR (task 06 step 8)."""
        hand_off(
            self._store,
            job,
            token=self._github_token,
            outcome=outcome,
            reviewed=reviewed,
            now=now,
        )
        return JobEnd(int(job["job_id"]), str(job["kind"]), "done", outcome)

    # --- making room for a coder loop (D8, step 12) ---------------------------

    def make_room(self, now: datetime | None = None) -> Eviction | None:
        """Cancel the youngest background run when a coder dispatch needs its slot.

        Called immediately before a coder-loop dispatch. Fabro admits runs first come,
        first served, so a background run holding the last slot would make the coder
        loop wait; the coder loop has precedence, so the background run goes (D8).
        """
        now = now or self._clock()
        if self._fabro is None:
            return None
        # No background run, nothing to cancel: decide that from the store before asking
        # fabro, so a coder dispatch with the lane idle makes no extra call.
        running = [job for job in self._store.running_background_jobs() if job["run_id"]]
        if not running:
            return None
        try:
            active = len(self._fabro.active_runs())
        except FabroError as exc:
            log.info("background: cannot count active runs (%s); not making room", exc)
            return None
        if active < self._config.max_concurrent_runs:
            return None
        # The youngest: it has done the least work, so it loses the least.
        youngest = max(running, key=lambda job: (job["last_started_at"] or "", job["job_id"]))
        run_id = str(youngest["run_id"])
        if not self._cancel(run_id, "a coder loop needs the run slot"):
            return None
        eviction = Eviction(
            box=str(youngest["box"]),
            run_id=run_id,
            job_id=int(youngest["job_id"]),
            kind=str(youngest["kind"]),
            reason="run slot for a coder loop",
        )
        self._evictions.append((now, eviction))
        return eviction

    def _cancel(self, run_id: str, why: str) -> bool:
        """Cancel one run. `False` when it could not be asked, so nothing is recorded."""
        if self._fabro is None:
            return False
        try:
            self._fabro.cancel_run(run_id)
        except FabroError as exc:
            log.info("background: could not cancel %s (%s): %s", run_id, why, exc)
            return False
        log.info("background: cancelled %s: %s", run_id, why)
        return True

    # --- what the page and the API read ---------------------------------------

    def _loop_on(self, box: str) -> Loop | None:
        """The coder loop leasing `box`, with its stage read **fresh** (C7 step 7)."""
        lease = next(
            (held for held in self._leases.active() if held.coder_pool == box), None
        )
        if lease is None:
            return None
        if self._fabro is None:
            return Loop(lease=lease, node=None, on_box=False)
        try:
            stages = self._fabro.get_stages(lease.run_id)
        except FabroError as exc:
            log.info("background: cannot read %s's stages (%s)", lease.run_id, exc)
            return Loop(lease=lease, node=None, on_box=False)
        node = running_node(stages)
        return Loop(
            lease=lease,
            node=node,
            on_box=is_box_stage(lease.box_stages, lease.issue_number, node),
        )

    def evictions_today(self, now: datetime | None = None) -> int:
        now = now or self._clock()
        today = now.date()
        self._evictions = [
            (at, eviction) for at, eviction in self._evictions if at.date() == today
        ]
        return len(self._evictions)

    def box_views(self, now: datetime | None = None) -> list[BoxView]:
        """Per box: the running job, or the first reason nothing can start.

        Reads only state the last tick left behind — the store, the lease table and the
        cached `/health` reading — so a page load makes no network call.
        """
        now = now or self._clock()
        running = {job["box"]: job for job in self._store.running_background_jobs()}
        queued = self._store.queued_background_jobs()
        views: list[BoxView] = []
        for box in self._config.box_health:
            health = self._readings.get(box)
            job = running.get(box)
            if job is not None:
                views.append(
                    BoxView(box=box, running=job_row(job), blocked_by=None, health=health)
                )
                continue
            views.append(
                BoxView(
                    box=box,
                    running=None,
                    blocked_by=self._blocked_by(box, health, queued),
                    health=health,
                )
            )
        return views

    def _blocked_by(self, box: str, health: Health | None, queued: list) -> str:
        """The first C7 condition that stops this box, as a stable machine token."""
        if not self._config.background:
            return "switch off"
        if box in self._store.drained_pools():
            return "drained"
        loop = self._loop_on(box)
        if loop is not None:
            if loop.node is None:
                return "loop between stages"
            if loop.on_box:
                return "loop in a box stage"
            if not loop.lease.box_stages:
                return "loop stages unknown"
        if health is None:
            return "box health unknown"
        if not health.idle:
            return "box busy"
        if health.idle_seconds < self._config.bg_gap_seconds:
            return "box not idle long enough"
        if self._free_slots() < self._config.bg_slot_reserve:
            return "slots"
        if not queued:
            return "no job queued"
        blocked, gap = self._gap_minutes(box, loop)
        if blocked:
            return "no gap"
        if self._pick(gap) is None:
            return "no job fits"
        return "ready"

    def state(self, now: datetime | None = None) -> dict[str, object]:
        """The whole lane, for `/api/background` and the queue page's strip (C7 step 14)."""
        now = now or self._clock()
        return {
            "switch": "on" if self._config.background else "off",
            "gap_seconds": self._config.bg_gap_seconds,
            "slot_reserve": self._config.bg_slot_reserve,
            "max_concurrent_runs": self._config.max_concurrent_runs,
            "kinds": list(self._config.bg_kinds),
            "evictions_today": self.evictions_today(now),
            "counts": self._store.background_counts(),
            "boxes": [
                {
                    "box": view.box,
                    "running": view.running,
                    "blocked_by": view.blocked_by,
                    "health": (
                        None
                        if view.health is None
                        else {
                            "busy": view.health.busy,
                            "queued": view.health.queued,
                            "idle_seconds": view.health.idle_seconds,
                        }
                    ),
                }
                for view in self.box_views(now)
            ],
            "reviews": self.review_state(now),
            "jobs": [job_row(row) for row in self._store.background_jobs(limit=50)],
        }

    def review_state(self, now: datetime | None = None) -> dict[str, object]:
        """The doctor's follow-up reviews: how many went out today, and which are waiting.

        Operator decision 3: the cap is counted per UTC day across every repository, a review
        that failed to dispatch does not count, and a pushed PR over the cap waits for the
        next day's slot. The waiting list is what the operator needs to see to answer "why
        has this PR had no review?" without reading the store.
        """
        now = now or self._clock()
        midnight = datetime(now.year, now.month, now.day, tzinfo=UTC)
        waiting = self._store.pending_review_jobs()
        return {
            "today": self._store.reviews_dispatched_since(midnight),
            "per_day": self._config.pr_doctor_reviews_per_day,
            "waiting": [
                {
                    "job_id": int(row["job_id"]),
                    "repo": row["repo"],
                    "pr": row["target"],
                    "created_at": row["created_at"],
                }
                for row in waiting
            ],
        }

    # --- the one producer in this task (C7 step 15) ---------------------------

    def enqueue_box_noop(self, repo: str, now: datetime | None = None) -> int | None:
        """Queue one `box-noop` job for `repo`, so task 12 can measure cancels."""
        now = now or self._clock()
        if "box-noop" not in self._config.bg_kinds:
            log.info("background: box-noop is not an enabled kind; not enqueuing")
            return None
        return self._store.enqueue_background_job(
            kind="box-noop",
            repo=repo,
            target="",
            inputs={},
            priority=BACKGROUND_JOB_PRIORITY["box-noop"],
            typical_box_minutes=BOX_NOOP_TYPICAL_MINUTES,
            now=now,
        )
