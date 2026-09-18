"""The scheduler's SQLite state: the issue cache and the per-repo ETag record.

Two tables, both keyed by repo, both owned exclusively by this service. The
database is at `/data/scheduler.db` on the compose `scheduler-data` volume, so it
survives a restart — which matters most for `first_seen`: the starvation ceiling
is measured from when *this scheduler* first saw an issue, and a restart that
reset it would reset every item's wait.

Draft 08 adds a `leases` table here and draft 11 an `overrides` one. Nothing about
this file's shape is draft-06-specific except which tables exist today.

**Only a successful fetch writes.** A failed fetch records the failure against the
repo and leaves every cached issue row alone, which is the difference between a
stale page and a page that says "starvation" because GitHub had a bad minute.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
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
"""


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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- internals --------------------------------------------------------------

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
