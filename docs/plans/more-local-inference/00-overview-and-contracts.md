# More local inference: overview and canonical contracts

**Status:** planned 2026-10-28. Phase 1 built, reviewed and deployed 2026-10-28 with the lane
`off`; task 12 Phase 1 steps 1 and 6-8 are next, and its exit check has not run. ADR 0031
(proposed). Phase 0's sampler runs on
the fabro host until about 2026-10-30 03:50 UTC (`~/coder-util/box-{a,b}.tsv`, read-only).
Revised 2026-10-28 after a review against the repository (see *Facts*).

**Phase gates passed:** none. *(The operator edits this line, and only this line, when a task 12
exit check passes: `Phase 1 passed 2026-11-02`. A task that says "start only after Phase N"
reads this line. If Phase N is not recorded here, stop and report; do not infer it from git.)*

**Read this file first.** Every task in this folder assumes the decisions, facts and contracts
below. If a task and this file disagree, this file is correct: stop and report the conflict.
Read ADR 0031 before you start. **Before you edit a `.fabro` file or a `workflow.toml`, invoke
the `/fabro-workflow` skill** and read `docs/agents/invariants-graph.md`. **Before you write
Rust, invoke `/ms-rust`.** Before you touch `ops/scheduler/`, read `ops/README.md` *The coder
scheduler*.

The planning doc on claude.ai (*Coder saturation plan*) is the history of the decision, not a
spec. Where it differs from this folder, this folder is correct.

## Hard rule for this series: do not push

The operator reviews before anything reaches `main`, because pushing to `main` deploys (see
`AGENTS.md`).
- Commit each task locally on `main`: one commit per task, with Conventional Commit subjects.
- After each task, run `make check` at the repository root. Use `CHECK_CARGO=always` for any
  task that touches `ops/fabro-io/`. For any task that touches `ops/scheduler/`, also run
  `uv run pytest` in `ops/scheduler/`. All of it must pass.
- Do not run `git push`, `make deploy*`, `make provision`, `make compose-up`, `make check-host`,
  `ssh`, or any `gh` command that writes. Do not edit a target repository.
- The work is in four phases, and **each phase stops for the operator**:

| Phase | Tasks, in this order | Then |
|---|---|---|
| 1. Claim and lane plumbing | **02, 01**, 03, 04, 05, 11 | stop; operator runs task 12, Phase 1 |
| 2. PR doctor | 06, then 11's Phase 2 rows | stop; operator runs task 12, Phase 2 |
| 3. Precheck and context packs | 07, 08, then 11's rows | stop; operator runs task 12, Phase 3 |
| 4. Pre-review and long-horizon jobs | 09, 10, then 11's rows | stop; operator runs task 12, Phase 4 |

**Task 02 comes before task 01** (D18). The deployed `fabro-io` rejects unknown manifest
fields, so the binary that accepts `box` must be built and deployed before any generated
manifest carries it. The operator pushes task 02's commit on its own and deploys images first.

Task 11 (the docs) is the last task of Phase 1, and each later phase adds its own rows to it
before it stops.

## What this is

The coder boxes are idle most of the day. A run holds its box for its whole lease but uses it
only for its box stages: 158 of 432 lease-hours since 2026-10-21. This series fills the gaps
between a coder loop's box stages with **background runs**. These are short fabro runs of
box-only packages, and the coder loop cancels them the moment it needs the box. Every
background job must raise the merge rate or lower hosted token use. No hosted model is
called by background work, and nothing new sits in the coder loop's path.

