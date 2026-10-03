# B5 · Elastic coder fleet: add, drain and remove Coders, and wait for hosted models

**Status:** in design (grilled 2026-10-03, stopped before the ADR). **Axis:** throughput.
**Effort:** M–L. **Feasibility:** medium-high. **Depends on:** nothing (the fabro upgrade it
waited for was applied 2026-09-25). **Replaces:** rev 2's "P2-8 manual fleet".

This record is the handoff from a design session. The next step is an ADR (0017) and a
`docs/elastic-fleet/` task series in the shape of `docs/merge-gate/` and
`docs/triage-split/`. The terms are in `CONTEXT.md`: **Coder**, **Capacity**, **Coder
slot**, **Coder instance**, **Drain**, **Remove**, **Model limit**, **Permit**. **Coder
pool** is retired.

## What the operator wants

1. Add a Coder when one is free, and take it away again. Examples: a second DeepSeek box
   that is idle for part of the day (`coders-c`), drained by hand during the hours it is
   needed elsewhere; a $100 OpenRouter credit, added as a hosted Coder and run until it is
   spent. Drain and remove are manual. No automatic schedules in the first feature set.
2. Hosted models with a concurrency limit (z.ai) must make a stage **wait** for a free seat,
   and must not fail it or fail it over at once.

## Facts found (verified 2026-10-03)

**Adding a box today** needs an overlay provider and model row, a `[run.model.fallbacks]` row
in `backlog/workflow.toml` (a commit), and a new name in `DEFAULT_CODER_POOLS`
(`ops/scheduler/src/fabro_scheduler/config.py:41`) with a rebuild. Drain exists
(`POST /api/pools/{pool}/drain`). The run's model is the `coder_pool` input itself
(`.coder { model: {{ inputs.coder_pool | default('coders-a') }} }`), so a Coder's slot id is a
catalog model id.

**Every fallback key must exist in the catalog.** `resolve_model_fallbacks`
(`context/fabro/lib/components/fabro-workflow/src/model_fallback.rs:199`) resolves each key
against the catalog and fails the run at compile otherwise. So all K slots must be permanent
catalog rows, also when no Coder is bound to them.

**A catalog row fixes `capabilities` and `limits`.** A slot re-pointed at a different model
must keep a shape that the model accepts (the glm-4.7 `reasoning` lesson in `AGENTS.md`).

**What fabro does today when z.ai is at its limit: it does not wait.**

1. The LLM call gets 3 attempts with 0.5 s then 1 s backoff, jittered
   (`fabro-llm/src/client.rs:23`), so about 1.5 s. A `Retry-After` is honoured only up to
   60 s (lithos-llm `middleware/retry.rs`, `DEFAULT_RETRY_AFTER_CAP`).
2. Then it fails over along `[run.model.fallbacks]`. A 429 is `RateLimit`, which is
   failover-eligible (lithos-llm `types/error.rs`, `failover_eligible`).
   `glm-5.3 -> kimi-k3`; `glm-5.3-flash -> glm-5.3`, which is the same z.ai account.
3. Then the node retries (`max_retries`, backoff 5 s doubling to 60 s,
   `fabro-workflow/src/retry.rs`), then takes its unconditional edge. In the merge phase
   that is `mark_needs_human`.

**A 402 (credit spent) is `ErrorKind::Provider`**, which is neither retryable nor
failover-eligible, so the stage fails outright (`fabro-llm/src/gateway.rs:107-121`).

**Observed on the host** (fabro logs retained since 2026-09-25):

| Mid-stage failover | Count, 2 weeks | Lands on a limited model? |
|---|---|---|
| `kimi/kimi-k3 -> zai/glm-5.3` | 252 | yes. The Kimi weekly quota was exhausted on 2026-10-03 |
| `kimi/kimi-for-coding -> zai/glm-5.3` | 10 | yes |
| any coder box `-> glm-5.3-flash` | 0 | yes |
| `glm-5.3-flash -> glm-5.3` | 0 | yes |

No z.ai 429 appears anywhere in those logs (two Coders, so low z.ai demand). The "z.ai = 3
sessions account-wide" figure in ADR 0001 was an assumption. The operator now states the
limits are **per model and do not overlap**: `glm-5.3` 3, `glm-5.3-flash` 5.

**Lease length**, from the scheduler's `/api/history` (144 leases): median 3.8 h, p75 5.9 h,
p90 7.7 h, max 23.4 h. Drain waits that long.

**Host:** 16 cores, 30 GB RAM. Sandboxes are 2 CPU / 4 GB (`writers-app` 4 CPU / 8 GB).

**The fabro hook surface makes a wait possible without a proxy**
(`context/fabro/lib/components/fabro-hooks/src/executor.rs`,
`fabro-workflow/src/lifecycle/hook.rs`, `fabro-core/src/executor.rs`):

