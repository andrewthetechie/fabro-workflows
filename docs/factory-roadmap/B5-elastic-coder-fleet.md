# B5 · Elastic coder fleet: add, drain and remove boxes without a commit

**Status:** proposed. **Axis:** throughput. **Effort:** M–L. **Feasibility:** medium-high.
**Depends on:** nothing. **Replaces:** rev 2's "P2-8 manual fleet".

## Problem

The operator wants to bring coder boxes online when they are free (a 3090, a borrowed
machine, an opencode-go subscription), use them, drain them after a job, and remove them.

**Drain already works:** `POST /api/pools/{pool}/drain` stops new dispatch and never destroys
work (`ops/scheduler/src/fabro_scheduler/app.py:573`). **Adding a box does not.** It needs:

1. a provider table plus a model row in fabro's settings overlay
   (`/storage/.home/settings.toml` in the container), with `display_name`. Omitting it is the
   2026-09-20 14-hour outage;
2. a `[run.model.fallbacks]` row for the new model id in `backlog/workflow.toml`, which is a
   commit to `main`;
3. a new name in `DEFAULT_CODER_POOLS` (`ops/scheduler/src/fabro_scheduler/config.py:41`),
   which is a code change and a rebuild;
4. a scheduler restart.

Nothing notices a dead box. A box that returns errors keeps being leased, and fabro silently
falls back to `glm-5.3-flash` for the whole run. The `fallback` Discord hook fires only on
specific escalation nodes.

## Design: static slots in fabro, a dynamic registry in the scheduler

**Provision K slots once** (say `coder-1` … `coder-8`): K catalog rows and K fallback rows,
committed and deployed once. Each slot's `base_url` points at the scheduler host:
`http://10.10.0.32:<proxy-port>/slot/<n>/v1`. From then on, fabro's config never changes when
the fleet does.

**The scheduler owns a registry and a slot proxy:**

- `POST /api/pools {base_url, api_key_ref, model, ctx, label}` registers a box. The scheduler
  probes `GET /v1/models` and a 1-token completion, assigns a free slot, and marks it
  undrained. The key is referenced by name from `scheduler.env`, never stored in the database.
- `POST /api/pools/{slot}/drain` (exists today), then `DELETE /api/pools/{slot}` once its lease
  has ended, which frees the slot.
- A **liveness beat** (every 30 s, in the existing probe thread) auto-drains a slot after N
  failed probes and sends one Discord message, with a cooldown.
- The **proxy** forwards `/slot/<n>/v1/*` to the registered `base_url`, streaming SSE through
  unchanged, and enforces a **stream-idle timeout**. That is the bound on a hung inference
  request that ADR 0007's task 02 recorded as lost when fabro went direct to providers and was
  never replaced.
- `choose_next` reads free, undrained, registered slots from the registry instead of the
  constant.

**Slot metadata** (`ctx`, measured tok/s, `label`) lets B2 later send `l`-sized tasks to the
fastest box.

## Alternative: the scheduler edits fabro's overlay

Fabro hot-reloads the catalog in about 5 s, and a worker reads it at run start, so a running
run is not disturbed. But a bad write **fails silently**: the server keeps the old catalog and
reports healthy, while every new worker dies. If this route is chosen, the writer must render
from a template, parse it with `tomllib`, write atomically, then check
`docker logs --since 1m fabro-fabro-1 | grep -c "Rejected reloaded"` for `0` and roll back
automatically. It also needs the Docker socket read-write, which the scheduler deliberately
does not have (`ops/docker-compose.yaml`, the `:ro` comment).

**Recommendation:** the slot proxy. It keeps fabro's config static, adds the missing timeout,
and needs no write access to fabro. The cost is a process in the token path. The load is
trivial (about 20 tok/s per box), but it is a single point of failure, so it lives in the
scheduler container, which is already required for any `backlog` run to exist.

**Off-the-shelf variant:** a host-local LiteLLM container (not the old Kubernetes ingress)
with `/model/new` for registration and its own timeouts. Less code, and a known component with
known quirks.

## Interaction with the upgrade

Build this **after** `docs/fabro-upgrade/`, because the catalog format and fallback resolution
are exactly what an upgrade can change.

## Verification

- A scheduler test with a fake upstream: register, probe, lease, auto-drain on failures,
  delete.
- On the host: register a third box (even a second port on box-a), dispatch one run onto it,
  drain, delete, with fabro's config untouched throughout (`md5sum` of the overlay before and
  after).

## Open questions

1. How many slots to pre-provision? Each slot is a catalog row and a fallback row, and unused
   slots cost nothing. Suggest 8.
2. The fallback per slot stays `zai:glm-5.3-flash`. Should a dead slot mid-run fail over to
   another *local* slot instead? Not in v1. That is a second lease.