| Piece | Path | Task |
|---|---|---|
| `fabro-io` reads `box` and `coder_box_stages`; `box-claim`; `box-guard` | `ops/fabro-io/src/manifest.rs`, `boxclaim.rs` | 02 |
| `box` flag per stage in the Stage manifest, checker rules R13, R14 | `.fabro/workflows/_io/manifest.json`, `ops/fabro-io-manifest.py`, `ops/check-graph-invariants.py` | 01 |
| The claim hook, the run's box file, the `FABRO_BOX_HEALTH` variable and its provisioning, R15 | `backlog`, `ralph`, `pr-review` `workflow.toml` and their `claim` nodes; `ops/provision-server-state.sh` | 03 |
| `coders-*-bg` model rows, the `box-noop` package, the guard hook, R16 | `ops/settings.toml.example`, `.fabro/workflows/box-noop/` | 04 |
| Scheduler: labels, job dispatch, background lane, eviction, switch, page | `ops/scheduler/src/fabro_scheduler/`, `ops/docker-compose.yaml` | 05 |
| PR doctor package, the `pr-review` lease, its one hosted review | `.fabro/workflows/pr-doctor/`, scheduler | 06 |
| Issue precheck package; the `background.md` input (C9); queue holds | `.fabro/workflows/issue-precheck/`, `backlog`/`ralph` `claim`, scheduler | 07 |
| Context pack package | `.fabro/workflows/context-pack/`, scheduler | 08 |
| In-run box pre-review | `backlog/workflow.fabro` | 09 |
| Repo orientation and flaky-test investigation | two packages | 10 |
| Docs | `AGENTS.md`, `CONTEXT.md`, `docs/README.md`, `docs/agents/*`, `ops/README.md`, ADR 0031 | 11 |
| **Operator only** | sampler analysis, server variable, overlay rows, push order, deploy, measure each phase | 12 |

## Decisions (settled; do not re-open)