- `stage_start` is blocking by default and runs in `before_attempt`, **once per attempt**.
  `stage_complete` and `stage_failed` run in `after_node`, **once per node**.
- An `http` hook POSTs the hook context (`run_id`, `node_id`, `attempt`, no model) and
  waits up to `timeout_ms` (default 60 s). It **fails open**: a transport error, a timeout or
  a non-2xx answer returns `Proceed`. A plain `http://` URL needs `tls = "off"`.
- `before_attempt` runs before `handler.execute`, and the node timeout is applied inside
  execute (`node_handler.rs:110`). A hook wait therefore appears **not** to consume the node
  timeout. Confirm this live.
- The stall watchdog (`pipeline/execute.rs:85`) sees no activity during a hook wait, so a
  wait must stay well under `stall_timeout` (60 m in both root graphs).
- A host-side command hook has no timeout at all (`wait_with_output` with no
  `tokio_timeout`).

**OpenRouter is a fabro built-in provider** that also carries `glm-5.3` and `kimi-k3`
(`AGENTS.md`, finding R5). Storing `OPENROUTER_API_KEY` would let it win resolution for those
ids. A hosted Coder must use an overlay-defined provider id with its own credential name.

## Decisions settled in the session

| # | Decision |
|---|---|
| D1 | **No proxy in the token path.** The slot proxy recommended in rev 1 of this record is rejected (the LiteLLM lesson, ADR 0007). Fabro stays the only thing that talks to providers. The hung-request timeout stays a separate open item and is not bundled here. |
| D2 | **Static Coder slots in fabro, re-pointed by a guarded helper.** K = 8 catalog rows and their fallback rows, committed once. Keep the existing ids: the slots are `coders-a`, `coders-b`, `coders-c` … `coders-h`, so stylesheet defaults and `fabro-fire-backlog.sh` do not change. An unbound slot points at a placeholder endpoint. A host helper (`fabro-coder add <name> <base_url> <api_model> [--key-env VAR] [--capacity N]`) picks a free slot, renders that slot's provider table from a template, parses it with `tomllib`, writes it atomically, waits for the 5 s reload, checks `docker logs --since 1m fabro-fabro-1 \| grep -c "Rejected reloaded"` is `0` (rolls back if not), then registers the Coder with the scheduler. |
| D3 | **The scheduler database is the source of truth for Coders**: name, slot, capacity, drained. `coders-a` and `coders-b` are seeded. `DEFAULT_CODER_POOLS` is deleted. `choose_next` reads free, undrained Coders with spare capacity. |
| D4 | **Capacity per Coder**, default 1. A hosted Coder may hold more than one lease. A Coder maps to one slot whatever its capacity. |
| D5 | **Drain** is pause. **Remove** is a separate, explicitly labelled control: cancel every run leased on the Coder, put each issue back to `agent`, set an **Override** on it so it goes to the front of the queue, free the slot. No "reclaim mid-run" control. |
| D6 | **Budget is the provider's job.** Fabro and the scheduler do not track spend. A credit limit lives on the OpenRouter key. |
| D7 | **`max_concurrent_runs` goes from 4 to 8**, in its own task, at an idle moment (a fabro restart fails every in-flight run). The scheduler is the real concurrency control. |
| D8 | **Coder health.** Auto-drain a Coder after N consecutive failures and send one Discord alert. N is configurable, default **5**. |
| D9 | **A Coders page** in the scheduler UI, beside the queue and history pages: Coders (slot, capacity, leases, drained, failure count) with drain, undrain and remove (remove behind a confirmation), and the Model limits with the Permits held and the stages waiting. Adding a Coder is helper-only, because the helper also writes the overlay. |
| D10 | **Permits for hosted models, granular per `(provider, model)`.** A blocking `stage_start` HTTP hook long-polls the scheduler for a Permit for the stage's model. `stage_complete` and `stage_failed` hooks release it. The scheduler reaps the Permits of terminal runs. The hook fails open. Wait bound about 30 min, then the stage proceeds and takes its chances with the fallback chain. Seed limits: `zai:glm-5.3` = 3, `zai:glm-5.3-flash` = 5. |

**Design consequence of D10 (proposed, not yet confirmed by the operator):** key a Permit by
**run**, not by stage. A run holds at most one Permit (its stages are sequential).
Acquiring a new one releases the old, and an acquire repeated by a retried attempt of the
same node is idempotent. This removes the acquire-per-attempt and release-per-node mismatch.

## Open questions (asked, not answered)

These were the frontier when the session stopped. Each has the recommendation that was put
to the operator.

