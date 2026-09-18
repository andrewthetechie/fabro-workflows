# fabro-ts:local -- sandbox profile image for the `ts` environment
# (the three womens-fantasy-sports automations).
#
#   built by build-images.sh; see Dockerfile.python for why the context matters

FROM oven/bun:1.4.2

ARG GH_VERSION=2.100.0
# backend/pyproject.toml: requires-python >=3.14,<4.0. compose.yml's `db` service
# is postgres:18, and backup-bats.yml pins the same fixture image.
ARG PYTHON_VERSION=3.14
ARG PG_MAJOR=18

# ---------------------------------------------------------------------------
# Toolchain
# ---------------------------------------------------------------------------
# Bun is the runtime and package manager. `node` in this base is bun's
# node-fallback symlink, not a Node runtime -- `node -v` prints bun's own help.
# Nothing in this repository's ci.sh needs real Node, so that is left alone.
#
# uv and Python are NEW: this image had neither, so womens-fantasy-sports's
# `.fabro/ci.sh` could not touch the backend at all and says so --
# "Backend pytest is excluded here because it requires Postgres + Python 3.14".
RUN apt-get update && apt-get install -y --no-install-recommends \
      git jq curl ca-certificates gnupg \
  && curl -LsSf https://astral.sh/uv/install.sh | sh \
  && curl -fsSL -o /tmp/gh.tgz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
  && tar -xzf /tmp/gh.tgz -C /usr/local --strip-components=1 && rm /tmp/gh.tgz \
  && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# PostgreSQL 18 -- see Dockerfile.python-node for why it lives in the image
# ---------------------------------------------------------------------------
# 18, not 17: compose.yml's `db` service is postgres:18, and backup-bats.yml notes
# that "pg_dump refuses to dump a server newer" than the client, so a lower major
# here would break the backup tests specifically.
RUN install -d /usr/share/postgresql-common/pgdg \
  && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends "postgresql-${PG_MAJOR}" "postgresql-client-${PG_MAJOR}" \
  && rm -rf /var/lib/apt/lists/*

COPY fabro-pg-ensure.sh /usr/local/bin/fabro-pg-ensure
RUN chmod +x /usr/local/bin/fabro-pg-ensure

ENV PGDATA=/var/lib/postgresql/fabro
RUN mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" && chmod 0700 "$PGDATA" \
  && su postgres -s /bin/sh -c "/usr/lib/postgresql/${PG_MAJOR}/bin/initdb -D '$PGDATA' -U postgres --auth=trust --auth-host=trust" >/dev/null \
  && echo "postgres ${PG_MAJOR} cluster initialised at $PGDATA"

# ---------------------------------------------------------------------------
# Cache layout -- see common.env.md
# ---------------------------------------------------------------------------
ENV PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    UV_PYTHON_DOWNLOADS=true \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_CACHE_DIR=/opt/cache/uv \
    UV_LINK_MODE=copy \
    BUN_INSTALL_CACHE_DIR=/opt/cache/bun

RUN uv python install "${PYTHON_VERSION}" && uv python list --only-installed

# ---------------------------------------------------------------------------
# Warm the caches from the repository's own lockfiles
# ---------------------------------------------------------------------------
# FOUR install roots, because this repository has four: a bun workspace at the
# root, a second bun workspace under site/, a uv project under backend/, and a
# root uv project whose dev group carries the lint toolchain (prek, zizmor,
# ansible-lint). The root one is easy to miss -- its pyproject has no [project]
# table at all, only [dependency-groups] -- and missing it left a 60 MB manylinux
# zizmor wheel being downloaded on every run.
# --no-install-workspace, not --no-install-project: the latter skips only the
# ROOT project, so in a uv workspace it still tries to build every member -- and
# a manifest-only context has no source to build them from. womens-fantasy-sports
# has `backend` as a workspace member and failed exactly that way:
#   error: Failed to build `app @ file:///tmp/warm/repo/backend`
#   cause: Call to `hatchling.build.build_editable` failed
# On a repository that is not a workspace the two flags behave identically.
COPY warm/ /tmp/warm/
RUN cd /tmp/warm/repo \
  && bun install --frozen-lockfile --ignore-scripts \
  && rm -rf node_modules \
  && if [ -f site/bun.lock ]; then cd /tmp/warm/repo/site && bun install --frozen-lockfile --ignore-scripts && rm -rf node_modules; fi \
  && if [ -f /tmp/warm/repo/backend/pyproject.toml ]; then cd /tmp/warm/repo/backend && uv sync --frozen --no-install-workspace && rm -rf .venv; fi \
  && if [ -f /tmp/warm/repo/uv.lock ]; then cd /tmp/warm/repo && uv sync --frozen --no-install-workspace && rm -rf .venv; fi \
  && sh /tmp/warm/warm-build-backend.sh /tmp/warm/repo \
  && rm -rf /tmp/warm /tmp/buildreqs \
  && du -sh /opt/cache/* /opt/uv-python
