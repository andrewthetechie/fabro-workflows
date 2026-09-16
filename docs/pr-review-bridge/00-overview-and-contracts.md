# Fabro PR-Review Bridge — Overview & Canonical Contracts

**Read this first.** Every task in this folder assumes the architecture, the API
contract, and the findings recorded here. This document is the single source of truth.

## What this is

The third stage of the fabro integration: after the `backlog` workflow opens a pull
request, it **triggers a `pr-review` run on that PR automatically**, as a positive,
in-graph action — not a poll, and not a human typing a curl.

The two workflows stay separate packages. `pr-review` is **not modified by this
stage at all** and remains independently runnable.

## Operator decisions (settled — do not re-litigate)

| # | Decision |
|---|---|
| 1 | The trigger is an **in-graph command node** in `backlog`, calling the fabro HTTP API from the sandbox. Not a hook, not a host sweeper, not a `house` node. |
| 2 | Both PR-producing paths trigger a review. `open_pr` is the only node that opens a PR, including on the `[P] Accept partial` rescue path, so one node covers both. |
| 3 | The handover is **`pr_number` only**. `pr-review`'s input contract is unchanged. Traceability rides on `parent_id` and run `labels`, not on new inputs. |
| 4 | Live on **all four repos** at once. No pilot, no allowlist — nothing fires on a schedule today. |
| 5 | The chain **ends at "review posted"**. No auto-merge. Nothing here may foreclose adding it later. |
| 6 | `backlog` **fires and exits**. It never waits for the review. |
| 7 | The trigger registers `fabro-workflows` **`@main`**, cloned fresh at trigger time. |
| 8 | Per-repo config (`environment_id`, git target) is **read from the `pr-review-<repo>` automations**, never hardcoded. |
| 9 | A failed trigger **never fails the backlog run** (`on_failure="succeed"`), but it is **never silent** — it notifies via Discord. |
| 10 | Ship `fire-pr-review.sh` as both the manual-fire tool and the standalone test harness. It lives under `.fabro/workflows/backlog/scripts/`, not `ops/`, because the trigger node executes it. |

## Three findings that invalidate earlier assumptions

These were established against the live server (`0.354.0-nightly.0`) and the fabro
source. Two of them contradict documentation that is still in this repo; task 09
corrects it.

### 1. The automation-fire endpoint ignores its request body

`AGENTS.md` documents firing a review as:

```sh
curl -X POST -d '{"trigger":"manual","inputs":{"pr_number":N}}' \
  http://<HOST>:32276/api/v1/automations/pr-review-<repo>/runs
```

**This cannot work.** `POST /api/v1/automations/{id}/runs` declares **no request
body** in the server's OpenAPI document — one path parameter and nothing else
("Creates a new run by firing the automation's enabled API trigger"). Fired with that
body it returns:

```
HTTP 422  {"code":"run_compile_invalid",
           "detail":"run intent could not be compiled: Validation failed"}
```

No run is created. The `inputs` are dropped, and compilation then fails on the
unbound `{{ inputs.pr_number }}` in `validate_input` — which is exactly what the
deliberate "`pr_number` unbound" validation warning exists to catch. **Keep that
warning.** Binding `[run.inputs] pr_number` would silence it and turn a
no-input fire into a review of PR #1.

Corroborating evidence: of the 20 runs in this deployment's history, all 16 `Backlog`
runs carry an `automation` object and **all four `PrReview` runs have
`automation: null`** — they were created directly through `POST /api/v1/runs`.

### 2. `house` / `stack.child_workflow` is not a child run

`shape=house` with `stack.child_workflow="../pr-review/workflow.fabro"` **does**
validate against this repo's real packages — sibling-package paths resolve, and
controls confirm it genuinely reads and parses the child graph (a bogus path fails
with `Failed to read`; a prompt file fails with a real DOT parse error).

It is still the wrong tool. From `fabro-workflow/src/handler/manager_loop.rs`:

| What the source does | Consequence |
|---|---|
| `run_id: services.run.emitter.run_id()` — *"Child workflows are part of the parent run's event stream"* | Same run. No `parent_id`, no `children_count`, no separate run record. |
| `settings: WorkflowSettings::default()` | **`pr-review`'s entire `workflow.toml` is discarded** — including `[run.clone] depth = 0`, whose absence breaks every git operation that workflow is built on. |
| `inputs: services.inputs.clone()` | The child inherits **backlog's** inputs. It cannot be handed a `pr_number`. |
| `validate_child_workflow` calls `promote_template_undefined_variables_to_errors()` then `raise_on_errors()` | `{{ inputs.pr_number }}` is therefore a **hard error at the manager node**, not a runtime surprise. |
| `git: None`, `base_branch: None` | No separate clone. It runs on backlog's checkout. |
| `ArtifactStore::new(Arc::new(InMemory::new()), …)` | Child artifacts are discarded. |

`import="…"` is worse: the reference calls it "a parse-time merge with **no runtime
boundary**."

### 3. `fabro_run_create` exists, does exactly what we want, and is disabled here

