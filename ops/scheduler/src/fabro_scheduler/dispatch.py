"""The dispatch loop: the ranked queue, the free boxes, and the lease that joins them.

This is the centre of the scheduler. Every 5 seconds it asks two questions and,
if both answer yes, starts one real `backlog` run on every free box that has
dispatchable work:

1. is a coder instance free and not drained?
2. is the queue non-empty?

**A free box is never left idle while any dispatchable item exists** (decision 6 as
"prefer diversity, never idle a box"): `choose_next` first takes the highest-ranked
item whose repo has no run in flight, so concurrent runs spread across repos; only
when every queued repo already has a run in flight does it fall through to a second
concurrent run from a busy repo, which is what keeps a single-repo workload
saturating all the boxes rather than idling the ones a lean queue cannot diversify.

**Dispatch leaves its receipt first.** In order: add `agent-in-progress`, remove
`agent`, then create the run, then start it, then record the lease. The label write
is what draft 09's recovery reads after the scheduler dies between labelling and
recording, so it cannot be the step that never happened. Add-then-remove and not
the reverse: an interruption between the two leaves the issue in the queue *and*
marked, which is recoverable, whereas the opposite order leaves it carrying neither
label and therefore invisible to both `acquire` and this loop.

**A failure anywhere before the run exists rolls the labels back** — remove
`agent-in-progress`, restore `agent` — and records no lease. A failure *after* the
run exists (`/runs/{id}/start` returning `409`, say) leaves a run in `submitted`
that will never execute: that is reported with its run id so it can be cancelled,
and it is the one loose end this draft knowingly leaves for draft 09.

**The database, not this loop, is what makes a box exclusive.** `leases` has
`coder_pool` as its primary key and the conflict is resolved inside the insert, so
entering the loop twice cannot hand one box two runs even though this class does
nothing to prevent it.

**A failed dispatch backs that repo off for the rest of the tick**, not forever:
the next tick tries it again 5 seconds later, which is the "rather than retrying
instantly in a tight loop" rule and no more than that. A repo that fails every tick
costs four label requests a minute, which is inside budget.

**What this draft does not do.** Nothing releases a lease, nothing requeues, and
nothing reconciles a restart; all three are draft 09. The consequence is bounded in
a way worth being explicit about, because it is what makes leaving this running
tolerable: with two coder instances the loop can hold at most two leases, so after
the two boxes are taken it stops dispatching by itself — it does not spin, and it
cannot consume more than the two boxes that exist. To clear them, delete the rows
(see `ops/README.md`, *The coder scheduler*).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .background import BackgroundLane, BoxHealth
from .config import SchedulerConfig
from .doctor import HANDOFF_REVIEW_NOT_STARTED_COMMENT, hand_off
from .fabro import FabroClient, FabroError, RunNotStarted
from .github import (
    IN_PROGRESS_LABEL,
    REQUIRED_LABEL,
    GitHubError,
    Issue,
    add_label,
    remove_label,
)
from .lease import Lease, LeaseConflict, LeaseStore
from .precheck import PrecheckProducer
from .queue import QueueItem, build_queue
from .store import Store
from .workflow_version import WorkflowVersionError

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 5.0

# How many failed attempts at dispatching one follow-up review before the PR is handed off
# without one. The job stays `review_pending` between attempts, so it is retried on the next
# tick that is not backing off. With the doubling backoff below, six attempts span about 31
# minutes: long enough to outlast a fabro restart or a GitHub blip (three attempts a minute
# apart turned a 2-3 minute restart into a pushed fix with no review), short enough that a
# missing automation row, which no retry fixes, hands the PR off the same hour.
REVIEW_DISPATCH_ATTEMPTS = 6

# The quiet period after the first failed review dispatch, doubled after each further one
# (60 s, 2, 4, 8, 16 min). Without it a missing `pr-review-<repo>` automation row would be
# re-read every 5 seconds; the job itself is untouched, so a fixed row is picked up on the
# next retry.
REVIEW_RETRY_SECONDS = 60.0

# Long enough for a label write over a LAN, short enough that a wedged GitHub does
# not hold the dispatch loop past its own interval.
REQUEST_TIMEOUT_SECONDS = 15.0

# How far one attempt got before it stopped. `failed_stage` is set on every failure
# and is what the loop keys its per-tick backoff on, so the *value* matters more
# than the message.
STAGE_CONFIG = "config"
STAGE_LABELS = "labels"
STAGE_CREATE = "create"
STAGE_START = "start"
STAGE_LEASE = "lease"


@dataclass(frozen=True)
class DispatchAttempt:
    """One dispatch the loop tried, and how far it got.

    A `run_id` means fabro created a run — the only step that cannot be undone. So
    a failure carrying one is logged as an error and needs a human, while a failure
    without one left nothing behind at all.
    """

    repo: str
    issue_number: int
    coder_pool: str
    run_id: str | None = None
    failed_stage: str | None = None
    error: str | None = None

    @property
    def dispatched(self) -> bool:
        """Whether the whole sequence completed: a run exists *and* holds its box."""
        return self.failed_stage is None and self.run_id is not None


def _pick_one(
    queue: Sequence[QueueItem],
    predicate: Callable[[QueueItem], bool],
    free_pools: Sequence[str],
    last_pool_for_repo: Callable[[str], str | None],
) -> tuple[QueueItem, str] | None:
    """The highest-ranked item passing `predicate`, paired with a free box.

    Box choice is **soft affinity** (decision 11), which is the last tiebreak and
    only a tiebreak: among the free boxes, the one this repo last ran on wins; if
    it is busy, any free box will do and the loop never waits for the preferred
    one. Non-determinism is the thing being avoided — with no affinity the choice
    would be `free_pools[0]`, which is right but means two repos alternately
    jumping between boxes and re-warming neither.
    """
    for item in queue:
        if not predicate(item):
            continue
        preferred = last_pool_for_repo(item.issue.repo)
        if preferred is not None and preferred in free_pools:
            return item, preferred
        # `free_pools` is in `coder_pools` order, so this is deterministic.
        return item, free_pools[0]
    return None


def choose_next(
    queue: Sequence[QueueItem],
    leases: Sequence[Lease],
    free_pools: Sequence[str],
    last_pool_for_repo: Callable[[str], str | None],
) -> tuple[QueueItem, str] | None:
    """The top-ranked item a free box may take, paired with the box to use.

    `queue` is already in dispatch order — this never re-ranks, so there remains
    exactly one definition of "next" in the system (`queue.rank`). `None` means
    "do not dispatch": no free box, an empty queue, or every queued item is
    itself already running.

    Two passes, so a free box is **never left idle while any dispatchable item
    exists** (decision 6 as "prefer diversity, never idle a box"):

    1. **Diversity.** Take the highest-ranked item whose repo has no run in
       flight, so concurrent runs spread across repos rather than stacking on one.
    2. **Saturation.** If that finds nothing — every queued repo already has a
       run in flight — take the highest-ranked item that is not itself the one
       already running, even from a busy repo. Idling a box because the best item
       happened to be a second one from a busy repo would give up the throughput
       the whole service exists to protect, at the cost of at most two concurrent
       runs in one repo. The running-keys check matters: a label write does not
       evict the local cache row until the 60s inventory poll, so without it the
       very next tick could re-dispatch the exact issue that is already on a box.
    """
    if not free_pools or not queue:
        return None

    busy_repos = {lease.repo for lease in leases}
    running = {(lease.repo, lease.issue_number) for lease in leases}

    chosen = _pick_one(
        queue,
        lambda item: item.issue.repo not in busy_repos,
        free_pools,
        last_pool_for_repo,
    )
    if chosen is not None:
        return chosen

    return _pick_one(
        queue,
        lambda item: (item.issue.repo, item.issue.number) not in running,
        free_pools,
        last_pool_for_repo,
    )


def choose_workflow(issue: Issue, share: int) -> str:
    """Which workflow works this issue: `ralph` or `backlog` (ADR 0029 D2, O2).

    Pure and total: same inputs, same answer, no I/O. The rule is the trial's:

    * `share` is 0 (the default, and the kill switch) -> always `backlog`;
    * `share` is 50 -> an EVEN issue number is `ralph`, an odd one `backlog`;
    * a `fast-path` issue is always `backlog`, whatever its number: the fast lane is
      `backlog`'s own feature (ADR 0023), and splitting it would compare two things
      at once.

    Parity is the issue's own number, so a Remainder issue or a Child issue takes
    the parity of ITS number, not its parent's. That is deliberate: the arms are
    compared per run, and a child run is a run. A requeued issue keeps its arm
    because its number does not change.

    The label is matched exactly (`fast-path`), never case-folded: GitHub label
    names are case-sensitive, and the graph's own lane check matches it the same
    way.
    """
    if share == 0:
        return "backlog"
    if "fast-path" in issue.labels:
        return "backlog"
    return "ralph" if issue.number % 2 == 0 else "backlog"


class DispatchLoop:
    """The 5-second loop, and the one-tick pass it runs each time.

    Split so a test can drive a tick by hand: `tick()` never touches a thread, and
    `start()`/`stop()` are the only things that do.
    """

    def __init__(
        self,
        config: SchedulerConfig,
        store: Store,
        leases: LeaseStore,
        fabro: FabroClient,
        github_token: str,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lane: BackgroundLane | None = None,
        health: BoxHealth | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        # The background lane (C7). Built here unless injected, so `tick` always has one;
        # with `SCHEDULER_BOX_HEALTH` empty it does nothing and makes no call at all.
        self._lane = (
            lane
            if lane is not None
            else BackgroundLane(config, store, leases, fabro, health=health, clock=clock)
        )
        # The precheck producer (more-local-inference 07): queues a box check for the next issues
        # in dispatch order, from the ranked queue this loop already builds. Off unless the lane
        # is on and `issue-precheck` is an enabled kind.
        self._precheck = PrecheckProducer(config, store)
        # The last peak-hours decision, so the log says when dispatch paused and
        # resumed once each rather than on every 5-second tick.
        self._peak_paused: bool | None = None
        self._store = store
        self._leases = leases
        self._fabro = fabro
        self._github_token = github_token
        self._pools = tuple(config.coder_pools)
        # When each doctor job's last review dispatch failed, monotonic. Process-local: a
        # restart retrying immediately is harmless (one extra call), and a persisted map would
        # be a third table for a 60-second quiet period.
        self._review_retry_at: dict[int, float] = {}
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Created in `start`. A hand-driven `tick()` leaves it `None`, and the label
        # calls then open and close a client each — correct, just not pooled.
        self._http: httpx.Client | None = None

    @property
    def interval_seconds(self) -> float:
        return self._interval

    @property
    def running(self) -> bool:
        """Whether a tick thread is live. Read by the page and by tests."""
        return self._thread is not None

    def tick(self) -> list[DispatchAttempt]:
        """One pass: fill every free box that has eligible work, then return.

        The pass fills *all* the free boxes rather than one per tick — "two boxes,
        two repos, one run each" is the tracer bullet's whole outcome, and doing it
        a tick at a time would leave the second box idle for five seconds for no
        reason. It terminates: every iteration either takes a pool (there are two)
        or rules a repo out for the rest of this tick (there are four).

        Inside the provider's peak hours (`peak.py`) it dispatches nothing, unless the
        operator has set the override. Runs already on a box are not touched.
        """
        now = self._clock()
        if self._peak_pause(now):
            # Background runs ignore the peak-hours pause (D7): they spend no hosted
            # credits, and a gap is a gap whatever the hour. The pause is when boxes are
            # most often free, so the prechecks are kept queued through it too.
            if self._precheck.enabled:
                self._precheck.tick(self._ranked(now), self._running_issues(), now)
            self._lane.tick(now)
            return []
        # A doctor's follow-up review goes before any issue: it spends hosted credits the
        # factory has already paid for the review, and it turns them into a merge (D14). It
        # takes a primary lease, so it is dispatched onto a free box exactly like an issue run.
        self._dispatch_reviews(now)

        ranked = self._ranked(now)

        attempts: list[DispatchAttempt] = []
        backed_off: set[str] = set()
        while True:
            free = self._leases.free_pools(self._pools, self._drained_pools())
            candidates = [
                item for item in ranked if item.issue.repo not in backed_off
            ]
            chosen = choose_next(
                candidates,
                # Issue leases only. A `pr` lease's `issue_number` is a PR number, and treating
                # it as a running issue would let PR #123 hide issue #123 from this tick's
                # diversity pass (more-local-inference 06).
                [
                    held
                    for held in self._leases.active()
                    if held.target_kind == "issue"
                ],
                free,
                self._leases.last_pool_for_repo,
            )
            if chosen is None:
                break

            item, pool = chosen
            # Fabro admits runs first come, first served under `max_concurrent_runs`, so
            # a background run holding the last slot would make the coder loop wait. The
            # coder loop has precedence (D8): cancel the youngest background run first.
            if self._config.box_health:
                evicted = self._lane.make_room(now)
                if evicted is not None:
                    log.info(
                        "dispatch: cancelled background run %s on %s to free a slot",
                        evicted.run_id,
                        evicted.box,
                    )
            attempt = self._dispatch(item, pool)
            attempts.append(attempt)
            if not attempt.dispatched:
                backed_off.add(item.issue.repo)

        # The lane runs after the primary dispatch, so a coder loop is never delayed by
        # it in the same tick: evict first (D17 layer 2), then fill what is left (D7). The
        # precheck producer reads the queue as this tick left it, so an issue just dispatched
        # is no longer in it and its queued precheck is skipped.
        if self._precheck.enabled:
            self._precheck.tick(self._ranked(now), self._running_issues(), now)
        self._lane.tick(now)
        return attempts

    def _running_issues(self) -> set[tuple[str, int]]:
        """Every `(repo, issue)` a coder loop holds; a `pr` lease's number is a PR's."""
        return {
            (held.repo, held.issue_number)
            for held in self._leases.active()
            if held.target_kind == "issue"
        }

    def _ranked(self, now: datetime) -> list[QueueItem]:
        return build_queue(
            self._config.schedulable_repos(),
            self._store,
            now,
            self._config.starvation_ceiling,
        )

    # --- the doctor's follow-up reviews (D14, task 06 steps 6 and 7) --------------

    def _dispatch_reviews(self, now: datetime) -> None:
        """Lease one `pr-review` per free box, ahead of the issue queue.

        Bounded by the free boxes, so a queue of waiting reviews fills the boxes in one tick
        and no further. A dispatch that fails stops this tick: the next one tries again after
        `REVIEW_RETRY_SECONDS`, and after `REVIEW_DISPATCH_ATTEMPTS` failures the PR is handed
        off without a review instead of sitting in a queue that cannot move.
        """
        if self._config.pr_doctor_reviews_per_day <= 0:
            return
        while True:
            free = self._leases.free_pools(self._pools, self._drained_pools())
            if not free:
                return
            row = self._next_review(now)
            if row is None:
                return
            if not self._dispatch_review(row, free[0], now):
                return

    def _next_review(self, now: datetime):
        """The oldest waiting review the daily cap and the backoff allow, or `None`."""
        midnight = datetime(now.year, now.month, now.day, tzinfo=UTC)
        dispatched = self._store.reviews_dispatched_since(midnight)
        if dispatched >= self._config.pr_doctor_reviews_per_day:
            return None
        monotonic = time.monotonic()
        for row in self._store.pending_review_jobs():
            job_id = int(row["job_id"])
            retry_at = self._review_retry_at.get(job_id)
            if retry_at is not None and monotonic < retry_at:
                continue
            return row
        return None

    def _dispatch_review(self, job, coder_pool: str, now: datetime) -> bool:
        """Create, start and lease one follow-up review. `False` when this tick must stop."""
        job_id = int(job["job_id"])
        repo = str(job["repo"])
        try:
            pr_number = int(str(job["target"] or 0))
        except (TypeError, ValueError):
            pr_number = 0
        if self._fabro is None or pr_number <= 0:
            self._review_failed(job, "no fabro client, or the job has no PR number", now)
            return False
        row = self._config.repo_named(repo)
        if row is None or not row.enabled:
            self._review_failed(job, f"{repo} is no longer a schedulable [[repo]]", now)
            return False
        try:
            settings = self._fabro.pr_review_config(repo)
        except FabroError as exc:
            self._review_failed(job, f"could not read the automations: {exc}", now)
            return False
        if settings is None:
            self._review_failed(job, f"no usable pr-review automation targets {repo}", now)
            return False
        # The review is a coder loop and takes a primary lease, so it gets a run slot the
        # same way an issue dispatch does (D8).
        if self._config.box_health:
            self._lane.make_room(now)
        try:
            result = self._fabro.dispatch_pr_review(
                repo=repo,
                pr_number=pr_number,
                environment_id=settings.environment_id,
                auto_merge=settings.auto_merge,
                coder_pool=coder_pool,
            )
        except RunNotStarted as exc:
            self._review_failed(job, f"run {exc.run_id} was created but not started", now)
            return False
        except (FabroError, WorkflowVersionError) as exc:
            self._review_failed(job, f"fabro refused the run: {exc}", now)
            return False
        lease = Lease(
            coder_pool=coder_pool,
            repo=repo,
            issue_number=pr_number,
            run_id=result.run_id,
            box_stages=self._fabro.box_stages(result.commit_sha, "pr-review"),
            dispatched_at=now,
            workflow="pr-review",
            target_kind="pr",
        )
        try:
            self._leases.acquire(lease)
        except LeaseConflict as exc:
            # The run is started, so it is THE review: record it, or the next tick would
            # dispatch a second one while this runs untracked. With no lease, the release
            # poll's orphan sweep hands the PR off once the run ends.
            self._store.start_background_review(job_id, run_id=result.run_id, now=now)
            log.error(
                "dispatch: %s#%s: the follow-up review run %s was started but its lease was "
                "refused (%s); it is recorded, and the PR is handed off when it ends",
                repo,
                pr_number,
                result.run_id,
                exc,
            )
            return False
        self._store.start_background_review(job_id, run_id=result.run_id, now=now)
        self._review_retry_at.pop(job_id, None)
        log.info(
            "dispatch: %s#%s -> follow-up review run %s on %s (env=%s, auto_merge=%s)",
            repo,
            pr_number,
            result.run_id,
            coder_pool,
            settings.environment_id,
            settings.auto_merge,
        )
        return True

    def _review_failed(self, job, why: str, now: datetime) -> None:
        """Count one failed review dispatch and hand off once it has failed enough times."""
        job_id = int(job["job_id"])
        failures = self._store.record_review_failure(job_id, now=now)
        self._review_retry_at[job_id] = time.monotonic() + REVIEW_RETRY_SECONDS * 2 ** max(
            failures - 1, 0
        )
        log.warning(
            "dispatch: pr-doctor job %s for %s#%s: the follow-up review could not be "
            "dispatched (%d of %d): %s",
            job_id,
            job["repo"],
            job["target"],
            failures,
            REVIEW_DISPATCH_ATTEMPTS,
            why,
        )
        if failures < REVIEW_DISPATCH_ATTEMPTS:
            return
        log.error(
            "dispatch: pr-doctor job %s for %s#%s gave up on its follow-up review; handing "
            "the PR off. %s",
            job_id,
            job["repo"],
            job["target"],
            HANDOFF_REVIEW_NOT_STARTED_COMMENT,
        )
        hand_off(
            self._store,
            job,
            token=self._github_token,
            outcome="handed_off_no_review",
            reviewed=False,
            now=now,
            body=HANDOFF_REVIEW_NOT_STARTED_COMMENT,
        )

    def _peak_pause(self, now: datetime) -> bool:
        """Whether this tick dispatches nothing because of the peak-hours block.

        The override is read fresh each tick, like the drain flags, so the page's
        button takes effect on the next tick with no restart.
        """
        peak = self._config.peak_hours
        status = peak.status(now)
        override = status.in_block and self._store.peak_override()
        paused = status.in_block and not override
        if paused != self._peak_paused:
            if paused:
                log.info(
                    "dispatch: paused for peak hours (%s, lead %s) until %s; "
                    "runs already on a box continue",
                    peak.spec,
                    peak.lead,
                    status.until.isoformat() if status.until else "?",
                )
            elif self._peak_paused is not None:
                log.info(
                    "dispatch: resumed%s",
                    " (peak-hours override is on)" if override else "",
                )
            self._peak_paused = paused
        return paused

    # --- one dispatch -----------------------------------------------------------

    def _dispatch(self, item: QueueItem, coder_pool: str) -> DispatchAttempt:
        repo = item.issue.repo
        number = item.issue.number

        def attempt(**fields: object) -> DispatchAttempt:
            return DispatchAttempt(
                repo=repo,
                issue_number=number,
                coder_pool=coder_pool,
                **fields,  # type: ignore[arg-type]
            )

        # One call, one arm: the lease, the run's label and `run_history` all read
        # this value, so a second call could only disagree with it.
        workflow = choose_workflow(item.issue, self._config.ralph_share)

        row = self._config.repo_named(repo)
        if row is None or not row.enabled:
            # Cannot happen for an item `build_queue` produced, which only keeps
            # repos it was handed. It is a refusal rather than an assumption
            # because the alternative is guessing an `environment_id`, and a
            # guessed one is a wrong image for the repo's CI.
            message = f"{repo} is no longer a schedulable [[repo]] in repos.toml"
            log.error("dispatch: %s#%s: %s", repo, number, message)
            return attempt(failed_stage=STAGE_CONFIG, error=message)

        # 1. The receipt, before anything exists. `agent` goes second so an
        #    interruption leaves the issue marked rather than invisible.
        try:
            add_label(
                repo,
                number,
                IN_PROGRESS_LABEL,
                self._github_token,
                client=self._http,
            )
            remove_label(
                repo, number, REQUIRED_LABEL, self._github_token, client=self._http
            )
        except GitHubError as exc:
            # Roll back even when the *first* write is the one that failed: a
            # timeout is not evidence that nothing was written, and both rollback
            # operations are idempotent, so an unnecessary one costs two requests
            # and undoes nothing. Leaving `agent-in-progress` on an issue with no
            # run would make it invisible to both `acquire` and this loop.
            self._rollback_labels(repo, number)
            log.warning("dispatch: %s#%s: could not label the issue: %s", repo, number, exc)
            return attempt(failed_stage=STAGE_LABELS, error=str(exc))

        # 2. Register the version, create the run, start it.
        try:
            result = self._fabro.dispatch(
                repo=repo,
                issue_number=number,
                coder_pool=coder_pool,
                environment_id=row.environment_id,
                workflow=workflow,
            )
        except RunNotStarted as exc:
            self._rollback_labels(repo, number)
            log.error(
                "dispatch: %s#%s: run %s was created but could not be started, so it "
                "sits in `submitted` and will never execute: %s",
                repo,
                number,
                exc.run_id,
                exc,
            )
            return attempt(
                run_id=exc.run_id, failed_stage=STAGE_START, error=str(exc)
            )
        except (FabroError, WorkflowVersionError) as exc:
            # Both mean the same thing: fabro has no run, so the labels go back.
            self._rollback_labels(repo, number)
            log.warning("dispatch: %s#%s: fabro refused the run: %s", repo, number, exc)
            return attempt(failed_stage=STAGE_CREATE, error=str(exc))

        # 3. The lease.
        lease = Lease(
            coder_pool=coder_pool,
            repo=repo,
            issue_number=number,
            run_id=result.run_id,
            # The loop's box stages, read from the version's generated manifest when it
            # was registered (C1, C7 step 6). Persisted on the lease so the lane can
            # judge this run after a scheduler restart.
            box_stages=self._fabro.box_stages(result.commit_sha, workflow),
            dispatched_at=datetime.now(UTC),
            # The item's wait travels with the lease, because the poll deletes its
            # cache row within a minute of this label write and draft 09's requeue
            # needs the original wait to put back.
            queued_since=item.issue.first_seen,
            workflow=workflow,
        )
        try:
            self._leases.acquire(lease)
        except LeaseConflict as exc:
            # The run is real and its labels stay — it will do its own label work,
            # and removing the receipt now would be a lie. This is an error and not
            # a warning because two runs are now sharing one single-slot box.
            log.error(
                "dispatch: %s#%s: run %s was started but its lease was refused, so "
                "nothing is tracking it: %s",
                repo,
                number,
                result.run_id,
                exc,
            )
            return attempt(
                run_id=result.run_id, failed_stage=STAGE_LEASE, error=str(exc)
            )

        # The bump has done its job. An override means "next", not "forever"
        # (overview decision 4), and the item is no longer waiting to be next — the
        # lease is the durable proof of that. Cleared after the lease and not before:
        # a dispatch that failed to record a lease is not one the queue should stop
        # prioritising. Logged only when there was something to clear, so the line
        # is evidence rather than noise.
        if self._store.clear_override_on_dispatch(repo, number):
            log.info(
                "dispatch: %s#%s: cleared the operator override on dispatch",
                repo,
                number,
            )

        log.info(
            "dispatch: %s#%s -> run %s on %s (%s, env=%s)",
            repo,
            number,
            result.run_id,
            coder_pool,
            workflow,
            row.environment_id,
        )
        return attempt(run_id=result.run_id)

    def _rollback_labels(self, repo: str, number: int) -> None:
        """Remove the receipt and restore the queue label. Never raises.

        Best-effort on purpose: this runs on the failure path, and letting a GitHub
        error escape would replace the fabro error that explains what happened with
        one that does not. Both writes are idempotent, so a recovery pass or an
        operator can simply repeat them.
        """
        try:
            remove_label(
                repo, number, IN_PROGRESS_LABEL, self._github_token, client=self._http
            )
        except GitHubError as exc:
            log.error(
                "dispatch: %s#%s: could not remove %s after a failed dispatch: %s",
                repo,
                number,
                IN_PROGRESS_LABEL,
                exc,
            )
        try:
            add_label(repo, number, REQUIRED_LABEL, self._github_token, client=self._http)
        except GitHubError as exc:
            log.error(
                "dispatch: %s#%s: could not restore %s after a failed dispatch: %s",
                repo,
                number,
                REQUIRED_LABEL,
                exc,
            )

    def _drained_pools(self) -> Set[str]:
        """The coder instances taken out of rotation, read from `pool_state`.

        **Drain** is draft 11's control (decision 15): it stops *new* dispatch to a
        box and lets the run already on it finish. It never cancels — cancelling the
        run is its own, explicitly-labelled route — so this is read only by
        `free_pools`, which excludes a drained pool from the set a new run may take
        and does nothing at all to the lease that is already there.
        """
        return set(self._store.drained_pools())

    # --- the loop ---------------------------------------------------------------

    def start(self) -> None:
        """Start ticking. Idempotent."""
        if self._thread is not None:
            return
        self._http = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._thread = threading.Thread(
            target=self._run, name="fabro-scheduler-dispatch", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop ticking and close the HTTP client. Safe to call twice."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        if self._http is not None:
            self._http.close()
            self._http = None

    def _run(self) -> None:
        """Tick immediately, then every `interval`.

        Immediately, because the first tick is what makes a freshly started
        container dispatch as soon as the inventory has filled the queue; there is
        nothing to wait for. A tick that raises is logged and the loop continues —
        an unhandled bug must not silently stop all dispatch while `/health` keeps
        answering `ok`, which is the failure mode this whole service exists to
        avoid.
        """
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - see the docstring
                log.exception("dispatch: tick failed")
            if self._stop.wait(self._interval):
                return
