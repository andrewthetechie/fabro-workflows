"""The scheduler's HTTP surface.

Two surfaces, and only one of them is HTTP. `GET /health` reports what the
service loaded — the point of this draft is that every later one has somewhere to
land and a way to prove it is running.

There is no auth: decision 16 binds this to the LAN, consistent with fabro's own
`:32276`. Nothing here echoes the environment, so the `FABRO_API_TOKEN` and
`GITHUB_TOKEN` the container holds for drafts 06 and 07 cannot leak through it.
`fabro_api_url` is deliberately absent from the payload as well — it is a URL
that could carry a credential in some future form, and it is not worth the risk
to publish it on an unauthenticated endpoint.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from . import __version__
from .config import DEFAULT_PORT, ConfigError, SchedulerConfig, load_config

# Baked into the image at this path by the Dockerfile and overridable with
# `--config` or `SCHEDULER_CONFIG`, so the container needs no bind mount. A
# compose bind mount of a single file silently creates a *directory* when the
# path is missing, which is a failure mode worth not having.
DEFAULT_CONFIG_PATH = Path("/app/repos.toml")


def build_app(config: SchedulerConfig) -> FastAPI:
    """The ASGI app for an already-validated config."""
    app = FastAPI(
        title="fabro coder scheduler",
        version=__version__,
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
        }

    return app


def create_app(
    config_path: Path | None = None, env: Mapping[str, str] | None = None
) -> FastAPI:
    """Build the app from a config file.

    Kept separate from `main` so tests and `uvicorn --factory` can build an app
    without the process exiting on a bad config.
    """
    path = config_path or Path(
        os.environ.get("SCHEDULER_CONFIG") or DEFAULT_CONFIG_PATH
    )
    return build_app(load_config(path, env))


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint: validate the config first, then serve.

    A bad config is reported as one line naming the key and the process exits
    non-zero *before* binding the port, so a broken file fails the container's
    healthcheck instead of producing a service that is up and wrong.
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

    path = args.config or Path(
        os.environ.get("SCHEDULER_CONFIG") or DEFAULT_CONFIG_PATH
    )
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(f"scheduler: cannot start: {exc}", file=sys.stderr)
        return 1

    uvicorn.run(
        build_app(config),
        host="0.0.0.0",  # decision 16: LAN-only, published by compose
        port=config.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by `python -m`
    raise SystemExit(main())
