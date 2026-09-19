"""The scheduler's HTTP surface.

The read surfaces are the operator's page (`GET /`), its JSON twin
(`GET /api/queue`), the per-repo freshness view (`GET /api/repos`) and `GET
/health`. The one write surface is `POST /api/dispatch-once`, which creates and
starts exactly one real fabro run — still by hand, because the loop that decides
when to call it is draft 08.

There is no auth: decision 16 binds this to the LAN, consistent with fabro's own
`:32276`. Nothing here echoes the environment, so the `GITHUB_TOKEN` and
`FABRO_API_TOKEN` the container holds stay out of every response — including
`/health`, which reports only whether each is *configured*, never its value.
`fabro_api_url` is deliberately absent from the payload as well — it is a URL that
could carry a credential in some future form, and it is not worth the risk to
publish it on an unauthenticated endpoint.

**Neither missing credential stops the service.** A missing `GITHUB_TOKEN` logs
one error, serves the page with a banner naming the variable, and reports
`github.configured: false` on `/health` for draft 12's monitor to alert on; a
missing `FABRO_API_TOKEN` does the same and makes `/api/dispatch-once` answer
`503`. Exiting instead would fail the container's healthcheck in a restart loop
that says nothing about which variable is missing, and the page is the surface
where that is visible at a glance.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
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
from .fabro import FabroClient, FabroError, RunNotStarted
from .inventory import InventoryPoller
from .queue import QueueItem, RepoStatus, build_queue, repo_statuses
from .store import Store

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


def build_app(
    config: SchedulerConfig,
    store: Store,
    *,
    github_token: str = "",
    fabro_token: str = "",
    fabro_client: FabroClient | None = None,
    start_poller: bool = False,
) -> FastAPI:
    """The ASGI app for an already-validated config and an open store.

    `start_poller` is off by default so a test can drive the inventory by hand
    instead of racing a background thread. `main` turns it on.

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

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["duration"] = humanise_duration

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if poller is not None:
            poller.start()
        try:
            yield
        finally:
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

    @app.post("/api/dispatch-once")
    def dispatch_once(body: DispatchOnceRequest) -> dict[str, object]:
        """Create and start one `backlog` run, by hand.

        The manual trigger draft 07 exists to prove: it takes `repo`,
        `issue_number` and `coder_pool` explicitly and does exactly one dispatch.
        Draft 08 replaces the body with the real loop and keeps this as the
        operator's override.

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
        except FabroError as exc:
            log.error("dispatch-once: %s#%s: %s", repo.name, body.issue_number, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

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
        return {
            "run_id": result.run_id,
            "workflow_version_id": result.workflow_version_id,
            "commit_sha": result.commit_sha,
            "version_reused": result.version_reused,
            "status": result.status,
        }

    @app.get("/", response_class=HTMLResponse)
    def queue_page(request: Request) -> HTMLResponse:
        items, now = current_queue()
        statuses = repo_statuses(config.schedulable_repos(), store)
        return templates.TemplateResponse(
            request,
            "queue.html",
            {
                "items": items,
                "repos": statuses,
                "now": now,
                "ceiling": config.starvation_ceiling,
                "poll_seconds": config.github_poll_seconds,
                "token_configured": token_configured,
                # `Starvation` in the CONTEXT.md sense — zero queue items across
                # every schedulable repo — which is not the starvation *ceiling*.
                "starvation": not items,
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


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def create_app(
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    store: Store | None = None,
    github_token: str | None = None,
    fabro_token: str | None = None,
    fabro_client: FabroClient | None = None,
    start_poller: bool = False,
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
        start_poller=start_poller,
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
        ),
        host="0.0.0.0",  # decision 16: LAN-only, published by compose
        port=config.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by `python -m`
    raise SystemExit(main())
