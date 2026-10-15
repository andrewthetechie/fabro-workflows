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
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .github import Issue
from .prfacts import PrFacts
from .summary import RunSummary
from .usage import ModelUsage

log = logging.getLogger(__name__)

# How many times the collector tries to summarize a run whose events could not be
# read before it gives up on it. A 404 is not retried at all (`missing`).
SUMMARY_MAX_ATTEMPTS = 3

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
--
-- The four usage columns are token counts and costs at release time, split by
-- where the tokens were spent: `local` (the LAN box providers `box-a`/`box-b`/
-- `spark`) versus `hosted` (zai/kimi/moonshot etc.). They are NULL when fabro
-- could not answer `/runs/{id}/usage` at release (— on the page, like a PR
-- lookup that failed). Cost is in US dollars × 1e6 (micros) so it stays an
-- integer and never drifts through a float.
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
  merged          INTEGER,        -- 0 | 1 | NULL
  local_tokens    INTEGER,        -- token counts at release; NULL = no usage data
  hosted_tokens   INTEGER,
  local_cost_usd_micros  INTEGER,
  hosted_cost_usd_micros INTEGER
);

-- Issue #22, the scorecard. Five tables the collector fills after a run is released
-- (`collector.py`), and nothing else writes. They are derived data: dropping them
-- loses nothing that the fabro run store and GitHub cannot rebuild, which the
-- collector does on its own (every `run_history` row without a summary is work).
--
-- `run_summary` is one row per summarized run, and its `status` is the collector's
-- bookkeeping: `ok`, `missing` (fabro answered 404, so the run was pruned; never
-- retried) or `error` (retried up to `attempts` times).
CREATE TABLE IF NOT EXISTS run_summary (
  run_id        TEXT PRIMARY KEY,
  status        TEXT    NOT NULL,
  attempts      INTEGER NOT NULL DEFAULT 1,
  summarized_at TEXT    NOT NULL,  -- ISO-8601 UTC
  terminal_node TEXT,              -- the last node the run started, other than exit
  path          TEXT,              -- JSON array of "from>to": the run's last edges
  refute        TEXT,              -- JSON array of {verdict, not_met, unverifiable_only, ci_credited}, in order
  events        INTEGER,
  error         TEXT,
  first_edit_ms TEXT,              -- JSON array: per coder attempt, ms to its first edit
  dispositions  TEXT,              -- JSON object: improve_gate disposition -> count
  task_validations TEXT,           -- JSON array: per `validate` visit {mode, reason_class, ok, ms}
  task_validation_unchecked INTEGER, -- summed task_validation_unchecked over validate visits
  task_validation_untested INTEGER, -- summed task_validation_untested over validate visits
  final_routes  TEXT               -- JSON array: each open_pr_prep visit's final_route, in order
);

-- One row per node per run: every visit's totals. `box_ms` is the part of
-- `wall_ms` spent on the run's coder pool (`summary.CODER_MODEL_PREFIX`).
CREATE TABLE IF NOT EXISTS run_stage_stats (
  run_id   TEXT    NOT NULL,
  node_id  TEXT    NOT NULL,
  handler  TEXT    NOT NULL,
  visits   INTEGER NOT NULL,
  wall_ms  INTEGER NOT NULL,
  box_ms   INTEGER NOT NULL,
  timeouts INTEGER NOT NULL,
  failures TEXT    NOT NULL,       -- JSON object, failure category -> count
  input_tokens      INTEGER,       -- summed over visits; NULL = summarized before #25
  output_tokens     INTEGER,       -- output plus reasoning
  cache_read_tokens INTEGER,
  PRIMARY KEY (run_id, node_id)
);

CREATE TABLE IF NOT EXISTS run_failovers (
  run_id     TEXT    NOT NULL,
  node_id    TEXT    NOT NULL,
  from_model TEXT    NOT NULL,     -- "kimi/kimi-k3"
  to_model   TEXT    NOT NULL,     -- "zai/glm-5.3"
  error_kind TEXT    NOT NULL,     -- the provider error's llm_kind: "access_denied"
  count      INTEGER NOT NULL,
  PRIMARY KEY (run_id, node_id, from_model, to_model, error_kind)
);