| # | Decision |
|---|---|
| D1 | One **coder loop** per box, as the scheduler leases today. A coder loop is a `backlog` or `ralph` run, or a `pr-review` run the scheduler leases (D14). It has **absolute precedence** on its box. |
| D2 | **The unit of precedence is one stage.** A box changes hands only between stages. When the coder loop leaves a box stage (say `coder`) for a hosted stage (say `review`), a background run may use the box until the loop's next box stage begins. Each stage starts a fresh session, so this costs nothing in the KV cache. Holding the box across a whole backlog task was rejected: it gains no cache reuse and loses the gap (ADR 0031 D1). |
| D3 | **No proxy.** Fabro calls strix-infer directly, as today. Two runs never interleave requests on one box. |
| D4 | Background work is whole fabro runs of **box-only packages**: every agent stage's model is `{{ inputs.coder_pool }}`, which the scheduler sets to `coders-a-bg` or `coders-b-bg` (C5). **At most one background run per box at a time.** |
| D5 | **The claim is fabro's, not the scheduler's.** Before each box stage, the coder loop's `fabro-io box-claim` hook cancels any background run on its box through fabro's run API, then waits for strix-infer to report the box idle (C3). It never calls the scheduler, and it never cancels its own run. Only coder-loop packages carry it (R15). |
| D6 | The claim exits 0 on every error of its own and gives up after **45 s**, inside the hook's **60 s** timeout. With the API or the box unreachable, the stage starts as it does today. |
| D7 | **The scheduler decides when** (C7). A background run starts only on a box whose coder loop is outside a box stage, or that has no coder loop, and whose strix-infer has been idle for at least `GAP_S`. The scheduler reads the loop's current stage **fresh from fabro at decision time**, never from the probe's 30 s cache, and a loop between stages (no stage running) does not qualify. Never on a drained box. Background runs ignore the peak-hours pause. |
| D8 | **Fabro's run cap protects the coder loop.** Fabro admits runs first come, first served under `max_concurrent_runs`. The scheduler starts a background run only while at least `BG_SLOT_RESERVE` (3) run slots are free, counting every run in an active status as holding a slot (C7). Before a coder-loop dispatch, it cancels a background run if one is needed to free a slot. |
| D9 | **Every background job is safe to cancel and idempotent.** It writes to GitHub only in its last node, and that node writes its marker first (C6). A cancelled job goes back on the queue; one cancelled 3 times waits for a gap at least twice its typical length. |
| D10 | **Job order, by value:** PR doctor (06), issue precheck (07), context pack (08), then repo orientation and flaky-test investigation (10). The scheduler's job pick follows this order (C7). The in-run pre-review (09) is not a background job; it runs on the coder loop's own lease. Every job must raise the merge rate or lower hosted token use, and task 12 measures which one it does. |
| D11 | Background outputs live in GitHub, as **one marker comment per job kind per target, edited in place** (C6), plus the PR doctor's push and labels. The scheduler keeps only job state in its SQLite store. |
| D12 | One switch, `SCHEDULER_BACKGROUND`, turns the lane off without a deploy of anything else. It is `off` by default **in code and in `ops/docker-compose.yaml`**; the operator turns it on in `~/fabro/.env` (task 12). Off means no new background run starts; running ones finish. |
| D13 | **PR doctor: one attempt per pull request, ever** (ADR 0031 D4). The attempt is recorded by the marker comment, which the doctor's last node posts before anything else (C6). Its push is followed by exactly one automatic `pr-review` run. Before pushing, the doctor removes `ai-review-needs-human` and `merge-on-green`, because the merge queue hands off any PR that carries the first. If the review does not end with the PR merged or queued, the PR gets `ai-review-needs-human` back and a comment saying a human must take it. The doctor never touches that PR again. |
| D14 | The doctor's `pr-review` run spends hosted credits and uses box stages (`rebase_agent_t1`, `refute_fix`, `resolve_base`), so it is a **coder loop**. It takes a primary lease, obeys the peak-hours pause, and its box stages claim. It is dispatched **ahead of the issue queue** on the next free box, because it turns spend already made into a merge. |
| D15 | Stay on fabro 0.362. Nothing here needs a newer build. A later upgrade is its own rehearsed series (`docs/fabro-upgrade/`) and must skip 0.370-0.386, where `stall_timeout` was not enforced (fabro PR #1032). |
| D16 | Out of scope: a z.ai credit governor, a second coder loop per box, in-run parallel background branches, scheduled automations for the lane, a proxy, and fixing the latent `issue.json` comments gap (*Facts*; the operator files it as its own issue). |
| D17 | **The precedence guarantee has three layers**, because the claim runs once, at stage start, and cannot see a background run created after it: (1) the scheduler's fresh stage read before a start (D7); (2) the scheduler **evicts**, on every tick, any background run on a box whose coder loop is in a box stage; (3) every background package runs **`fabro-io box-guard`** before each of its own agent stages, which cancels its own run when the box's coder loop is in a box stage or the box is busy (C10). |
| D18 | **The binary ships before the data.** `fabro-io`'s manifest structs use `deny_unknown_fields`, so a manifest carrying `box` or `coder_box_stages` fails to load in the deployed 0.8.1, and with it `submit`, `serve`, `guard` and `git-guard` in every run. Task 02 teaches the binary the new fields and is pushed and deployed alone, before task 01's data. Task 01 raises `min_binary` to task 02's version. |
| D19 | **Hosted stages read background output through a Stage I/O input, `/tmp/fabro/background.md` (C9), never through issue comments.** `issue.json` carries no comments (*Facts*). The `claim` nodes of `backlog` and `ralph` build the file from the newest marker comment of each kind. |
| D20 | **Precheck verdicts hold an issue; they do not reorder it.** `already_done`, `contradiction` and `unclear` keep an issue out of dispatch while its `precheck-*` label is on it; a human removes the label to release it. `blocked` holds it while the cited blocker, in the same repository, is open. The scheduler never closes an issue or edits its body. |
| D21 | **Live trials are not confounded silently.** Task 09 changes only `backlog`, so Phase 4 does not start until the ralph trial (ADR 0029) has a recorded verdict, or the operator records in the Status line that the confound is accepted. Context packs change `plan`'s credits in both arms of the box-plan trial (`docs/plan-box-trial/`), so the scheduler records per run whether a pack existed at dispatch (task 08), and the trial's analysis stratifies on it. |
| D22 | **Repository orientation lives in one pinned issue per repository**, labelled `fabro-orientation`, whose body the job edits in place. It is never a pull request, so it needs no hosted review. No automation selects issues by that label. |
| D23 | `fabro-io` gains one HTTP client: **`ureq`, with default features off** (no TLS: every URL it calls is plain `http://` on the LAN) plus its JSON feature. No other dependency is added for this series. |

## Facts (verified 2026-10-28)

- **Boxes.** `coders-a` is `http://10.10.0.29:5300`, `coders-b` is `http://10.10.0.56:5300`, both
  strix-infer serving `qwen3.8-flash-next`. `GET /health` returns JSON with `busy` (bool),
  `queued` (int), `idle_seconds` (float) and `live_tokens`. `GET /metrics` is Prometheus text;
  `strix_requests_total{outcome="cancelled"}` counts cancelled requests. The live overlay's
  `coders-a`/`coders-b` rows carry `limits = { context_tokens = 262144, max_output_tokens =
  38400 }`, the same as `ops/settings.toml.example` (read on the host 2026-10-28, Phase 1
  deploy; an earlier line here said they carried none). `coders-c` and `coders-d` are not strix-infer boxes and are not in the lane:
  the lane covers exactly the pools in `FABRO_BOX_HEALTH`.
- **Gaps.** Since 10-21, 157 runs: 432 lease-hours, 158 box-stage hours, and 274 gap-hours.
  Gaps have a median of 11 min (p75 20, p90 49), and 253 h fall in gaps of 10 min or more.
  Box busy share for the whole day, 10-25 to 10-27: box-a 32-39%, box-b 44-48%.
- **Box stages today**, the nodes whose stylesheet class resolves to `{{ inputs.coder_pool }}`:
  - `backlog`: `coder`, `rework_t1`, `resolve_merge`, `extra_decompose`, `plan` (only in the
    box-plan trial's box arm), and from the imported phase `review_merge.refute_fix` and
    `review_merge.resolve_base`.
  - `ralph`: `iterate_t1`, `learn`, `resolve_base`, and `plan` (box arm only). Ralph's
    `resolve_merge` is on `.coder-t4` (hosted), and it has no `refute_fix`.
  - `pr-review`: `rebase_agent_t1`, `refute_fix`, `resolve_base`.
  - `dependabot`: none (its `.refute-fix` and `.merge-resolve` are hosted).
- **The default stylesheet rule is a box.** `backlog` and `ralph` open their stylesheets with
  `* { model: coders-a; }` (`backlog/workflow.fabro:189`, `ralph/workflow.fabro:62`). An agent
  node with no class, or a class with no rule, runs on box-a whatever the run's lease. Every
  agent node has a class today. `pr-review`'s `*` is `glm-5.3`.
- **The box-plan trial's arm** is `(issue_number // 2) % 2 == 1`, written as a stylesheet
  `{% if (inputs.issue_number | default(0) | int) // 2 % 2 == 1 %}` in `backlog` and `ralph`
  (`docs/plan-box-trial/`).
- **Hooks.** A blocking `stage_start` hook's wait counts toward the run's `stall_timeout`.
  Verified with a probe run: a 100 s hook under a 1-minute watchdog failed at 60 s. Sandbox
  hooks run with `FABRO_WORKFLOW` and `FABRO_NODE_ID` in the environment
  (`ops/fabro-io/src/stage.rs`). The manifest arrives as `FABRO_IO_MANIFEST`
  (`ops/fabro-io/src/manifest.rs` `load()`). A blocking hook must exit 0 on its own failure;
  fabro treats any other exit as a block, and an unrunnable sandbox hook reports -1
  (invariants-graph *Agents and hooks*). The `stage_start` matcher `^agent$` matches every
  agent stage (`io-stage` uses it). **Verified 2026-10-28 (task 12):** a blocking hook that outlives
  its `timeout` reports -1 and fails the stage and the run. And fabro records nothing a
  sandbox hook prints unless it blocks, so C3's and C10's stderr lines are read from the
  events instead (`ops/fabro-box-claims.py`).
- **Run identity inside a hook.** `FABRO_RUN_ID` is set for host hooks (`discord-notify.sh`
  reads it). `FABRO_HOOK_CONTEXT` names a JSON file with the hook's context (`common.rs`).
  **Verified 2026-10-28 (task 12):** a sandbox `stage_start` hook gets both: `FABRO_RUN_ID`, and a
  context file with `run_id`. `FABRO_WORKFLOW` is the graph name (`Backlog`), not the package.
- **`fabro-io` today** is version 0.8.1; the manifest's `min_binary` is 0.5.1. Its manifest
  structs (`Manifest`, `Stage`, `Input`, `Output`) are all `#[serde(deny_unknown_fields)]`.
  It has **no HTTP client** among its dependencies (axum, rmcp, tokio, serde_json and others;
  `flakeradar.rs` makes no HTTP calls), **no dev-dependencies**, and its release profile sets
  `panic = "abort"`, so a panic cannot be caught in-process.
- **Sandbox network.** A sandbox command reaches both boxes' `/health`, fabro's API
  (`:32276`) and the scheduler (`:32280`). Verified by probe run `01M60BHGTRG2YP0XHJG740X2DP`.
  `backlog` and `ralph` already pass `FABRO_API_TOKEN` and `FABRO_API_URL` into their sandbox
  (`workflow.toml`, after the agent guide block). Values there are `{{ secrets.X }}` or server
  variables `{{ vars.X }}`. **Verified 2026-10-28 (task 12):** `FABRO_API_TOKEN` from a sandbox
  cancels another run (202, and the run ended `failed`/`cancelled`).
- **An unset server variable fails the run.** An unset `{{ vars.X }}` fails the RunIntent at
  compile time, so no run is created at all (`invariants-merge.md`, the `FABRO_AUTO_MERGE` row).
  `ops/provision-server-state.sh` `provision_variable` creates variables, create-if-absent.
- **A command-node loop of short visits keeps the stall watchdog quiet.** A routing node needs
  `output_schema="routing"`, or its `context_updates` do not route (R8).
- **Run labels.** The scheduler sends `args.labels` with string values (`source`, `issue`;
  `fabro.py` `build_run_intent`). Fabro returns them on every run object as `labels`.
- **Run API.** The active statuses are `pending`, `runnable`, `initializing`, `running`,
  `blocked` and `removing` (`fabro.py` `ACTIVE_BOARD_COLUMNS`). `GET /runs` pages with the
  bracketed `page[limit]`/`page[offset]`; a bare `limit` is clamped to 20. `POST /runs/{id}/cancel`
  answers 200 for a run that had not started, 202 for a live run, and 409 for one already
  finished. A cancelled run ends `failed` with `reason: "cancelled"`; a fabro restart ends it
  `failed` with `reason: "terminated"`.
- **The scheduler today.**
  - The dispatch tick is 5 s. The run probe caches each leased run's current stage for 30 s,
    and between stages it reports the stage that just finished (`probe.py` `_current_stage`).
  - `leases` is keyed by `coder_pool` and requires `issue_number`; `run_history` requires it
    too (`store.py`). `build_run_intent` builds only an issue run with inputs
    `issue_number`/`coder_pool` (`fabro.py`). `workflow_version.py` `ENTRYPOINTS` knows only
    `backlog` and `ralph`.
  - The queue is the open issues labelled `agent` (`github.py`), ranked in tiers; the
    age-ceiling tier ignores priority (`queue.py`), so nothing can be pushed "to the bottom".
  - Its environment comes only from `ops/docker-compose.yaml`'s `environment:` lines
    (`${VAR:-default}`, interpolated from `~/fabro/.env`) and `~/fabro/scheduler.env`; it
    deliberately does not read `.env` as an env file. `make deploy-compose` copies the compose
    file; `make deploy-scheduler` runs `docker compose up -d --build --no-deps scheduler`.
  - `summary.CODER_MODEL_PREFIX` is `"coders-"`, which `coders-a-bg` also matches.
  - The factory's commit identities are `DEFAULT_AUTOMATION_AUTHOR`
    (`andrews-ai-agent,noreply@fabro.sh`); any other author is an operator commit on the
    scorecard.
- **`fire-pr-review.sh` is not deployed by `deploy-scripts`.** The host's
  `~/bin/fabro-fire-pr-review.sh` is a wrapper that runs the copy on `origin/main`, so a change
  to it is live the moment it is pushed, together with the graph change in the same push.
  Nothing in `backlog` or `ralph` fires `pr-review` today; only hand fires do.
- **Held PRs.** The merge phase's last report comment has a `**Reason:**` line
  (`render.jq`). `prfacts.py` already parses `report` and `reason` per factory PR into
  `pr_facts` (`report` is one of `merged`, `queued`, `complete`, `blocked`, `needs-human`).
  - `mark_needs_human` adds `ai-review-needs-human` to every needs-human PR
    (`review-merge.fabro:1660`, ralph's copy, `pr-review/workflow.fabro:290`), and nothing ever
    removes it. The merge queue hands off any PR that carries it
    (`merge-queue/workflow.fabro:233`) and adds it on its own hand-offs (`:326`).
  - The merge queue merges only the approved head, and any other push is handed off
    (invariants-merge-queue C7). `rm_setup` removes `merge-on-green` on every entry to the
    merge phase.
  - About 30 places write a block reason (`merge_block_reason` / `needs_human_reason` in
    `review-merge.fabro` and ralph's copy). Known texts include `There are unfixed findings; a
    human must merge this PR.`, `CI is still red after the maximum number of fix attempts.`,
    `CI is still failing after two fix attempts (...)`, `Diff hygiene blocks the merge: ...`,
    and `retryable: <node> failed (transient_infra)`. Task 06 enumerates them all.
- **What blocks PRs** (from `pr_facts`, since 10-20): unfixed review findings, Refuter
  `not_met` or `unverifiable`, CI still red after two fix attempts, and diff hygiene
  (`asserts_removed`).
- **How runs end** (since 10-20): `close_noop` 5, `return_to_triage` 5, `mark_stuck` 22 of 176.
- **`issue.json` carries no comments.** `claim` in `backlog` and `ralph` writes it with
  `gh issue view --json state,number,title,body,labels` (`backlog/workflow.fabro:286`,
  `ralph/workflow.fabro:118`). The `decompose` and `plan` prompts nevertheless tell the agent to
  read `.comments`; that is a latent bug, out of scope here (D16). `ralph`'s `claim` and `prep`
  are checked copies of `backlog`'s (`ops/check-graph-invariants.py` R10, `R10_COPIES`): an edit
  to one needs the same edit in the other, or a recorded replacement.
- **`fast-path` is an issue label** that routes `backlog` into its one-task lane
  (`invariants-backlog.md`); it means nothing on a pull request.
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
- `false`: the stage never runs on the box. Written explicitly on every stage.
- `true`: the stage always runs on the run's box.
- `"plan-arm"`: the stage runs on the box only when `(issue_number // 2) % 2 == 1`.

`ops/fabro-io-manifest.py generate` carries it into each `FABRO_IO_MANIFEST` block. A stage of
an imported phase uses the importing workflow's stylesheet, so its box-ness depends on the
importer. The phase entry therefore gives a per-importer map,
`"box": {"backlog": true, "pr-review": true, "dependabot": false}`, and the generator flattens
it per workflow.

The generated block of every **background package** (task 04's `BACKGROUND_PACKAGES`) also
carries a top-level `coder_box_stages`: for each coder-loop workflow (`backlog`, `ralph`,
`pr-review`), a map from every stage id whose `box` is `true` or `"plan-arm"` to that value.
`box-guard` (C10) reads it to judge another run's current stage.

`fabro-io`'s `Stage` gains `box` (optional; absent reads `false`) and `Manifest` gains
`coder_box_stages` (optional), before any manifest carries them (D18).

### C2. The run's box file

The `claim` command node of `backlog`, `ralph` and `pr-review` writes `/tmp/fabro/box`, one
line holding `{{ inputs.coder_pool }}` (`coders-a`, `coders-b`, `coders-c` or `coders-d`). A
missing or empty file means no box is known, and the claim does nothing. Background packages'
`claim` nodes also write it, with the `-bg` suffix removed; `box-guard` reads it.

### C3. `fabro-io box-claim`

Run as a sandbox `stage_start` hook in coder-loop packages only (C4, R15). Inputs:
- environment: `FABRO_WORKFLOW`, `FABRO_NODE_ID`, `FABRO_IO_MANIFEST`, `FABRO_API_URL`,
  `FABRO_API_TOKEN`, `FABRO_BOX_HEALTH`, and the run's own id (`FABRO_RUN_ID`, else the
  `FABRO_HOOK_CONTEXT` file's `run_id`, else unknown);
- files: `/tmp/fabro/box` and, for `"plan-arm"`, `/tmp/fabro/issue.json` (`.number`).

`FABRO_BOX_HEALTH` is comma-separated `pool=url` pairs:
`coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.56:5300/health`.

Steps:
1. Look up the stage's `box` value in the manifest. Absent stage, `false`, or `"plan-arm"`
   with the arm rule false: exit 0 at once, printing nothing.
2. Read the box from `/tmp/fabro/box`. Missing, or no `FABRO_BOX_HEALTH` entry for it: log and
   exit 0.
3. `GET {FABRO_API_URL}/runs?status=pending&status=runnable&status=initializing&status=running&status=blocked&page[limit]=100`.
   Keep runs whose `labels.lane == "background"` and `labels.box == <box>`, and whose id is not
   the run's own. `POST {FABRO_API_URL}/runs/{id}/cancel` each one; 200, 202 and 409 all count
   as done.
4. Poll `FABRO_BOX_HEALTH[box]` every 2 s until `busy == false` and `queued == 0` on two
   consecutive reads, or until the deadline.
5. Exit 0. One stderr line:
   `box-claim: <wf>/<node> box=<box> cancelled=<n> waited=<s>s idle=<true|false>`.

- The whole command has a **45 s deadline**, and every HTTP call has a 5 s timeout.
- **Every failure logs one stderr line and exits 0.** It never prints on stdout. It contains no
  `unwrap`/`expect` on I/O; a crash is caught by the hook script's `; exit 0` (C4), because the
  release profile aborts on panic.
- No step depends on the scheduler.

### C4. The claim hook

In `backlog`, `ralph` and `pr-review` `workflow.toml`, after `io-stage`:

```toml
[[run.hooks]]
id = "box-claim"
event = "stage_start"
matcher = "^agent$"
blocking = true
sandbox = true
timeout = "60s"
script = "fabro-io box-claim; exit 0"
```

The `[run.environment.env]` table gains
`FABRO_BOX_HEALTH = "{{ vars.FABRO_BOX_HEALTH }}"`. `pr-review` also gains
`FABRO_API_URL = "{{ vars.FABRO_API_URL }}"` and
`FABRO_API_TOKEN = "{{ secrets.FABRO_API_TOKEN }}"`, which `backlog` and `ralph` already have.
**The server variable must exist before this reaches `main`**, or no run of the three
workflows is created (*Facts*). Task 03 adds it to `provision-server-state.sh`; task 12 runs the
provisioning before the push.

Command-node scripts take value substitution only, with no filters. So `{{ inputs.coder_pool }}`
in `pr-review`'s `claim` needs every `pr-review` fire to pass `coder_pool`. Task 03 changes
`fire-pr-review.sh` to send `coder_pool: "coders-a"`; that change and the graph change must be
in the same push (they are in one commit). The scheduler's fires (06) always send it.

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

**GitHub markers.** Each job kind keeps **one** comment per target, whose first line is its
marker. A rerun **edits that comment in place** (`PATCH /repos/{o}/{r}/issues/comments/{id}`)
instead of posting another. `body=` is the first 12 hex characters of the SHA-256 of the
issue's title, a newline, and its body, at the time the job read it.
- PR doctor: `<!-- fabro:pr-doctor head=<sha-before> pushed=<pending|sha-after|none> run=<run_id> -->`
- Precheck: `<!-- fabro:precheck verdict=<v> body=<hash> main=<sha> at=<iso> run=<run_id> -->`
- Context pack: `<!-- fabro:context-pack body=<hash> main=<sha> at=<iso> run=<run_id> -->`
- Flaky investigation: `<!-- fabro:flaky-investigation test=<test_id> at=<iso> run=<run_id> -->`
- Orientation (the pinned issue's body, D22): `<!-- fabro:orientation main=<sha> at=<iso> run=<run_id> -->`

**The last node writes the marker first.** A job's final node posts or edits its marker
before any other GitHub write. A cancel before that node leaves nothing on GitHub; a cancel
inside it leaves a marker, which the producer and the hand-off read (task 06).

**Staleness**, for precheck and context pack: a marker is stale when its `body=` differs from
the issue's current hash, or when `main` has moved since its `main=` **and** its `at=` is more
than 24 h old. Never on `updated_at`: the job's own comment and the scheduler's labels bump it.

**Labels the series writes.** `precheck-already-done`, `precheck-contradiction`,
`precheck-unclear`, `precheck-blocked` (issues, by the scheduler; D20); `fabro-orientation`
(the pinned issue); and the PR doctor's removal and re-adding of `ai-review-needs-human` and
removal of `merge-on-green` (D13). GitHub creates a missing label on first use.

### C7. The background lane (scheduler)

State lives in a new SQLite table, `background_jobs`:
`(job_id PK, kind, repo, target, inputs_json, priority, typical_box_minutes, created_at,
state queued|running|done|failed|skipped, run_id, box, cancels, last_started_at,
last_finished_at, outcome)`. `priority` comes from D10's order (PR doctor 1, precheck 2,
context pack 3, orientation and flaky investigation 4, `box-noop` 5).

The dispatch tick handles the background lane **after** primary dispatch. First it **evicts**
(D17): for each box whose coder loop's fresh current stage is a box stage, it cancels any
background run on that box. Then, for each box in `SCHEDULER_BOX_HEALTH`, it starts one job
when **all** of these hold:
1. `SCHEDULER_BACKGROUND` is `on`.
2. The box is not drained, and it has no running background run.
3. The box has no coder loop, or the loop's current stage, **read fresh** with `GET
   /runs/{id}/stages`, is a stage that is running and is not a box stage. The scheduler reads C1
   from the generated manifest of the loop's workflow version. A loop with no running stage
   (between stages) does not qualify this tick.
4. strix-infer reports `busy == false` and `queued == 0`, with `idle_seconds >= GAP_S`
   (default 30).
5. Fabro has at least `BG_SLOT_RESERVE` (default 3) free run slots: `max_concurrent_runs`
   minus the number of runs in any active status (*Facts*), including `pending`.
6. A queued job fits the predicted gap: its `typical_box_minutes` is the run's whole expected
   wall time, from dispatch to finish, including sandbox start, clone and dependency install.

The job picked is the lowest `priority`, then the oldest `created_at`, among queued jobs that
fit. A job with `cancels >= 3` fits only a predicted gap of at least twice its
`typical_box_minutes`.

**Gap prediction** in v1 is a static table, keyed by **(workflow, node id)**, to the loop's
median remaining gap after that node, measured in task 12 and kept in `config.py`. An unknown
key predicts 0, so only jobs of 5 minutes or less start. With no coder loop on the box, the gap
is unlimited.

Jobs are created by producers (06, 07, 08, 10), each of which reads GitHub or `pr_facts` and
inserts a job only when its marker is missing or stale. `SCHEDULER_BG_KINDS` (comma-separated,
default every kind) names the producers that run, so one kind can be turned off without the
rest. A run that ends `failed` with reason
`cancelled` puts its job back to `queued` with `cancels + 1`, except where task 06 says
otherwise.

### C8. Measurement

Every phase's exit check is in task 12. It is read from fabro run data, the scheduler's
store and strix-infer's `/metrics`, never from raw event exports. Background runs' usage is
kept apart from coder-loop usage in every scheduler summary (task 05).

### C9. `/tmp/fabro/background.md`

Written by the `claim` node of `backlog` and `ralph` (task 07), after `issue.json`. It lists the
issue's comments once with `gh api repos/{owner}/{repo}/issues/{n}/comments --paginate`, keeps
the newest comment whose first line starts with each of `<!-- fabro:precheck`,
`<!-- fabro:context-pack` and `<!-- fabro:flaky-investigation`, and writes them, in that order,
each under a `## <kind>` heading. No marker found: the file is not written. Any failure: one
stderr line, no file, and the node goes on; nothing goes to stdout.

It is a Stage I/O input, `required: false`, of `backlog` `decompose` and `plan` and `ralph`
`plan`, with an `absent` sentence saying there is no background note. The whole file is capped
at 12,000 characters (precheck 1,500, pack 8,000, investigation 2,500), cut with a note if
longer.

### C10. `fabro-io box-guard`

Run as a sandbox `stage_start` hook, `matcher = "^agent$"`, in background packages only (R16),
before each of their agent stages. Inputs: `FABRO_IO_MANIFEST` (its `coder_box_stages`),
`FABRO_API_URL`, `FABRO_API_TOKEN`, `FABRO_BOX_HEALTH`, the run's own id (as in C3), and
`/tmp/fabro/box`.

Steps:
1. Read the box. Missing, or unknown pool: log and exit 0 (the scheduler's eviction still
   applies).
2. List active runs (as C3 step 3) with `labels.lane == "coder"` and `labels.box == <box>`. For
   each, `GET /runs/{id}/stages` and take the stage that is running. It is a box stage when
   `coder_box_stages[<the run's workflow>][<node>]` is `true`, or is `"plan-arm"` with the arm
   rule true for the run's `labels.issue`.
3. Read `/health` once.
4. **Clear** (no coder-loop box stage running, and `busy == false`, `queued == 0`): exit 0.
5. **Not clear:** cancel the run's own id through the API and exit 0; the stage dies with the
   run, and the scheduler requeues the job as a cancel. If the run id is unknown or the cancel
   fails, exit 2, which blocks the stage and fails the run.

- 20 s deadline, 5 s per HTTP call. Any error of its own other than step 5: log and exit 0.
- One stderr line: `box-guard: <wf>/<node> box=<box> clear=<true|false> action=<none|self-cancel|block>`.

The hook:

```toml
[[run.hooks]]
id = "box-guard"
event = "stage_start"
matcher = "^agent$"
blocking = true
sandbox = true
timeout = "30s"
script = "fabro-io box-guard; [ $? -eq 2 ] && exit 2; exit 0"
```
