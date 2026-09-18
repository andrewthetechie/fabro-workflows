# The coder scheduler — Overview & canonical contracts

**Read this first.** Every task in this folder assumes the architecture, the
contracts and the findings recorded here. This document is the single source of
truth.

## What this is

A small service that decides **which issue, in which repo, gets worked next, and on
which inference box**. It owns admission to the two llama.cpp coder instances; fabro
keeps owning everything that happens inside a run.

It exists because the factory currently has two scarce resources and no coordination
between them. Four `backlog` automations fire on independent schedules; each run that
reaches a coder stage lands on whichever of the two boxes LiteLLM happens to pick; and
because each box serves exactly one request at a time, two concurrent runs spend
roughly half their turns queued behind each other. Measured 2026-09-18: 0.57 minutes
per tool call under Sandcastle's one-loop-per-repo scheme against 1.33 under fabro —
**2.3×**, of which about 1.7× is this contention and 1.38× was `reasoning_effort`
(fixed separately, commit `ce831c0`).

**It does not fix reliability.** The stalls, the four-hour dead ends and the unanswered
human gates are addressed by commits `644cfdd`, `86ea484`, `ce831c0` and the LiteLLM
timeout change in `c14b538`. None of those had been observed across a full clean run at
the time of writing. The scheduler improves throughput and stops the overnight thrash
of four uncoordinated schedules; it will faithfully park a box on a run that stalls.

## Operator decisions (settled — do not re-litigate)

| # | Decision |
|---|---|
| 1 | **A coder instance is leased for a whole run**, not per stage. Interleaving hosted-model stages of one run with coder stages of another is explicitly deferred, not rejected — see ADR 0006. |
| 2 | **The scheduler owns admission**; fabro's `max_concurrent_runs` becomes a backstop at **4**. See ADR 0005. |
| 3 | **The scheduler picks the issue** and passes `issue_number` as a run input. `acquire`'s selection and `claim`'s ULID arbitration are deleted, not refined. |
| 4 | **Priority is a signed integer; smallest wins.** `-99` beats `0` beats `7`. Repo defaults live in `ops/scheduler/repos.toml`; a per-issue override lives in the scheduler's database and is ephemeral by design. |
| 5 | **Strict priority with a starvation ceiling.** Anything queued longer than `T` (default 4h) jumps the front. Not a continuous aging score — "nothing waits more than T" is a claim you can verify by looking. |
| 6 | **One in-flight run per repo.** Matches Sandcastle. With four repos and two boxes a box only idles when three of four repos are simultaneously out of work. |
| 7 | **The queue lives entirely in the scheduler.** Nothing is created in fabro until a box is free. Runs are never parked in fabro as `submitted`. |
| 8 | **The scheduler polls fabro every 15s** for terminal states, and GitHub every 60s with conditional requests. No push, no SSE, no webhook. |
| 9 | **The scheduler sets `agent-in-progress` at dispatch.** The label is the durable handoff receipt a restart rebuilds from. |
| 10 | **Requeue on infra-shaped failures only**, discriminated by the run's failure `category`. Agent-shaped failures wait for a human. |
| 11 | **Soft repo affinity** in box selection: prefer the box that last ran this repo, never wait for it. |
| 12 | **Box pinning is a LiteLLM model group plus a run input**, not a fabro provider. Three groups: `coders-a`, `coders-b`, and `coders` (both, retained as the fallback). |
| 13 | **`issue-triage` stays on its own schedule** and is never queued — it needs no coder. Its `check_capacity` stage is **deleted**. |
| 14 | **The standalone `pr-review` package stays**, and `fabro-fire-pr-review.sh` enqueues at top priority rather than firing directly. |
| 15 | **Drain and cancel are separate controls.** Drain stops new dispatch to a box; cancel kills the run on it and requeues the issue. |
| 16 | **The scheduler serves its own web UI.** LAN-only, no auth, consistent with fabro's own `:32276`. |
| 17 | **Health alerting belongs to `fabro-monitor.sh`**, not to the scheduler. Something outside the scheduler must notice when the scheduler is dead. |
| 18 | **Roll out to all four repos at once.** There is no working baseline to protect: of four runs observed on 2026-09-18, zero produced a clean end-to-end merge. |
| 19 | **Python + FastAPI + SQLite**, `uv`-managed, a container in the `~/fabro` compose project, code in this repository under `ops/scheduler/`. |