`fabro_tool::create_runs` takes `workflow_version_id`, `args.inputs`,
`environment_id`, `parent_id`, `title`, `goal`, `start`, forces `parent_id` to the
current run when called from inside one, and creates a real separate durable run.

It is registered for agent nodes by `register_fabro_run_tools` — **but the server's
run-execution path passes `fabro_run_tools: None`** (`fabro-server/src/server.rs`,
in the `operations::start` / `operations::resume` services block). Only the CLI
runner enables it. `ask_fabro` sessions expose just `fabro_run_events` and
`fabro_run_get`.

So the tool is unavailable to every run this deployment executes. The command node
in task 04 is the HTTP equivalent of that tool. **If fabro ever enables it
server-side, the node becomes replaceable by a native tool call** — note that in the
deployment log when it happens.

## Architecture

The trigger is one command node, `trigger_review`, on `open_pr → trigger_review →
exit`. It does, in order:

1. Read `/tmp/fabro/pr_number` (written by `open_pr`; task 04).
2. Skip if `/tmp/fabro/review_triggered` exists.
3. Derive its own run id from the run branch: `basename $(git rev-parse --abbrev-ref HEAD)`.
4. Shallow-clone `https://github.com/andrewthetechie/fabro-workflows` at `main`.
5. `POST /workflow-versions` with the `pr-review` package → `workflow_version_id`.
6. `GET /automations`, select `workflow == "pr-review" && target.repo == <this repo>`.
7. `POST /runs` with a `RunIntent` → a run in `submitted`, **not running**.
8. `POST /runs/{id}/start` — without this the run never executes and nothing reports it.
9. Write the marker; print `context_updates`.

### Why the child run is correct

`fabro-server/src/run_intent.rs` resolves the run's settings layer from the
**workflow version's own `workflow.toml`** (`version.config_path()`, then
`version.files().get(&config_local)`). So the created run gets `pr-review`'s
`[run.clone] depth = 0`, `[run.run_branch]`, `[run.integrations.github.permissions]`
and `[run.model.fallbacks]` — the exact settings a hand-fired review gets.

This is the single property that makes the whole design work. Task 03 proves it
empirically before anything is wired into a graph.

## The API contract

Base URL inside the sandbox: `http://10.10.0.32:32276/api/v1` (the docker bridge
gateway `172.17.0.1` also answers; both were verified at HTTP 200 from a live
sandbox). Auth: `Authorization: Bearer $FABRO_API_TOKEN`.

### `POST /workflow-versions`

Content-addressed and idempotent — *"Repeating the same canonical content returns the
same identifier."* Body is a `WorkflowVersion`:

| Field | Value here |
|---|---|
| `entrypoint` | the package's canonical entrypoint path |
| `files` | every workflow-local text file, keyed by canonical path: `workflow.toml`, `workflow.fabro`, `prompts/*.md.j2` |
| `workflow_dependencies` | `{}` — `pr-review` has no child workflows |

Returns `{"workflow_version_id": "..."}`. Responses: 201, 400, 413, 422, 500.

**Enumerate `files` from the directory, never from a hardcoded list.** A prompt added
to `pr-review` later must not silently fail to register.

### `POST /runs`

**This creates a run. It does not start one.** `RunIntent` is *"a request to create,
but not start, one run from an immutable workflow version"*, and the created run sits
in `submitted` with `stages: []` indefinitely — measured against `0.354.0-nightly.0`
by leaving one alone for 90s. The CLI has the same two-step shape: `fabro create`
stops at `submitted`, `fabro start <run>` launches it. Task 03 found this the hard
way, on a fire that produced a review run which never ran and raised no error
anywhere.

Body is a `CreateRunRequest` = `RunManifest | RunIntent`. Use `RunIntent`:

| Field | Required | Value here |
|---|---|---|
| `workflow_version_id` | yes | from the previous call |
| `target` | yes | `GitRunTarget` — repo and branch from the automation |
| `args` | yes | `RunIntentArgs`; `inputs = {"pr_number": <int>}`, plus `labels` |
| `environment_id` | no | from the automation; omission selects `default` and would be wrong |
| `parent_id` | no | the backlog run id, when it validates as a ULID |
| `title` / `goal` | no | omitted |

`args.labels` carries at least `source: "backlog"` and the originating issue number,
so lineage degrades to something greppable if `parent_id` has to be omitted.

### `POST /runs/{id}/start`

No request body. Moves the run from `submitted` to `runnable` and hands it to the
scheduler. Returns 200 with the run record; 404 if the id is unknown, 409 if the run
is not in `submitted`.

Read `lifecycle.queue_position` off **this** response, not the create response — the
start call is the first point the scheduler has seen the run, so it is the only
meaningful place to observe backpressure at `max_concurrent_runs = 3`.

A run left created-but-not-started is inert and invisible: no sandbox, no stages, no
error. Treat a failure here as a failure of the whole trigger.

### What must **not** be used

- `POST /automations/{id}/runs` — finding 1.
- `GET /api/v1/workflows` — returns **501**; workflow cataloging is unimplemented,
  so there is no lookup path from a name to a version id. Registration is the only way.

