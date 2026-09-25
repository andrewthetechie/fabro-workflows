# B8 · Keep fabro current: 0.362 now, the Petri line next

**Status:** step 1 is **applied** (`docs/fabro-upgrade/`, 2026-09-25; the host now runs 0.362.0-nightly.0). Step 2 is proposed. **Axis:** repeatability. **Effort:** M–L per step.
**Feasibility:** required. The only choice is planned or broken.

## Problem

The host has been pinned at `0.354.0-nightly.0` since 2026-09-15, because 0.357 crash-looped
on SQLite migration `2026091101` at run-history activation (`~/fabro/.env` comment). Every
graph, the scheduler client (`fabro.py`, verified against 0.354's API), and the ops tooling are
written against 0.354. Upstream moves daily. Each skipped release widens the jump, and newer
releases are where the fixes and features this roadmap would like to use actually ship.

## Step 1: 0.354 → 0.362.0-nightly.0 (2026-09-20)

165 upstream commits, **before** upstream's switch to the Petri engine. Rehearsed on a copy of
the live database on 2026-09-25. It works, but it needs two things: fabro's run history must be
dropped (0.362's run-history activation rejects every run 0.354 stored, because `billing` became
`usage`), and the catalog overlay must move to `codecs = [...]`. `fabro parse`, the
legacy run event log and the checkpoint endpoint all still exist at this tag. Planned in
`docs/fabro-upgrade/`, which is the output of a grilling session. Run it before any other
roadmap item that edits a `.fabro` file.

## Step 2: onto the Petri line (upstream `main` after 0.362)

Upstream `main` on 2026-09-25 has replaced the engine. Commit subjects that break our tooling:

| Upstream change | What of ours breaks |
|---|---|
| "Delete the checkpoint endpoint, `fabro parse`, and the fabro-workflow shims" | `ops/check-routing-schemas.py:61` shells out to `fabro parse` |
| "Delete the legacy run event log", "Serve the run stream as the only run event API", "Drop the `run_events` table" | `reconcile.py`'s event-tail reads (cancel category), `fabro-run-status.sh`, `fabro-monitor.sh`, `fabro events -p` usage in `AGENTS.md` |
| "Remove the `[server.slatedb]` settings" | only if the overlay sets it |
| "Remove the engine flag: every run is a Petri run" | every graph, re-validated on Petri's DOT parser ("Read workflow graphs through Petri's DOT parser") |
| "Restore the timeline, fork, rewind and retry operations over checkpoints" | **a gain.** The scheduler could fork a run that died of infrastructure after 5 of 7 tasks, instead of requeuing it from zero |

Plan step 2 as its own series once step 1 is stable. It will need a scratch fabro on the new
version (a second compose project, its own port and volume), a port of the routing checker to
Petri's validation, and a port of the scheduler's event reads to the run stream.

## Standing rule to add to `ops/README.md`

Each upgrade is a series in `docs/`, with a scratch-server rehearsal, the database restore
tested **before** the cutover, and the version bump as a single commit that a revert undoes on
the config side, while the database side is undone by a restore. `AGENTS.md` already records
that migrations make rollback a restore, not a tag change.
