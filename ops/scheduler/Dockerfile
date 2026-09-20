# The coder scheduler.
#
# Built by `docker compose up -d --build scheduler` from ~/fabro/, where this
# directory is rsynced to ~/fabro/scheduler/ and so has to sit beside the compose
# file: `build.context` is resolved relative to it.
#
# Two stages, so the runtime image carries no `uv`, no build tooling and no wheel
# cache. The uv image is itself FROM python3.11-slim-bookworm, so the venv's
# interpreter path and its shebangs stay valid when it is copied across.
#
# Python 3.11 and not 3.12: the same interpreter the operator's `uv run pytest`
# uses on the Mac (`.python-version`) and the one AGENTS.md names for `tomllib`
# checks. `tomllib` is stdlib from 3.11, which is the only version requirement
# the code has.

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Only the dependency inputs, resolved separately from the source, so editing a
# module does not invalidate the installed-environment layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY repos.toml ./repos.toml
RUN uv sync --frozen --no-dev


FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    SCHEDULER_CONFIG=/app/repos.toml

# `git`, for the one thing this service does that a Python library could not do
# as cheaply: fetch `.fabro/` from `andrewthetechie/fabro-workflows@main`. The
# workflow version is built from a shallow clone of that tree, exactly as
# `fire-pr-review.sh` fetches it, and `python:slim` ships no git. `--no-install-
# recommends` keeps it to git and its hard dependencies; `ca-certificates` is
# listed explicitly because a clone over TLS is the whole point and it is one
# base-image change away from not being there.
#
# NB: no `docker` CLI is installed. The scheduler reaches the socket it is given
# through the Docker Engine HTTP API (see `fabro_scheduler.docker`), so no client
# binary is needed.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && rm -rf /var/lib/apt/lists/*

# Non-root, with a fixed uid so the /data volume's ownership is predictable.
# `--system` on the group and user keeps them out of the login range.
RUN groupadd --system --gid 1000 scheduler \
 && useradd --system --uid 1000 --gid scheduler --no-create-home scheduler

# The host's docker group GID, so the non-root `scheduler` user can open the
# mounted /var/run/docker.sock (which is `root:docker` mode 660 on the host).
# This container only ever runs on the single fabro host, so the default pins
# that host's observed GID; override with --build-arg if the host is rebuilt.
# Mounting the socket IS the security boundary — it grants root-equivalent host
# access regardless of UID — so this group only keeps the process non-root for
# everything it touches that is not the socket.
ARG DOCKER_GID=983
RUN groupadd --system --gid ${DOCKER_GID} docker \
 && gpasswd -a scheduler docker

WORKDIR /app
COPY --from=builder --chown=1000:1000 /app /app

# /data has to exist in the image, owned by the runtime user: compose mounts a
# *named* volume there, and Docker seeds an empty named volume from the image's
# directory including its ownership. A bind mount would not, and the SQLite file
# this holds from draft 06 onward would fail to open as a non-root user.
RUN mkdir -p /data && chown 1000:1000 /data

USER scheduler
EXPOSE 32280

# `python -m` rather than the console script: it needs nothing on PATH but the
# venv that is already there.
CMD ["python", "-m", "fabro_scheduler"]
