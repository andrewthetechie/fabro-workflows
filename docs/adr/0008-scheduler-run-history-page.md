# The scheduler keeps a run-history page, persisted at release time

**Status:** accepted (2026-09-20), applied 2026-09-20

The scheduler's web page is the operator's window onto admission, but it only knows the
*present*: `queue_page` renders the queue, the repo statuses and the live leases and
nothing else (`ops/scheduler/src/fabro_scheduler/app.py:627-652`). When a run goes
terminal its lease row is deleted, and the per-run facts go with it — which box ran it,
how long it took, how it ended. Only two traces survive, and neither answers the
question: `repo_affinity` keeps the box that last ran each repo (`store.py:99`), and
`requeue_count` keeps an attempt tally until it is cleared. The rest of the deployment
asks "what has the factory been doing", and the only answer today is to reconstruct it
from fabro's store and the deployment log by hand, ssh session required.

We will add a **run-history** page — a second route, `GET /history`, reached from a nav
link that `queue.html` does not have yet and will need — built from a new SQLite
`run_history` table written when the scheduler releases a coder lease.

## The table

The schema is the one irreversible decision here. `Store.__init__` runs `SCHEMA` through
`executescript` (`store.py:200`), and `CREATE TABLE IF NOT EXISTS` **will not add a
column to a table that already exists** — the constraint that forced `queued_since` into
`leases` a draft early (`store.py:85-87`, `lease.py:37-39`). Once this ships, a new
column is a hand-written `ALTER TABLE` against the live database on the `scheduler-data`
volume. Every column is therefore decided now, including the ones nothing reads yet.