1. **Overflow from a mid-stage failover** (Q10/Q14). A failover inside a stage reaches a
   limited model without a Permit, and the observed source is the Kimi quota running out.
   Options: (a) a **reserve**: grant `limit - reserve` Permits, default reserve 1; (b) an
   **unlimited tail**: every fallback chain that enters a limited model ends in an unlimited
   one, for example `kimi-k3 = ["zai:glm-5.3", "openai:gpt-6-luna"]`; (c) **follow the
   failover**: the scheduler marks a model *out* when it sees `agent.route.failover` away
   from it, and while the model is out, stages whose primary is that model take a Permit
   for the next model in their chain. The mark expires or is cleared on the page.
   *Recommended:* (a) and (b) in v1, (c) as a later task once the measurement shows whether
   the reserve is enough. The operator asked "is there any way to work around it" and did
   not choose.
2. **How the scheduler learns a stage's model** (Q15). *Recommended:* generate the hook rows
   into each `workflow.toml`: one `stage_start` hook per limited model, with `matcher` set
   to an anchored regex of the nodes whose class resolves to that model (allow the
   `review_merge.` prefix) and `?model=zai:glm-5.3` in the URL, plus the release hooks. Do
   this from the stylesheet and the node classes, as `ops/fabro-io-manifest.py` generates
   `FABRO_IO_MANIFEST`, with `check` failing on drift. The alternative, a map held by the
   scheduler, drifts from the version a run was compiled from.
3. **What counts as a Coder failure for D8** (Q16). *Recommended:* count both (i) a failed
   **idle** probe (`GET /v1/models` only, never a completion, and only while the Coder holds
   no lease) and (ii) an `agent.route.failover` away from the Coder's slot during a leased
   run, read on the existing 30 s probe beat. A success resets the count. A failed run does
   not count.
4. **Where Model limits and the reserve live** (Q17). *Recommended:* the scheduler
   database, edited on the Coders page. A file in git means a scheduler rebuild, and every
   rebuild re-dates the draft-14 window.
5. **Which workflows take Permits** (Q18). *Recommended:* all four. `issue-triage` and
   `arch-review` use `glm-5.3` and are not dispatched by the scheduler.
6. **Order of waiting stages** (Q19). *Recommended:* FIFO per model. The alternative is
   merge-phase stages first.

## Questions not yet reached

- **Slot shape for a hosted model.** The OpenRouter model is not chosen. If it does not
  accept the slot rows' `capabilities = { reasoning = true }` and limits, the slots need a
  second shape, or the helper must also rewrite `capabilities`/`limits`. Settle this when
  the model is named.
- **Credentials for a hosted Coder.** Confirm how an overlay provider with `auth = { type =
  "bearer" }` names its vault secret (Kimi uses `KIMI_API_KEY`), and that the helper's
  `--key-env` maps to `fabro secret set <NAME>` with no restart.
- **The slot fallback chain.** Today `coders-* = ["zai:glm-5.3-flash", "openai:gpt-6-luna"]`.
  Whether a hosted Coder's slot should fall back the same way depends on open question 1.
- **Measurement task.** z.ai's real behaviour at its limit: a 429 or a server-side queue,
  any `Retry-After`, and whether the operator's stated limits (3 and 5, independent) hold.
  Fire N concurrent requests per model at an idle moment. The seed limits stand until this
  is done.
- **Live confirmation** that a `stage_start` hook wait does not count against the node
  `timeout`, and how fabro shows the waiting stage in `fabro events` and on the scheduler
  page.
- **`CONTEXT.md` still describes the Cap as 4** and the Coder lease record as `(box, …)`.
  Update both when D7 and D3 are applied.

## Suggested next steps

1. Answer the six open questions above (they are independent of each other).
2. Write `docs/adr/0017-elastic-coder-fleet.md` from the decisions table. Use the format of
   ADR 0013–0016. It supersedes the "two boxes" assumption in ADR 0006, and refines ADR 0001
   (per-model limits) and ADR 0007 (no proxy, slots in the overlay).
3. Write `docs/elastic-fleet/00-overview-and-contracts.md` and the tasks. A likely order:
   measurement → `max_concurrent_runs = 8` → Coder registry and seeding → slots and fallback
   rows → helper → Remove and requeue → health and alert → Coders page → Permit service →
   generated hooks → overflow workaround → docs/AGENTS.md invariants → live verification
   (add a third Coder, dispatch, drain, remove; saturate `glm-5.3` and watch a stage wait).
4. Add the new deployment invariants to `AGENTS.md` when they are applied (slots are
   permanent catalog rows; the hook rows are generated; Permits fail open).

## Suggested skills for the next session

- `grilling` and `domain-modeling` (or `grill-with-docs`) to close the open questions and
  keep `CONTEXT.md` current.
- `fabro-workflow` before touching any hook, stylesheet, `workflow.toml` or fallback chain.
- `issue-decomposer` or `request-refactor-plan` style thinking for the task series (the
  repository's own series format is the template; read `docs/merge-gate/00-overview-and-contracts.md`).
- `fastapi-pro` and `tdd` for the scheduler registry, Permit service and Coders page
  (`ops/scheduler/`, tests under `ops/scheduler/tests/`).
- `unslop` for the ADR and task prose, to match the repository's style.
