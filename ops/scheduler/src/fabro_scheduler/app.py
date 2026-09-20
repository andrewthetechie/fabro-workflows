"""The scheduler's HTTP surface.

The read surfaces are the operator's page (`GET /`), its JSON twins
(`GET /api/queue`, `GET /api/repos`, `GET /api/pools`) and `GET /health`. The write
surfaces are `POST /api/dispatch-once` — the operator's manual override, which
creates and starts exactly one real fabro run — and draft 11's three controls:
bump an issue, drain or undrain a coder instance, and cancel the run on one.

**Drain and cancel are separate routes on purpose.** Draining stops *new* dispatch
to a box and lets its current run finish; cancelling is a second, explicitly-labelled
button that ends that run. Collapsing them would make the word "drain" destroy work
(CONTEXT.md; overview decision 15). Cancel does not release the lease either — it
asks fabro to stop the run and lets draft 09's one release path notice.

**There is no auth, by decision 16**, and the compensating control is that every
mutation is logged with what changed: there is no identity to attribute it to, so
the log is the whole audit trail. Every mutating route is `POST`, so a link preload
cannot trigger one. Nothing here echoes the environment, so the `GITHUB_TOKEN` and
`FABRO_API_TOKEN` the container holds stay out of every response — including
`/health`, which reports only whether each is *configured*, never its value.
`fabro_api_url` is deliberately absent from the payload as well — it is a URL that
could carry a credential in some future form, and it is not worth the risk to
publish it on an unauthenticated endpoint.

The other writer is not a request at all: draft 08's `DispatchLoop` runs on its own
thread, and while it is running this service writes `agent-in-progress` to GitHub
and creates runs without anyone asking. It is off unless `main` turns it on, which
`start_dispatch_loop` is there to make explicit — a test builds the same app with
no thread at all, and an unarmed process still serves the whole read surface.

**Neither missing credential stops the service.** A missing `GITHUB_TOKEN` logs
one error, serves the page with a banner naming the variable, and reports
`github.configured: false` on `/health` for draft 12's monitor to alert on; a
missing `FABRO_API_TOKEN` does the same, makes `/api/dispatch-once` answer `503`,
and keeps the dispatch loop from starting. Exiting instead would fail the
container's healthcheck in a restart loop that says nothing about which variable is
missing, and the page is the surface where that is visible at a glance.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from . import __version__
from .config import ConfigError, SchedulerConfig, load_config
from .dispatch import DispatchLoop
from .docker import DockerClient
from .fabro import FabroClient, FabroError, RunNotStarted
from .inventory import InventoryPoller
from .lease import Lease, LeaseConflict, LeaseStore
from .probe import RunProbe
from .queue import QueueItem, RepoStatus, build_queue, repo_statuses
from .reconcile import ReleasePoller, recover
from .store import DEFAULT_HISTORY_LIMIT, DEFAULT_HISTORY_SORT, Store
from .workflow_version import WorkflowVersionError

log = logging.getLogger(__name__)

# Baked into the image at this path by the Dockerfile and overridable with
# `--config` or `SCHEDULER_CONFIG`, so the container needs no bind mount. A
# compose bind mount of a single file silently creates a *directory* when the
# path is missing, which is a failure mode worth not having.
DEFAULT_CONFIG_PATH = Path("/app/repos.toml")

TEMPLATES_DIR = Path(__file__).parent / "templates"


class DispatchOnceRequest(BaseModel):
    """The body of `POST /api/dispatch-once`.

    `extra="forbid"` because this is a hand-typed operator call: a misspelled key
    that is silently ignored would dispatch the wrong thing, and the operator
    would have no way to tell from the response.
    """

    model_config = ConfigDict(extra="forbid")

    repo: str
    # An issue number is a positive integer, and it has to arrive as one: the
    # graph guards it with a POSIX `case`, so it must reach fabro as a JSON
    # number rather than a string. `strict=True` because pydantic would otherwise
    # coerce `true` to 1 and `"123"` to 123 — and `true` becoming "dispatch issue
    # #1" is the same silent-mis-dispatch trap AGENTS.md records for `pr_number`.
    issue_number: int = Field(gt=0, strict=True)
    coder_pool: str


@dataclass(frozen=True)
class PoolState:
    """One coder instance's current state, for `GET /api/pools` and the page.

    `drained` and `lease` are independent on purpose. A drained box that is still
    running its last run is the normal mid-drain state (overview decision 15), so
    the page has to be able to say "drained, still busy" without either field
    implying the other.
    """

    coder_pool: str
    drained: bool
    lease: Lease | None


def build_app(
    config: SchedulerConfig,
    store: Store,
    *,
    github_token: str = "",
    fabro_token: str = "",
    fabro_client: FabroClient | None = None,
    run_probe: RunProbe | None = None,
    start_poller: bool = False,
    start_dispatch_loop: bool = False,
) -> FastAPI:
    """The ASGI app for an already-validated config and an open store.

    `start_poller` and `start_dispatch_loop` are both off by default so a test can
    drive the inventory and the dispatch decisions by hand instead of racing two
    background threads. `main` turns both on.

    `fabro_client` is injected rather than built here, which is what lets a test
    exercise the dispatch path through the HTTP surface with neither a git binary
    nor a live fabro. When it is not supplied, a client is built from
    `fabro_token`; when that is empty too, there is no client and
    `/api/dispatch-once` answers `503`.
    """
    token_configured = bool(github_token.strip())
    client = fabro_client
    if client is None and fabro_token.strip():
        client = FabroClient(config.fabro_api_url, fabro_token)

    # One lease table for the whole process: the dispatch loop writes it and the
    # manual override reads it, which is the only way the override can avoid
    # putting a second run on a box the loop already took.
    leases = LeaseStore(store)

    # The run probe: a background refresher of per-lease task count and current
    # stage. Created whenever it is not injected, so the page always has a
    # `run_progress` view to render (it just shows "—" until a beat fills it). The
    # thread only starts when there is a fabro client to fetch stages from.
    probe = run_probe
    if probe is None:
        probe = RunProbe(
            client,
            leases,
            DockerClient(),
            interval_seconds=config.run_probe_seconds,
        )

    poller: InventoryPoller | None = None
    if start_poller:
        if token_configured:
            poller = InventoryPoller(
                config.schedulable_repos(),
                store,
                github_token,
                interval_seconds=config.github_poll_seconds,
            )
        else:
            log.error(
                "GITHUB_TOKEN is not set: the GitHub inventory will not be polled. "
                "The queue page will show only what the cache already holds."
            )

    dispatcher: DispatchLoop | None = None
    releaser: ReleasePoller | None = None
    if start_dispatch_loop:
        # Both credentials are required and neither is optional: the loop's first
        # act is a label write, and without a fabro token there is no run to
        # dispatch onto. Starting it half-armed would retry a doomed dispatch every
        # 5 seconds forever, which is worse than not starting it and saying so.
        if client is None:
            log.error(
                "FABRO_API_TOKEN is not set: the dispatch loop will not start. "
                "Nothing will be dispatched automatically."
            )
        elif not token_configured:
            log.error(
                "GITHUB_TOKEN is not set: the dispatch loop will not start, because "
                "a dispatch whose label write fails leaves nothing behind. Nothing "
                "will be dispatched automatically."
            )
        else:
            dispatcher = DispatchLoop(config, store, leases, client, github_token)
            # Draft 09. The same credentials that arm dispatch arm recovery: it
            # reads fabro and writes the same GitHub labels.
            releaser = ReleasePoller(config, store, leases, client, github_token)

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["duration"] = humanise_duration
    # A global rather than a filter: it takes the page's whole sort state, not a
    # value being formatted.
    templates.env.globals["sort_link"] = _sort_link
    templates.env.globals["final_state"] = final_state_label

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # The poller and the dispatch loop both run on their own threads; the
        # poller starts first so the loop reads a fresh queue. Draft 09's recovery
        # runs **before either**, synchronously, because a loop that dispatches
        # onto a box it has not yet reconciled is the failure this whole pass
        # exists to prevent. Recovery is best-effort, but only half of it is
        # retried: the 15-second poll re-runs the fabro pass every tick, while the
        # receipt scan is startup-only, so a throw here costs this boot its orphan
        # repair and the next restart is what fixes it. Logged as an exception for
        # exactly that reason.
        if releaser is not None:
            try:
                recover(config, store, leases, client, github_token)
            except Exception:  # noqa: BLE001 - startup must not fail on a blip
                log.exception("recovery failed; the release poll will retry")
        if poller is not None:
            poller.start()
        if releaser is not None:
            releaser.start()
        if dispatcher is not None:
            dispatcher.start()
        # The probe populates the page's task/stage columns. It runs whenever the
        # service has a fabro client — a read-only observer, independent of the
        # dispatch loop, so a page is live even when automatic dispatch is off.
        if probe is not None and client is not None:
            probe.start()
        try:
            yield
        finally:
            if probe is not None and client is not None:
                probe.stop()
            if dispatcher is not None:
                dispatcher.stop()
            if releaser is not None:
                releaser.stop()
            if poller is not None:
                poller.stop()

    app = FastAPI(title="fabro coder scheduler", version=__version__, lifespan=lifespan)

    def current_queue() -> tuple[list[QueueItem], datetime]:
        # Assembled per request rather than cached: it is four indexed SELECTs and
        # a sort over a handful of rows, and a cached copy would be one more thing
        # that can be stale in a way the page does not show.
        now = datetime.now(UTC)
        items = build_queue(
            config.schedulable_repos(), store, now, config.starvation_ceiling
        )
        return items, now

    def pool_states() -> list[PoolState]:
        """Every configured coder instance, its drain flag and its lease.

        One definition for both `GET /api/pools` and the page, so the two can never
        disagree about whether a box is drained. Built per request for the same
        reason the queue is: it is two reads over at most four rows, and "the page
        says drained but the API says not" is exactly the kind of staleness that
        costs an ssh session.
        """
        drained = store.drained_pools()
        held = {lease.coder_pool: lease for lease in leases.active()}
        return [
            PoolState(
                coder_pool=pool,
                drained=pool in drained,
                lease=held.get(pool),
            )
            for pool in config.coder_pools
        ]

    def require_pool(coder_pool: str) -> None:
        """Refuse an unknown coder instance before it can be written to `pool_state`.

        A 404 rather than creating a row: a `pool_state` row for a name that is not
        in `repos.toml` would never be read (the dispatch loop only walks the
        configured pools), so accepting it would report a drain that does nothing.
        """
        if coder_pool not in config.coder_pools:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{coder_pool} is not a coder instance; expected one of "
                    f"{', '.join(config.coder_pools)}"
                ),
            )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "repos": [
                {
                    "name": repo.name,
                    "priority": repo.priority,
                    "environment_id": repo.environment_id,
                    "enabled": repo.enabled,
                }
                for repo in config.ordered_repos()
            ],
            "coder_pools": list(config.coder_pools),
            "github": {
                "configured": token_configured,
                "poll_seconds": config.github_poll_seconds,
            },
            # A boolean, never the value. Draft 12's monitor alerts on a scheduler
            # that cannot dispatch; the operator reading this page wants to know
            # whether the dispatch path is armed without triggering a dispatch.
            "fabro": {"configured": client is not None},
            "starvation_ceiling_seconds": int(
                config.starvation_ceiling.total_seconds()
            ),
        }

    @app.get("/api/queue")
    def api_queue() -> list[dict[str, object]]:
        """The ranked queue, as JSON, in dispatch order.

        A bare list, not an envelope: the ordering *is* the content, and every
        consumer of this — the page, draft 11's bump control, whatever the
        operator curls — wants it in exactly this order.
        """
        items, _ = current_queue()
        return [_queue_item_payload(item) for item in items]

    @app.get("/api/repos")
    def api_repos() -> list[dict[str, object]]:
        """Per-repo inventory state: freshness, ETag, cached item count.

        Not part of the plan's named contract, and small enough to justify itself:
        without it, "this repo is stale" or "the ETag stopped changing" is only
        visible in the logs, which means an ssh session to answer a question the
        service already knows.
        """
        return [_repo_status_payload(status) for status in repo_statuses(
            config.schedulable_repos(), store
        )]

    @app.get("/api/pools")
    def api_pools() -> list[dict[str, object]]:
        """Every coder instance, with its drain flag and its current lease or null.

        Draft 12's wedged-scheduler condition reads exactly this: a non-empty queue
        with every pool idle and none drained is the wedge it alerts on. That is why
        every configured pool appears whether or not it is leased — an absent row
        and an idle box would otherwise be the same answer, and only one of them is
        a problem.
        """
        return [_pool_payload(state) for state in pool_states()]

    @app.get("/api/history")
    def api_history(
        sort: str | None = None,
        dir: str | None = None,
        limit: str | None = None,
    ) -> list[dict[str, object]]:
        """Released runs, newest finished first. The JSON twin of `/history`.

        `limit` is a string rather than an int because `all` is a legal value;
        `_history_query` is what turns it into `None`.
        """
        column, descending, capped = _history_query(sort, dir, limit)
        return [
            _history_payload(row)
            for row in store.history_rows(
                sort=column, descending=descending, limit=capped
            )
        ]

    @app.post("/api/dispatch-once")
    def dispatch_once(body: DispatchOnceRequest) -> dict[str, object]:
        """Create and start one `backlog` run, by hand.

        The operator's override, which draft 07 built to prove the client and draft
        08 keeps. It takes `repo`, `issue_number` and `coder_pool` explicitly and
        does exactly one dispatch; the loop does the rest.

        **It takes the lease, and refuses a box that is already leased.** Without
        that, the one write path the loop does not own would be the one way to put
        two runs on a single-slot coder instance — which is the contention the whole
        service exists to remove. It does **not** write labels: the issue a human
        names by hand need not be in the `agent` queue at all, and a rollback would
        have to decide what to restore.

        `environment_id` is **not** a parameter: it comes from the repo's row in
        `repos.toml`, which is the only place it is decided. A wrong one is a
        server-side error the operator gets to read — fabro answers `422`
        `unknown environment id 'nope'`, and that sentence is returned verbatim.
        """
        if client is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "FABRO_API_TOKEN is not set; the scheduler cannot create runs. "
                    "See `ops/README.md`, *The coder scheduler*."
                ),
            )

        repo = config.repo_named(body.repo)
        if repo is None:
            raise HTTPException(
                status_code=404,
                detail=f"{body.repo} is not a [[repo]] in repos.toml",
            )
        if not repo.enabled:
            raise HTTPException(
                status_code=409,
                detail=f"{repo.name} is disabled in repos.toml",
            )
        if body.coder_pool not in config.coder_pools:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"coder_pool must be one of {', '.join(config.coder_pools)}; "
                    f"got {body.coder_pool!r}"
                ),
            )

        held = next(
            (
                lease
                for lease in leases.active()
                if lease.coder_pool == body.coder_pool
            ),
            None,
        )
        if held is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{body.coder_pool} is already leased by {held.repo}"
                    f"#{held.issue_number} (run {held.run_id})"
                ),
            )

        try:
            result = client.dispatch(
                repo=repo.name,
                issue_number=body.issue_number,
                coder_pool=body.coder_pool,
                environment_id=repo.environment_id,
            )
        except RunNotStarted as exc:
            # 502, not fabro's own status: the caller asked the scheduler for a
            # run, and it is the scheduler that failed to finish making one.
            log.error("dispatch-once: %s#%s: %s", repo.name, body.issue_number, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (FabroError, WorkflowVersionError) as exc:
            # `WorkflowVersionError` is the fetch-and-read half: git is missing, the
            # clone timed out, `.fabro/` moved. Fabro was never called, so no run
            # exists — but the operator still gets the sentence that says why.
            log.error("dispatch-once: %s#%s: %s", repo.name, body.issue_number, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        # The primary key is the real guard; the check above is what makes the
        # common case a clean refusal instead of a started run.
        try:
            cached = store.get_issue(repo.name, body.issue_number)
            leases.acquire(
                Lease(
                    coder_pool=body.coder_pool,
                    repo=repo.name,
                    issue_number=body.issue_number,
                    run_id=result.run_id,
                    dispatched_at=datetime.now(UTC),
                    # The manual fire need not name a queue item at all, so the
                    # wait is recorded when there is one to record. This path
                    # writes no labels and so does not itself evict the cache row.
                    queued_since=None if cached is None else cached.first_seen,
                )
            )
        except LeaseConflict as exc:
            log.error(
                "dispatch-once: run %s was started but holds no lease: %s",
                result.run_id,
                exc,
            )
            raise HTTPException(
                status_code=409,
                detail=f"run {result.run_id} was started but has no lease: {exc}",
            ) from exc

        log.info(
            "dispatch-once: %s#%s -> run %s pool=%s env=%s status=%s reused=%s",
            repo.name,
            body.issue_number,
            result.run_id,
            body.coder_pool,
            repo.environment_id,
            result.status,
            result.version_reused,
        )
        # Same rule as the loop's dispatch: a bump means "next", and this item has
        # just stopped waiting. Absent for an issue that was never in the cache,
        # which `clear_override_on_dispatch` reports as `False` rather than failing.
        if store.clear_override_on_dispatch(repo.name, body.issue_number):
            log.info(
                "dispatch-once: %s#%s: cleared the operator override on dispatch",
                repo.name,
                body.issue_number,
            )
        return {
            "run_id": result.run_id,
            "workflow_version_id": result.workflow_version_id,
            "commit_sha": result.commit_sha,
            "version_reused": result.version_reused,
            "status": result.status,
        }

    @app.post("/api/queue/{repo:path}/{issue_number}/bump")
    def bump_issue(repo: str, issue_number: int) -> dict[str, object]:
        """Mark one queued issue to be dispatched next.

        Takes `MIN(override_rank) - 1`, so the most recent bump wins and the newest
        mark sorts above every earlier one. It changes ordering **only**: it never
        pre-empts a running lease, and one in-flight run per repo (decision 6) still
        applies, so a bumped issue whose repo is busy goes next *for that repo*.

        The mark is cleared when the item is dispatched. It is deliberately not a
        permanent priority and deliberately not editable beyond "next": repo
        priority lives in `repos.toml`, which is reviewed policy, and the two never
        share a field (overview decision 4).
        """
        scheduled = config.repo_named(repo)
        if scheduled is None or not scheduled.enabled:
            raise HTTPException(
                status_code=404,
                detail=f"{repo} is not a schedulable [[repo]] in repos.toml",
            )
        # `scheduled.name`, not the path segment: `repo_named` matches
        # case-insensitively (GitHub resolves `o/Repo` and `o/repo` to one
        # repository), and everything else in the service keys off the spelling in
        # `repos.toml`. Writing the other spelling would create an override that
        # matches no cached row and silently does nothing.
        repo = scheduled.name
        # The page can only offer a row it is showing, so a miss here is a typo or a
        # stale page rather than a normal case — and an override for an item that is
        # not queued is invisible and would never be cleared by a dispatch.
        if store.get_issue(repo, issue_number) is None:
            raise HTTPException(
                status_code=404,
                detail=f"{repo}#{issue_number} is not in the queue",
            )

        rank = store.bump_override(repo, issue_number)
        log.info(
            "override: %s#%s bumped to rank %d; it dispatches next unless its repo "
            "is busy",
            repo,
            issue_number,
            rank,
        )
        return {"override_rank": rank}

    @app.post("/api/pools/{coder_pool}/drain")
    def drain_pool(coder_pool: str) -> dict[str, object]:
        """Stop new dispatch to one coder instance, leaving its current run alone.

        **Drain never destroys work** (CONTEXT.md; overview decision 15). The lease
        already on the box is untouched and runs to completion; only `free_pools`
        stops offering it. Cancelling the run is the separate route below.
        """
        require_pool(coder_pool)
        store.set_drained(coder_pool, True)
        held = next(
            (lease for lease in leases.active() if lease.coder_pool == coder_pool),
            None,
        )
        log.info(
            "drain: %s drained; new dispatch stops%s",
            coder_pool,
            (
                f", its run {held.run_id} ({held.repo}#{held.issue_number}) continues"
                if held is not None
                else "; it holds no lease"
            ),
        )
        return {"drained": True}

    @app.post("/api/pools/{coder_pool}/undrain")
    def undrain_pool(coder_pool: str) -> dict[str, object]:
        """Put one coder instance back in rotation. Takes effect on the next tick.

        No restart and no second step: the flag is read fresh every time the dispatch
        loop asks which pools are free.
        """
        require_pool(coder_pool)
        store.set_drained(coder_pool, False)
        log.info("drain: %s undrained; new dispatch may resume", coder_pool)
        return {"drained": False}

    @app.post("/api/pools/{coder_pool}/cancel")
    def cancel_pool(coder_pool: str) -> dict[str, object]:
        """Cancel the run currently on one coder instance.

        Asks fabro to cancel, and **stops there**. It does not release the lease, and
        it does not touch `pool_state`: the run is only over when fabro says it is
        terminal, which draft 09's release poll observes within 15 seconds, and that
        one release path is the only thing that frees a box. A cancel is asynchronous
        for a live run (fabro answers `202`), so releasing here would free the box
        while the worker was still using it.

        The cancel itself does not un-drain the box: a drained instance stays drained
        after its run is cancelled, which is the point of draining it.
        """
        require_pool(coder_pool)
        # The lease is checked before the credential so an unleased pool always gets
        # the honest 409 — "there is nothing to cancel" — rather than a 503 about a
        # token that is irrelevant to the answer.
        held = next(
            (lease for lease in leases.active() if lease.coder_pool == coder_pool),
            None,
        )
        if held is None:
            raise HTTPException(
                status_code=409,
                detail=f"{coder_pool} has no active lease, so there is no run to cancel",
            )
        if client is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "FABRO_API_TOKEN is not set; the scheduler cannot cancel run "
                    f"{held.run_id}"
                ),
            )

        try:
            client.cancel_run(held.run_id)
        except FabroError as exc:
            if exc.status_code == 409:
                # Fabro says the run already finished or was already cancelled. That
                # is not a failed cancel — the outcome is the one the operator wanted
                # — but the box is still leased for the next few seconds, so the
                # honest answer is the 409 with what to expect, not a claimed success.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"run {held.run_id} is already over ({exc}); the release "
                        f"poll will free {coder_pool} shortly"
                    ),
                ) from exc
            log.error(
                "cancel: %s run %s (%s#%s): fabro refused: %s",
                coder_pool,
                held.run_id,
                held.repo,
                held.issue_number,
                exc,
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        log.info(
            "cancel: %s run %s (%s#%s) asked to stop; the release poll frees the box "
            "when fabro reports it terminal",
            coder_pool,
            held.run_id,
            held.repo,
            held.issue_number,
        )
        return {"cancelled_run_id": held.run_id}

    @app.get("/", response_class=HTMLResponse)
    def queue_page(request: Request) -> HTMLResponse:
        items, now = current_queue()
        statuses = repo_statuses(config.schedulable_repos(), store)
        active = leases.active()
        return templates.TemplateResponse(
            request,
            "queue.html",
            {
                "items": items,
                "repos": statuses,
                "leases": active,
                # Rich `PoolState`s, not the pool names: the page needs the drain
                # flag and the lease to draw both the status and the controls.
                "pools": pool_states(),
                "now": now,
                "ceiling": config.starvation_ceiling,
                "poll_seconds": config.github_poll_seconds,
                "token_configured": token_configured,
                "dispatching": dispatcher is not None,
                # The live task/stage view for each leased run, read from the
                # probe's cache so a page load does no API or socket work.
                "run_progress": {
                    lease.run_id: probe.snapshot(lease.run_id) for lease in active
                },
                "run_probe_seconds": config.run_probe_seconds,
                # `Starvation` in the CONTEXT.md sense — zero queue items across
                # every schedulable repo — which is not the starvation *ceiling*.
                "starvation": not items,
                # The fabro web UI root for the run link, derived from the API base
                # by stripping `/api/v1`. Rendered only into the server-side HTML
                # page the operator views, never into a JSON payload.
                "fabro_ui_url": config.fabro_api_url.removesuffix("/api/v1").removesuffix("/"),
            },
        )

    @app.get("/history", response_class=HTMLResponse)
    def history_page(
        request: Request,
        sort: str | None = None,
        dir: str | None = None,
        limit: str | None = None,
    ) -> HTMLResponse:
        """Released runs, newest finished first.

        Deliberately carries no `poll_seconds`: unlike the queue page this one has
        no live data and no meta-refresh (ADR 0008). A released run never changes.
        """
        column, descending, capped = _history_query(sort, dir, limit)
        rows = store.history_rows(sort=column, descending=descending, limit=capped)
        return templates.TemplateResponse(
            request,
            "history.html",
            {
                "views": [_history_view(row) for row in rows],
                "sort": column,
                "descending": descending,
                "limit": capped,
                # The fabro web UI root for the run link, derived from the API
                # base, as on the queue page.
                "fabro_ui_url": config.fabro_api_url.removesuffix("/api/v1").removesuffix("/"),
            },
        )

    return app


def humanise_duration(delta: timedelta) -> str:
    """`3h 12m`, `2m 05s`, `4s` — the units the operator thinks in."""
    seconds = max(0, int(delta.total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def final_state_label(row: sqlite3.Row) -> str:
    """What a released run's ending means, in the operator's vocabulary.

    Derived, never stored (ADR 0008): the raw facts are the record and this is
    presentation, so rewording a label is this function plus its test and nothing
    else. That matters more than usual here, because `run_history` cannot gain a
    column after it ships.

    The ordering below is the whole logic, and two steps in it are not obvious:

    * `succeeded` is checked against `merged` BEFORE anything else, because a
      run whose PR was deliberately not auto-merged -- a risk-4 block -- also
      ends `succeeded`. `kind` alone cannot tell shipping from blocking.
    * a failure's REASON is checked before its CATEGORY, because the canonical
      infra failure lies about its category: a fabro restart ends every in-flight
      run `failed/terminated` with category `deterministic`, and a cancel carries
      no category in the projection at all.
    """
    kind = row["kind"]
    reason = row["reason"]

    if kind == "lost":
        return _with_requeues("lost (fabro 404)", row)

    if kind == "succeeded":
        if row["pr_lookup"] == "failed":
            return _with_requeues("succeeded, PR unknown", row)
        if row["pr_lookup"] == "none":
            return _with_requeues("succeeded, no PR", row)
        return _with_requeues("merged" if row["merged"] else "not merged", row)

    if kind == "failed":
        if reason == "terminated":
            return _with_requeues("failed (fabro restart)", row)
        if reason == "cancelled":
            return _with_requeues("cancelled", row)
        if row["category"] == "transient_infra":
            return _with_requeues("failed (infra)", row)
        return _with_requeues(f"failed ({reason})" if reason else "failed", row)

    if kind == "dead":
        return _with_requeues("dead", row)

    return _with_requeues(f"{kind}/{reason}" if reason else str(kind), row)


def _with_requeues(label: str, row: sqlite3.Row) -> str:
    """Append the requeue count, when there was one.

    A run that took three attempts to get here is a different fact from one that
    took none, and it is the fact that precedes a human being called -- the
    requeue budget is `MAX_REQUEUES = 3`.
    """
    attempts = row["requeue_attempt"] or 0
    return f"{label} ×{attempts + 1}" if attempts else label


def _queue_item_payload(item: QueueItem) -> dict[str, object]:
    return {
        "repo": item.issue.repo,
        "number": item.issue.number,
        "title": item.issue.title,
        "labels": sorted(item.issue.labels),
        "repo_priority": item.repo_priority,
        "override_rank": item.override_rank,
        "first_seen": item.issue.first_seen.isoformat(),
        "waited_seconds": round(item.waited.total_seconds(), 1),
    }


def _repo_status_payload(status: RepoStatus) -> dict[str, object]:
    return {
        "repo": status.name,
        "priority": status.priority,
        "enabled": status.enabled,
        "items": status.item_count,
        "stale": status.stale,
        "pending": status.pending,
        "etag": status.etag,
        "last_attempt_at": _iso(status.last_attempt_at),
        "last_success_at": _iso(status.last_success_at),
        "last_error": status.last_error,
    }


def _pool_payload(state: PoolState) -> dict[str, object]:
    """`GET /api/pools`'s documented shape: the lease as a flat object, or `null`.

    `issue_number` and `run_id` are what the page and the operator need to act; the
    lease's `queued_since` is deliberately not published, because it is an internal
    detail of the starvation accounting and nothing outside reads it.
    """
    lease = state.lease
    return {
        "coder_pool": state.coder_pool,
        "drained": state.drained,
        "lease": (
            None
            if lease is None
            else {
                "repo": lease.repo,
                "issue_number": lease.issue_number,
                "run_id": lease.run_id,
                "dispatched_at": lease.dispatched_at.isoformat(),
            }
        ),
    }


def _history_query(
    sort: str | None, direction: str | None, limit: str | None
) -> tuple[str, bool, int | None]:
    """Three URL values into `(sort, descending, limit)`. Never raises.

    Shared by `/api/history` and `/history` so the two can never disagree about
    what a query string means.

    Everything here arrives as text from a URL and ends up shaping SQL, so the
    rule is "turn anything into a valid triple", not "reject the invalid". A
    typo shows the operator the ordinary page rather than a 422:

    * an unknown sort column is left for `Store.history_rows`, whose whitelist
      is the actual guard and which falls back to `finished_at`;
    * any direction other than the literal `"asc"` means descending, which is
      the useful default for a history;
    * `limit="all"` means every row; a non-integer or a non-positive value means
      the default page.
    """
    descending = (direction or "").lower() != "asc"

    if (limit or "").lower() == "all":
        capped: int | None = None
    else:
        try:
            parsed = int(limit) if limit else DEFAULT_HISTORY_LIMIT
        except ValueError:
            parsed = DEFAULT_HISTORY_LIMIT
        capped = parsed if parsed > 0 else DEFAULT_HISTORY_LIMIT

    return (sort or DEFAULT_HISTORY_SORT), descending, capped


def _history_payload(row: sqlite3.Row) -> dict[str, object]:
    """One `run_history` row as JSON.

    `merged` is stored as 0/1/NULL because SQLite has no boolean, and is
    published as a real `true`/`false`/`null` -- a consumer should not have to
    know about the storage type. `pr_lookup` is published as-is: it is the field
    that says whether `merged` means anything, and flattening it into the null
    would lose exactly the distinction it exists to make.
    """
    merged = row["merged"]
    return {
        "run_id": row["run_id"],
        "coder_pool": row["coder_pool"],
        "repo": row["repo"],
        "issue_number": row["issue_number"],
        "dispatched_at": row["dispatched_at"],
        "finished_at": row["finished_at"],
        "kind": row["kind"],
        "reason": row["reason"],
        "category": row["category"],
        "requeue_attempt": row["requeue_attempt"],
        "pr_lookup": row["pr_lookup"],
        "pr_number": row["pr_number"],
        "pr_url": row["pr_url"],
        "merged": None if merged is None else bool(merged),
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _sort_link(column: str, *, active: str, descending: bool, limit: int | None) -> str:
    """The href for one sortable header.

    Clicking the column that is already active flips the direction; clicking any
    other column starts it descending, which is the useful default for every one
    of them -- newest, longest, most requeues first.

    `?limit=all` -- `limit=None` here -- is the one value carried through, because
    an operator who asked for every row still means it after a re-sort. A numeric
    limit is deliberately dropped: the default is the default, and a link that
    pinned a page size would make `?limit=5` sticky with nothing on the page to
    undo it.
    """
    flip = not descending if column == active else True
    query = f"sort={column}&dir={'desc' if flip else 'asc'}"
    if limit is None:
        query += "&limit=all"
    return f"/history?{query}"


@dataclass(frozen=True)
class HistoryView:
    """One `run_history` row, with the two things the template cannot compute.

    `row` is passed through whole rather than unpacked, because the template
    reads it by column name and a second field list here would be one more thing
    to keep in step with a schema that cannot change.
    """

    row: sqlite3.Row
    elapsed: timedelta | None      # finished_at - dispatched_at, when both parse


def _history_view(row: sqlite3.Row) -> HistoryView:
    """Attach the run's elapsed time. Never raises on a malformed timestamp."""
    try:
        started = datetime.fromisoformat(row["dispatched_at"])
        ended = datetime.fromisoformat(row["finished_at"])
    except (TypeError, ValueError):
        return HistoryView(row=row, elapsed=None)
    return HistoryView(row=row, elapsed=ended - started)


