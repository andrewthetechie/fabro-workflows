# Run history — Overview & canonical contracts

**Read this first.** Every task in this folder assumes the contracts recorded here.
`docs/adr/0008-scheduler-run-history-page.md` carries the decision and its reasoning.
This document carries the plan and the artifacts each task is written against.

## What this is

Seventeen tasks that give the coder scheduler a **run-history** page. Today the page
knows only the present — `queue_page` renders the queue, the repo statuses and the live
leases and nothing else (`ops/scheduler/src/fabro_scheduler/app.py:627-652`). When a run
goes terminal its lease row is deleted and the per-run facts go with it. After this
series the scheduler writes one durable row per released run and serves it at
`GET /history`.

The series is worked **in numeric order**. Several tasks have no genuine blocking edge
on their immediate predecessor — those edges are named honestly in each draft — but the
order above is the order to do them in.

ADR 0008 is `proposed`. Task 16 is what flips it to `accepted`, and nothing before task
15 touches the host.

## The nine decisions this series implements

Settled before decomposition. A task that appears to contradict one of these is wrong.

| # | Decision |
|---|---|
| 1 | **Persist at release, not on page load.** The scheduler answers the operator without fabro being up, which is exactly when an incident runs. |
| 2 | **The GitHub enrichment ships in v1**, by REST, with `state=all`. |
| 3 | **`requeue_attempt` is an integer**, not a boolean. |
| 4 | **A run fabro has lost (404) is recorded** as `kind="lost"`; a receipt with no lease writes **no row**. |
| 5 | **Server-side sort**, no meta-refresh on `/history`, no JavaScript. |
| 6 | **The insert shares the release transaction.** A crash must not lose the row and the lease together. |
| 7 | **`pr_lookup` is tri-state** — `"found"`, `"none"`, `"failed"` — so "no PR" and "GitHub was unreachable" never read alike. |
| 8 | **200 rows by default**, `?limit=all` as the escape hatch, sort applied in SQL **before** the limit. |
| 9 | **ADR 0008 stays standalone.** This series cites it; it does not cite this series. |

## The gate: the schema cannot be changed after it ships

`Store.__init__` runs `SCHEMA` through `executescript`
(`ops/scheduler/src/fabro_scheduler/store.py:200`), and `CREATE TABLE IF NOT EXISTS`
**will not add a column to a table that already exists**. The code says so twice, in the
two places it already cost something (`store.py:85-87`, `lease.py:37-39`). Once task 15
deploys, a new column is a hand-written `ALTER TABLE` against the live database on the
`scheduler-data` volume.

Task 01 therefore creates **every column the series will ever use**, including the four
that nothing writes until task 08. Do not trim it.

## The canonical schema

Task 01 adds exactly this to `SCHEMA` in `store.py`. Every later task is written against
it verbatim.

```sql
-- One row per released coder lease: what the run turned out to be. Written inside
-- the same transaction that deletes the lease, so a crash cannot lose the row and
-- the lease together (ADR 0008). Point-in-time: it records what the ending WAS,
-- not what became of the PR afterwards.
CREATE TABLE IF NOT EXISTS run_history (
  run_id          TEXT PRIMARY KEY,   -- always non-empty; the orphan path writes no row
  coder_pool      TEXT NOT NULL,      -- "coders-a" | "coders-b", as `leases.coder_pool`
  repo            TEXT NOT NULL,
  issue_number    INTEGER NOT NULL,
  dispatched_at   TEXT NOT NULL,      -- ISO-8601 UTC, copied from `leases.dispatched_at`
  finished_at     TEXT NOT NULL,      -- ISO-8601 UTC
  kind            TEXT NOT NULL,      -- "succeeded" | "failed" | "dead" | "lost"
  reason          TEXT,               -- lifecycle.status.reason, when there is one
  category        TEXT,               -- failure category, only when already read
  requeue_attempt INTEGER NOT NULL DEFAULT 0,
  pr_lookup       TEXT NOT NULL,      -- "found" | "none" | "failed"
  pr_number       INTEGER,
  pr_url          TEXT,
  merged          INTEGER             -- 0 | 1 | NULL
);
```

