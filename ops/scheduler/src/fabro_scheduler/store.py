"""The scheduler's SQLite state: the issue cache, the per-repo ETag record, and the
coder leases.

Four tables, all owned exclusively by this service. The database is at
`/data/scheduler.db` on the compose `scheduler-data` volume, so it survives a
restart — and two of the things it holds are *only* safe because it does:

* `first_seen` — the starvation ceiling is measured from when *this scheduler*
  first saw an issue, and a restart that reset it would reset every item's wait;
* the `leases` table — a restart must adopt the leases it already holds rather
  than forget them and put a second run on a busy box. Draft 09's recovery
  reconciles those rows against fabro; it does not rebuild them.

**Only a successful fetch writes a cache row.** A failed fetch records the failure
against the repo and leaves every cached issue row alone, which is the difference
between a stale page and a page that says "starvation" because GitHub had a bad
minute. The lease tables are not inventory and are not covered by that rule: they
are written by dispatch, which never touches the cache.

`repo_affinity` is the one table not named in draft 08's contract, and it exists
because decision 11's soft affinity has to outlive a lease. `leases` holds only the
*active* claim — one row per box, deleted when the run ends — so after draft 09
releases a row there would be nothing left anywhere to say which box last ran a
repo, and `last_pool_for_repo` would always answer `None`.

Draft 11 adds two more, and both are operator state rather than inventory:
`overrides` (the UI's "make this next" mark, ephemeral by design — overview
decision 4) and `pool_state` (the **Drain** flag per coder instance). They live in
SQLite for the same reason the leases do: a restart that forgot a drained box would
quietly put new work straight back onto it, and an operator who drained a
misbehaving instance would have to notice and drain it again.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .github import Issue

log = logging.getLogger(__name__)

# `first_seen` is set once, on the first sighting, and never updated. `last_seen`
# is the observation time of the most recent sighting; both are ISO-8601 UTC.
SCHEMA = """
CREATE TABLE IF NOT EXISTS issue_cache (
  repo       TEXT    NOT NULL,
  number     INTEGER NOT NULL,
  title      TEXT    NOT NULL,
  labels     TEXT    NOT NULL,  -- JSON array of label names
  first_seen TEXT    NOT NULL,  -- ISO-8601 UTC; never rewritten
  last_seen  TEXT    NOT NULL,  -- ISO-8601 UTC; every successful sighting
  PRIMARY KEY (repo, number)
);

CREATE TABLE IF NOT EXISTS repo_etag (
  repo            TEXT PRIMARY KEY,
  etag            TEXT,
  last_attempt_at TEXT,
  last_success_at TEXT,
  last_error      TEXT
);

-- One row per coder instance currently holding a run. The primary key on
-- `coder_pool` is what makes double-dispatch to one box impossible: it is the
-- database enforcing it, not the loop, so it holds even if the loop is entered
-- twice or two scheduler processes share the file. `run_id` is UNIQUE as a
-- second, different guard — the same run must never hold two boxes.
--
-- `queued_since` is the item's `first_seen`, copied off the `issue_cache` row at
-- dispatch, and it is the one column here that draft 08's contract does not name.
-- It is load-bearing for draft 09: the issue leaves the `agent` collection the
-- moment the scheduler labels it `agent-in-progress`, so `sync_repo` deletes the
-- cache row within a minute of dispatch and `first_seen` goes with it. Without a
-- copy here, a requeued item would come back with a fresh wait, its starvation
-- ceiling would restart, and decision 5's "nothing waits more than T" would be
-- quietly false for every item that ever failed. Nullable because it is optional
-- knowledge: draft 09 falls back to "now" when it is absent. Added now rather
-- than by draft 09 because `CREATE TABLE IF NOT EXISTS` will not add a column to
-- a table that already exists, and this one will exist on the host as soon as
-- draft 08 is deployed.
CREATE TABLE IF NOT EXISTS leases (
  coder_pool    TEXT PRIMARY KEY,
  repo          TEXT NOT NULL,
  issue_number  INTEGER NOT NULL,
  run_id        TEXT NOT NULL UNIQUE,
  dispatched_at TEXT NOT NULL,  -- ISO-8601 UTC
  queued_since  TEXT            -- ISO-8601 UTC; `issue_cache.first_seen`
);

