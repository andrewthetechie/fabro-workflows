# Two-slot boxes: overview and canonical contracts

**Status:** tasks 01–03 done and reviewed 2026-11-11 (local commits, not pushed). Task 04 is ready.

**Read this file first.** Every task in this folder assumes the decisions, facts and contracts
below. If a task and this file disagree, this file is correct: stop and report the conflict.
Read `docs/plans/more-local-inference/00-overview-and-contracts.md` (C3, C5, C6, C7, C10) and
ADR 0031 before you start: this series changes the box-precedence machinery that series built.
**Before you write Rust, invoke `/ms-rust`.** Before you touch `ops/scheduler/`, read
`ops/README.md` *The coder scheduler*. Before you edit a `workflow.toml`, invoke
`/fabro-workflow` and read `docs/agents/invariants-graph.md` and `invariants-host.md`.

## Goal

The two strix boxes each get a new strix-infer build. It serves **two concurrent sessions of
`qwen3.8-flash-next` at a 524288-token context** behind one endpoint. The pools then map to boxes
like this:

| Box | Host | Pools (slots) | Today |
|---|---|---|---|
| box 1 | strix 10.10.0.29 | `coders-a`, `coders-b` | `coders-a` only |
| box 2 | strix 10.10.0.56 | `coders-c`, `coders-d` | `coders-b` only |

`coders-c` (spark vLLM, `deepseek-v4-flash-0731`) and `coders-d` (freellm `auto`) leave the
coder pools. The spark box keeps serving `long-context`, and nothing else changes there.

## Facts (checked against the repository on 2026-11-11)

- F1. The scheduler leases **pools**: `DEFAULT_CODER_POOLS = ("coders-a", …, "coders-d")`
  (`config.py`), one run per pool. A slot is a pool. **Leasing needs no change.**
- F2. Everything in the background lane treats one pool as one box:
  - `SCHEDULER_BOX_HEALTH` and the server var `FABRO_BOX_HEALTH` are `pool=url` maps. Each
    `workflow.toml` that claims a box passes `FABRO_BOX_HEALTH = "{{ vars.FABRO_BOX_HEALTH }}"`.
  - Runs carry the label `box=<pool>` (C6).
  - `background.py` loops `for box in config.box_health`. `_loop_on(box)` finds the lease
    whose `coder_pool == box`. A background run is dispatched with `coder_pool=f"{box}-bg"`.
  - `fabro-io` `boxclaim.rs` `lane_runs_on(runs, lane, box_name, own)` matches `label box ==
    box_name`. The guard (`guard_clear`) judges only the coder runs labelled with its own box.
- F3. `box-claim` (`boxclaim.rs` `claim`) **always** calls `wait_for_idle` after cancelling,
  even when it cancelled nothing. It waits until two consecutive `/health` reads say idle, or
  until its 45 s deadline.
- F4. The new `/health` is **box-wide**, with no per-slot fields:
  `{"status":"ok","model":"qwen3.8-flash-next",…,"context_length":524288,"busy":true,
  "queued":0,"idle_seconds":0.0,"live_tokens":131985}`. `busy`, `queued` and `idle_seconds`
  are what `Health::parse` already reads. `live_tokens` is new and unused.
  **Superseded 2026-11-11 by strix-infer 0.7.0**, which both boxes run before task 04. Its
  body adds `"slots":2,"busy_slots":<n>` and a `slot_states` array (plus `kv_budget`,
  `backend`, `vision`). `busy` is still box-wide: true while any slot serves. Both parsers
  ignore the extra fields. Only the claim's wait uses the slot fields (D4).
- F5. Model rows live in the host settings overlay. `ops/settings.toml.example` is the
  tracked template. `coders-a`, `coders-a-bg`, `coders-b` and `coders-b-bg` use providers
  `box-a` and `box-b`, each `limits.context_tokens = 262144`. `coders-c` is on `spark` and
  `coders-d` on `freellm`. The `-bg` rows must never have a fallback row (R14).

## Decisions

- **D1. A box is the set of pools that share one health URL.** The `pool=url` format does not
  change. `coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.29:5300/health` *is*
  the declaration that a and b share a box. The scheduler and `fabro-io` both derive the box
  from it, so there is one source of truth and no new variable. A pool whose URL no other pool
  shares is a one-slot box, which is today's behaviour exactly. That is why tasks 01 and 02
  ship and deploy before any host value changes.