Two naming notes. `coder_pool` and `dispatched_at` carry the names `leases` already uses
(`store.py:88-95`); CONTEXT.md separates a **Coder instance** from a **Coder pool**, and
what is stored is the pool. `merged` is an `INTEGER` because Python's `sqlite3` adapts
`bool` to `0`/`1` natively — pass a `bool`, read back an `int`.

## The `RunOutcome` contract

Task 01 adds this dataclass to `store.py`, beside `RepoFetch`. It lives there, not in
`reconcile.py`, because `store.py` cannot import `reconcile.py` — `reconcile.py` already
imports `Store`.

```python
@dataclass(frozen=True)
class RunOutcome:
    """What a released run turned out to be, for its `run_history` row.

    Every field after `kind` has a default, so the paths that know less — a run
    fabro has lost, a release before the PR lookup exists — construct one without
    inventing facts they do not have.
    """

    kind: str                              # "succeeded" | "failed" | "dead" | "lost"
    reason: str | None = None
    category: str | None = None
    finished_at: datetime | None = None    # None -> the release time
    requeue_attempt: int = 0
    pr_lookup: str = "none"                # "found" | "none" | "failed"
    pr_number: int | None = None
    pr_url: str | None = None
    merged: bool | None = None
```

## The five release paths

`reconcile_leases` has four exits, not one. A fifth path releases nothing but reaches the
same `_requeue` helper, so a row written from a single assumed seam is wrong on two of
the five. Task 04 wires the three terminal ones, task 05 the fourth, task 06 guards the
fifth.

| Path | Anchor | Row |
|---|---|---|
| Terminal, not requeued | `reconcile.py:292` | full row |
| Terminal, requeued | `reconcile.py:368`, via `_requeue` | full row |
| Terminal, requeue budget spent | `reconcile.py:315` | full row |
| Fabro 404 — the run is lost | `reconcile.py:232-241` | `kind="lost"`, `reason="fabro 404"` |
| Receipt with no lease (orphan) — the **GitHub pass**, not `reconcile_leases` | `reconcile.py:494`, `:512-521` | **no row** |

The 404 path releases the lease with **no run projection at all**. There is no `kind`, no
`reason` and no `completed_at` to read, and the row is written from the lease alone.

The orphan path writes nothing because `_orphan_lease` synthesises
`Lease(coder_pool="", run_id="", ...)` for a GitHub receipt that no lease and no live run
accounts for. It has no run, no box and no dispatch time, and an empty `run_id` in a
table keyed on `run_id` would collide with the next one.

## Verified external contracts

Everything here was read from source or from the live deployment. Do not restate any of
it from memory in a task — quote this table.

### fabro's run projection

`GET /runs/{id}` returns `lifecycle.status.kind` and `lifecycle.status.reason`:

```json
{"lifecycle": {"status": {"kind": "succeeded", "reason": "completed"},
               "error": null, "archived": false}}
```

The terminal set is exactly `succeeded | failed | dead`
(`docs/scheduler/00-overview-and-contracts.md:176`, finding 9). There is no `cancelled`
kind — a cancel arrives as `failed` with `reason: "cancelled"`, one L.

`timestamps.completed_at` is `Option<DateTime<Utc>>`
(`context/fabro/lib/foundation/fabro-types/src/run_summary.rs:238-245`):

```rust
pub struct RunTimestamps {
    pub created_at:    DateTime<Utc>,
    #[serde(default)] pub started_at:    Option<DateTime<Utc>>,
    #[serde(default)] pub last_event_at: Option<DateTime<Utc>>,
    #[serde(default)] pub completed_at:  Option<DateTime<Utc>>,
}
```

It serialises as an ISO-8601 string or `null`, so read it as
`run["timestamps"]["completed_at"]` and fall back to the release time.

### The failure `category` is not always read

`_with_category` (`reconcile.py:324-337`) returns early when the status reason is
`terminated` or `cancelled`, because the reason already decides the requeue and the
category costs a second call (`GET /runs/{id}/events`). Those two are the fabro restart —
the canonical infra failure — and the operator cancel, so `category` is **null for the
most common failures by design**. Never add an unconditional events call to the release
pass to fill it in.

```python
REQUEUE_REASONS = frozenset({"terminated", "cancelled"})
INFRA_CATEGORY = "transient_infra"
MAX_REQUEUES = 3
```