-- The box that last ran each repo, kept after its lease is gone. Rewritten on
-- every acquire; one row per repo, most recent wins.
CREATE TABLE IF NOT EXISTS repo_affinity (
  repo        TEXT PRIMARY KEY,
  coder_pool  TEXT NOT NULL,
  recorded_at TEXT NOT NULL     -- ISO-8601 UTC
);

-- Draft 11. One row per issue the operator has bumped to the front. `override_rank`
-- is a signed integer that sorts *before* the starvation ceiling, ascending, so
-- `bump_override` takes `MIN(...) - 1` and the most recent bump wins. The row is
-- deleted the moment the item is dispatched: it means "next", not "forever"
-- (overview decision 4), and a mark that outlived its dispatch would pin an item
-- to the front of the queue for a reason nobody remembers.
--
-- Not a `priority` column and not in `repos.toml` on purpose: repo priority is a
-- reviewed policy decision, this is one operator's click, which is why the two
-- never share a field.
CREATE TABLE IF NOT EXISTS overrides (
  repo          TEXT    NOT NULL,
  issue_number  INTEGER NOT NULL,
  override_rank INTEGER NOT NULL,
  created_at    TEXT    NOT NULL,  -- ISO-8601 UTC
  PRIMARY KEY (repo, issue_number)
);

-- Draft 11. The **Drain** flag, one row per coder instance. A row is written only
-- when the operator drains or undrains a box, so "no row" and `drained = 0` mean
-- the same thing; `drained_pools` reads only the `1`s. Draining never touches the
-- lease column: the run already on the box keeps running (overview decision 15),
-- which is why this is a separate table and not a column on `leases`.
CREATE TABLE IF NOT EXISTS pool_state (
  coder_pool TEXT PRIMARY KEY,
  drained    INTEGER NOT NULL DEFAULT 0
);

-- How many times each issue has been requeued after a failure. The requeue rule
-- is what keeps work moving across a fabro restart, and it is also the one rule
-- that can loop: it puts the issue straight back at the front of the queue, so a
-- failure that recurs every time reserves a coder box forever and never reaches a
-- human.
--
-- That is not hypothetical. On 2026-09-19 fabro classified a **flaky unit test**
-- as `category: "transient_infra"` (run 01M2X2MVPC2NXY1BW5CVKPFF3S, a test the run
-- never touched, on a `main` whose own CI was green). Until the same day's fix to
-- `last_failure_category` that category could never be read, so the predicate was
-- dead code and the loop was unreachable; repairing the read is what arms it.
--
-- Cleared when the issue reaches an ending that is NOT a requeue, because that is
-- the issue leaving the queue on its own terms.
CREATE TABLE IF NOT EXISTS requeue_counts (
  repo         TEXT    NOT NULL,
  issue_number INTEGER NOT NULL,
  count        INTEGER NOT NULL DEFAULT 0,
  last_at      TEXT    NOT NULL,  -- ISO-8601 UTC
  PRIMARY KEY (repo, issue_number)
);