-- One row per model per run: `GET /runs/{id}/usage`'s `by_model`, kept whole for the
-- Metrics tab's "Tokens by model" table (`usage.model_rows`). Filled by the collector
-- after the run is summarized; `run_summary.model_usage` says whether it has been read.
-- `output_tokens` includes reasoning. `cost_usd_micros` is NULL when fabro priced none of it.
CREATE TABLE IF NOT EXISTS run_model_usage (
  run_id             TEXT    NOT NULL,
  provider           TEXT    NOT NULL,  -- "zai", "box-a", "kimi"
  model_id           TEXT    NOT NULL,  -- "glm-5.3"
  stages             INTEGER NOT NULL,  -- usage-bearing stage visits on this model
  input_tokens       INTEGER NOT NULL,
  output_tokens      INTEGER NOT NULL,
  cache_read_tokens  INTEGER NOT NULL,
  cache_write_tokens INTEGER NOT NULL,
  cost_usd_micros    INTEGER,
  PRIMARY KEY (run_id, provider, model_id)
);

-- What became of each factory PR (`prfacts.py`). Refetched while `state` is
-- `open`; a merged or closed PR is final and is not fetched again.
CREATE TABLE IF NOT EXISTS pr_facts (
  repo             TEXT    NOT NULL,
  pr_number        INTEGER NOT NULL,
  fetched_at       TEXT    NOT NULL,  -- ISO-8601 UTC
  state            TEXT    NOT NULL,  -- "open" | "merged" | "closed"
  created_at       TEXT,
  merged_at        TEXT,
  closed_at        TEXT,
  labels           TEXT    NOT NULL,  -- JSON array of label names
  additions        INTEGER NOT NULL,
  deletions        INTEGER NOT NULL,
  commits          INTEGER NOT NULL,
  operator_commits INTEGER NOT NULL,
  report           TEXT,              -- the last merge-phase report's kind
  reason           TEXT,              -- its **Reason:** line, when it has one
  queue_approved   INTEGER NOT NULL,  -- 0 | 1: the merge phase queued it (ADR 0024)
  queue_handoff    INTEGER NOT NULL,  -- 0 | 1: the merge queue then handed it off
  PRIMARY KEY (repo, pr_number)
);
"""

# Columns added to `run_history` after the table already exists. `CREATE TABLE IF
# NOT EXISTS` will not add a column, and the table ships on the host's volume, so
# `_apply_migrations` ALTERs these in on open. Keeping the DDL here (rather than
# inline in the migration) means the fresh-table shape and the migrated shape
# cannot diverge.
RUN_HISTORY_COLUMNS = {
    "local_tokens": "local_tokens INTEGER",
    "hosted_tokens": "hosted_tokens INTEGER",
    "local_cost_usd_micros": "local_cost_usd_micros INTEGER",
    "hosted_cost_usd_micros": "hosted_cost_usd_micros INTEGER",
}

# The same, for the scorecard tables: the plan trial (#25) added per-stage tokens and
# the coder's time to first edit. A run summarized before them keeps NULLs, which the
# scorecard reads as "not measured", never as zero.
ADDED_COLUMNS = {
    "run_history": RUN_HISTORY_COLUMNS,
    "run_stage_stats": {
        "input_tokens": "input_tokens INTEGER",
        "output_tokens": "output_tokens INTEGER",
        "cache_read_tokens": "cache_read_tokens INTEGER",
    },
    "run_summary": {
        "first_edit_ms": "first_edit_ms TEXT",
        "dispositions": "dispositions TEXT",
        # Task validation (ADR 0025). Kept as JSON so the whole visit list fits one row;
        # a run summarized before it keeps NULLs, read as "not measured".
        "task_validations": "task_validations TEXT",
        "task_validation_unchecked": "task_validation_unchecked INTEGER",
        "task_validation_untested": "task_validation_untested INTEGER",
        "final_routes": "final_routes TEXT",
        # Whether `run_model_usage` has been read for the run: NULL (not yet), `ok`,
        # `none` (fabro reported no model usage), `missing` (404) or `error` (retried).
        "model_usage": "model_usage TEXT",
        "model_usage_at": "model_usage_at TEXT",
    },
}


# The columns `/history` may be sorted by. This is a WHITELIST, not documentation:
# SQLite cannot parameterise an ORDER BY column, so the name is interpolated into
# the statement and membership here is the only thing between a URL query string
# and arbitrary SQL. Adding a column to `run_history` does not add it here.
HISTORY_SORT_COLUMNS = frozenset({
    "finished_at",
    "repo",
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
    # Usage at release, split local (LAN boxes) vs hosted. None = fabro had no
    # usage answer, rendered as "—" on the history page.
    local_tokens: int | None = None
    hosted_tokens: int | None = None
    local_cost_usd_micros: int | None = None
    hosted_cost_usd_micros: int | None = None


@dataclass(frozen=True)
class ScorecardRows:
    """The raw rows one scorecard window is computed from (`scorecard.compute`)."""

    history: list[sqlite3.Row]
    summaries: list[sqlite3.Row]
    stages: list[sqlite3.Row]
    failovers: list[sqlite3.Row]
    prs: list[sqlite3.Row]
    models: list[sqlite3.Row] = field(default_factory=list)  # run_model_usage


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
        self._apply_migrations()
        self._conn.commit()

    def _apply_migrations(self) -> None:
        """Add columns introduced after their table first shipped (`ADDED_COLUMNS`).

        `CREATE TABLE IF NOT EXISTS` never alters an existing table, and the real
        database lives on the `scheduler-data` volume, so every added column has
        to be `ALTER TABLE ... ADD COLUMN`ed here. A missing column is the only
        case that runs, and `ADD COLUMN` is idempotent per column under the
        `table_info` guard. String interpolation is safe: the table names and keys
        come from the static `ADDED_COLUMNS`, never from a caller.
        """
        with self._lock:
            for table, columns in ADDED_COLUMNS.items():
                present = {
                    row["name"]
                    for row in self._conn.execute(f"PRAGMA table_info({table})")
                }
                for name, ddl in columns.items():
                    if name not in present:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


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

    def set_queue_order(self, ordered: Sequence[tuple[str, int]]) -> None:
        """Persist an explicit queue order for the given items, in one transaction.

        `ordered` is `(repo, number)` in the order they should appear; each gets
        `override_rank` equal to its index (0, 1, 2, …, so the front sorts first,
        the same ascending rule every override tier already uses). `rank()` puts
        everything with a rank before everything without one, so this is how an
        operator takes manual control of the queue's front: the items listed here
        become the ranked prefix in exactly this order, and anything not listed
        (a freshly arrived issue) stays unranked and sorts after them by the
        computed tiers.

        One write under one lock so a drag and a dispatch-facing clear cannot
        interleave and leave a half-applied order.
        """
        stamp = _stamp(datetime.now(UTC))
        with self._lock, self._conn:
            for rank, (repo, number) in enumerate(ordered):
                self._upsert_override(repo, number, rank, stamp)

    def avg_run_duration(self, repo: str) -> timedelta | None:
        """Mean wall duration of released runs for one repo, or `None` with none.

        Feeds the queue page's time-left estimate (feature: ETA). An average over
        the repo's own history is more predictive than a global one — waste-tool
        and womens-fantasy-sports do not run in the same time. Returns `None`,
        not an error, when the repo has no released runs yet, so the caller can
        fall back to its next-best estimate (see `queue_page`).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT finished_at, dispatched_at FROM run_history "
                "WHERE repo = ? AND finished_at IS NOT NULL AND dispatched_at IS NOT NULL",
                (repo,),
            ).fetchall()
        total = timedelta(0)
        count = 0
        for row in rows:
            try:
                started = datetime.fromisoformat(row["dispatched_at"])
                ended = datetime.fromisoformat(row["finished_at"])
            except (TypeError, ValueError):
                continue
            total += ended - started
            count += 1
        return total / count if count else None


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
                "  pr_url, merged, local_tokens, hosted_tokens,"
                "  local_cost_usd_micros, hosted_cost_usd_micros"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
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
                    outcome.local_tokens,
                    outcome.hosted_tokens,
                    outcome.local_cost_usd_micros,
                    outcome.hosted_cost_usd_micros,
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
        the lease is held that gap is harmless (`choose_next`'s running-keys guard
        excludes the leased repo#issue from its saturation pass), and at the
        instant the lease is released it
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

    # --- scorecard (issue #22) ----------------------------------------------------

    def runs_to_summarize(
        self,
        *,
        finished_before: datetime,
        retry_before: datetime,
        limit: int,
        max_attempts: int = SUMMARY_MAX_ATTEMPTS,
    ) -> list[str]:
        """Released runs the collector has not summarized yet, newest first.

        Newest first so the current week fills before the backfill reaches old ones.
        A run is left alone until `finished_before`, giving fabro a moment to
        write its last events. An `error` row is retried once its last attempt is
        older than `retry_before`, up to `max_attempts`; a `missing` row never is.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT h.run_id FROM run_history h "
                "LEFT JOIN run_summary s ON s.run_id = h.run_id "
                "WHERE h.finished_at <= ? AND ("
                "  s.run_id IS NULL OR "
                "  (s.status = 'error' AND s.attempts < ? AND s.summarized_at <= ?)"
                ") ORDER BY h.finished_at DESC, h.run_id DESC LIMIT ?",
                (finished_before.isoformat(), max_attempts, retry_before.isoformat(), limit),
            ).fetchall()
        return [row["run_id"] for row in rows]

    def save_run_summary(self, summary: RunSummary, *, at: datetime | None = None) -> None:
        """Replace one run's summary rows, in one transaction."""
        stamp = _stamp(at)
        refute = [
            {
                "verdict": verdict.verdict,
                "not_met": verdict.not_met,
                "unverifiable_only": verdict.unverifiable_only,
                "ci_credited": verdict.ci_credited,
            }
            for verdict in summary.refute
        ]
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM run_stage_stats WHERE run_id = ?", (summary.run_id,))
            self._conn.execute("DELETE FROM run_failovers WHERE run_id = ?", (summary.run_id,))
            self._conn.executemany(
                "INSERT INTO run_stage_stats (run_id, node_id, handler, visits, wall_ms,"
                " box_ms, timeouts, failures, input_tokens, output_tokens, cache_read_tokens)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        summary.run_id,
                        stat.node_id,
                        stat.handler,
                        stat.visits,
                        stat.wall_ms,
                        stat.box_ms,
                        stat.timeouts,
                        json.dumps(dict(sorted(stat.failures.items()))),
                        stat.input_tokens,
                        stat.output_tokens,
                        stat.cache_read_tokens,
                    )
                    for stat in summary.stages.values()
                ],
            )
            self._conn.executemany(
                "INSERT INTO run_failovers (run_id, node_id, from_model, to_model,"
                " error_kind, count) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (summary.run_id, node, src, dst, kind, count)
                    for (node, src, dst, kind), count in summary.failovers.items()
                ],
            )
            self._conn.execute(
                "INSERT INTO run_summary (run_id, status, attempts, summarized_at,"
                " terminal_node, path, refute, events, error, first_edit_ms, dispositions,"
                " task_validations, task_validation_unchecked, task_validation_untested,"
                " final_routes)"
                " VALUES (?, 'ok', 1, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET status = 'ok',"
                "  attempts = run_summary.attempts + 1, summarized_at = excluded.summarized_at,"
                "  terminal_node = excluded.terminal_node, path = excluded.path,"
                "  refute = excluded.refute, events = excluded.events, error = NULL,"
                "  first_edit_ms = excluded.first_edit_ms, dispositions = excluded.dispositions,"
                "  task_validations = excluded.task_validations,"
                "  task_validation_unchecked = excluded.task_validation_unchecked,"
                "  task_validation_untested = excluded.task_validation_untested,"
                "  final_routes = excluded.final_routes",
                (
                    summary.run_id,
                    stamp,
                    summary.terminal_node,
                    json.dumps(summary.path),
                    json.dumps(refute),
                    summary.events,
                    json.dumps(summary.first_edit_ms),
                    json.dumps(dict(sorted(summary.dispositions.items()))),
                    json.dumps([asdict(visit) for visit in summary.task_validations]),
                    summary.task_validation_unchecked,
                    summary.task_validation_untested,
                    json.dumps(summary.final_routes),
                ),
            )

    def save_summary_failure(
        self, run_id: str, status: str, error: str, *, at: datetime | None = None
    ) -> None:
        """Record that a run could not be summarized: `missing` or `error`."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO run_summary (run_id, status, attempts, summarized_at, error)"
                " VALUES (?, ?, 1, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status,"
                "  attempts = run_summary.attempts + 1,"
                "  summarized_at = excluded.summarized_at, error = excluded.error",
                (run_id, status, _stamp(at), error[:500]),
            )

    def resummarize_window(self, start: datetime, end: datetime) -> tuple[int, int, int]:
        """Drop the summaries of the runs dispatched in `[start, end)`, for a rebuild.

        The collector's only entry point (`runs_to_summarize`) is a `LEFT JOIN` on
        `run_summary`, so deleting the summary rows is what puts a window back in the
        queue; it streams the events again and writes the current fields. Returns the
        number of runs in the window and the `run_summary` / `run_stage_stats` rows
        deleted. `run_history`, the leases and the PR facts are never touched: they are
        the window's own record, not a summary of it.
        """
        bounds = (start.isoformat(), end.isoformat())
        with self._lock, self._conn:
            run_ids = [
                row["run_id"]
                for row in self._conn.execute(
                    "SELECT run_id FROM run_history WHERE dispatched_at >= ? AND"
                    " dispatched_at < ?",
                    bounds,
                )
            ]
            if not run_ids:
                return 0, 0, 0
            placeholders = ",".join("?" * len(run_ids))
            summaries = self._conn.execute(
                f"DELETE FROM run_summary WHERE run_id IN ({placeholders})", run_ids
            ).rowcount
            stages = self._conn.execute(
                f"DELETE FROM run_stage_stats WHERE run_id IN ({placeholders})", run_ids
            ).rowcount
        return len(run_ids), summaries, stages

    def runs_to_read_models(self, *, retry_before: datetime, limit: int) -> list[str]:
        """Summarized runs whose model usage has not been read, newest first.

        Only `ok` summaries: a `missing` run is gone from fabro, and an `error` one is
        read once its summary succeeds. An `error` read is retried once its last
        attempt is older than `retry_before`. One small GET per run, so the whole
        history backfills within a few ticks of a deploy.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.run_id FROM run_summary s JOIN run_history h ON h.run_id = s.run_id "
                "WHERE s.status = 'ok' AND (s.model_usage IS NULL OR "
                "  (s.model_usage = 'error' AND s.model_usage_at <= ?)) "
                "ORDER BY h.finished_at DESC, h.run_id DESC LIMIT ?",
                (retry_before.isoformat(), limit),
            ).fetchall()
        return [row["run_id"] for row in rows]

    def save_model_usage(
        self, run_id: str, rows: Sequence[ModelUsage], *, at: datetime | None = None
    ) -> None:
        """Replace one run's model rows and mark it read (`ok`, or `none` if empty)."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM run_model_usage WHERE run_id = ?", (run_id,))
            self._conn.executemany(
                "INSERT INTO run_model_usage (run_id, provider, model_id, stages, input_tokens,"
                " output_tokens, cache_read_tokens, cache_write_tokens, cost_usd_micros)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        row.provider,
                        row.model_id,
                        row.stages,
                        row.input_tokens,
                        row.output_tokens,
                        row.cache_read_tokens,
                        row.cache_write_tokens,
                        row.cost_usd_micros,
                    )
                    for row in rows
                ],
            )
            self._conn.execute(
                "UPDATE run_summary SET model_usage = ?, model_usage_at = ? WHERE run_id = ?",
                ("ok" if rows else "none", _stamp(at), run_id),
            )

    def save_model_usage_failure(
        self, run_id: str, status: str, *, at: datetime | None = None
    ) -> None:
        """Record that a run's model usage could not be read: `missing` or `error`."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE run_summary SET model_usage = ?, model_usage_at = ? WHERE run_id = ?",
                (status, _stamp(at), run_id),
            )

    def prs_to_refresh(self, *, stale_before: datetime, limit: int) -> list[tuple[str, int]]:
        """Factory PRs whose facts are missing, or open and older than `stale_before`.

        Never-fetched PRs first, then the stalest open ones. A merged or closed PR
        is final and is never returned again.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT h.repo, h.pr_number, MAX(h.finished_at) AS last_finished,"
                "  f.fetched_at FROM run_history h "
                "LEFT JOIN pr_facts f ON f.repo = h.repo AND f.pr_number = h.pr_number "
                "WHERE h.pr_lookup = 'found' AND h.pr_number IS NOT NULL "
                "  AND (f.repo IS NULL OR (f.state = 'open' AND f.fetched_at <= ?)) "
                "GROUP BY h.repo, h.pr_number "
                "ORDER BY f.fetched_at IS NOT NULL, f.fetched_at, last_finished DESC "
                "LIMIT ?",
                (stale_before.isoformat(), limit),
            ).fetchall()
        return [(row["repo"], row["pr_number"]) for row in rows]

    def save_pr_facts(
        self, repo: str, number: int, facts: PrFacts, *, at: datetime | None = None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO pr_facts (repo, pr_number, fetched_at, state, created_at,"
                " merged_at, closed_at, labels, additions, deletions, commits,"
                " operator_commits, report, reason, queue_approved, queue_handoff)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(repo, pr_number) DO UPDATE SET"
                "  fetched_at = excluded.fetched_at, state = excluded.state,"
                "  created_at = excluded.created_at, merged_at = excluded.merged_at,"
                "  closed_at = excluded.closed_at, labels = excluded.labels,"
                "  additions = excluded.additions, deletions = excluded.deletions,"
                "  commits = excluded.commits, operator_commits = excluded.operator_commits,"
                "  report = excluded.report, reason = excluded.reason,"
                "  queue_approved = excluded.queue_approved,"
                "  queue_handoff = excluded.queue_handoff",
                (
                    repo,
                    number,
                    _stamp(at),
                    facts.state,
                    facts.created_at,
                    facts.merged_at,
                    facts.closed_at,
                    json.dumps(list(facts.labels)),
                    facts.additions,
                    facts.deletions,
                    facts.commits,
                    facts.operator_commits,
                    facts.report,
                    facts.reason,
                    int(facts.queue_approved),
                    int(facts.queue_handoff),
                ),
            )

    def scorecard_rows(self, start: datetime, end: datetime) -> ScorecardRows:
        """Everything the scorecard reads for runs dispatched in `[start, end)`.

        Six SELECTs under one lock, so the window is one consistent snapshot.
        """
        bounds = (start.isoformat(), end.isoformat())
        in_window = "SELECT run_id FROM run_history WHERE dispatched_at >= ? AND dispatched_at < ?"
        with self._lock:
            history = self._conn.execute(
                "SELECT * FROM run_history WHERE dispatched_at >= ? AND dispatched_at < ? "
                "ORDER BY dispatched_at",
                bounds,
            ).fetchall()
            summaries = self._conn.execute(
                f"SELECT * FROM run_summary WHERE run_id IN ({in_window})", bounds
            ).fetchall()
            stages = self._conn.execute(
                f"SELECT * FROM run_stage_stats WHERE run_id IN ({in_window})", bounds
            ).fetchall()
            failovers = self._conn.execute(
                f"SELECT * FROM run_failovers WHERE run_id IN ({in_window})", bounds
            ).fetchall()
            prs = self._conn.execute(
                "SELECT f.* FROM pr_facts f WHERE EXISTS (SELECT 1 FROM run_history h"
                "  WHERE h.repo = f.repo AND h.pr_number = f.pr_number"
                "  AND h.dispatched_at >= ? AND h.dispatched_at < ?)",
                bounds,
            ).fetchall()
            models = self._conn.execute(
                f"SELECT * FROM run_model_usage WHERE run_id IN ({in_window})", bounds
            ).fetchall()
        return ScorecardRows(history, summaries, stages, failovers, prs, models)

    def first_dispatch(self) -> datetime | None:
        """When the oldest `run_history` row was dispatched, or None if there is none."""
        with self._lock:
            row = self._conn.execute("SELECT MIN(dispatched_at) AS d FROM run_history").fetchone()
        return _parse_or_none(row["d"]) if row is not None else None

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
