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

**The host runs `0.354.0-nightly.0`, not `0.357.0`** — `FABRO_VERSION` in `~/fabro/.env`,
and `fabro version` inside the container agrees (build 2026-09-12). Findings 1–8 were read
from a *newer* checkout than the deployed binary, so take their line numbers as pointers
to the idea and re-check behaviour against the container before relying on it. Findings
9–11 were read out of the deployed tag and the live API.

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

### 9. The terminal status set is `succeeded | failed | dead`, and a cancel is a `failed`

`RunStatusKind` has eleven variants and `is_terminal()` matches exactly three of them:
`succeeded`, `failed`, `dead`
(`lib/foundation/fabro-types/src/status.rs:22-33,80-86` at `v0.354.0-nightly.0`). The live
`GET /api/v1/openapi.json` agrees — the `RunStatus` discriminator lists the same eleven
names, and the only terminal ones are those three.

There is no `cancelled` kind and no `errored` kind. Cancelling a run produces
`{"kind":"failed","reason":"cancelled"}`, verified live on 2026-09-18 by cancelling run
`01M2V2ZWWWPBZQM4NBY40H0Q24` and reading it straight back out of `GET /runs`. `dead` is a
reachable escape hatch (`can_transition_to` accepts it from any status,
`status.rs:132-135`) and is terminal, so a Release rule that lists the kinds by name must
include it or a dead run holds its lease forever.

### 10. The failure `category` is not in the run projection, and the canonical infra failure is not `transient_infra`

The Requeue rule keys on the failure `category`. Two problems, both verified live on
2026-09-18 against all 315 runs in the store.

**The category is not in the run projection.** `GET /runs` and `GET /runs/{id}` return
`lifecycle.status` (kind and reason), `lifecycle.error`, `queue_position` and `archived`,
and nothing else about the failure — `RunLifecycle` in the live OpenAPI has no category
field, and on a cancelled run `lifecycle.error` is `null`. The category exists only in the
`run.failed` event payload, at `properties.failure.detail.category`, so classifying a
terminal run costs one extra call — and it has to be read from the **tail**:

```
GET /runs/{id}/events?order=desc&limit=100     the run.failed event is in there
```

Verified on the 10.4-hour run `01M2PSA224HXTXJSSVBGTVPYF1` (3233 events) and on a 5-event
run alike. An ascending read cannot work: the failure is the second-to-last event, the
endpoint caps at 1000 events, and `page[offset]` is ignored on it (finding 11). A
first-page ascending read of any run with more than 100 events contains **no** `run.failed`
event at all, which is indistinguishable from "this run has no failure category".

The reason, by contrast, *is* in the projection as `lifecycle.status.reason`
(`FailureReason`, `status.rs:304-315`) — so a scheduler that can decide on `reason` alone
never needs the events call.

**The failure decision 10 exists to requeue is classified `deterministic`.** The runs this
deployment loses to a fabro restart (finding 5) report:

```
reason:   "terminated"
message:  "Fabro server restarted before the run reached a terminal state."
category: "deterministic"
```

Sixty-six of the 315 runs ended `failed/terminated` — the largest failure class — and every
one sampled carried exactly that message and that category (`terminated` also covers
`"Worker exited before emitting a terminal run event"`, same category). Forty-nine ended
`failed/cancelled`; one ended `failed/bootstrap_failed` with category `deterministic`; none
carried a `transient_infra` reason; none were `dead`. A requeue predicate of
`category == "transient_infra"` therefore requeues **nothing** on a fabro bounce — the issue
keeps `agent-in-progress` and waits for a human, which is the failure the whole design was
written to avoid. This is a fact about fabro, not a proposal: which reasons are requeued is
decision 10's business. Draft 09 has to name `terminated`-with-a-restart message alongside
`transient_infra`.