## Canonical state files

| File | Written by | Contract |
|---|---|---|
| `/tmp/fabro/pr_number` | `open_pr` (task 04) | the PR number the run just opened; the trigger's only input |
| `/tmp/fabro/review_triggered` | `trigger_review` | presence means a review was already fired this run |

## Context keys the trigger emits

`trigger_review` carries `output_schema="routing"` and emits exactly one of:

```json
{"context_updates": {"review_run_id": "01M2...", "review_trigger_error": ""}}
{"context_updates": {"review_run_id": "", "review_trigger_error": "<one line>"}}
```

**Both keys are written on both branches.** A stale key from an earlier visit must
never satisfy a condition meant for this one.

## Secrets

`FABRO_API_TOKEN` is a **vault** entry, injected into backlog's sandbox with:

```toml
[run.environment.env]
FABRO_API_TOKEN = "{{ secrets.FABRO_API_TOKEN }}"
```

`[run.environment.*]` is a sparse override that applies to the **selected server
catalog environment** — it does not redefine `python` / `python-node` / `ts` /
`rust-node`. `RunEnvironmentLayer.env` is `StickyMap<InterpString>`; `{{ secrets.* }}`
resolves from the server vault at launch, fails closed when missing, and the resolved
value is never persisted in the run definition.

Only the **name** appears in this repo. The repo is public.

## Golden rules — inherited, and they all still apply

1. **`output_schema="routing"`** on every command node that prints `context_updates`.
   Nothing else in fabro tells you it is missing.
2. **A failed node still routes**, taking its *unconditional* edge. `trigger_review`
   uses `on_failure="succeed"` deliberately — see task 04 for why that is safe here
   and what it costs.
3. **Hook matchers are unanchored regexes.** Write `^trigger_review$`.
4. **`//` is the comment; `#` only inside quoted strings.**
5. **`\"` is the only backslash allowed in a `.fabro` file.**
6. **Inline scripts are POSIX `sh`** — no `[[ ]]`, no `pipefail`, no arrays. `sh -n`
   is blind inside `jq '...'`; compile embedded jq separately.
7. **`tomllib` needs Python 3.11+.** macOS ships 3.9.
8. **Never print a secret.** `set -x` anywhere near the token is a leak into the
   stage log.

## Known risks, recorded deliberately

| Risk | Detail |
|---|---|
| The bridge token cannot be rotated independently | `dev_token` is `Option<String>` and `dev_token_matches` compares against one expected value — fabro supports exactly **one** dev token. Revoking the bridge's access means rotating the server token, which also breaks the CLI and `discord-notify.sh`. |
| The token is visible to agents | `[run.environment.env]` reaches "command **and** agent execution". Every agent in a backlog sandbox can read `FABRO_API_TOKEN`. Accepted: that sandbox already holds a `GITHUB_TOKEN` with `contents: write` on four repositories, on a trusted single-tenant box. |
| `parent_id` couples to branch naming | It is derived from `fabro/run/<run_id>`. If `[run.run_branch] enabled` ever goes `false` in backlog, lineage breaks. The trigger validates the ULID shape and omits `parent_id` rather than sending garbage. |
| The `pr-review-*` automations are now config-only | They are read for `environment_id` and `target`, and **never fired**. They look dead. Do not delete them — the bridge resolves its per-repo config from them. |
| Backpressure is unproven | `server.scheduler.max_concurrent_runs = 3`. `lifecycle.queue_position` exists in the schema but is `null` across all 20 runs, so queuing has never been exercised here. The trigger logs the response code and `queue_position` on every fire so the evidence accumulates before it matters. |
| A lost `start` response can leak a second review | The marker is written only after a successful fire. If `POST /runs/{id}/start` succeeds server-side but its response is lost, the trigger exits non-zero, writes no marker, and a `human_rescue → [P] Accept partial → open_pr` revisit fires a second review on the same PR — the double-`--force-with-lease` hazard the marker exists to prevent. Narrow, and reachable only on the rescue path. Accepted; the alternative is writing the marker before the fire, which converts every transient failure into a silently skipped review. |
| Auto-merge is out of scope, not foreclosed | The terminal signal a future auto-merge stage would key on — the `ai-review-complete` label plus green CI — already exists and is unchanged by this stage. |

## Task index

| # | Task | Needs smarter LLM? |
|---|---|---|
| 00 | This document | — |
| 01 | Vault secret and injection proof | no |
| 02 | `fire-pr-review.sh` | **yes** |
| 03 | Prove the API sequence against a real PR | **yes** |
| 04 | backlog graph: `pr_number` file + `trigger_review` node | **yes** |
| 05 | backlog `workflow.toml`: env injection + hook | no |
| 06 | `discord-notify.sh`: the `review-triggered` kind | no |
| 07 | Validate | no |
| 08 | Deploy + live E2E on jelly-swipe | **yes** |
| 09 | Correct AGENTS.md and ops/README | no |
| 10 | Update the deployment log | no |

Do them in order. 03 needs 02. 07 needs 04–06. 08 needs 07 and 01. 09 and 10 need 08.
