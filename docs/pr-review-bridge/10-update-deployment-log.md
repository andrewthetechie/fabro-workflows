# Task 10 — Update the deployment log

**Depends on:** 08, 09. **Blocks:** nothing. **LLM level:** local is fine.

Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac
(mode 600, deliberately outside this public tree). **Append — never edit an earlier
section.** The log is a chronological record.

## What this entry has to carry

The log's value is that it records what each deployment did **and did not** prove,
plus the bugs found on the way. This stage found three things in fabro's behaviour
that are not written down anywhere else, and two of them contradicted this repo's own
documentation. Those belong in the log even though task 09 also fixed the docs —
the docs say what is true now, the log says what was believed before and why it was
wrong.

### The three findings

1. **`POST /automations/{id}/runs` ignores its request body.** Declares no body in
   the OpenAPI document; returns `422 run_compile_invalid` when fired with
   `inputs`, and creates no run. `AGENTS.md` had documented that call as the way to
   fire a review. Corroborated by run history: all four historical `PrReview` runs
   have `automation: null`.
2. **`house` / `stack.child_workflow` is not a durable child run.** It validates
   against sibling packages, but `manager_loop.rs` shares the parent's `run_id`, uses
   `WorkflowSettings::default()` (discarding the child's `workflow.toml`), passes the
   parent's `inputs`, and discards artifacts to an in-memory store.
3. **`fabro_run_create` exists and is disabled server-side.** `fabro-server`'s
   run-execution path passes `fabro_run_tools: None`; only the CLI runner enables it.
   Record this as a **watch item**: if a future fabro version enables it, the
   `trigger_review` command node can be replaced by a native tool call, and the API
   token can come out of the sandbox entirely.

### The E2E result

Record the backlog run id, the `pr-review` run id it spawned, the PR, and
specifically:

- whether the child run's clone was **shallow or full** — the property the whole
  design rests on
- the child's sandbox image
- `parent_id` / `children_count`
- `lifecycle.queue_position` at creation, whatever it was

That last one is the only backpressure evidence this deployment has. `queue_position`
exists in the schema but was `null` across all 20 runs before this stage, so nothing
here has ever exercised `max_concurrent_runs = 3`. Start the record now, while it is
cheap, rather than when four 15-minute schedules are enabled and each PR also spawns
a review.

### What this stage did **not** prove

Be explicit, in the log's usual style:

- **Nothing about concurrency.** One backlog run, one review. Queuing behaviour at
  `max_concurrent_runs` remains unobserved.
- **Nothing about the other three repos.** The bridge went live on all four; only
  `jelly-swipe` was exercised. `lawncare-saas`, `womens-fantasy-sports` and
  `writers-app` are live-but-untested, and `writers-app` additionally has no
  `api:manual` trigger on its backlog automation.
- **Nothing about the double-fire guard.** `/tmp/fabro/review_triggered` was not
  exercised unless the E2E happened to route through `human_rescue → [P] Accept
  partial`.
- **Nothing about token rotation.** fabro supports one dev token; the bridge shares
  the server's. Untested and, by construction, not independently revocable.

### Operational notes worth recording

- The vault entry `FABRO_API_TOKEN` is now a **hard dependency of every backlog
  run**, not just of the bridge. `{{ secrets.* }}` fails closed, so removing it stops
  backlog on all four repos at startup. That is also the fastest kill switch, and it
  is deliberately blunt.
- The four `pr-review-*` automations are now configuration-only.
- A leftover sandbox container was observed up for four hours after its run had
  finished (`fabro-run-01M2JS6Y37TTQ2QDMQZK7GKB9N`). Unrelated to this stage, but
  worth a line — sandbox lifecycle may not be reaping cleanly.

## Acceptance

- A new dated section exists; no earlier section was modified.
- Both run ids and the PR are recorded.
- The clone-depth result is stated explicitly, in those words.
- The "did not prove" list is present.
- File mode is still 600 and it is still outside this repository.