**"Cancel" is spelled two ways, and only one of them is the category.** A cancelled run
reports `reason: "cancelled"` (two Ls) under `kind: "failed"`, but
`category: "canceled"` (one L), with `message: "Pipeline cancelled"`. Verified live on
2026-09-18 by cancelling run `01M2V80N42R3V08SWHPNQBY81J`. The requeue predicate keys on the
category, so a predicate written against `"cancelled"` matches nothing at all — the same
silent-no-op shape as `category == "transient_infra"` matching no fabro bounce. Drafts 07
and 09 both listed `cancelled` as a status *kind*; the kinds are finding 9's
`succeeded | failed | dead`. Also worth knowing: a cancelled run is the one failure whose
repair is not optional, because it exits before the graph's terminal label work — the issue
keeps `agent-in-progress` and is invisible to `acquire` until a human restores `agent`.

### 11. The two list endpoints page with different, mostly-undocumented parameter names

`GET /runs` reads `page[limit]` (max 100, default 20) and `page[offset]`. A bare
`limit=100` is not an error and is not honoured — it is clamped to 20, so the response looks
like a 20-run store. `meta.total` (315 here) is the only trustworthy count; `meta.has_more`
says whether the page was truncated.

`GET /runs/{id}/events` reads `limit` (max 1000, default 100), `order` (`asc`/`desc`),
`since_seq` (ascending only) and `before_seq` (descending only). The bracketed
`page[limit]`/`page[offset]` that work on `/runs` are **ignored** here, silently falling
back to the first 100 events.

Nothing in the dispatch loop needs either kind of pagination — it polls its own lease rows
by id, and `reason` alone classifies the common failure (finding 10). But a
reconcile-the-world step that enumerates runs, or a classification step that reads an event
stream from the head, is quietly wrong rather than loudly broken.

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
(`succeeded`, `failed`, `dead` — finding 9), observed by the 15s poll.

### Requeue

Only when the run's failure `category` is `transient_infra`. The scheduler removes
`agent-in-progress`, restores `agent`, and the item re-enters the queue keeping its
original enqueue time so the starvation ceiling is not reset. Finding 10 covers where
that category can be read, and why a fabro restart does not carry it.

### Recovery

On scheduler start: read lease rows; for each, query fabro. A run that is terminal, or
that fabro has never heard of, releases its lease. Then reconcile GitHub: any issue
labelled `agent-in-progress` that neither a live lease nor a live fabro run accounts for
is un-labelled and requeued.

The GitHub half also runs **every 10 minutes**, not only at start. A receipt is orphaned
by any ending that skips the graph's own label work, which a restart is not required to
produce — see the dated block's `mark_stuck` finding. While the dispatch loop is running
the scan cannot act on a single sighting, because dispatch writes the receipt seconds
before the run exists: an issue must look orphaned in **two consecutive scans** before
anything is written.

## Before you start

Read `AGENTS.md` first — it carries the deployment invariants, the validation
recipe and the rule that **pushing to `main` deploys**. Then read this file, then
your own task file. You should not need anything else.

What each frontier task needs, so you find out now rather than halfway through:

| Draft | Access it needs | How to get it |
|---|---|---|
| 01 | LiteLLM **master key** (`PROXY_ADMIN`). `FABRO_LITELLM_KEY` is an `internal_user` and `POST /model/update` answers **403**. | `kubectl -n litellm exec deploy/litellm -- printenv PROXY_MASTER_KEY` (context `admin@nauvoo`) |
| 02 | fabro dev token; `ssh andrew@10.10.0.32` | `ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/server.dev-token'` |
| 03 | fabro dev token; ssh; container settings overlay at `/storage/.home/settings.toml` | same as 02 |
| 04 | ssh + docker on the host. No secrets. | — |

Later drafts additionally need a **GitHub token** with `issues: write` on the four
target repos (drafts 06, 08, 09, 10).

There is no `fabro` binary on this Mac. Every `fabro validate` runs inside the
container — `AGENTS.md` has the rsync/`docker cp` recipe and the current baselines.

**This repository is public.** No token, key or webhook URL belongs in a tracked
file, a comment, or a command's output.

### Validating before you push

Whatever your task touches, if it changes anything under `.fabro/`:

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && for w in backlog pr-review issue-triage; do \
  docker compose exec -T fabro fabro validate /tmp/check/workflows/$w/workflow.toml; done'
