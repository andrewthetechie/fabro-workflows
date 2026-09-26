#!/bin/sh
#
# Start the image's baked PostgreSQL cluster and hand back pristine databases.
# Installed at /usr/local/bin/fabro-pg-ensure in every profile image whose
# repository's tests need a database. Idempotent: call it from `.fabro/setup.sh`,
# from `.fabro/ci.sh`, or from both.
#
#   fabro-pg-ensure                      # cluster up, `test` role and database ready
#   PG_EXTRA_DBS="mig_test" fabro-pg-ensure
#
# WHY THIS EXISTS RATHER THAN A SERVICE
#
# Fabro cannot start a database beside the sandbox. `GET /runs/{id}/sandbox/services`
# is discovery -- it shells `ss -H -ltnp` and reports the ports it finds -- so it
# lists a server, it never starts one. The docker sandbox-driver does support
# `sidecars`, but fabro builds its DockerProviderConfig in one hardcoded place
# (fabro-sandbox/src/docker.rs:47) and every field but auto_pull stays at its
# default. There is also no Docker-in-Docker inside a sandbox, so the
# `docker run postgres:17` path the repositories use on a developer machine is
# not available either.
#
# So the server lives in the image and runs as an ordinary process. This is the
# same fallback Sandcastle-loop/pg-ensure.sh takes for the same reason
# ("Sandbox ... there is no Docker-in-Docker, so we fall back to a self-managed
# cluster"), with two differences: the cluster is initdb'd at image build time
# rather than on first call, and the sandbox runs as root, so every server
# command drops to the `postgres` user.
#
# WHAT IT GUARANTEES
#
# A superuser `test` with password `test`, a database `test`, on PGPORT. That is
# exactly what a `postgres:N` GitHub Actions service gives a job, which is what
# the repositories' conftest files are written against -- lawncare-saas expects
# postgresql+asyncpg://test:test@localhost:5432/test with CI set.
set -eu

PGPORT=${PGPORT:-5432}
PGDATA=${PGDATA:-/var/lib/postgresql/fabro}
PG_EXTRA_DBS=${PG_EXTRA_DBS:-}
PG_READY_TIMEOUT=${PG_READY_TIMEOUT:-60}

BINDIR=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)
[ -n "$BINDIR" ] || { echo "fabro-pg-ensure: no PostgreSQL server binaries in this image" >&2; exit 1; }
PATH="$BINDIR:$PATH"
export PATH

# The sandbox is root and postgres refuses to run as root. `su postgres -c` is
# used rather than runuser so the script works on any base that has `su`.
as_pg() { su postgres -s /bin/sh -c "$1"; }

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  # Only reached if the image's build-time initdb was skipped. Slow but correct.
  echo "fabro-pg-ensure: no cluster at $PGDATA; running initdb"
  mkdir -p "$PGDATA"
  chown postgres:postgres "$PGDATA"
  chmod 0700 "$PGDATA"
  as_pg "initdb -D '$PGDATA' -U postgres --auth=trust --auth-host=trust --encoding=UTF8 --locale=C.UTF-8"
fi

if ! as_pg "pg_ctl -D '$PGDATA' status" >/dev/null 2>&1; then
  as_pg "pg_ctl -D '$PGDATA' -l '$PGDATA/server.log' \
          -o '-p $PGPORT -c listen_addresses=localhost -c fsync=off \
              -c full_page_writes=off -c synchronous_commit=off' -w start" >/dev/null
fi

# fsync=off and friends above: this cluster is thrown away with the sandbox, so
# durability buys nothing and costs a great deal on a test suite that commits.

i=0
while [ "$i" -lt "$PG_READY_TIMEOUT" ]; do
  if as_pg "pg_isready -p $PGPORT -q" >/dev/null 2>&1; then break; fi
  i=$((i + 1))
  sleep 1
done
if [ "$i" -ge "$PG_READY_TIMEOUT" ]; then
  echo "fabro-pg-ensure: cluster did not accept connections within ${PG_READY_TIMEOUT}s" >&2
  tail -30 "$PGDATA/server.log" >&2 2>/dev/null || true
  exit 1
fi

psql_su() { as_pg "psql -p $PGPORT -U postgres -d postgres -v ON_ERROR_STOP=1 $1" >/dev/null; }

# A superuser, because a `postgres:N` Actions service makes its POSTGRES_USER one
# and the suites rely on it (lawncare-saas creates roles from inside a migration).
as_pg "psql -p $PGPORT -U postgres -d postgres -tAc \"SELECT 1 FROM pg_roles WHERE rolname='test'\"" \
  | grep -q 1 || psql_su "-c \"CREATE ROLE test LOGIN SUPERUSER PASSWORD 'test'\""

# Recreate rather than reuse. The suites commit rows and create tables they never
# clean up, because a GitHub Actions service is a fresh container every job -- so
# a reused database fails the SECOND run of a stage with DuplicateTable or
# UniqueViolation, not the first. `validate` runs more than once per fabro run.
for db in test $PG_EXTRA_DBS; do
  psql_su "-c \"DROP DATABASE IF EXISTS $db WITH (FORCE)\" -c \"CREATE DATABASE $db OWNER test\""
done

echo "fabro-pg-ensure: postgres ready on port $PGPORT (databases: test $PG_EXTRA_DBS)"
