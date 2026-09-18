"""The scheduler's HTTP surface.

Three surfaces today, and the only one that writes anything is GitHub — which this
service never writes to. `GET /` is the operator's page, `GET /api/queue` is the
same data as JSON for whatever comes next (draft 11's mutations read it), and
`GET /health` reports what the service loaded and whether it can reach GitHub.

There is no auth: decision 16 binds this to the LAN, consistent with fabro's own
`:32276`. Nothing here echoes the environment, so the `GITHUB_TOKEN` the container
holds stays out of every response — including `/health`, which reports only whether
one is *configured*, never its value. `fabro_api_url` is deliberately absent from
the payload as well — it is a URL that could carry a credential in some future
form, and it is not worth the risk to publish it on an unauthenticated endpoint.

**A missing `GITHUB_TOKEN` does not stop the service.** It logs one error, serves
the page with a banner naming the variable, and reports `github.configured: false`
on `/health` for draft 12's monitor to alert on. Exiting instead would fail the
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import __version__
from .config import ConfigError, SchedulerConfig, load_config
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


def build_app(
    config: SchedulerConfig,
    store: Store,
    *,
    github_token: str = "",
    start_poller: bool = False,
) -> FastAPI:
    """The ASGI app for an already-validated config and an open store.

    `start_poller` is off by default so a test can drive the inventory by hand
    instead of racing a background thread. `main` turns it on.
    """
    token_configured = bool(github_token.strip())

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
    return build_app(
        config,
        store if store is not None else Store(config.db_path),
        github_token=token,
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
            start_poller=True,
        ),
        host="0.0.0.0",  # decision 16: LAN-only, published by compose
        port=config.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by `python -m`
    raise SystemExit(main())