ssh andrew@10.10.0.32 'cd ~/fabro && python3 /tmp/check-routing-schemas.py \
  /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro'
```

`fabro validate` does **not** catch a routing-schema mismatch on a command node,
in either direction; both fail only at runtime. Run the checker too.

## Task series

**Status, 2026-09-19: drafts 01–13 are built and deployed; draft 14's shakedown
window is running from `15:13:51Z`. The dated block at the end of this file is the
current state — read it after this table.**

Fourteen drafts. The initial frontier is 01, 02, 03 and 04 — fully parallel.

| # | Task | Blocked by |
|---|---|---|
| 01 | Split the `coders` model group into `coders-a` and `coders-b` | — |
| 02 | Fix `fire-pr-review.sh`: root the workflow version at `.fabro/` | — |
| 03 | Delete `issue-triage`'s capacity check and raise the run cap to 4 | — |
| 04 | Scheduler skeleton: container, `repos.toml`, health endpoint | — |
| 05 | Thread `coder_pool` through both root stylesheets | 01 |
| 06 | GitHub inventory with ETag caching, and a read-only queue page | 04 |
| 07 | Fabro client: register a `.fabro`-rooted version, create and start a run | 02, 04 |
| 08 | Lease state machine and the dispatch loop | 05, 06, 07 |
| 09 | Release, requeue and recovery | 08 |
| 10 | Collapse `acquire`/`claim` and add the manual-fire script | 08 |
| 11 | Reorder, drain and cancel in the web UI | 09 |
| 12 | Add scheduler conditions to `fabro-monitor.sh` | 11 |
| 13 | Turn off the four `backlog` automation schedules | 10 |
| 14 | Shakedown and deployment-log entry | 12, 13 |

Two drafts are unsafe to leave running unattended before their successor lands:
**08** acquires leases and never releases them until **09**, and **13** makes the
scheduler the only producer of `backlog` runs, so **10** must already have shipped
the manual escape hatch.

Each draft is written to be implementable from this file plus its own file plus
the repository — no other context.

**Finding, 2026-09-19 (found by draft 08's acceptance run).** Drafts 08 and 09 both
assume the run works the issue the scheduler dispatched. It does not until draft 10:
`acquire` still selects, so run `01M2VHAGXNM9JNEY71085HV82Y` claimed and worked
`jelly-swipe#351` while the scheduler labelled and leased `jelly-swipe#350`. Every lease
row this scheduler writes therefore names the scheduler's pick, not the run's. **Do not
deploy 09 before 10.** 09's GitHub pass would un-label the run's own claim (`#351` has no
lease) and leave the scheduler's orphan in place (`#350` has the lease) — both halves of
its intent inverted. Draft 09's file carries the detail and the two ways out.
## Status, 2026-09-19 (draft 14, session 2) — drafts 01–13 deployed; the shakedown window is RUNNING

This block is the series' current state. Later text above is planning history; where it
and this block disagree, this block is right. It supersedes session 1's block, which said
the container was stopped and the window unstarted — both were true when written and
neither is true now.

**Built and live.** Drafts 01–13 are committed on `main` and the host runs that image.
Everything the earlier drafts left "not yet deployed" is deployed: release, requeue and
recovery (09), the collapsed `claim` (10), the page's bump/drain/cancel controls (11), the
monitor's C7/C8 (12) and the four `backlog` schedules off (13).

**The window runs from `2026-09-19T15:13:51Z`** — the scheduler container's `StartedAt`.
It was first armed at `14:55:27Z`, when the `claim` fix landed; the restart that deployed
this block's own follow-ups moved the clock, and the unattended count runs from the later
time. Draft 14's acceptance criteria are **pending**, not unstarted: elapsed time is the
only thing most of them are waiting on.

Evidence from the earlier arming still stands, because the restart carried it over rather
than resetting it — the two leases were **adopted**, not released, keeping their original
`dispatched_at` of `14:55:38Z` and `14:55:41Z`, and their runs never paused:

