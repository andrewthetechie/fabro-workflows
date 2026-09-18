"""The fabro coder scheduler.

Owns admission to the coder instances: it inventories work from GitHub, orders
it, and creates one fabro run at a time.

Draft 04 is the skeleton — configuration plus a health endpoint. Draft 06 added the
GitHub inventory, the SQLite cache and the read-only queue page; the fabro client
and the dispatch loop are drafts 07 and 08. The task series, the decisions behind
it and the contracts each later draft consumes are in `docs/scheduler/`, starting
at `00-overview-and-contracts.md`.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