def create_app(
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    store: Store | None = None,
    github_token: str | None = None,
    fabro_token: str | None = None,
    fabro_client: FabroClient | None = None,
    run_probe: RunProbe | None = None,
    start_poller: bool = False,
    start_dispatch_loop: bool = False,
) -> FastAPI:
    """Build the app from a config file.

    Kept separate from `main` so tests and `uvicorn --factory` can build an app
    without the process exiting on a bad config. `store` defaults to the
    configured database path, which a test almost always wants to override with a
    `tmp_path` — `/data` does not exist off the host.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    path = config_path or Path(
        os.environ.get("SCHEDULER_CONFIG") or DEFAULT_CONFIG_PATH
    )
    config = load_config(path, environ)
    token = environ.get("GITHUB_TOKEN", "") if github_token is None else github_token
    fabro = environ.get("FABRO_API_TOKEN", "") if fabro_token is None else fabro_token
    return build_app(
        config,
        store if store is not None else Store(config.db_path),
        github_token=token,
        fabro_token=fabro,
        fabro_client=fabro_client,
        run_probe=run_probe,
        start_poller=start_poller,
        start_dispatch_loop=start_dispatch_loop,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint: validate the config first, then serve.

    A bad config is reported as one line naming the key and the process exits
    non-zero *before* binding the port, so a broken file fails the container's
    healthcheck instead of producing a service that is up and wrong. The database
    gets the same treatment: an unopenable `/data` is reported the same way, since
    a service that cannot persist what it sees should not claim to be healthy.
    """
    parser = argparse.ArgumentParser(
        prog="fabro-scheduler",
        description="Owns admission to the coder instances.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "path to repos.toml (default: $SCHEDULER_CONFIG, else "
            f"{DEFAULT_CONFIG_PATH})"
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("fabro_scheduler").setLevel(logging.INFO)
    # httpx logs every request at INFO, which duplicates this service's own line
    # for the same fetch; four repos a minute is ~11k lines a day of noise in
    # `docker compose logs`. The `github:` line carries the repo, the status, the
    # ETag and the rate limit, which is everything the request line had.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    path = args.config or Path(
        os.environ.get("SCHEDULER_CONFIG") or DEFAULT_CONFIG_PATH
    )
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(f"scheduler: cannot start: {exc}", file=sys.stderr)
        return 1

    try:
        store = Store(config.db_path)
    except sqlite3.Error as exc:
        print(
            f"scheduler: cannot start: {config.db_path}: {exc} "
            "(is the data volume mounted?)",
            file=sys.stderr,
        )
        return 1

    uvicorn.run(
        build_app(
            config,
            store,
            github_token=os.environ.get("GITHUB_TOKEN", ""),
            fabro_token=os.environ.get("FABRO_API_TOKEN", ""),
            start_poller=True,
            start_dispatch_loop=True,
        ),
        host="0.0.0.0",  # decision 16: LAN-only, published by compose
        port=config.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by `python -m`
    raise SystemExit(main())
