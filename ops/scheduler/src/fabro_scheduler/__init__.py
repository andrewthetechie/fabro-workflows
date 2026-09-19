"""The fabro coder scheduler.

Owns admission to the coder instances: it inventories work from GitHub, orders it,
and creates one fabro run at a time.

Draft 04 was the skeleton — configuration plus a health endpoint. Draft 06 added the
GitHub inventory, the SQLite cache and the read-only queue page; draft 07 added the
fabro client and `POST /api/dispatch-once`, which creates and starts one real run by
hand. **Draft 08 is the dispatch loop**: on a 5-second tick it takes the top-ranked
item whose repo has no run in flight, labels it `agent-in-progress`, removes `agent`,
creates and starts its run on a free coder instance, and records the lease. It
writes to GitHub and starts real runs, so it runs only when the process is started
with the loop armed.

Nothing releases a lease yet — that is draft 09, with requeue and recovery — so with
two coder instances the loop holds at most two leases and stops dispatching on its
own once both are taken. The task series, the decisions behind it and the contracts
each later draft consumes are in `docs/scheduler/`, starting at
`00-overview-and-contracts.md`.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