## Findings that constrain the design

Read out of `context/fabro` (nightly `0.357.0`) on 2026-09-18. Each one closed off an
approach that looked obvious.

### 1. Fabro's queue cannot be steered

Ordering is strict FIFO on run **creation** time (`lib/apps/fabro-server/src/server.rs:4538`,
sorting `id.created_at()` captured at `handler/lifecycle.rs:185`). There is no priority
field anywhere in `RunIntent` (`lib/foundation/fabro-types/src/run_intent.rs:10-42`).
`lifecycle.queue_position` is hardcoded `None` in the only production construction site
(`lib/components/fabro-store/src/run_state.rs:1212`); the one computer is `#[cfg(test)]`.

So the scheduler cannot hand fabro an ordering and let it sort. It must hold the queue
itself and release exactly one run at a time. This is decision 7.

### 2. Automations cannot be parameterised

`POST /api/v1/automations/{id}/runs` has **no request body** — the handler signature
takes only auth, state, headers and the path id
(`lib/apps/fabro-server/src/server/handler/automations.rs:211-218`), and the OpenAPI
operation declares no `requestBody`. It reconstitutes everything from the stored row.

The scheduler must therefore use `POST /workflow-versions` → `POST /runs` →
`POST /runs/{id}/start`. `POST /runs` creates a `submitted` run and does **not** start
it; the automation path auto-starts, the intent path does not.

### 3. Pinning a box is model routing, not worker placement

Workers are always local subprocesses of the server (`WorkerRef::Local` is the only
variant, `worker_runtime.rs:17-31`). "Pin a run to a box" can only mean "route its LLM
calls to that box".

The obvious route — a fabro provider per box — is blocked: provider `base_url` is
server-global settings (`docs/public/reference/user-configuration.mdx:191`) and
`RunIntent` can only name a provider **id**, so adding a box means a settings change.

The route that works keeps both boxes behind the one `litellm` provider and splits them
in **LiteLLM** instead:

```
coders-a  -> http://10.10.0.29:8000/v1
coders-b  -> http://10.10.0.56:8000/v1
coders    -> both        (retained; the fallback for anything unpinned)
```

and selects between them from the run's inputs.

### 4. The stylesheet is the only per-run routing lever, and it is strict

`args.model` / `args.provider` on the intent set only the run's *default* model
(`run_materialization.rs:53-54`), and **node-level stylesheet assignments win**
(`docs/public/core-concepts/models.mdx:293`). Every coder node in these graphs is
explicitly assigned, so `args.model` would be ignored.

But the root graph's `model_stylesheet` **is** a MiniJinja template and receives
`inputs` (`docs/public/workflows/stylesheets.mdx:43-51`). So:

```dot
.coder  { model: {{ inputs.coder_pool | default("coders") }}; reasoning_effort: medium; }
.rebase { model: {{ inputs.coder_pool | default("coders") }}; reasoning_effort: medium; }
```

with `args.inputs.coder_pool = "coders-a"` on the intent.

The `default()` filter is **load-bearing**. The stylesheet renders through
`render_template_for_target_outcome` (`transforms/model_stylesheet_template.rs:53`) in
strict mode at run time — an unbound input does not render empty, it **fails the run at
compile**, the same `422 run_compile_invalid` that an unbound `pr_number` produces.
Without the default, every hand-fired run would break.

Do **not** bind `coder_pool` in `[run.inputs]` instead. That is the trap AGENTS.md
already records for `pr_number`: a bound default silently converts "no input supplied"
into "one specific answer", which here would send every manual run to one box.

`args.inputs` values are **scalars only** (`handler/runs.rs:630-645`), so the scheduler
can pass `issue_number` but never an issue body. The run still fetches it.

### 5. A fabro restart fails everything in flight

`reconcile_incomplete_runs_on_startup` walks `Runnable | Starting | Running | Blocked |
Paused | Removing` and appends a **failure** event to each (`server.rs:3101-3139`).
Runs left in `submitted` are untouched.

