"""The dispatch loop: the ranked queue, the free boxes, and the lease that joins them.

This is the centre of the scheduler. Every 5 seconds it asks three questions and,
if all three answer yes, starts exactly one real `backlog` run:

1. is a coder instance free and not drained?
2. is the queue non-empty?
3. does the highest-ranked eligible item's repo have no run in flight?

**One in-flight run per repo** (decision 6) is the reason question 3 exists, and
the reason the loop *skips* rather than waits: if the top-ranked item's repo is
busy, the next-ranked item from a different repo is dispatched instead. Idling a
free box because the best item happened to be a second one from a busy repo would
give up the throughput the whole service exists to protect. Skipping is only safe
because of the next rule.

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
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import SchedulerConfig
from .fabro import FabroClient, FabroError, RunNotStarted
from .github import (
    IN_PROGRESS_LABEL,
    REQUIRED_LABEL,
    GitHubError,
    add_label,
    remove_label,
)
from .lease import Lease, LeaseConflict, LeaseStore
from .queue import QueueItem, build_queue
from .store import Store
from .workflow_version import WorkflowVersionError

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 5.0

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


def choose_next(
    queue: Sequence[QueueItem],
    leases: Sequence[Lease],
    free_pools: Sequence[str],
    last_pool_for_repo: Callable[[str], str | None],
) -> tuple[QueueItem, str] | None:
    """The top-ranked item a free box may take, paired with the box to use.

    `queue` is already in dispatch order — this never re-ranks, so there remains
    exactly one definition of "next" in the system (`queue.rank`). `None` means
    "do not dispatch": no free box, or every item's repo already has a run in
    flight.

    **Soft affinity** (decision 11) is the last tiebreak and only a tiebreak: among
    the free boxes, the one this repo last ran on wins; if it is busy, any free box
    will do and the loop never waits for the preferred one. Non-determinism is the
    thing being avoided — with no affinity the choice would be `free_pools[0]`,
    which is right but means two repos alternately jumping between boxes and
    re-warming neither.
    """
    if not free_pools:
        return None

    busy_repos = {lease.repo for lease in leases}
    for item in queue:
        if item.issue.repo in busy_repos:
            continue
        preferred = last_pool_for_repo(item.issue.repo)
        if preferred is not None and preferred in free_pools:
            return item, preferred
        # `free_pools` is in `coder_pools` order, so this is deterministic.
        return item, free_pools[0]
    return None


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
    ) -> None:
        self._config = config
        self._store = store
        self._leases = leases
        self._fabro = fabro
        self._github_token = github_token
        self._pools = tuple(config.coder_pools)
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
        """
        now = datetime.now(UTC)
        ranked = build_queue(
            self._config.schedulable_repos(),
            self._store,
            now,
            self._config.starvation_ceiling,
        )

        attempts: list[DispatchAttempt] = []
        backed_off: set[str] = set()
        while True:
            free = self._leases.free_pools(self._pools, self._drained_pools())
            candidates = [
                item for item in ranked if item.issue.repo not in backed_off
            ]
            chosen = choose_next(
                candidates,
                self._leases.active(),
                free,
                self._leases.last_pool_for_repo,
            )
            if chosen is None:
                return attempts

            item, pool = chosen
            attempt = self._dispatch(item, pool)
            attempts.append(attempt)
            if not attempt.dispatched:
                backed_off.add(item.issue.repo)

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
            dispatched_at=datetime.now(UTC),
            # The item's wait travels with the lease, because the poll deletes its
            # cache row within a minute of this label write and draft 09's requeue
            # needs the original wait to put back.
            queued_since=item.issue.first_seen,
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
            "dispatch: %s#%s -> run %s on %s (env=%s)",
            repo,
            number,
            result.run_id,
            coder_pool,
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
