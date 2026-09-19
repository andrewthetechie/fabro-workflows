"""Release, requeue and recovery — draft 09.

Closes the loop draft 08 opened. Three mechanisms, in overview decision 10 /
ADR 0006's vocabulary:

**Release** — a coder instance is leased for one whole run. When the run reaches a
terminal state (`succeeded | failed | dead`, finding 9) the lease drops and the box
is free again. Draining, cancelling and reordering are draft 11; the exclusion from
`free_pools` lives in `lease.py`.

**Requeue** — infra-shaped failures go back in the queue; agent-shaped ones do not.
The issue is un-labelled (`agent-in-progress` off, `agent` on) and restored to the
cache with its **original `first_seen`** so its starvation ceiling is not reset.
Two failures requeue that are not `transient_infra`, and both are *reason* checks
rather than category checks:

* the fabro restart, which ends every in-flight run `failed/terminated` with
  category `deterministic` (finding 10) — reading only `transient_infra` would
  requeue nothing on the exact bounce the design exists to survive; and
* an **operator cancel**, which arrives `failed/cancelled` with category
  `canceled` (one L, and only in the events tail). Decision 15 makes a cancel
  requeue the issue, and it has to: a cancel exits before the graph's terminal
  label work, so without this the issue keeps `agent-in-progress`, leaves the
  cache, and is invisible to `acquire` and to the queue — work lost silently.

Selections: `mark_stuck` and `close_noop` are agent-shaped and are *not* requeued.

**Recovery** — on startup, reconcile every lease against fabro (drop the ones whose
run is terminal or is gone), then scan GitHub for receipts (`agent-in-progress`)
that neither a lease nor a live fabro run accounts for, and requeue those. Recovery
runs **before** the dispatch loop, or it would dispatch onto a box it had not yet
reconciled.

The receipt scan also runs **periodically** (`DEFAULT_RECEIPT_SCAN_SECONDS`), which
draft 09 did not do. Its reasoning was that only a restart could orphan a receipt;
2026-09-19 showed otherwise — `mark_stuck` read the issue number only from
`issue.json` and did no label work at all when `claim` had died before writing it,
so two issues kept `agent-in-progress` and left the queue with the scheduler up and
healthy. The graph now writes `/tmp/fabro/issue_number` before any network call and
`mark_stuck` falls back to it, which closes that path; the periodic scan closes the
class, for every future ending that skips the graph's label work. Running it while
the loop dispatches needs the confirmation gate described on `_github_pass`.

Draft 10 (which collapsed `acquire`/`claim`) landed before this on purpose: it is
what makes the lease's `issue_number` the issue the run actually works, so a
GitHub-pass that keys on `lease.issue_number` can no longer un-label the issue a
run is implementing. See `docs/scheduler/09-release-requeue-recovery.md`,
*Corrected during draft 08's acceptance run*.

What draft 10 did **not** close is the other half of the same premise. "A receipt
with no lease is an orphan" assumes every live run holds a lease, and draft 10's own
`ops/fabro-fire-backlog.sh` breaks that on purpose — a hand fire takes no lease, and
it exists for when the scheduler is down, which is the state the scheduler recovers
*from*. So the receipt scan asks fabro which issues its live runs are working
(`FabroClient.active_issue_claims`) and treats those as accounted for too.

Every release and requeue is logged with run id, repo and issue so the deployment
log can reconstruct what happened.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import SchedulerConfig
from .fabro import FabroClient, FabroError, status_kind
from .github import (
    IN_PROGRESS_LABEL,
    REQUIRED_LABEL,
    GitHubError,
    add_label,
    fetch_in_progress,
    remove_label,
)
from .lease import Lease, LeaseStore
from .store import Store

log = logging.getLogger(__name__)

# The terminal status set (overview finding 9). There is no `cancelled` and no
# `errored` kind: a cancel arrives as `failed` with `reason: "cancelled"`, and
# `dead` has to be here or a dead run holds its lease forever.
TERMINAL = frozenset({"succeeded", "failed", "dead"})

# The requeue predicate's category (overview decision 10).
INFRA_CATEGORY = "transient_infra"

# The one infra failure that does *not* carry it. A fabro restart or a worker exit
# ends every in-flight run as `failed` with `reason: "terminated"` and `category:
# "deterministic"` (finding 10). Requeueing on category alone would requeue nothing
# on the bounce the whole design exists to survive, so the reason is checked
# alongside the category. Selections: `mark_stuck` and `close_noop` are agent-shaped
# and are *not* requeued.
TERMINATED_REASON = "terminated"

# The operator's cancel (draft 11), and a second reason that has to be checked
# because the category does not appear at all until the events tail is read. A
# cancel is `failed` with `reason: "cancelled"` and the events category `canceled`,
# one L. Decision 15 makes it requeue: a cancel exits before the graph's terminal
# label work, so the issue would otherwise keep `agent-in-progress` and be invisible
# to `acquire` and to the queue (finding 10). Reading the reason also saves the
# events call — it is in the projection, the category is not.
CANCELLED_REASON = "cancelled"

# Reasons that requeue whatever the category says. Both are read out of the run
# projection, so neither costs the extra `GET /runs/{id}/events`.
REQUEUE_REASONS = frozenset({TERMINATED_REASON, CANCELLED_REASON})

# Decision 8 / the Release contract: poll fabro this often for terminal runs.
DEFAULT_RELEASE_INTERVAL_SECONDS = 15.0

# How often the receipt scan runs *after* startup. Four uncached GitHub requests
# per scan (one `agent-in-progress` collection per repo), so at 600s that is 24
# requests an hour against the 5,000/hour budget — the poll that matters, the 60s
# inventory one, is conditional and free.
#
# It exists because draft 09's original "a question only a restart can raise" is
# false. A receipt is orphaned by any ending that does not reach the graph's own
# label work, and `mark_stuck` skipped that work entirely whenever `claim` died
# before writing `issue.json` — which stranded `jelly-swipe#353` and
# `lawncare-saas#2277` on 2026-09-19 with no scheduler crash involved. The graph
# fix closes that path; this closes the class.
DEFAULT_RECEIPT_SCAN_SECONDS = 600.0


# --- the two predicates ---------------------------------------------------------


def is_terminal(run: Mapping[str, object]) -> bool:
    """Whether the run projection has reached a terminal status kind."""
    return status_kind(run) in TERMINAL


def should_requeue(run: Mapping[str, object]) -> bool:
    """True only for a failure that must go back in the queue.

    Reads the failure `category` (from `_last_failure`, which the reconcile pass
    populates from the events tail when the projection does not carry it) **and**
    the projection's `reason`, for the two failures whose category is either a lie
    (`terminated`, the fabro restart) or absent (`cancelled`, the operator's
    cancel — decision 15). A missing category is NOT infra-shaped: that default
    fails toward dropping work rather than looping a broken issue forever, which
    is the direction a human notices (Risk 4).
    """
    lifecycle = run.get("lifecycle")
    if isinstance(lifecycle, Mapping):
        status = lifecycle.get("status")
        if isinstance(status, Mapping) and status.get("reason") in REQUEUE_REASONS:
            return True

    failure = run.get("_last_failure")
    return bool(
        isinstance(failure, Mapping) and failure.get("category") == INFRA_CATEGORY
    )


def _classify(run: Mapping[str, object]) -> str:
    """A one-line reason for the log: what a terminal run was and why it is gone."""
    lifecycle = run.get("lifecycle")
    status = lifecycle.get("status") if isinstance(lifecycle, Mapping) else None
    if isinstance(status, Mapping):
        kind = status.get("kind")
        reason = status.get("reason")
        if reason:
            return f"{kind}/{reason}"
        if kind:
            return str(kind)
    return "unknown"


# --- the fabro pass -------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseAction:
    """What one lease became after a reconcile pass."""

    lease: Lease
    # "adopted" (still running, lease kept) | "released" | "released+requeued" |
    # "failed" (could not decide, lease kept)
    outcome: str
    reason: str


def reconcile_leases(
    config: SchedulerConfig,
    store: Store,
    leases: LeaseStore,
    fabro_client: FabroClient,
    github_token: str,
) -> list[ReleaseAction]:
    """The fabro pass: for every lease, decide on its run's state.

    A lease whose run is terminal, or which fabro no longer has (404 — "fabro
    lost it"), is released; terminal failures that are infra-shaped are requeued
    too. A run fabro cannot currently answer for (a transient error, not a 404) is
    left on its lease — this pass must not wrongly requeue, and the next poll
    retries. The issue keeps its lease until the run is provably over.

    Runs to completion in one call, so it is used both as recovery's first pass
    and as the body of the 15-second release poll.
    """
    actions: list[ReleaseAction] = []
    for lease in leases.active():
        try:
            run = fabro_client.get_run(lease.run_id)
        except FabroError as exc:
            if exc.status_code == 404:
                _requeue(store, leases, lease, github_token)
                actions.append(
                    ReleaseAction(
                        lease, "released+requeued", "fabro has no such run (404)"
                    )
                )
                log.info(
                    "reconcile: %s#%s run %s lost by fabro; requeued",
                    lease.repo, lease.issue_number, lease.run_id,
                )
            else:
                log.error(
                    "reconcile: %s#%s run %s: fabro error, keeping lease: %s",
                    lease.repo, lease.issue_number, lease.run_id, exc,
                )
                actions.append(ReleaseAction(lease, "failed", str(exc)))
            continue

        if not is_terminal(run):
            actions.append(
                ReleaseAction(
                    lease, "adopted", f"run {status_kind(run) or 'unknown'}, not terminal"
                )
            )
            continue

        # Terminal. Decide requeue, fetching the failure category from the events
        # tail only when the projection does not already decide (`terminated`).
        # Inside the try because that is a second call and it can fail on its own:
        # letting it escape would abandon every lease after this one on the tick,
        # and at startup it would skip the GitHub pass for the whole boot.
        try:
            classified = _with_category(run, lease, fabro_client)
        except FabroError as exc:
            log.error(
                "reconcile: %s#%s run %s: could not read the failure category, "
                "keeping lease: %s",
                lease.repo, lease.issue_number, lease.run_id, exc,
            )
            actions.append(ReleaseAction(lease, "failed", str(exc)))
            continue

        if should_requeue(classified):
            _requeue(store, leases, lease, github_token)
            actions.append(
                ReleaseAction(lease, "released+requeued", _classify(run))
            )
            log.info(
                "reconcile: %s#%s run %s terminal (%s); requeued",
                lease.repo, lease.issue_number, lease.run_id, _classify(run),
            )
        else:
            # The cache row goes with the box. Dispatch removed `agent` from this
            # issue, so the row has been stale since then; while the lease was
            # held that was harmless (one in-flight run per repo kept the repo out
            # of `choose_next`), and at this instant it is the whole bug — the
            # 5-second dispatch tick would start a second run off it, up to a
            # minute before the inventory poll cleared it. An agent-shaped ending
            # must not come back, and this is what makes that true rather than
            # merely likely.
            store.forget_issue(lease.repo, lease.issue_number)
            leases.release(lease.run_id)
            actions.append(ReleaseAction(lease, "released", _classify(run)))
            log.info(
                "reconcile: %s#%s run %s terminal (%s); released, not requeued",
                lease.repo, lease.issue_number, lease.run_id, _classify(run),
            )
    return actions


def _with_category(
    run: dict, lease: Lease, fabro_client: FabroClient
) -> dict:
    """Attach `_last_failure.category` to a failed run when its reason does not
    already decide the requeue (i.e. it is neither `terminated` nor `cancelled`)."""
    status = run.get("lifecycle", {}).get("status", {})
    if not isinstance(status, Mapping):
        status = {}
    if status.get("reason") in REQUEUE_REASONS:
        return run
    if status_kind(run) == "failed":
        category = fabro_client.last_failure_category(lease.run_id)
        if category:
            run["_last_failure"] = {"category": category}
    return run


def _requeue(
    store: Store,
    leases: LeaseStore,
    lease: Lease,
    github_token: str,
) -> None:
    """Requeue = remove the receipt, restore the queue label, restore the item with
    its original wait, then free the box.

    The label writes are best-effort on purpose: a GitHub hiccup must not block the
    rest of the pass, and if it leaves `agent-in-progress` on the issue the next
    start's GitHub pass repairs it. The one ordering that matters is that the issue
    is back in the cache *before* the box is freed, so the dispatch loop cannot see
    a free box with no eligible work on it for a tick.
    """
    for label, action in (
        (IN_PROGRESS_LABEL, _remove_label),
        (REQUIRED_LABEL, _add_label),
    ):
        try:
            action(lease.repo, lease.issue_number, label, github_token)
        except GitHubError as exc:
            log.error(
                "reconcile: requeue %s#%s: could not %s %s: %s",
                lease.repo, lease.issue_number, action.__name__, label, exc,
            )
    store.requeue(lease.repo, lease.issue_number, first_seen=lease.queued_since)
    leases.release(lease.run_id)


def _remove_label(repo: str, number: int, label: str, token: str) -> None:
    remove_label(repo, number, label, token)


def _add_label(repo: str, number: int, label: str, token: str) -> None:
    add_label(repo, number, label, token)


# --- the GitHub pass ------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryReport:
    """What one startup recovery did, for logging and for tests."""

    fabro_pass: list[ReleaseAction]
    orphaned: list[tuple[str, int]]  # (repo, issue) requeued by the GitHub pass
    github_errors: list[str]


def recover(
    config: SchedulerConfig,
    store: Store,
    leases: LeaseStore,
    fabro_client: FabroClient,
    github_token: str,
) -> RecoveryReport:
    """Run the fabro pass then the GitHub pass to completion — startup recovery.

    Called once, before the dispatch loop starts. Order matters: the fabro pass
    may itself requeue and release, so the GitHub pass reads the *post-fabro*
    lease set when deciding what is orphaned.
    """
    fabro_pass = reconcile_leases(
        config, store, leases, fabro_client, github_token
    )
    # `pending=None`: requeue an orphan the moment it is seen. Safe here and only
    # here, because `recover` runs before the dispatch loop starts, so there is no
    # dispatch in flight for this pass to mistake for an orphan. The periodic scan
    # has no such guarantee and gates on confirmation instead.
    return _github_pass(config, store, leases, fabro_client, github_token, fabro_pass)


def _github_pass(
    config: SchedulerConfig,
    store: Store,
    leases: LeaseStore,
    fabro_client: FabroClient,
    github_token: str,
    fabro_pass: list[ReleaseAction],
    *,
    pending: set[tuple[str, int]] | None = None,
) -> RecoveryReport:
    """The receipt scan: issues wearing `agent-in-progress` that nothing is working.

    The scheduler died between labelling and recording the lease (or a cancel left
    the receipt behind), so the issue is invisible to `acquire` and to this queue.
    Anything nothing is working is un-labelled and requeued.

    **Two things count as "being worked", not one.** A live lease is the first, and
    with draft 10 landed `lease.issue_number` *is* the issue the run works, so
    matching on it can no longer touch a run's own claim. The second is a live
    fabro run that holds no lease, which is not a hypothetical: draft 10's
    `ops/fabro-fire-backlog.sh` fires an issue by hand and deliberately takes no
    lease ("a hand fire must not pretend to hold a lease it does not"), for use
    exactly when the scheduler is down — and the scheduler starting back up is when
    this pass runs. On the lease test alone it would strip that run's receipt,
    requeue its issue and let the loop dispatch a second run onto work already in
    progress: draft 09's own correction, with the manual path standing in for
    `acquire`.

    **A fabro that cannot answer stops the pass**, rather than letting it proceed
    on an empty claim set — which is indistinguishable from "nothing is live" and
    un-labels everything. An orphan left one more restart is cheap; two runs on one
    issue is the failure this pass exists to prevent.

    **`pending` is what makes this safe to run while the loop is dispatching.**
    At startup it is `None` and an orphan is repaired on sight. On the periodic
    scan it is a set carried between scans, and a receipt has to look orphaned in
    **two consecutive scans** before anything is written — because `_dispatch`
    writes the receipt several seconds before the run exists (it clones `main` in
    between), and a scan landing in that gap sees a receipt with no lease and no
    live run. Confirming across a scan interval closes a window measured in
    seconds with one measured in minutes, and costs an orphan one extra scan.
    """
    live = {(lease.repo, lease.issue_number) for lease in leases.active()}
    errors: list[str] = []
    orphaned: list[tuple[str, int]] = []
    # Receipts that looked orphaned this pass, and so are eligible next pass.
    # Anything requeued is deliberately absent: it is repaired, not pending.
    candidates: set[tuple[str, int]] = set()

    try:
        live |= fabro_client.active_issue_claims()
    except FabroError as exc:
        log.error(
            "recover: could not list live runs, so no receipt can be shown to be "
            "orphaned; leaving every label alone: %s",
            exc,
        )
        return RecoveryReport(
            fabro_pass=fabro_pass, orphaned=[], github_errors=[f"fabro: {exc}"]
        )

    for repo in config.schedulable_repos():
        try:
            numbers = fetch_in_progress(repo.name, github_token)
        except GitHubError as exc:
            log.error("recover: %s: could not list agent-in-progress: %s", repo.name, exc)
            errors.append(f"{repo.name}: {exc}")
            continue
        for number in numbers:
            key = (repo.name, number)
            if key in live:
                continue
            if pending is not None and key not in pending:
                candidates.add(key)
                log.info(
                    "recover: %s#%s has a receipt with no lease; confirming on the "
                    "next scan before touching it",
                    repo.name, number,
                )
                continue
            _requeue(store, leases, _orphan_lease(repo.name, number), github_token)
            orphaned.append(key)
            log.info(
                "recover: %s#%s had the receipt with no lease; requeued",
                repo.name, number,
            )

    if pending is not None:
        # Rebuilt rather than unioned: a receipt that stopped looking orphaned —
        # its run started, or a human re-labelled it — must not stay armed. A repo
        # whose fetch failed contributes nothing, so its candidates lapse and need
        # two more scans, which is the direction that does not write on a guess.
        pending.clear()
        pending.update(candidates)

    return RecoveryReport(fabro_pass=fabro_pass, orphaned=orphaned, github_errors=errors)


def _orphan_lease(repo: str, number: int) -> Lease:
    """A synthetic lease for a receipt with no row behind it.

    Enough for `_requeue` to restore the cache row and free nothing (no run_id,
    so `leases.release` is a no-op). `queued_since=None` falls back to now, which
    is correct: there is no lease to read the original wait from.
    """
    return Lease(
        coder_pool="", repo=repo, issue_number=number, run_id="", dispatched_at=datetime.now(UTC)
    )


# --- the 15-second release poll -------------------------------------------------


class ReleasePoller:
    """The 15-second loop that turns terminal fabro runs into free boxes.

    One thread, exactly like `InventoryPoller`: `tick()` is pure and testable, and
    `start`/`stop` are the only things that touch a thread.

    Two cadences on the one thread, because they answer different questions at
    very different prices. `tick()` is the fabro pass every 15 seconds — one cheap
    `GET /runs/{id}` per held lease, at most two. `receipt_tick()` is the GitHub
    receipt scan every `receipt_scan_seconds`, four uncached requests that catch an
    `agent-in-progress` label no run is behind. Draft 09 ran the second only at
    startup on the premise that only a restart could orphan a receipt; the
    2026-09-19 `mark_stuck` stranding disproved that, so it runs here too — gated
    on confirmation across two scans so it can never race a dispatch.
    """

    def __init__(
        self,
        config: SchedulerConfig,
        store: Store,
        leases: LeaseStore,
        fabro_client: FabroClient,
        github_token: str,
        *,
        interval_seconds: float = DEFAULT_RELEASE_INTERVAL_SECONDS,
        receipt_scan_seconds: float = DEFAULT_RECEIPT_SCAN_SECONDS,
    ) -> None:
        self._config = config
        self._store = store
        self._leases = leases
        self._fabro = fabro_client
        self._github_token = github_token
        self._interval = interval_seconds
        self._receipt_interval = receipt_scan_seconds
        # Carried between receipt scans; see `_github_pass`. Starts empty because
        # `recover` has just repaired everything orphaned at boot, so the first
        # periodic scan has nothing legitimately outstanding to confirm.
        self._pending_orphans: set[tuple[str, int]] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> list[ReleaseAction]:
        """One fabro pass, by hand — the testing seam and the loop body."""
        return reconcile_leases(
            self._config,
            self._store,
            self._leases,
            self._fabro,
            self._github_token,
        )

    def receipt_tick(self) -> RecoveryReport:
        """One confirmation-gated receipt scan, by hand.

        The same pass `recover` runs, minus the fabro leg and plus the two-scan
        confirmation. Separate from `tick()` rather than folded into it on a
        counter, so a test drives each cadence directly instead of faking a clock.
        """
        return _github_pass(
            self._config,
            self._store,
            self._leases,
            self._fabro,
            self._github_token,
            [],
            pending=self._pending_orphans,
        )

    def start(self) -> None:
        """Start releasing. Idempotent."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="fabro-scheduler-release", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop ticking. Safe to call twice."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None

    def _run(self) -> None:
        """Tick immediately, then every `interval`, forever until stopped.

        The receipt scan rides the same thread on its own, much longer schedule,
        anchored on completion so a slow GitHub cannot queue up a backlog of them.
        It is deliberately not run on the first pass: `recover` has just done that
        work synchronously, and repeating it immediately would spend four requests
        to re-answer a question with a fresh answer.

        A tick that raises is logged and the loop continues: an unhandled bug must
        not silently stop all releasing while `/health` keeps answering `ok`, the
        same rule the dispatch and inventory loops follow. The two cadences are
        caught separately, so a broken receipt scan cannot stop leases releasing.
        """
        next_receipt = time.monotonic() + self._receipt_interval
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - see the docstring above
                log.exception("release: tick failed")

            if time.monotonic() >= next_receipt:
                try:
                    self.receipt_tick()
                except Exception:  # noqa: BLE001 - as above, and independently
                    log.exception("release: receipt scan failed")
                next_receipt = time.monotonic() + self._receipt_interval

            if self._stop.wait(self._interval):
                return
