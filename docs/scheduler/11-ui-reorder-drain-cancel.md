# Reorder, drain and cancel in the web UI

## Tracer-Bullet Outcome
From `http://10.10.0.32:32280/` the operator can click an issue to make it go
**next**, mark a coder instance as drained so nothing new lands on it, and cancel
the run currently on a box.

## User Story
As the operator, I want to jump one issue to the front and take a misbehaving box
out of rotation without editing a config file or restarting anything, so that I can
steer the factory while it is running.

## Description
The last functional slice. Three mutations on top of draft 09's read-only page.

**Drain and cancel are separate controls, deliberately.** Drain never destroys
work: it stops *new* dispatch to a box and lets the current run finish. Cancelling
the run on a box is a second, explicitly-labelled button. `CONTEXT.md` defines
**Drain** this way; do not collapse them.

## Context Pack
- Source decisions: overview decisions 4 (per-issue override lives in the
  scheduler DB and is ephemeral by design — it means "next", not "forever"), 15
  (drain and cancel are separate), 16 (LAN-only, no auth).
- Repo facts: `CONTEXT.md` defines **Drain**, **Coder lease**, **Queue item**.
  Repo priority lives in `repos.toml` and is **not** editable from the UI — that
  is a reviewed policy decision, per overview decision 4.
- Non-goals: editing repo priorities; authentication; editing `repos.toml`;
  forcing a GitHub re-poll; any change to ranking beyond the override.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/app.py          (GET /api/pools + three POST routes)
  ops/scheduler/src/fabro_scheduler/store.py        (overrides + drain state)
  ops/scheduler/src/fabro_scheduler/queue.py        (honour override_rank)
  ops/scheduler/src/fabro_scheduler/templates/queue.html
  ops/scheduler/tests/test_overrides.py
  ```

- Interfaces and names:

  ```
  GET  /api/pools                                -> 200 [{"coder_pool":"coders-a",
                                                          "drained":false,
                                                          "lease":{"repo":"o/a","issue_number":7,
                                                                   "run_id":"01M...","dispatched_at":"..."}}]
  POST /api/queue/{repo}/{issue_number}/bump     -> 200 {"override_rank": -1}
  POST /api/pools/{coder_pool}/drain             -> 200 {"drained": true}
  POST /api/pools/{coder_pool}/undrain           -> 200 {"drained": false}
  POST /api/pools/{coder_pool}/cancel            -> 200 {"cancelled_run_id": "..."} | 409
  ```

  ```sql
  CREATE TABLE IF NOT EXISTS overrides (
    repo TEXT NOT NULL, issue_number INTEGER NOT NULL,
    override_rank INTEGER NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (repo, issue_number)
  );
  CREATE TABLE IF NOT EXISTS pool_state (
    coder_pool TEXT PRIMARY KEY, drained INTEGER NOT NULL DEFAULT 0
  );
  ```

  `rank()` from draft 06 gains a preceding tier: items with a non-null
  `override_rank` sort **first**, ascending, ahead of the starvation ceiling.

- Verified external contracts (observed live on `10.10.0.32` on 2026-09-18):

  ```
  POST /api/v1/runs/{id}/cancel   -> 200
  ```

  Verified by cancelling three `submitted` test runs; afterwards their
  `.lifecycle.status.kind` read `failed`, **not** `cancelled`. Do not assert
  `cancelled` — treat any of `TERMINAL` as "it is over".

  Note for draft 09's interaction: a cancelled run fires **no** `run_complete`
  hook (`lib/components/fabro-workflow/src/lifecycle/hook.rs:157-159` returns early
  when `state.cancelled`). The scheduler's 15s poll is what notices, which is one
  more reason polling was chosen over hooks.

- Behavior rules:
  - **Bump** sets `override_rank = (min existing override_rank) - 1`, so the most
    recent bump wins. It affects ordering only; it never pre-empts a running lease.
  - An override is **cleared when the item is dispatched**. It means "next", not a
    permanent priority.
  - Bumping an issue whose repo already has an in-flight run still only changes
    ordering — one-run-per-repo (decision 6) still applies, so it goes next *for
    that repo*.
  - **Drain** sets `drained=1`; the dispatch loop excludes that pool via
    `free_pools`. The current lease is untouched and runs to completion.
  - **Cancel** calls `POST /runs/{id}/cancel` for the lease on that pool, then
    lets draft 09's normal release path observe the terminal state. It does **not**
    release the lease directly — one release path only.
  - Cancel returns `409` with a plain message when the pool has no active lease.
  - Drain state survives a restart (it is in SQLite); overrides do too, but are
    expected to be short-lived.
- Error and security rules: no auth by decision 16 — but every mutation must be
  logged with what changed and when, because there is no identity to attribute it
  to. All four routes are `POST`, so a link preload cannot trigger them.

## Acceptance Criteria
- [ ] `uv run pytest` passes.
- [ ] Bumping an issue moves it to the top of `GET /api/queue`, above both the
      ceiling tier and repo priority.
- [ ] A bumped issue dispatches next when its repo is free, and its override is
      gone afterwards.
- [ ] Draining `coders-a` stops new dispatch to it while its current run continues
      to completion.
- [ ] Undraining restores dispatch without a restart.
- [ ] Cancel on a leased pool terminates the run; within 15s the lease is released
      by the normal path and the box takes new work (unless drained).
- [ ] Cancel on an unleased pool returns `409` and changes nothing.
- [ ] Drain state survives `docker compose restart scheduler`.
- [ ] `GET /api/pools` reports every pool with its `drained` flag and its current
      lease or `null` — draft 12's C8 condition reads exactly this.

## Test Expectations
Framework: **pytest** with FastAPI's `TestClient`; `uv run pytest` from
`ops/scheduler/`. fabro faked with `respx`; SQLite in `tmp_path`.

Concrete case in `tests/test_overrides.py`:

```python
def test_bump_outranks_both_ceiling_and_priority(client, store):
    store.upsert_issue(Issue("o/urgent", 1, "t", frozenset({"agent"}),
                             first_seen=NOW - timedelta(hours=9)))   # past the ceiling
    store.upsert_issue(Issue("o/normal", 2, "t", frozenset({"agent"}),
                             first_seen=NOW))
    client.post("/api/queue/o%2Fnormal/2/bump")
    assert [i["number"] for i in client.get("/api/queue").json()] == [2, 1]

def test_bump_is_cleared_on_dispatch(store):
    store.set_override("o/a", 5, -1)
    store.clear_override_on_dispatch("o/a", 5)
    assert store.get_override("o/a", 5) is None
```

## Dependencies
- Blocked by: "Release, requeue and recovery"
- Why blocked: cancel depends on the release path existing, and drain is only
  meaningful once dispatch is automatic and self-releasing.
- Blocks: "Shakedown and deployment-log entry"

## Labels
`feature`, `ops/scheduler`, `priority:medium`

## Estimate
Medium

## Risk
3 - unauthenticated mutating endpoints on the LAN, one of which cancels a
long-running job. Consistent with fabro's own posture on the same host, and the
cancel path is the same one an operator already has in fabro's UI.

## Validator Stopping Point
`uv run pytest` green; bump, drain, undrain and cancel each observed working
against a live queue.