This is the canonical infra-shaped failure of decision 10, and the reason decision 7
keeps work outside fabro. The scheduler must treat "fabro bounced" as "everything I
dispatched is now failed", not as something to resume.

### 6. `Blocked` and `Paused` runs consume fabro capacity

`counts_toward_scheduler_capacity` matches `Starting | Running | Blocked { .. } |
Paused { .. }` (`server.rs:2905-2913`). A run on a human gate holds a fabro slot for up
to its gate timeout — 4h on `human_rescue` since commit `644cfdd`.

It also holds its coder lease, by decision 1. That is accepted: a human can answer a
gate and route the run straight back into a coder stage, so releasing the lease on
`blocked` would mean re-acquiring one to continue, which the design has no mechanism
for.

### 7. The fabro web UI cannot host this

The SPA is `rust-embed`-baked into the binary at compile time
(`lib/apps/fabro-spa/src/lib.rs:5-9`). There is a `static_asset_root` override but it is
described in-code as a test fixture and is not exposed as a server setting. Changing the
UI means forking and rebuilding fabro. Hence decision 16.

### 8. GitHub conditional requests are genuinely free

Verified live against `andrewthetechie/jelly-swipe` on 2026-09-18:

```
request 1 (cold)           200   x-ratelimit-remaining: 4999   etag: "4d062fc1..."
request 2 (If-None-Match)  304   x-ratelimit-remaining: 4999   x-ratelimit-used: 1
```

A `304` costs **zero** quota against the 5,000/hr authenticated budget. Four repos on a
60s cadence is ~240 requests an hour, essentially all 304s.

## Contracts

### Queue item

One open, `agent`-labelled issue that is neither `agent-in-progress` nor `agent-stuck`.
One queue item becomes exactly one `backlog` run, which may decompose into many tasks
and produces one PR.

### Dispatch

The scheduler dispatches when **all** hold:

1. a coder instance is free and not drained;
2. the queue is non-empty;
3. the highest-ranked eligible item's repo has no in-flight run (decision 6).

Ranking is: any item queued longer than `T` first (oldest first), then by repo priority
ascending, then by issue number ascending.

On dispatch, in order:

1. add `agent-in-progress`, remove `agent` — the durable receipt;
2. `POST /runs` with `args.inputs = { issue_number, coder_pool }`;
3. `POST /runs/{id}/start`;
4. record the lease: `(box, repo, issue, run_id, dispatched_at)`.

If step 2 or 3 fails, the label is rolled back.

### Release

A lease releases only on a terminal `lifecycle.status.kind` for its run
(`succeeded`, `failed`, `cancelled`, `errored`), observed by the 15s poll.

### Requeue

Only when the run's failure `category` is `transient_infra`. The scheduler removes
`agent-in-progress`, restores `agent`, and the item re-enters the queue keeping its
original enqueue time so the starvation ceiling is not reset.

### Recovery

On scheduler start: read lease rows; for each, query fabro. A run that is terminal, or
that fabro has never heard of, releases its lease. Then reconcile GitHub: any issue
labelled `agent-in-progress` with no live lease is un-labelled and requeued.

## Task series

| # | Task |
|---|---|
| 01 | Split `coders` in LiteLLM into `coders-a` / `coders-b`, keep `coders`; extend the fallback table |
| 02 | Thread `coder_pool` through both root stylesheets with the `default()` filter; validate |
| 03 | Collapse `acquire`/`claim` to input validation and issue fetch; delete the ULID arbitration |
| 04 | Delete `issue-triage`'s `check_capacity`; raise `max_concurrent_runs` to 4 |
| 05 | `repos.toml` schema and loader |
| 06 | GitHub inventory with ETag caching |
| 07 | Queue, ranking and the starvation ceiling |
| 08 | Fabro client: workflow-version registration, run intent, start, poll |
| 09 | Lease state machine, dispatch, release, requeue, recovery |
| 10 | Web UI: queue view, reorder, drain, cancel |
| 11 | Compose service, image, volume, deploy |
| 12 | `fabro-monitor.sh` scheduler conditions |
| 13 | Disable the four `backlog` automation schedule triggers |
| 14 | `fabro-fire-pr-review.sh` enqueues instead of firing |
| 15 | Shakedown and the deployment-log entry |