- **D2. The `box=<pool>` label keeps its meaning (the run's own pool).** Nothing relabels. Code
  that asks "which runs are on my box" asks for runs whose `box` label is any **sibling**
  pool, the pools that share its URL, its own pool included.
- **D3. One background run per box, not per pool.** A background run may start on a box only
  when **no** coder loop on any sibling pool is in a box stage, the box `/health` has been idle
  for `SCHEDULER_BG_GAP_S`, no background run is running on any sibling, and no sibling pool
  is drained. The gap estimate is the **minimum** over the sibling loops' predicted gaps, and
  a sibling with no lease counts as unbounded. The run uses the `-bg` row of the box's
  **first pool in sorted order** (`coders-a-bg` on box 1, `coders-c-bg` on box 2), and its job
  row records that pool as `box`. Eviction cancels it when any sibling loop enters a box stage.
  *Why not one per slot:* `/health` is box-wide (F4). The lane cannot tell a free slot from a
  busy one. One background run per box is the conservative reading of ADR 0031's "the coder
  loop always wins".
- **D4. `box-claim` waits for idle only when waiting can help.** It cancels background runs on
  every sibling pool, as today. It then waits for idle **only if** it cancelled at least one run
  **and** no other sibling coder loop is in a box stage. Otherwise it returns immediately with
  `idle=skip`. On a two-slot box, a busy sibling makes the box read busy for the whole
  45 s deadline. This change removes that stall from every box stage. On a one-slot box,
  "cancelled nothing" now skips today's 2–4 s confirm-idle wait. That is accepted: with
  nothing cancelled, nothing of ours is on the box.
  *Amended for 0.7.0 (F4):* the wait itself confirms **a free slot** (`busy_slots < slots`
  and `queued == 0`, read twice), not an idle box. A box without the slot fields falls back
  to `busy == false`. So a sibling that starts a box stage *during* the wait no longer holds
  the claim to its deadline. The sibling check reads the sibling run's stage from
  `coder_box_stages`, which the coder loops' generated manifests now carry too.
- **D5. The guard judges every sibling.** `box-guard` cancels its own background run when
  any coder loop labelled with a sibling pool is in a box stage, or when the box reads busy
  (C10, unchanged otherwise).
- **D6. Context is 524288 on all four `coders-*` rows and on the `-bg` rows of box 1 and box 2.**
  `max_output_tokens` stays at today's `box-a` value (38400).
- **D7. Rollout is two cutovers.** Box 1 first (a and b are already draining), then box 2
  (the operator drains c and d, then repoints them). In between, `coders-c` and `coders-d`
  keep their current backends and stay out of `SCHEDULER_BOX_HEALTH` and `FABRO_BOX_HEALTH`,
  as they are today.

## Contracts

- **C1. Siblings.** Both languages implement one function with one meaning:
  `siblings(health_map, pool) -> sorted list of pools whose URL equals pool's URL`. The result
  includes `pool`. It is empty when `pool` is absent from the map. URLs compare after
  `strip()`, exactly as parsed today. Python: `config.py` (`box_siblings`). Rust:
  `boxclaim.rs` (`siblings`). Each has a test over the same three cases: two pools on one
  URL, a singleton, and an absent pool.
- **C2. Boxes.** `boxes(health_map) -> list of (first_pool, siblings)`, ordered by first pool
  (Python only). The background lane iterates this list in place of `config.box_health`'s keys.
- **C3. Claim log line.** It is unchanged except for `idle=`, which may now be `skip`, and one
  appended field `siblings=<n>`. `ops/fabro-box-claims.py` must parse both (task 01).

## Tasks

| # | File | What |
|---|---|---|
| 01 | `01-fabro-io-siblings.md` | `box-claim` and `box-guard` judge sibling pools (C1, D2, D4, D5) |
| 02 | `02-scheduler-box-groups.md` | The background lane iterates boxes, not pools (C1, C2, D3) |
| 03 | `03-model-rows-and-config.md` | Settings template, compose default, fallbacks, docs |
| 04 | `04-deploy-and-cutover.md` | Ship 01–03 in order, cut over box 1, then box 2 |

01, 02 and 03 are independent, and each ends with `make check` green and one local commit.
**Do not push from 01–03.** Task 04 pushes and deploys them in order: the images first
(`fabro-io`), then the scheduler, then the host values.
