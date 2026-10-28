# More local inference: overview and canonical contracts

**Status:** planned 2026-10-28, nothing built. ADR 0031 (proposed). Phase 0's sampler runs on
the fabro host until about 2026-10-30 03:50 UTC (`~/coder-util/box-{a,b}.tsv`, read-only).

**Read this file first.** Every task in this folder assumes the decisions, facts and contracts
below. If a task and this file disagree, this file is correct: stop and report the conflict.
Read ADR 0031 before you start. **Before you edit a `.fabro` file or a `workflow.toml`, invoke
the `/fabro-workflow` skill** and read `docs/agents/invariants-graph.md`. **Before you write
Rust, invoke `/ms-rust`.** Before you touch `ops/scheduler/`, read `ops/README.md` *The coder
scheduler*.

## Hard rule for this series: do not push

The operator reviews before anything reaches `main`, because pushing to `main` deploys (see
`AGENTS.md`).
- Commit each task locally on `main`: one commit per task, with Conventional Commit subjects.
- After each task, run `make check` at the repository root. Use `CHECK_CARGO=always` for any
  task that touches `ops/fabro-io/`. For any task that touches `ops/scheduler/`, also run
  `uv run pytest` in `ops/scheduler/`. All of it must pass.
- Do not run `git push`, `make deploy*`, `make compose-up`, `make check-host`, `ssh`, or any
  `gh` command that writes. Do not edit a target repository.
- The work is in four phases, and **each phase stops for the operator**:

| Phase | Tasks | Then |
|---|---|---|
| 1. Claim and lane plumbing | 01-05 | stop; operator runs task 12, Phase 1 |
| 2. PR doctor | 06 | stop; operator runs task 12, Phase 2 |
| 3. Precheck and context packs | 07-08 | stop; operator runs task 12, Phase 3 |
| 4. Pre-review and long-horizon jobs | 09-10 | stop; operator runs task 12, Phase 4 |

Task 11 (the docs) is done with Phase 1 and amended by each later phase.

## What this is

The coder boxes are idle most of the day. A run holds its box for its whole lease but uses it
only for its box stages: 158 of 432 lease-hours since 2026-10-21. This series fills the gaps
between a coder loop's box stages with **background runs**. These are short fabro runs of
box-only packages, and the coder loop cancels them the moment it needs the box. Every
background job must raise the merge rate or lower hosted token use. No hosted model is
called by background work, and nothing new sits in the coder loop's path.

| Piece | Path | Task |
|---|---|---|
| `box` flag per stage in the Stage manifest, checker rules R13, R14 | `.fabro/workflows/_io/manifest.json`, `ops/fabro-io-manifest.py`, `ops/check-graph-invariants.py` | 01 |
| `fabro-io box-claim` | `ops/fabro-io/src/boxclaim.rs` | 02 |
| The claim hook, the run's box file, `FABRO_BOX_HEALTH` | `backlog`, `ralph`, `pr-review` `workflow.toml` and their `claim` nodes | 03 |
| `coders-*-bg` model rows, the `box-noop` package | `ops/settings.toml.example`, `.fabro/workflows/box-noop/` | 04 |
| Scheduler: labels, background lane, switch, page | `ops/scheduler/src/fabro_scheduler/` | 05 |
| PR doctor package and its one hosted review | `.fabro/workflows/pr-doctor/`, scheduler | 06 |
| Issue precheck package | `.fabro/workflows/issue-precheck/`, scheduler | 07 |
| Context pack package | `.fabro/workflows/context-pack/`, `decompose` and `plan` prompts | 08 |
| In-run box pre-review | `backlog/workflow.fabro` | 09 |
| Repo orientation and flaky-test investigation | two packages | 10 |
| Docs | `AGENTS.md`, `CONTEXT.md`, `docs/README.md`, `docs/agents/*`, `ops/README.md`, ADR 0031 | 11 |
| **Operator only** | sampler analysis, overlay rows, server variable, deploy, measure each phase | 12 |

## Decisions (settled; do not re-open)