-- One row per released coder lease: what the run turned out to be. Written inside
-- the same transaction that deletes the lease, so a crash cannot lose the row and
-- the lease together (ADR 0008). Point-in-time: it records what the ending WAS, not
-- what became of the PR afterwards.
--
-- Every column the series needs is here, including the four the PR lookup does not
-- fill until a later task, because `CREATE TABLE IF NOT EXISTS` will not add a
-- column to a table that already exists -- the same constraint that put
-- `queued_since` on `leases` a draft early.
--
-- `run_id` is the key because it is already UNIQUE on `leases` and one dispatch
-- produces exactly one run. A requeued issue comes back under a NEW run id, so a
-- second attempt is a second row rather than a collision.
CREATE TABLE IF NOT EXISTS run_history (
  run_id          TEXT PRIMARY KEY,
  coder_pool      TEXT NOT NULL,
  repo            TEXT NOT NULL,
  issue_number    INTEGER NOT NULL,
  dispatched_at   TEXT NOT NULL,  -- ISO-8601 UTC, copied from `leases.dispatched_at`
  finished_at     TEXT NOT NULL,  -- ISO-8601 UTC
  kind            TEXT NOT NULL,  -- "succeeded" | "failed" | "dead" | "lost"
  reason          TEXT,
  category        TEXT,
  requeue_attempt INTEGER NOT NULL DEFAULT 0,
  pr_lookup       TEXT NOT NULL,  -- "found" | "none" | "failed"
  pr_number       INTEGER,
  pr_url          TEXT,
  merged          INTEGER         -- 0 | 1 | NULL
);
"""


# The columns `/history` may be sorted by. This is a WHITELIST, not documentation:
# SQLite cannot parameterise an ORDER BY column, so the name is interpolated into
# the statement and membership here is the only thing between a URL query string
# and arbitrary SQL. Adding a column to `run_history` does not add it here.
HISTORY_SORT_COLUMNS = frozenset({
    "finished_at",
    "dispatched_at",
    "repo",
    "issue_number",
    "coder_pool",
    "kind",
    "merged",
    "requeue_attempt",
})

# Most-recently-finished first, and the page size the operator gets when they ask
# for nothing in particular.
DEFAULT_HISTORY_SORT = "finished_at"
DEFAULT_HISTORY_LIMIT = 200


@dataclass(frozen=True)
class RunOutcome:
    """What a released run turned out to be, for its `run_history` row.

    Every field after `kind` has a default, so the paths that know less -- a run
    fabro has lost, a release taken before the PR lookup exists -- construct one
    without inventing facts they do not have. `finished_at=None` means "use the
    release time", which is the only answer the lost-run path has.
    """

    kind: str                              # "succeeded" | "failed" | "dead" | "lost"
    reason: str | None = None
    category: str | None = None
    finished_at: datetime | None = None
    requeue_attempt: int = 0
    pr_lookup: str = "none"                # "found" | "none" | "failed"
    pr_number: int | None = None
    pr_url: str | None = None
    merged: bool | None = None


@dataclass(frozen=True)
class RepoFetch:
    """What is known about one repo's last attempt at the inventory.

    Raw facts only. Whether a repo is *stale* or merely *pending* is presentation,
    and that decision lives in exactly one place: `queue.RepoStatus`.
    """

    repo: str
    etag: str | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None


class Store:
    """The scheduler's SQLite handle.

    One connection, shared. FastAPI runs sync endpoints in a worker thread while
    the poller runs in its own, so `check_same_thread=False` plus an explicit lock
    around **every** statement — reads included — is the honest arrangement: the
    invariant is "one statement at a time on this connection", and it is far
    cheaper to keep than to reason about which statements are safe to interleave.
    The contention is a handful of statements per minute.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # The parent directory is deliberately not created. `/data` is built into
        # the image and seeded from it by the named volume; if it is missing, the
        # volume is not mounted and a clear "unable to open database file" is the
        # failure the operator needs, not a directory that quietly appears inside
        # the container's writable layer and is lost on the next recreate.
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, timeout=5.0
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        # WAL: the poller writes while request threads read. Readers never block
        # the writer and vice versa.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # --- reads ------------------------------------------------------------------

    def override_ranks(self) -> dict[tuple[str, int], int]:
        """Every operator override, keyed by `(repo, issue_number)`.

        Read whole by `build_queue`, which needs the rank for each cached issue
        anyway — one SELECT beats one per item, and the table is a handful of rows.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT repo, issue_number, override_rank FROM overrides"
            ).fetchall()
        return {
            (row["repo"], row["issue_number"]): row["override_rank"] for row in rows
        }

    def get_override(self, repo: str, number: int) -> int | None:
        """This issue's override rank, or `None` if the operator never bumped it."""
        with self._lock:
            row = self._conn.execute(
                "SELECT override_rank FROM overrides "
                "WHERE repo = ? AND issue_number = ?",
                (repo, number),
            ).fetchone()
        return None if row is None else int(row["override_rank"])

    def drained_pools(self) -> set[str]:
        """The coder instances taken out of rotation, by name.

        Only the `drained = 1` rows: a row exists for a box that was drained and
        then undrained, and it must not read as drained. Unknown names are returned
        as they are stored — filtering against `config.coder_pools` is
        `LeaseStore.free_pools`'s job, which already only walks the configured set.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT coder_pool FROM pool_state WHERE drained = 1"
            ).fetchall()
        return {row["coder_pool"] for row in rows}

    def get_issue(self, repo: str, number: int) -> Issue | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT repo, number, title, labels, first_seen "
                "FROM issue_cache WHERE repo = ? AND number = ?",
                (repo, number),
            ).fetchone()
        return None if row is None else _row_to_issue(row)

    def issues(self) -> list[Issue]:
        """Every cached queue item, across every repo.

        Includes repos that are no longer in `repos.toml` or are `enabled = false`
        — the caller decides which repos it will schedule for, and a disabled
        repo's rows are harmless. Nothing here filters.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT repo, number, title, labels, first_seen FROM issue_cache "
                "ORDER BY repo, number"
            ).fetchall()
        return [_row_to_issue(row) for row in rows]

    def issue_counts(self) -> dict[str, int]:
        """Cached queue-item count per repo, for repos that have any rows.

        A repo absent from the result has none — the page renders that as `0`,
        which is the same thing.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT repo, COUNT(*) AS n FROM issue_cache GROUP BY repo"
            ).fetchall()
        return {row["repo"]: row["n"] for row in rows}

    def lease_rows(self) -> list[sqlite3.Row]:
        """Every active lease, one row per coder instance, ordered by pool.

        Plain columns rather than a `Lease`: that dataclass lives in `lease.py`,
        which imports this module, and a persistence layer that returned a domain
        type would have to import it back. `LeaseStore` is where the mapping
        belongs.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT coder_pool, repo, issue_number, run_id, dispatched_at, "
                "queued_since FROM leases ORDER BY coder_pool"
            ).fetchall()

    def history_rows(
        self,
        *,
        sort: str = DEFAULT_HISTORY_SORT,
        descending: bool = True,
        limit: int | None = DEFAULT_HISTORY_LIMIT,
    ) -> list[sqlite3.Row]:
        """Released runs, newest first by default.

        `sort` is interpolated, not parameterised -- SQLite has no placeholder for an
        ORDER BY column -- so it is checked against `HISTORY_SORT_COLUMNS` first. An
        unknown column falls back to the default rather than raising: this is reached
        from a URL query string, and a typo should show the operator the ordinary page
        rather than a 500.

        `limit=None` means every row, which is what `?limit=all` asks for.

        The ORDER BY and the LIMIT are one statement on purpose. Sorting a fetched
        page in Python would answer a different question -- "the longest of the most
        recent 200" rather than "the longest" -- and it would look right.

        `run_id` is the tiebreaker on every sort so the order is total and a page does
        not reshuffle between two requests that sort on equal values.
        """
        column = sort if sort in HISTORY_SORT_COLUMNS else DEFAULT_HISTORY_SORT
        direction = "DESC" if descending else "ASC"
        statement = (
            f"SELECT * FROM run_history ORDER BY {column} {direction}, run_id {direction}"
        )
        params: tuple[object, ...] = ()
        if limit is not None:
            statement += " LIMIT ?"
            params = (limit,)
        with self._lock:
            return self._conn.execute(statement, params).fetchall()

    def last_pool_for_repo(self, repo: str) -> str | None:
        """The coder instance this repo most recently ran on, if it ever has.

        Read from `repo_affinity`, not from `leases`, so the answer survives the
        lease being released — which is exactly the moment affinity is useful.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT coder_pool FROM repo_affinity WHERE repo = ?", (repo,)
            ).fetchone()
        return None if row is None else row["coder_pool"]

    def fetch_state(self, repo: str) -> RepoFetch | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT etag, last_attempt_at, last_success_at, last_error "
                "FROM repo_etag WHERE repo = ?",
                (repo,),
            ).fetchone()
        if row is None:
            return None
        return RepoFetch(
            repo=repo,
            etag=row["etag"],
            last_attempt_at=_parse_or_none(row["last_attempt_at"]),
            last_success_at=_parse_or_none(row["last_success_at"]),
            last_error=row["last_error"],
        )

    # --- writes -----------------------------------------------------------------

    def bump_override(
        self, repo: str, number: int, *, created_at: datetime | None = None
    ) -> int:
        """Mark one issue to be dispatched next, and return the rank it got.

        `MIN(override_rank) - 1` (`-1` when the table is empty), so the most recent
        bump is the smallest number and sorts first — `rank()` orders this tier
        ascending. The read and the write are one transaction under one lock: two
        operators clicking at the same instant must not both be handed `-1` for
        different issues, and the rank is what the response reports.
        """
        stamp = _stamp(created_at)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT MIN(override_rank) AS lowest FROM overrides"
            ).fetchone()
            lowest = None if row is None else row["lowest"]
            rank = -1 if lowest is None else int(lowest) - 1
            self._upsert_override(repo, number, rank, stamp)
        return rank

    def set_override(
        self,
        repo: str,
        number: int,
        override_rank: int,
        *,
        created_at: datetime | None = None,
    ) -> None:
        """Write one override rank verbatim. Upserts, so a re-set replaces it.

        The explicit-rank half of `bump_override`, kept separate because the two
        have different callers: this one is what a test (or a future "set the
        order" control) needs, and that one is what the UI's bump button needs.
        """
        with self._lock, self._conn:
            self._upsert_override(repo, number, override_rank, _stamp(created_at))

    def clear_override_on_dispatch(self, repo: str, number: int) -> bool:
        """Drop an issue's override, returning whether one was there.

        Called when the item is dispatched, because the mark means "next" and the
        item is no longer waiting for it. Returning the boolean is what lets the
        dispatch loop log the clear only when there was something to clear.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM overrides WHERE repo = ? AND issue_number = ?",
                (repo, number),
            )
        return cursor.rowcount > 0

    def requeue_count(self, repo: str, number: int) -> int:
        """How many times this issue has been requeued. `0` when never."""
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM requeue_counts WHERE repo = ? AND issue_number = ?",
                (repo, number),
            ).fetchone()
        return 0 if row is None else int(row["count"])

    def bump_requeue_count(
        self, repo: str, number: int, *, at: datetime | None = None
    ) -> int:
        """Record one more requeue for this issue, returning the new total.

        Read and write in one statement under one lock, so two paths requeueing
        the same issue cannot both read the same number and write it back.
        """
        stamp = _stamp(at)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO requeue_counts (repo, issue_number, count, last_at) "
                "VALUES (?, ?, 1, ?) "
                "ON CONFLICT(repo, issue_number) DO UPDATE SET "
                "  count = requeue_counts.count + 1, "
                "  last_at = excluded.last_at",
                (repo, number, stamp),
            )
            row = self._conn.execute(
                "SELECT count FROM requeue_counts WHERE repo = ? AND issue_number = ?",
                (repo, number),
            ).fetchone()
        return int(row["count"])

    def clear_requeue_count(self, repo: str, number: int) -> None:
        """Forget this issue's requeue history.

        Called on an ending that is not a requeue — the issue left the queue on its
        own terms, so the next time it appears it starts with a clean budget.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM requeue_counts WHERE repo = ? AND issue_number = ?",
                (repo, number),
            )

    def set_drained(self, coder_pool: str, drained: bool) -> None:
        """Put a coder instance in or out of rotation for *new* dispatch.

        The current lease is untouched and runs to completion — that is the whole
        difference between **Drain** and cancel (CONTEXT.md; decision 15). The row
        is written for the undrained case too, rather than deleted: the flag is the
        record, and an absent row and `drained = 0` are read the same way.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO pool_state (coder_pool, drained) VALUES (?, ?) "
                "ON CONFLICT(coder_pool) DO UPDATE SET drained = excluded.drained",
                (coder_pool, 1 if drained else 0),
            )

    def upsert_issue(self, issue: Issue) -> None:
        """Insert one item, or update it without disturbing `first_seen`."""
        with self._lock, self._conn:
            self._upsert_issue(issue)

    def sync_repo(
        self,
        repo: str,
        issues: Sequence[Issue],
        etag: str | None,
        fetched_at: datetime,
    ) -> None:
        """Replace one repo's cache with a `200` response's contents.

        The upsert runs **before** the delete, so an issue present in both the old
        and the new response keeps its original `first_seen` — it is updated in
        place rather than deleted and re-inserted. Only issues that are genuinely
        gone from the collection (closed, relabelled out of `agent`, converted to
        a PR) lose their row.
        """
        with self._lock, self._conn:
            for issue in issues:
                self._upsert_issue(issue)

            numbers = [issue.number for issue in issues]
            if numbers:
                placeholders = ",".join("?" * len(numbers))
                self._conn.execute(
                    f"DELETE FROM issue_cache WHERE repo = ? "
                    f"AND number NOT IN ({placeholders})",
                    (repo, *numbers),
                )
            else:
                self._conn.execute("DELETE FROM issue_cache WHERE repo = ?", (repo,))

            # A 200 carries the ETag for the next conditional request.
            self._conn.execute(
                "INSERT INTO repo_etag "
                "(repo, etag, last_attempt_at, last_success_at, last_error) "
                "VALUES (?, ?, ?, ?, NULL) "
                "ON CONFLICT(repo) DO UPDATE SET "
                "  etag = excluded.etag, "
                "  last_attempt_at = excluded.last_attempt_at, "
                "  last_success_at = excluded.last_success_at, "
                "  last_error = NULL",
                (repo, etag, fetched_at.isoformat(), fetched_at.isoformat()),
            )

    def note_not_modified(self, repo: str, etag: str | None, checked_at: datetime) -> None:
        """Record a `304`.

        A `304` is a success: it proves the cached contents are still current, so
        the attempt time, the success time and the clearing of any previous error
        are all the same as for a `200`. The issue rows and their `first_seen` are
        left exactly as they are.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO repo_etag "
                "(repo, etag, last_attempt_at, last_success_at, last_error) "
                "VALUES (?, ?, ?, ?, NULL) "
                "ON CONFLICT(repo) DO UPDATE SET "
                "  etag = COALESCE(excluded.etag, repo_etag.etag), "
                "  last_attempt_at = excluded.last_attempt_at, "
                "  last_success_at = excluded.last_success_at, "
                "  last_error = NULL",
                (repo, etag, checked_at.isoformat(), checked_at.isoformat()),
            )

    def note_failure(self, repo: str, attempted_at: datetime, error: str) -> None:
        """Record a failed attempt, keeping every cached item and the ETag.

        The ETag is kept on purpose: the next attempt should be conditional again,
        and a failed request is not evidence that the cached copy is wrong.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO repo_etag "
                "(repo, etag, last_attempt_at, last_success_at, last_error) "
                "VALUES (?, NULL, ?, NULL, ?) "
                "ON CONFLICT(repo) DO UPDATE SET "
                "  last_attempt_at = excluded.last_attempt_at, "
                "  last_error = excluded.last_error",
                (repo, attempted_at.isoformat(), error),
            )

    def release_lease(self, run_id: str) -> sqlite3.Row | None:
        """Drop the lease for `run_id`, returning the row it held (or `None`).

        The single delete, and the reason it reads before it deletes: draft 09's
        requeue needs the `queued_since` (and the repo/issue) off the row *after*
        the decision to release has been made, and the only safe way to hand them
        out is to read and delete in the same transaction so a concurrent
        dispatch cannot re-take the box in between. Returns `None` when `run_id`
        is not leased at all, which a recovery pass treats as already-released.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT coder_pool, repo, issue_number, run_id, dispatched_at, "
                "queued_since FROM leases WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
            return row

    def archive_and_release_lease(
        self, run_id: str, outcome: RunOutcome, *, at: datetime | None = None
    ) -> sqlite3.Row | None:
        """Record the run in `run_history` and drop its lease, in one transaction.

        The archiving counterpart to `release_lease`, and the only way a release
        should be taken once a run's ending is known. Returns the lease row exactly
        as `release_lease` does -- the requeue rule reads `queued_since` off it --
        or `None` when `run_id` is not leased, in which case nothing is written.

        One transaction is the whole point. The row and the lease are the same fact
        seen from two sides, and a crash between a delete and a separate insert
        would lose both.

        `at` is the release time, defaulting to now. It is what `finished_at`
        becomes when the outcome does not carry one, which is every path that has no
        run projection to read `timestamps.completed_at` from.
        """
        released = _stamp(at)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT coder_pool, repo, issue_number, run_id, dispatched_at, "
                "queued_since FROM leases WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "INSERT INTO run_history ("
                "  run_id, coder_pool, repo, issue_number, dispatched_at, finished_at,"
                "  kind, reason, category, requeue_attempt, pr_lookup, pr_number,"
                "  pr_url, merged"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO NOTHING",
                (
                    row["run_id"],
                    row["coder_pool"],
                    row["repo"],
                    row["issue_number"],
                    row["dispatched_at"],
                    (
                        released
                        if outcome.finished_at is None
                        else outcome.finished_at.isoformat()
                    ),
                    outcome.kind,
                    outcome.reason,
                    outcome.category,
                    outcome.requeue_attempt,
                    outcome.pr_lookup,
                    outcome.pr_number,
                    outcome.pr_url,
                    outcome.merged,
                ),
            )
            self._conn.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
            return row

    def forget_issue(self, repo: str, number: int) -> bool:
        """Drop one cached queue item. Returns whether a row was there to drop.

        The counterpart to `requeue`, and it exists for the release that does
        **not** requeue. Dispatch removes `agent` from the issue, so from that
        moment the cached row is stale by construction — but only the 60-second
        inventory poll notices, and the dispatch loop ticks every 5 seconds. While
        the lease is held that gap is harmless (one in-flight run per repo keeps
        the repo out of `choose_next`), and at the instant the lease is released it
        is exactly wrong: a run that ended agent-shaped would be dispatched again
        off a row describing a queue membership GitHub no longer has. So the row
        goes when the box does.

        Deliberately **not** used on the requeue path, and the difference is
        `first_seen`. This drops the wait along with the row, so an issue that is
        somehow still `agent`-labelled comes back on the next poll as a brand-new
        sighting. That is right here — every non-requeued ending (`mark_stuck`,
        `close_noop`, a merged PR, a cancel) leaves the issue out of the `agent`
        collection, so there is nothing to come back — and it is precisely wrong
        for a requeue, which is why `requeue` restores the row itself from
        `lease.queued_since` instead.
        """
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM issue_cache WHERE repo = ? AND number = ?",
                (repo, number),
            )
        return cursor.rowcount > 0

    def requeue(
        self,
        repo: str,
        number: int,
        *,
        first_seen: datetime | None = None,
    ) -> None:
        """Put an item back in the queue without resetting its wait.

        Draft 09: a requeued item must keep its original `first_seen` or its
        starvation ceiling restsarts, which makes decision 5's "nothing waits
        longer than `T`" quietly false for everything that ever failed. The
        caller hands it the lease's `queued_since`; when that is `None` (an
        orphan adopted with no lease to read it from) and the row does not
        already exist, the wait falls back to now.

        Idempotent: if the row is already in the cache (a manual-path lease
        never evicted it, so its `first_seen` is still there and authoritative)
        nothing is written at all. The re-inserted title/labels are a placeholder
        until the next inventory poll repopulates them from GitHub; what dispatch
        needs is `(repo, number)` and the wait.
        """
        existing = self.get_issue(repo, number)
        if existing is not None:
            return
        stamp = (
            first_seen.isoformat()
            if first_seen is not None
            else datetime.now(UTC).isoformat()
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO issue_cache "
                "(repo, number, title, labels, first_seen, last_seen) "
                "VALUES (?, ?, '', ?, ?, ?)",
                (repo, number, json.dumps(["agent"]), stamp, stamp),
            )

    def acquire_lease(
        self,
        *,
        coder_pool: str,
        repo: str,
        issue_number: int,
        run_id: str,
        dispatched_at: datetime,
        queued_since: datetime | None = None,
    ) -> bool:
        """Take one coder instance, or report that someone else holds it.

        Returns `False` when `coder_pool` is already leased and `True` when this
        call took it. `ON CONFLICT(coder_pool) DO NOTHING` is the whole mechanism:
        the conflict is resolved inside SQLite, in the same statement that would
        insert, so there is no read-then-write window for a second caller to slip
        through. A conflicting `run_id` still raises — that is a bug in the
        caller, not a race, and silently ignoring it would hide it.

        The affinity row is written in the same transaction and only on success,
        so "this repo last ran on this box" is never recorded for a lease that was
        refused.
        """
        stamp = dispatched_at.isoformat()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO leases "
                "(coder_pool, repo, issue_number, run_id, dispatched_at, queued_since) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(coder_pool) DO NOTHING",
                (
                    coder_pool,
                    repo,
                    issue_number,
                    run_id,
                    stamp,
                    None if queued_since is None else queued_since.isoformat(),
                ),
            )
            if cursor.rowcount == 0:
                return False
            self._conn.execute(
                "INSERT INTO repo_affinity (repo, coder_pool, recorded_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(repo) DO UPDATE SET "
                "  coder_pool = excluded.coder_pool, "
                "  recorded_at = excluded.recorded_at",
                (repo, coder_pool, stamp),
            )
            return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- internals --------------------------------------------------------------

    def _upsert_override(
        self, repo: str, number: int, override_rank: int, stamp: str
    ) -> None:
        """The one `overrides` upsert. Callers hold the lock and the transaction."""
        self._conn.execute(
            "INSERT INTO overrides (repo, issue_number, override_rank, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(repo, issue_number) DO UPDATE SET "
            "  override_rank = excluded.override_rank, "
            "  created_at = excluded.created_at",
            (repo, number, override_rank, stamp),
        )

    def _upsert_issue(self, issue: Issue) -> None:
        self._conn.execute(
            "INSERT INTO issue_cache (repo, number, title, labels, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(repo, number) DO UPDATE SET "
            "  title = excluded.title, "
            "  labels = excluded.labels, "
            "  last_seen = excluded.last_seen",
            (
                issue.repo,
                issue.number,
                issue.title,
                json.dumps(sorted(issue.labels)),
                issue.first_seen.isoformat(),
                # `fetch_issues` stamps `first_seen` with the observation time, so
                # for a fresh sighting this is "when we last saw it". The conflict
                # clause above is what keeps the *stored* `first_seen` intact.
                issue.first_seen.isoformat(),
            ),
        )


def _row_to_issue(row: sqlite3.Row) -> Issue:
    return Issue(
        repo=row["repo"],
        number=row["number"],
        title=row["title"],
        labels=frozenset(json.loads(row["labels"])),
        first_seen=datetime.fromisoformat(row["first_seen"]),
    )


def _parse_or_none(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _stamp(value: datetime | None) -> str:
    """An ISO-8601 UTC timestamp, defaulting to now."""
    return (datetime.now(UTC) if value is None else value).isoformat()