- ≥1 run on each of `coders-a` and `coders-b`, dispatched three seconds apart;
- startup recovery released both of session 1's stale leases and requeued the two receipts
  behind them (`jelly-swipe#353`, `lawncare-saas#2277`);
- `jelly-swipe#356` reached a **succeeded `coder` stage** on `coders-a`, which is the first
  end-to-end proof that a pinned box serves a whole coder stage.

### The `claim` regression, and what it changed

Session 1's bring-up armed the loop for 5m8s and both runs it dispatched died at the second
node: `bash: line 13: /tmp/fabro/issue.json: No such file or directory`. Draft 10 deleted
`acquire`, `acquire` was the only node that ran `mkdir -p /tmp/fabro`, and `claim` — the
run's first node since — still redirected into that directory. `prep` creates
`/tmp/fabro/review` one node too late. Every `backlog` run failed there, including
`ops/fabro-fire-backlog.sh`, so the factory's only two producers of work were both dead.

`5fa974d` restored the `mkdir`. Two scheduler-dispatched runs cleared `claim` and `prep`
within minutes of the re-arm, which is the evidence that closed it.

Three things came out of that failure and are now part of the design rather than notes
about one incident:

1. **`ops/test-task-gates.sh` covers `claim` and `mark_stuck`** (112 checks, up from 82).
   Session 1 recorded "no offline gate can catch it" as a fact about the tooling; it was a
   fact about the gate's coverage. `claim` is staged against a sandbox directory that does
   **not** exist, which is the only way a test can observe a node creating one — every
   other gate rebases onto a directory the harness already made. Deleting the `mkdir`
   again now fails eight checks offline.

2. **`mark_stuck` no longer depends on `issue.json` alone.** It read the issue number only
   from that file and did no label work at all when the file was missing — which is
   exactly the run that reaches it after `claim` fails. That is what left `jelly-swipe#353`
   and `lawncare-saas#2277` wearing `agent-in-progress` with nothing working them. `claim`
   now writes `/tmp/fabro/issue_number` before any network call and `mark_stuck` falls back
   to it, the same shape `pr-review` already uses for `pr_number`.

3. **The receipt scan runs periodically, not only at startup.** Draft 09 justified
   startup-only with "a question only a restart can raise"; the `mark_stuck` stranding
   raised it with the scheduler up and healthy. `ReleasePoller` now runs the scan every
   10 minutes as well. Because `_dispatch` writes the receipt several seconds before the
   run exists — it clones `main` in between — a scan landing in that gap sees exactly what
   an orphan looks like, so a receipt must appear orphaned in **two consecutive scans**
   before anything is written. Cost: four uncached GitHub requests per scan, 24/hour
   against a 5,000/hour budget.

### A fourth thing, accepted rather than fixed

A `claim` failure is infra-shaped in cause and agent-shaped in outcome, and the requeue
rule cannot see it. The node's unconditional edge parks the run on `human_rescue`, whose
default choice is `mark_stuck`, so the run ends **`succeeded`** — and decision 10's
predicate keys on a *failed* run's reason. A transient `gh` blip at `claim` therefore costs
a coder box for up to four hours and never requeues.

`claim` now retries `gh issue view` three times, five seconds apart, which removes the
common cause. The structural gap is left open on purpose: closing it means either
releasing a lease on `blocked` (which ADR 0006 rejected, because there is no mechanism to
re-acquire one) or teaching the scheduler to read a succeeded run's *stage* outcomes, which
is a second definition of failure to keep in step with fabro's.

### Observation for whoever reads the window's numbers

Every queue item currently shows `waited_seconds` around 58,000 (≈16h), far past the 4h
ceiling `T`, so the **entire** queue sits in decision 5's ceiling tier and is ordered
oldest-first across repos. Repo priority is inert until the queue drains below `T`. That is
the ceiling working as designed. A fresh database — where every `first_seen` starts at the
scheduler's first sighting — orders by repo priority instead, and the two states must be
told apart before anyone reads a priority inversion out of the page.

No credential appears in this block.
