# A1 · Andon cord: stop the line on a systemic fault

**Status:** proposed. **Axis:** repeatability. **Effort:** S–M. **Feasibility:** high.
**Depends on:** nothing. **Feeds:** B6 (red `main` becomes the first bug source).

## Problem

The factory keeps dispatching through a fault that breaks every run.

- The scheduler has no circuit breaker. `reconcile.py` classifies each terminal run
  separately. An infrastructure-shaped failure is requeued up to `MAX_REQUEUES = 3`
  (`reconcile.py:138`), and then the lease is released and the next issue is taken. Nothing
  counts failures *across* runs.
- On 2026-09-20 a settings overlay missing `display_name` made every new run die at its
  first node in about 0.2 s. For 14 hours the scheduler drained all four repos at roughly one
  issue a minute, leaving issues on `agent-in-progress`, until the page read "no open work"
  (`AGENTS.md`, invariants table).
- Nothing watches `main` after a factory merge. A squash-merge that turns `main` red is not
  noticed by the factory, and the next run on that repo branches from a red `main`. Its
  `validate` then fails on a fault no task caused, and it walks the rework ladder against it.
  That is the same wasted-ladder shape as the missing-npm image incident (`AGENTS.md`,
  "A profile image must satisfy its repository's `.fabro/ci.sh`").

A dark factory needs a stop that trips on its own, because nobody is watching when it
matters.

## Design

Two deterministic triggers. Both write a persisted halt row in the scheduler's SQLite, show
it on the queue page, and send one Discord message.

### 1. Fast-failure breaker (global)

- Count runs that end `failed` within **M minutes of dispatch** (suggest M = 5), across all
  repos and pools, in a sliding window (suggest 30 min).
- At **N such runs** (suggest N = 3), set `halted = global` with the reason and the run ids.
- While halted: no new dispatch. Live runs are **not** cancelled. Requeued issues stay
  queued, so work is not lost.
- A human clears it with a button on the page (`POST /api/halt/clear`). Clearing resets the
  window.
- Do not count an operator cancel (`failed/cancelled`, category `canceled`, `reconcile.py`
  docstring), because it is not a fault.

### 2. Red `main` (per repo)

- After a run whose PR the scheduler records as merged (`run_history.merged = true`), watch
  that repo's `main` check runs (`gh api repos/{r}/commits/main/check-runs`, cached with the
  existing ETag logic in `inventory.py`).
- If a required check on `main` goes red and **stays red after one rerun** of its failed jobs
  (the same "rerun once" rule as ADR 0011 D2), then:
  1. set `halted = repo:<name>`, so no new dispatch for that repo;
  2. turn off that repo's auto-merge with the existing per-repo switch
     (`ops/fabro-auto-merge-switch.sh <repo> off` semantics, called through the fabro API);
  3. file one bug issue: title `fix(ci): main is red after #<PR>`, body with the failing job
     names, the log tail and the suspect PR, labels `agent`, `priority`, `bug`, `ai-generated`,
     plus a fingerprint marker `<!-- fabro:red-main sha=<sha> -->` so it is filed once.
- The repo halt clears on its own when `main` is green again. Auto-merge is **not** re-armed
  automatically: re-arming is a human decision, recorded in the Discord message.

**Exception to the halt:** the red-main bug issue itself must be dispatchable while its repo
is halted, or nothing can fix it. Dispatch only issues carrying the red-main marker while a
repo halt is active.

## Where it lives

In the scheduler. It already has the lease table, the run outcomes (`run_history`), the
GitHub token and a page. Do not put it in `fabro-monitor.sh`: its 15-minute beat is too slow
for a breaker, and it can alert but cannot stop dispatch.

## Verification

- Unit tests in `ops/scheduler/tests/`: three fast failures in the window trip the breaker, a
  cancel does not count, clearing resets it, a halted repo still dispatches a red-main issue.
- One staged run on the host: fire three runs against a deliberately broken
  `environment_id` and confirm the page shows `halted` after the third.

## Open questions

1. Values of N and M. They are start values; B1's history will show the real distribution of
   run durations for failures that are *not* systemic.
2. Should a global halt also flip `FABRO_AUTO_MERGE` to `0`? Suggest no. A merge in flight on
   a healthy PR is not what a fast-failure fault threatens.

## Out of scope

Backend liveness probing of the coder boxes (that is B5), and Discord alerting on persistent
LLM errors (also B5).