```sql
CREATE TABLE IF NOT EXISTS run_history (
  run_id          TEXT PRIMARY KEY,   -- always non-empty; see the release paths below
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

`coder_pool` and `dispatched_at` carry the names the `leases` table already uses
(`store.py:88-95`). CONTEXT.md separates a **Coder instance** from a **Coder pool**;
what is stored is the pool, and today each of `coders-a`/`coders-b` addresses exactly one
instance.

`finished_at` is fabro's `timestamps.completed_at`, which is
`Option<DateTime<Utc>>` in the projection
(`context/fabro/lib/foundation/fabro-types/src/run_summary.rs:245`), falling back to
release time when it is absent — which it always is on the lost-run path below.

`category` is **not** present for every failure, and the ADR does not ask for it to be.
`_with_category` returns early when the status reason is `terminated` or `cancelled`
(`reconcile.py:324-337`), because the reason already decides the requeue and the category
costs a second call — `GET /runs/{id}/events`. Those two are the fabro restart (the
canonical infra failure, overview finding 10) and the operator cancel, so `category` will
be null for the most common failures by design. Reading it unconditionally would add an
events call to the release pass, which is the cost that code was written to avoid. The
derived label falls back to `reason`.

`requeue_attempt` is the integer, not a boolean. `reconcile.py` already tracks it against
`MAX_REQUEUES = 3`, so it is free at release time, and "is this issue burning its budget"
is the question that precedes a human being called. `> 0` recovers the boolean at render
time.

## The release paths

`reconcile_leases` has four exits, not one, and they do not all hold the same facts. A
fifth path releases nothing but reaches the same `_requeue` helper, so a row written from
a single assumed seam would be wrong on two of the five.

| Path | Anchor | Row |
|---|---|---|
| Terminal, not requeued | `reconcile.py:292` | full row, `requeue_attempt` as counted |
| Terminal, requeued | `reconcile.py:368`, via `_requeue` | full row |
| Terminal, requeue budget spent | `reconcile.py:315` | full row |
| Fabro 404 — the run is lost | `reconcile.py:232-241` | `kind = "lost"`, `reason = "fabro 404"`, `finished_at` = release time |
| Receipt with no lease (orphan) — the GitHub pass, not `reconcile_leases` | `reconcile.py:494`, `:512-521` | **no row** |

The 404 path releases the lease with **no run projection at all**, so there is no `kind`,
no `reason` and no `completed_at` to write. It gets a row anyway, under its own `kind`: a
run fabro lost still held a coder box for however long it held the lease, and that is
exactly the fact this page exists to stop reconstructing by hand.

The orphan path writes nothing. `_orphan_lease` synthesises `Lease(coder_pool="",
run_id="", ...)` for a GitHub receipt that no lease and no live run accounts for
(`reconcile.py:512-521`); it has no run, no box and no dispatch time, and an empty
`run_id` in a table keyed on `run_id` would collide with the next one.

## Persist at release, rather than query fabro on page load

The scheduler exists because fabro's own run queue cannot be steered, and its job is to
answer the operator's questions *without* fabro having to be up — which is exactly when
an incident runs. A fabro restart fails everything in flight (overview finding 5), so a
history page that goes blank during the incident it exists to explain would be
self-defeating.

The release pass is where a run's terminal state is already known, so writing the row
there adds one insert to work the scheduler does anyway. Building the table from
`GET /runs` on demand instead would depend on fabro being healthy, hit its paginated,
limit-clamped run listing — 100 per page, and a bare `limit` is silently clamped to 20
(`fabro.py:354-355`, overview finding 11) — and lose history the moment an operator
prunes runs, which fabro supports (`/system/prune/runs`,
`context/fabro/lib/apps/fabro-server/src/server.rs:2197`).

**The insert shares the release transaction.** `Store.release_lease` reads the row and
deletes it inside one transaction precisely so the lease's facts cannot be lost between
the two steps (`store.py:521-540`). The history row inherits that exposure: a process
that dies between the delete and a separate insert loses the row *and* the lease, leaving
nothing to reconstruct it from. So this is not a new call beside the release — it is
`Store.archive_and_release_lease(run_id, *, kind, reason, finished_at, ...)`, one
transaction. The cost is that `store.py` gains knowledge of terminal run facts it does
not have today; that is where every other durable fact the scheduler owns already lives.

## Scheduler-dispatched backlog runs only

A lease is the only place the scheduler has all the columns at once — coder pool,
dispatch time, issue, run. A **Manual fire** takes no lease by deliberate design
(CONTEXT.md), and hand-fired `pr-review` runs never had one; since `54c21be` no
`pr-review` run is created by `backlog` at all, because review-and-merge is spliced into
the backlog run through `_shared/review-merge/` (CONTEXT.md, **Bridge** *(retired)*).

Those runs stay out of this page, and this is a real gap rather than a relocation. They
are **not** recorded anywhere durable: this repository's own conclusion is that "the API
has almost no run history — do not pretend otherwise"
(`docs/turn-it-on/04-user-guide.md:20`). The gap is accepted — a manual fire is the
escape hatch for when the scheduler is down, and a history page that only the scheduler
writes cannot cover the case where the scheduler is not running.

## Merged state is a snapshot taken at release

`merged` is not in fabro's projection. Fabro reports `succeeded | failed | dead` plus a
reason (overview finding 9), and "succeeded but deliberately not auto-merged" is a real
ending — a risk-4 block ends the run `succeeded`. Knowing whether the PR merged needs one
GitHub call per release, resolved by the PR's head branch, `fabro/run/<run_id>`. That
branch is on the remote because **fabro's checkpoint publishes it after every stage**;
`open_pr`'s own `git push -u origin HEAD` is always `Everything up-to-date`
(`.fabro/workflows/backlog/workflow.fabro:800-802`).

**The call is REST, not `gh`.** There is no `gh` in the scheduler image — the runtime
stage installs `ca-certificates` and `git` and nothing else
(`ops/scheduler/Dockerfile:49`), confirmed live with `docker exec fabro-scheduler sh -c
'command -v gh'`. The whole GitHub half of this service is `httpx`, and this is a third
read function beside `fetch_issues` and `fetch_in_progress` (`github.py:227`):

```
GET /repos/{owner}/{repo}/pulls?head={owner}:fabro/run/<run_id>&state=all&per_page=1
```

`state=all` is load-bearing. Both `gh pr list --head` and this endpoint default to
`state=open`, and since review-and-merge runs *inside* the backlog run, a PR that
auto-merged is already closed before the run goes terminal. Defaulting to open would
leave `merged` null for every successfully merged run — the one ending the column exists
to record — and correct only for the blocks. `merged` is `merged_at is not None` on the
returned item; `pr_number` is `number` and `pr_url` is `html_url`.

**`pr_lookup` keeps the page honest about what it does not know.** A null `pr_number` is
reachable two ways — a run that died before `open_pr` (no PR exists, and never will) and
a GitHub hiccup at release (a PR exists, we missed it) — and those are opposite
conclusions for an operator. The column records which: `"none"`, `"failed"`, or
`"found"`.

The call is **best-effort and never raises**: a GitHub miss must not block a lease
release, so it leaves `pr_lookup = "failed"` and the PR fields null rather than failing
the pass. It carries its own **5s timeout**, not `github.py`'s module default of 15s
(`github.py:53`), because the release poll runs every 15s
(`DEFAULT_RELEASE_INTERVAL_SECONDS`, `reconcile.py:133`) and a call that can consume a
whole tick delays the release of the *other* instance's lease — the scarcest resource the
scheduler has. At roughly a dozen releases a day it is one uncached request per release
against the 5,000/hour budget, which is negligible (overview finding 8).

The snapshot means a PR a human merges an hour after the run ends stays unmerged in the
record; that is accepted, because this is a history page, not live state, and re-fetching
on every page view would make the page depend on the GitHub token being live.

**It adds `pull_requests: read` to the token requirement.** The scheduler's
`GITHUB_TOKEN` comes from `gh auth token`, which covers it; a fine-grained token that
replaces it later must carry the scope, and AGENTS.md already records what an
under-scoped scheduler token costs.

## The page sorts on the server

Columns are sortable by `GET /history?sort=<column>&dir=<asc|desc>`, rendered as header
links, defaulting to most-recently-finished first. **The sort is applied in SQL, before
the limit** — otherwise "sort by longest run" silently means "longest of the rows on this
page", which is a wrong answer that looks like a right one. The default page is 200 rows,
with `?limit=all` as the escape hatch.

Not client-side JavaScript, for two reasons that both come from the existing page.
`queue.html:8` sets `<meta http-equiv="refresh">` on the inventory poll interval (60s by
default), so a JS-sorted column would reset itself a minute later; and the page's
established contract is that it works with scripting off — every control is a real form
POST with a `<noscript>` fallback (`queue.html:254-313`). A server-side sort keeps that
contract, survives a reload, and produces a URL that can be pasted into the deployment
log. `/history` carries **no** meta-refresh: it has no live data, so the refresh is pure
loss.

## Validation

`ops/scheduler/tests/` is the surface. `test_reconcile.py` covers the module that
changes and needs a case per release path in the table above, including the lost-run row
and the orphan path's *absence* of one; `test_app.py` covers the new route, the sort
parameters and the limit; `test_github.py` covers the PR lookup, in particular that a
merged PR is found (the `state=all` regression) and that a GitHub failure yields
`pr_lookup = "failed"` rather than an exception.

## Consequences

`run_history` grows unboundedly. At two single-slot coder instances and roughly four
hours a run, that is on the order of a dozen rows a day and a few thousand a year, which
SQLite handles without pruning.

The final-state label shown to the operator is *derived* from the stored raw facts
(`kind`, `reason`, `category`, `merged`, `requeue_attempt`, `pr_lookup`) at render time,
so changing the wording of a label is a template change, not a migration. Given that the
schema cannot gain a column, deriving presentation from raw facts is what makes the first
schema survivable.

In-flight runs have no row and never appear here; live work stays on the queue page.

The page adds no auth and leaks no secrets — only GitHub links and scheduler-owned facts
— consistent with decision 16, "LAN-only, no auth"
(`docs/scheduler/00-overview-and-contracts.md:47`).

Built and deployed on 2026-09-20, as seventeen tasks. The deploy restarted the scheduler
container and so re-dated draft 14's shakedown window, to
`2026-09-20T21:50:41.952847122Z`.