### GitHub: the PR for a run branch

The head branch is `fabro/run/<run_id>`. It is on the remote because **fabro's checkpoint
publishes it after every stage**; `open_pr`'s own `git push -u origin HEAD` is always
`Everything up-to-date` (`.fabro/workflows/backlog/workflow.fabro:800-802`).

```
GET https://api.github.com/repos/{owner}/{repo}/pulls
    ?head={owner}:fabro/run/{run_id}&state=all&per_page=1
```

`state=all` is load-bearing. Both `gh pr list --head` and this endpoint default to
`state=open`, and since `54c21be` review-and-merge runs *inside* the backlog run, so a PR
that auto-merged is already closed before the run goes terminal. Defaulting to open would
null out `merged` for every successfully merged run.

Response is a JSON array. Read `number` (int), `html_url` (str) and `merged_at`
(ISO-8601 string or `null`); `merged` is `merged_at is not None`.

**There is no `gh` in the scheduler image.** The runtime stage installs `ca-certificates`
and `git` and nothing else (`ops/scheduler/Dockerfile:49`), confirmed live with
`docker exec fabro-scheduler sh -c 'command -v gh'` → `NO_GH`. The GitHub half of this
service is `httpx` throughout. Task 07 adds a third read function beside `fetch_issues`
and `fetch_in_progress` (`github.py:227`).

This adds `pull_requests: read` to the token requirement. The scheduler's `GITHUB_TOKEN`
comes from `gh auth token`, which covers it.

### The release poll's budget

```python
DEFAULT_RELEASE_INTERVAL_SECONDS = 15.0    # reconcile.py:133
REQUEST_TIMEOUT_SECONDS = 15.0             # github.py:53, the module default
```

The PR lookup carries its own **5s** timeout, not the module's 15s, because a call that
can consume a whole tick delays the release of the *other* instance's lease — the
scarcest resource the scheduler has. At roughly a dozen releases a day this is one
uncached request per release against the 5,000/hour budget.

## Repo conventions every task must follow

**`Store` holds one connection under one lock.** Every statement — reads included — runs
inside `with self._lock` (and `with self._conn` when it writes). The invariant is "one
statement at a time on this connection" (`store.py:170-200`).

**`store.py` returns `sqlite3.Row`, never a domain type.** `lease.py` imports `store.py`,
so a persistence layer that returned `Lease` would have to import back. The mapping lives
in `lease.py:168-187` (`_lease_from_row`).

**Tests are pytest 8 with `respx` for HTTP.** The command is:

```sh
cd ops/scheduler && uv run pytest
```

`[tool.pytest.ini_options] testpaths = ["tests"]`. The `Store` fixture that every store
test uses, copied from `tests/test_reconcile.py:60-65`:

```python
@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()
```

**Deployment is task 15 and nothing before it.** The scheduler tree is rsynced to
`~/fabro/scheduler/` and rebuilt with `docker compose up -d --build scheduler`, which
names one service and leaves `fabro` alone — a fabro restart fails every in-flight run.
AGENTS.md carries the exact sequence.

## The task series

| # | Task | Blocked by |
|---|---|---|
| 01 | The `run_history` table and `RunOutcome` | — |
| 02 | `archive_and_release_lease`: the atomic release seam | 01 |
| 03 | `_outcome_from_run`: a `RunOutcome` from a terminal projection | 01 |
| 04 | Write a row on every terminal release | 02, 03 |
| 05 | Record the run fabro lost | 04 |
| 06 | Guard: the orphan path writes no row | 04 |
| 07 | `fetch_pull_for_branch` in `github.py` | — |
| 08 | Resolve the PR at release | 04, 07 |
| 09 | `Store.history_rows`: sorted, limited reads | 01 |
| 10 | `GET /api/history` | 09 |
| 11 | `GET /history` and the page | 09 |
| 12 | Sortable header links | 11 |
| 13 | Nav links between the queue and the history | 11 |
| 14 | Derived final-state labels | 11 |
| 15 | Deploy and verify on the host | 05, 06, 08, 10, 12, 13, 14 |
| 16 | Correct the docs and accept ADR 0008 | 15 |
| 17 | Append the deployment-log section | 15 |

## Status

Nothing in this series is built. ADR 0008 is `proposed`.