| # | Decision |
|---|---|
| D1 | One **coder loop** per box, as the scheduler leases today. A coder loop is a `backlog` or `ralph` run, or a `pr-review` run the scheduler leases (D14). It has **absolute precedence** on its box. |
| D2 | **The unit of precedence is one stage.** A box changes hands only between stages. When the coder loop leaves a box stage (say `coder`) for a hosted stage (say `review`), a background run may use the box until the loop's next box stage begins. Each stage starts a fresh session, so this costs nothing in the KV cache. Holding the box across a whole backlog task was rejected: it gains no cache reuse and loses the gap (ADR 0031 D1). |
| D3 | **No proxy.** Fabro calls strix-infer directly, as today. Two runs never interleave requests on one box. |
| D4 | Background work is whole fabro runs of **box-only packages**: every stage's model is `{{ inputs.coder_pool }}`, which the scheduler sets to `coders-a-bg` or `coders-b-bg` (C5). **At most one background run per box at a time.** |
| D5 | **The claim is fabro's, not the scheduler's.** Before each box stage, the coder loop's `fabro-io box-claim` hook cancels any background run on its box through fabro's run API, then waits for strix-infer to report the box idle (C3). It never calls the scheduler. |
| D6 | The claim exits 0 on every error of its own and gives up after 80 s, inside the hook's 90 s timeout. With the API or the box unreachable, the stage starts as it does today. |
| D7 | **The scheduler decides when** (C7). A background run starts only on a box whose coder loop is outside a box stage, or that has no coder loop, and whose strix-infer has been idle for at least `GAP_S`. Never on a drained box. Background runs ignore the peak-hours pause. |
| D8 | **Fabro's run cap protects the coder loop.** Fabro admits runs first come, first served under `max_concurrent_runs`. The scheduler starts a background run only while at least `BG_SLOT_RESERVE` (3) run slots are free. Before a coder-loop dispatch, it cancels a background run if one is needed to free a slot. |
| D9 | **Every background job is safe to cancel and idempotent.** It writes to GitHub only in its last stage. A cancelled job goes back on the queue; one cancelled 3 times waits for a gap at least twice its typical length. |
| D10 | **Job order, by value:** PR doctor (06), issue precheck (07), context pack (08), then repo orientation and flaky-test investigation (10). The in-run pre-review (09) is not a background job; it runs on the coder loop's own lease. Every job must raise the merge rate or lower hosted token use, and task 12 measures which one it does. |
| D11 | Background outputs live in GitHub (comments, labels, pushes), with hidden markers (C6). The scheduler keeps only job state in its SQLite store. |
| D12 | One switch, `SCHEDULER_BACKGROUND` (`off` by default, compose `on`), turns the lane off without a deploy of anything else. Off means no new background run starts; running ones finish. |
| D13 | **PR doctor: one attempt per pull request, ever** (ADR 0031 D4). Its push is followed by exactly one automatic `pr-review` run. If that review does not end with the PR merged or queued, the PR gets `ai-review-needs-human` and a comment saying a human must take it. The doctor never touches that PR again. |
| D14 | The doctor's `pr-review` run spends hosted credits and uses box stages (`rebase_agent_t1`, `refute_fix`, `resolve_base`), so it is a **coder loop**. It takes a primary lease, obeys the peak-hours pause, and its box stages claim. |
| D15 | Stay on fabro 0.362. Nothing here needs a newer build. A later upgrade is its own rehearsed series (`docs/fabro-upgrade/`) and must skip 0.370-0.386, where `stall_timeout` was not enforced (fabro PR #1032). |
| D16 | Out of scope: a z.ai credit governor, a second coder loop per box, in-run parallel background branches, scheduled automations for the lane, and a proxy. |

## Facts (verified 2026-10-28)

- **Boxes.** `coders-a` is `http://10.10.0.29:5300`, `coders-b` is `http://10.10.0.56:5300`, both
  strix-infer serving `qwen3.8-flash-next`. `GET /health` returns JSON with `busy` (bool),
  `queued` (int), `idle_seconds` (float) and `live_tokens`. `GET /metrics` is Prometheus text;
  `strix_requests_total{outcome="cancelled"}` counts cancelled requests. `ops/settings.toml.example`
  is stale for box-b's limits row; the live overlay's `coders-a`/`coders-b` rows carry no
  `limits` line.
- **Gaps.** Since 10-21, 157 runs: 432 lease-hours, 158 box-stage hours, and 274 gap-hours.
  Gaps have a median of 11 min (p75 20, p90 49), and 253 h fall in gaps of 10 min or more.
  Box busy share for the whole day, 10-25 to 10-27: box-a 32-39%, box-b 44-48%.
- **Box stages today**, the nodes whose stylesheet class resolves to `{{ inputs.coder_pool }}`:
  - `backlog`: `coder`, `rework_t1`, `resolve_merge`, `extra_decompose`, `plan` (only in the
    box-plan trial's box arm), and from the imported phase `review_merge.refute_fix` and
    `review_merge.resolve_base`.
  - `ralph`: `iterate_t1`, `learn`, `resolve_base`, and `plan` (box arm only).
  - `pr-review`: `rebase_agent_t1`, `refute_fix`, `resolve_base`.
  - `dependabot`: none.
- **The box-plan trial's arm** is `(issue_number // 2) % 2 == 1`, written as a stylesheet
  `{% if %}` in `backlog` and `ralph` (`docs/plan-box-trial/`).
- **Hooks.** A blocking `stage_start` hook's wait counts toward the run's `stall_timeout`.
  Verified with a probe run: a 100 s hook under a 1-minute watchdog failed at 60 s. Sandbox
  hooks run with `FABRO_WORKFLOW` and `FABRO_NODE_ID` in the environment
  (`ops/fabro-io/src/stage.rs`). The manifest arrives as `FABRO_IO_MANIFEST`
  (`ops/fabro-io/src/manifest.rs` `load()`). A blocking hook must exit 0 on its own failure;
  fabro treats any other exit as a block (invariants-graph *Agents and hooks*).
- **Sandbox network.** A sandbox command reaches both boxes' `/health`, fabro's API
  (`:32276`) and the scheduler (`:32280`). Verified by probe run `01M60BHGTRG2YP0XHJG740X2DP`.
  `backlog` already passes `FABRO_API_TOKEN` and `FABRO_API_URL` into its sandbox
  (`backlog/workflow.toml`, after the agent guide block). Values there are `{{ secrets.X }}`
  or server variables `{{ vars.X }}`.
- **A command-node loop of short visits keeps the stall watchdog quiet.** A routing node needs
  `output_schema="routing"`, or its `context_updates` do not route (R8).
- **Run labels.** The scheduler sends `args.labels` with string values (`source`, `issue`,
  `workflow`; `fabro.py` `build_run_intent`). Fabro returns them on every run object as
  `labels`. Cancel is `POST /runs/{id}/cancel` and answers 202 for a live run.
- **Nothing writes the run's `coder_pool` into the sandbox today.** The value exists only as
  the stylesheet input.
- **Held PRs.** The merge phase's last report comment has a `**Reason:**` line
  (`render.jq`). `prfacts.py` already parses `report` and `reason` per factory PR into
  `pr_facts`. The human-handoff label is `ai-review-needs-human`. The merge queue merges only
  the approved head, and any other push is handed off (invariants-merge-queue C7).
  `rm_setup` removes `merge-on-green` on every entry to the merge phase.
- **What blocks PRs** (from `pr_facts`, since 10-20): unfixed review findings, Refuter
  `not_met` or `unverifiable`, CI still red after two fix attempts, and diff hygiene
  (`asserts_removed`).
- **How runs end** (since 10-20): `close_noop` 5, `return_to_triage` 5, `mark_stuck` 22 of 176.
- `decompose`'s prompt reads `.body` and every `.comments[].body` of `issue.json`.
- **Fabro 0.370-0.396 adds no cross-run priority or per-pool concurrency.**
- **z.ai credits** (docs.z.ai/devpack/overview, read 2026-10-28):
  - credits = tokens × rate / 10,000;
  - glm-5.3 rates are input 6.9, cached input 1.7, output 24, and glm-5.3-flash is exactly a
    third of that;
  - off-peak usage is billed at 50%; peak is Mon-Fri 14:00-18:00 UTC+8;
  - the Max plan's weekly cap is 140k credits.

  In the week of 10-22, cached input was 60% of credits. Backlog `plan` was 22% and
  `decompose` 14%.

## Contracts

### C1. The `box` field in the Stage manifest

Every stage entry under `workflows.<wf>.stages` and `phases.<phase>` in
`.fabro/workflows/_io/manifest.json` gains `"box"`, with one of three values:
- `false` (or absent): the stage never runs on the box.
- `true`: the stage always runs on the run's box.
- `"plan-arm"`: the stage runs on the box only when `(issue_number // 2) % 2 == 1`.

`ops/fabro-io-manifest.py generate` carries it into each `FABRO_IO_MANIFEST` block. A stage of
an imported phase uses the importing workflow's stylesheet, so its box-ness depends on the
importer. The phase entry therefore gives a per-importer map,
`"box": {"backlog": true, "pr-review": true, "dependabot": false}`, and the generator flattens
it per workflow.

### C2. The run's box file

The `claim` command node of `backlog`, `ralph` and `pr-review` writes `/tmp/fabro/box`, one line holding `{{ inputs.coder_pool }}` (`coders-a`, `coders-b`,
`coders-c` or `coders-d`). A missing or empty file means no box is known, and the claim does
nothing. Background packages also write it, with the `-bg` suffix removed.

### C3. `fabro-io box-claim`

Run as a sandbox `stage_start` hook (C4). Inputs:
- environment: `FABRO_WORKFLOW`, `FABRO_NODE_ID`, `FABRO_IO_MANIFEST`, `FABRO_API_URL`,
  `FABRO_API_TOKEN`, `FABRO_BOX_HEALTH`;
- files: `/tmp/fabro/box` and, for `"plan-arm"`, `/tmp/fabro/issue.json` (`.number`).

`FABRO_BOX_HEALTH` is comma-separated `pool=url` pairs:
`coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.56:5300/health`.

Steps:
1. Look up the stage's `box` value in the manifest. Not `true`, and not `"plan-arm"` with the
   arm rule true: exit 0 at once, printing nothing.
2. Read the box from `/tmp/fabro/box`. Missing, or no `FABRO_BOX_HEALTH` entry for it: log and
   exit 0.
3. `GET {FABRO_API_URL}/runs?status=pending&status=runnable&status=initializing&status=running&status=blocked&page[limit]=100`.
   Keep runs whose `labels.lane == "background"` and `labels.box == <box>`.
   `POST {FABRO_API_URL}/runs/{id}/cancel` each one; 202 and 409 both count as done.
4. Poll `FABRO_BOX_HEALTH[box]` every 2 s until `busy == false` and `queued == 0` on two
   consecutive reads, or until the deadline.
5. Exit 0. One stderr line:
   `box-claim: <wf>/<node> box=<box> cancelled=<n> waited=<s>s idle=<true|false>`.

- The whole command has an **80 s deadline**, and every HTTP call has a 5 s timeout.
- **Every failure logs one stderr line and exits 0.** It never prints a decision on stdout.
- No step depends on the scheduler.

### C4. The hook

In `backlog`, `ralph` and `pr-review` `workflow.toml`:

```toml
[[run.hooks]]
id = "box-claim"
event = "stage_start"
matcher = "^agent$"
blocking = true
sandbox = true
timeout = "90s"
script = "fabro-io box-claim; exit 0"
```

The `[run.environment.env]` table gains
`FABRO_BOX_HEALTH = "{{ vars.FABRO_BOX_HEALTH }}"`. `pr-review` also gains
`FABRO_API_URL = "{{ vars.FABRO_API_URL }}"` and
`FABRO_API_TOKEN = "{{ secrets.FABRO_API_TOKEN }}"`, which `backlog` and `ralph` already have.

Command-node scripts take value substitution only, with no filters. So `{{ inputs.coder_pool }}`
in `pr-review`'s `claim` needs every `pr-review` fire to pass `coder_pool`. Task 03 changes
`fire-pr-review.sh` to do so; the scheduler's fires (06) always do.

### C5. Background model rows

The settings overlay gains `coders-a-bg` on provider `box-a` and `coders-b-bg` on `box-b`,
each with the same `api_model` as its box row. No `workflow.toml` may have a
`[run.model.fallbacks]` row for a `-bg` name (checker R14), so a box error fails the stage
instead of reaching a hosted model.

### C6. Markers and labels

**Run labels.** The scheduler adds these to every run it starts:
- `box` = the pool without `-bg` (`coders-a`);
- `lane` = `coder` or `background`;
- for background runs, also `job` (`pr-doctor`, `issue-precheck`, `context-pack`,
  `orientation`, `flaky-investigation`, `box-noop`).

**GitHub markers**, the first line of the comment each job posts:
- PR doctor: `<!-- fabro:pr-doctor head=<sha-before> pushed=<sha-after|none> run=<run_id> -->`
- Precheck: `<!-- fabro:precheck verdict=<v> issue_updated=<iso> main=<sha> run=<run_id> -->`
- Context pack: `<!-- fabro:context-pack issue_updated=<iso> main=<sha> run=<run_id> -->`

### C7. The background lane (scheduler)

State lives in a new SQLite table, `background_jobs`:
`(job_id PK, kind, repo, target, created_at, state queued|running|done|failed|skipped,
run_id, box, cancels, last_started_at, last_finished_at, outcome)`.

The dispatch tick handles the background lane **after** primary dispatch. For each configured
box, it starts one job when **all** of these hold:
1. `SCHEDULER_BACKGROUND` is `on`.
2. The box is not drained, and it has no running background run.
3. The box has no coder loop, or the run probe shows the loop's current stage is not a box
   stage. The scheduler reads C1 from the generated manifest of the loop's workflow version.
4. strix-infer reports `busy == false` and `queued == 0`, with `idle_seconds >= GAP_S`
   (default 30).
5. Fabro has at least `BG_SLOT_RESERVE` (default 3) free run slots.
6. A queued job's `typical_box_minutes` fits the predicted gap.

**Gap prediction** in v1 is a static table, from the loop's current node to its median
remaining gap, measured in task 12 and kept in `config.py`. An unknown node predicts 0, so only
jobs of 5 minutes or less start.

Jobs are created by producers (06, 07, 08, 10), each of which reads GitHub or `pr_facts` and
inserts a job only when its marker is missing or stale. A run that ends cancelled puts its job
back to `queued` with `cancels + 1`.

### C8. Measurement

Every phase's exit check is in task 12. It is read from fabro run data, the scheduler's
store and strix-infer's `/metrics`, never from raw event exports.
