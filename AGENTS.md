# AGENTS.md

Four production Fabro workflows and the ops tooling for the host that runs them.

## Pushing to `main` deploys

Every automation resolves `workflow_source` to `andrewthetechie/fabro-workflows@main`
at fire time, so a commit on `main` is live on the next fire. Those runs open pull
requests and force-push branches in four real repositories. There is no staging
branch, no review gate, and no rollback other than another commit. Validate before
every push.

Commit to `main` directly. That is this repo's entire history, and it is the only
branch anything reads.

## This repo is public

No token, key, or webhook URL belongs in a tracked file. Use `${VAR}` and `.env`
indirection and reference secrets by name; `ops/README.md` maps each one to where it
actually lives.

## Layout

| Path | What it is |
|---|---|
| `.fabro/workflows/<name>/` | **The only tree the automations read.** Four runnable packages — `backlog`, `pr-review`, `issue-triage`, `arch-review` — plus two importable graphs with no `workflow.toml`: `_shared/review-merge/`, which `backlog` and `pr-review` splice in, and `_shared/triage/`, which `issue-triage` and `arch-review` splice in. `backlog/scripts/discord-notify.sh` is executed by hooks, so changing it is a deploy. |
| `ops/` | Host replication: compose, the coder scheduler, profile images, provisioning, branch sweeper. Start at `ops/README.md`. No automation reads this tree. |
| `docs/pr-review-bridge/` | The task series for the third stage: `backlog` triggers a `pr-review` run on the PR it just opened. `00-overview-and-contracts.md` first. |
| `docs/auto-merge/` | A cross-cutting series for the fourth stage (the squash-merge), spanning `backlog` and `pr-review`: kill switches, Conventional-Commits titles, the merge graph, `ci_fix`. `00-overview-and-contracts.md` first. |
| `docs/merge-rate/` | The implementation plan for ADR 0011: stop per-stage checkpoint pushes, rerun flaky CI once, `ci_fix_t1` on `glm-5.3-flash`, CI parity in two target repos, an `autofix` stage, and an 8-task budget whose remainder the scheduler queues with a new `priority` label once the parent PR merges. `00-overview-and-contracts.md` first. Tasks 05–07 run in other repositories and task 11 needs the host. **Applied 2026-09-24** in this repository; the target-repository halves of tasks 05 and 06 are PRs there. The budget was then amended so that extra-review follow-ups are exempt (ADR 0011 D6 amendment). |
| `docs/issue-triage/` | The task series for the front of the chain: triage a `needs-triage` issue, ask the human only what the repository cannot answer, and promote it to the `agent` label `backlog` acquires from. |
| `docs/architecture-review/` | The task series for ADR 0012: `arch-review` scans a repository for deepening candidates twice a week, files the strongest as `architecture` issues, and triages the waiting issues toward `agent` through the shared `_shared/triage/` phase. `00-overview-and-contracts.md` first. An `architecture` PR is never auto-merged. **Applied 2026-09-25.** |
| `docs/fabro-upgrade/` | The task series that moved the host from fabro 0.354.0-nightly.0 to **0.362.0-nightly.0**. It was rehearsed on a copy of the live database on 2026-09-25: the upgrade must drop fabro's run history (0.362's run-history activation rejects every run 0.354 stored) and rewrite the catalog to `codecs = [...]` (0.362 refuses `codec` and the protocol adapter ids at startup). `00-overview-and-contracts.md` first. **Applied 2026-09-25.** |
| `docs/factory-roadmap/` | The ranked improvement plan for the whole factory, one design record per item (A1–C4, H): andon cord, proposal gate, anti-oscillation, diff hygiene, cross-family refuter, scorecard, value-based queue, submit tool, shared code context, elastic fleet, scout, memory, upgrades. `00-overview.md` first. Records, not task series. An item becomes its own `docs/<name>/` series when picked up. |
| `docs/<workflow>/` | The numbered task series each workflow was built from — operator decisions, file contracts, and the reasoning behind every non-obvious choice. |
| `ops/check-routing-schemas.py` | Catches command-node routing-schema mismatches that `fabro validate` accepts and fabro only reports at runtime. Reads the parsed AST, not the DOT text. |
| `ops/test-task-gates.sh` | Runs `backlog`'s `claim`, `mark_stuck`, `open_pr`, `open_pr_prep` and task-queue nodes (`decompose_gate`, `improve_gate`, `next_task`) against fixtures, extracted verbatim from the graph. The only gate that executes a node's shell. Offline; no host or container. |
| `ops/fabro-run-status.sh` | LLM-free health check for an in-flight run: alive, where in the graph, making progress, and whether a compaction says the decomposition was oversized. |
| `docs/scheduler/` | The coder scheduler: an external service that owns admission to the two llama.cpp instances, because fabro's own queue is FIFO-on-creation with no priority and no per-pool concurrency. `00-overview-and-contracts.md` first — it carries 19 settled operator decisions and the 11 source findings behind them, then 14 numbered task drafts. Each draft is written to be implementable from the overview plus its own file plus this repository. **Drafts 01–13 are built and deployed, and draft 14's 24-hour shakedown window restarts with the container** — its acceptance criteria are pending, not unstarted. Do not trust a start time written here: every `docker compose up -d --build scheduler` re-dates the window, and 2026-09-20 re-dated it twice (the run-history deploy, then a queue-page deploy). Read it with `docker inspect -f '{{.State.StartedAt}}' fabro-scheduler`. The `claim` regression that killed the first bring-up is fixed (`5fa974d`, the `mkdir -p /tmp/fabro` that died with `acquire`) and two runs have cleared that node since. `ops/test-task-gates.sh` now covers `claim` and `mark_stuck`, so the same class of shell-level regression fails offline instead of costing two coder boxes four hours each. Draft 13 turned the four `backlog-<repo>` schedules off, so the scheduler is the only producer of `backlog` runs; `ops/fabro-automation-schedule.sh` is the way back on, and `ops/fabro-fire-backlog.sh` is the manual escape hatch. The service itself is `ops/scheduler/`; its LAN page is `http://10.10.0.32:32280/`, and `docs/scheduler/OPERATING.md` is the operator's quickstart — the page, the three controls, changing repo priority (a rebuild), and the two monitor conditions. |
| `docs/perf/` | Why a run takes four hours, measured from the run store rather than guessed. `00-overview-and-measurements.md` first — it is also where the source-verified list of what fabro's docker provider **cannot** do lives (no mounts, no service provisioning). `01` is what was applied, `02` is what needs an operator decision, `03` is the per-repository `.fabro/ci.sh` work, `04` is why compaction is a sizing alarm rather than a cost and what the task-size work changed. |
| `docs/run-history/` | The task series for the scheduler's run-history page: a `run_history` row written inside the lease-release transaction, and `GET /history` to read it. `00-overview-and-contracts.md` first — it carries the canonical schema, the five release paths and the verified GitHub contract. ADR 0008 holds the decision. **Applied 2026-09-20.** |
| `docs/research_improvements/` | An audit of all three packages against the Fabro source: what we hand-roll that Fabro already does, four operator-observed gaps traced to Fabro lines, and nine settled dead ends. A plan, not a changelog. `00-overview.md` first: its status table (2026-09-24) records what shipped (the stall watchdog, the rescue-gate label, the `truncate` preamble), the one gap still open in that work (nothing deletes `rescue.md` between tasks), and the priority of the rest. The merge-rate review of the same date is ADR 0011. |
| `docs/direct-providers/` | A four-task plan to take fabro off LiteLLM and point it at each inference endpoint directly, so a request for a box on the LAN stops crossing a Kubernetes ingress and coming back. **Tasks 01 and 03 are applied and live as of 2026-09-20** — `coders-a`/`coders-b`/`long-context` resolve to `box-a`/`box-b`/`spark`, the hosted models to `zai`/`kimi`, and no `litellm:` target remains in a workflow TOML. Tasks 02 and 04 are not applied, and **ADR 0007 is `accepted`**. Task 03's first application omitted `display_name` on `[llm.providers.kimi]` and cost a 14-hour outage; the invariants table carries the rule. `00-overview-and-contracts.md` first: it holds the endpoint mapping read from the live LiteLLM, the five findings, the model-id contract, and the two checks that need a scratch overlay. Task 03 is the only one that needs an operator decision, because it moves `ZAI_API_KEY` and `KIMI_API_KEY` onto the fabro host. Task 02 is the one that matters most: the ingress is currently the only thing that bounds a hung inference request, so removing it without a replacement makes a 15-minute stall unbounded. |
| `docs/plan/` | Single-file design records for changes that cross packages and are too small for a numbered series. `model-escalation.md` is the Discord alert that fires when a stage leaves the local coder box for a hosted model — applied 2026-09-20, and the record of why the matcher is `(^|[.])<id>$` rather than `^<id>$`. `local-inference-tuning.md` is where the inference actually runs and why — per-node token and peak-context measurements from the run store, `improve` moved onto the run's pinned box, and the six reviewers split into six stylesheet classes so they can be tuned apart; applied 2026-09-21. |
| `.scratch/` | Untracked working notes. |

## Writing, changing, or diagnosing a workflow

**Invoke the `/fabro-workflow` skill first.** It carries the DOT attribute tables,
node types, condition grammar, the transition cascade, and the CLI. Treat it as the
source of truth for how Fabro behaves; this file covers only what is specific to this
deployment.

Then read `docs/<workflow>/00-overview-and-contracts.md`. Each workflow is built on
file-backed contracts: an agent writes JSON to a path under `/tmp/fabro/`, and a small
command node validates it with `jq` and emits `context_updates` that decide routing.
Changing a contract's shape without changing the gate that reads it, or the prompt that
writes it, is the most common way to break one of these.

## Validating

There is no `fabro` binary on this Mac, and the host CLI resolves `@prompts`
differently from the server. Validate in the container:

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/pr-review/workflow.toml'
```

Baselines as of 2026-09-24, re-confirmed on 2026-09-25 against **fabro 0.362.0-nightly.0** (review+merge shared and imported; ADR 0011 added
`autofix` and `file_remainder`):
`Backlog (61 nodes, 143 edges)` with exactly one warning — `issue_number` unbound in
`claim` (draft 10's deliberate fail-closed input, the same shape as `pr_number`) — and
`PrReview (30 nodes, 65 edges)` with exactly one warning — `pr_number` unbound in
`validate_input` — and `IssueTriage (13 nodes, 26 edges)` clean, and `ArchReview (21 nodes, 44 edges)` clean. Both include the 10 nodes of `_shared/triage/`. Backlog and PrReview both include the ~21
nodes of `_shared/review-merge/`, which `fabro validate` splices in; `fabro parse`
shows the unexpanded placeholder instead, so node counts only match after validate. That warning is deliberate. Binding
`[run.inputs] pr_number` would silence it and let a run fired with no input review PR
#1 instead of failing at admission. (`auto_merge` is another unbound `{{ inputs.* }}` in the
same node, and it is *not* silenced by being supplied at fire time — `pr_number` is
supplied that way too and still warns. fabro emits one undefined-input diagnostic per
node **attribute**, and both references live in `validate_input`'s `script`, so the
second is folded into the first. Prove it exists by validating a scratch copy with
`pr_number` literalised: it then warns about `auto_merge`.)

`fabro validate` does NOT catch a routing-schema mismatch on a command node, in
either direction, and both fail only at runtime. Run the checker as well:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && python3 /tmp/check-routing-schemas.py \
  /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro'
```

`ops/check-routing-schemas.py`, deployed alongside the graphs the same way. It exits
non-zero on any mismatch, so it drops into a pre-push hook. A node that declares
`output_schema="routing"` and prints no routing object fails deterministically with no
retry — that cost run `01M2R057XAWN8ZG0A7ZV7YPJXK` at `pr_handoff`, with the PR
already open.

Neither gate runs the command nodes' shell. `backlog`'s task queue is several hundred
bytes of `jq` spread across `decompose_gate`, `improve_gate` and `next_task`, and a
cursor off-by-one there silently skips a task rather than failing — the same class of
bug, one layer down. `ops/test-task-gates.sh` extracts those `script` attributes from
the graph verbatim, rebases `/tmp/fabro` onto a scratch directory and runs them against
fixtures:

```sh
./ops/test-task-gates.sh      # 335 checks, offline — no host, container or network
```

It needs only `jq` and `python3`, so it belongs in the same pre-push hook. It covers
the task-queue gates, `open_pr`, and — since 2026-09-19 — `claim`, `mark_stuck` and the
shared review-merge graph's `merge`. Since 2026-09-21 it also covers `open_pr_prep`'s
empty-diff floor, and that section is the one place here that uses the REAL `git`: it
builds an actual repo and clones it over `file://`, because what is under test is whether
`git diff --quiet origin/main HEAD` tells the truth about a branch carrying nothing but
checkpoint commits. A stub would only test the stub. It restores `$ORIG_PATH` first —
`next_task` prepends an `exit 0` `git` and never takes it off, so every later
`SAVED_PATH="$PATH"` captures that stub too.
It says nothing about whether an agent fills a contract correctly.

**`claim` is in there because leaving it out cost the cutover.** Draft 10 deleted
`acquire`, `acquire` was the only node that ran `mkdir -p /tmp/fabro`, and `claim`
— now the run's first node — still redirected into that directory. Every `backlog`
run died at its second node and parked on `human_rescue` for four hours holding a
coder box; `fabro-fire-backlog.sh` failed identically; both offline gates stayed
green throughout, because neither runs a node's shell. `claim` is staged against a
sandbox path that *does not exist*, which is the only way a test can observe that a
node creates it — every other gate rebases onto a directory the harness already
made. If you add a node that writes before `prep`, stage it the same way.

`fabro validate` does not parse `workflow.toml` strictly. A dotted model key that loses
its quotes becomes a nested table and the automation fire returns 422, with nothing
reported until then. Check it separately, on 3.11+ — macOS ships 3.9, which has no
`tomllib`:

```sh
python3.11 -c 'import tomllib; print(tomllib.load(open("workflow.toml","rb")))'
```

`fabro preflight` is **not** an offline check: its `LLM` check makes a real completion —
its detail line reads `Probe: basic generation` — so it fails with `server request timed
out after 30s` whenever a coder box is busy, which on this host is most of the time. The
summary it prints for that check is the model it probed, so preflight *is* the way to see
which model a stylesheet rule resolved to at real run time. Read a timeout there as
congestion, not as a graph fault; `fabro validate` and the routing checker are the gates
that need no box, and they are the ones to run before every push. Two gotchas when
preflighting `pr-review`: supply `-I pr_number=1` or it stops on the deliberate unbound
`pr_number`, and `-I auto_merge=0` as well or it then stops on the second unbound input in
the same `script` attribute.

## Deployment invariants

These pass `fabro validate` and fail at runtime. All three workflows depend on all of
them, except where a rule names one by id.

| Rule | What happens otherwise |
|---|---|
| `output_schema="routing"` on every command node whose script prints `context_updates` | Fabro never scans that node's stdout. Routing goes inert, the run walks its unconditional edges, and no error appears anywhere. This cost one live debugging round already. |
| Model stylesheet class selectors use `[a-z0-9-]` only — `.rebase-t2`, never `.rebase_t2` | `expected '{' after selector` |
| A per-run model choice goes in the root graph's `model_stylesheet` as `{{ inputs.X \| default('value') }}` — with single quotes — and `X` is never bound in `[run.inputs]` | The root stylesheet is the only per-run routing lever fabro has: node `model` attributes are literal text and `args.model` loses to any explicit stylesheet assignment. The render is **strict**, so an unbound input does not render empty, it fails the whole run at compile with `422 run_compile_invalid` — hence `default()`. A double quote terminates the enclosing DOT attribute string, so single quotes are the form that works; `\"` also parses but depends on the one allowed backslash. Binding `X` in `[run.inputs]` instead is the `pr_number` trap: it turns "no input supplied" into one specific answer, silently sending every hand-fired run to one box. |
| A model named by a stylesheet must also exist under some `[llm.providers.*.models.*]` in the server's settings overlay | Serving the name upstream is not enough: fabro resolves a node's model against its own catalog and fails before any call is made. Since 2026-09-20 the rows are spread across `box-a`/`box-b`/`spark` (the three LAN boxes), `zai`, `kimi` and `litellm` — `litellm` keeps only `coders`. `fabro validate` does not check the catalog, so a green validation says nothing here. Unlike `max_concurrent_runs`, catalog entries take effect with **no restart** — `replace_runtime_settings` (the 5s poll) rebuilds the catalog; only the cap is copied out once at startup. |
| A provider name fabro does not know built-in declares `display_name`; a known one may be extended by a bare model table | Fabro knows `zai`, `litellm` and `moonshot`, so `[llm.providers.zai.models."glm-5.3"]` alone is valid. It does not know `kimi`, `box-a`, `box-b` or `spark`, so each needs a full provider table **including `display_name`**. Omitting it fails the whole `[llm]` layer with `catalog layer "settings [llm]" is invalid TOML: missing field display_name` — and the failure is close to silent. The server rejects the reload and **keeps the previous catalog**, so it stays healthy and `GET /settings`, `fabro model list` and `fabro doctor` all keep answering from the good copy. Only a **worker** builds its catalog from the file, so every new run dies at its first node with `Worker exited before emitting a terminal run event: exit status: 1`, in about 0.2s. Runs already in flight finish normally, which hides it further. On 2026-09-20 that cost 14 hours and drained all four repos: the scheduler requeued each instant failure three times, then released the lease leaving the issue on `agent-in-progress`, until the queue was empty and the page read "no open work". After any overlay edit, check `docker logs --since 1m fabro-fabro-1 \| grep -c "Rejected reloaded"` prints `0`. |
| A provider table uses `codecs = [...]` (default `["openai-chat"]`), never `codec` or a protocol adapter id (`openai-compatible`, `openai`, `anthropic`, `gemini`) | A cold start refuses to boot (0.362's catalog loader rejects `codec`; finding R2), and a hot reload is rejected and the old catalog kept — the silent 2026-09-20 failure. The `Rejected reloaded` check is the same one in the `display_name` row above. |
| Every model id a stylesheet names must resolve to the **intended** provider, not merely to a provider | Two providers may carry the same id, and a **built-in one wins** over an overlay-defined one. Storing `KIMI_API_KEY` for the new `kimi` provider also flips fabro's built-in `moonshot` to `configured`, so an unqualified `kimi-k3` resolved to `moonshot` and failed `Invalid Authentication` — a coding-plan key is not valid for moonshot's endpoint. It hit `backlog`'s `.review-frontier` (`spec`, `extra_decompose`). A stylesheet **cannot** qualify its way out: `kimi:kimi-k3` is `Unknown model`, because `provider:model` is only valid as a `[run.model.fallbacks]` target — which is why the fallbacks were fine and only the stylesheet broke. The fix is `[llm.providers.moonshot] enabled = false` in the overlay. `fabro model list` shows the collision; only `fabro model test -m <id>` shows which side won. An unconfigured provider (`venice` carries `kimi-k3` and `glm-5.3` with no key) never competes. |
| Do not store a key for `openrouter`, `fireworks`, `vercel` or `venice` without re-running `fabro model test` for every stylesheet id | Those new built-ins also carry `glm-5.3`/`kimi-k3` (finding R5), and a configured built-in wins over an overlay provider — the `moonshot` lesson again, see the row above. |
| `[run.git.author]` is set in the server overlay (`andrews-ai-agent`) | Without it fabro derives the identity from the GitHub token user and fails the run at setup if the lookup fails (0.362 behaviour change 4), and factory commits become indistinguishable from the operator's. |
| A class used by a node in `_shared/review-merge/` has an explicit rule in **both** importing stylesheets | The phase is spliced into `backlog` and `pr-review`, whose `*` rules differ — `coders-a` and `glm-5.3`. A class with no rule silently inherits `*`, so one forgotten in `backlog` runs a merge-phase reviewer on a **local box** instead of failing: the auto-merge gate is quietly downgraded and nothing reports it. `fabro validate` checks stylesheet syntax, never coverage. Six classes carry this today (`merge-standards`, `merge-spec`, `merge-fix`, `ci-fix`, `rebase-t2`, and `rebase`, which only `pr-review`'s own rebase agent still uses). |
| A `#` comment inside a `script=` attribute is paid **twice** in the next agent's prompt | Fabro writes a command node's whole script into `outcome.notes` (`handler/command.rs:198`) and its preamble prints the same script again as `- Script:`, at every `summary:*` fidelity. Measured 2026-09-19 on run `01M2X2MVPC2NXY1BW5CVKPFF3S`: 13.7 KB of 89 KB of agent prompt was that one duplication, and `claim` was 54% comments. Put the prose in `//` DOT comments above the node, where it costs nothing. `truncate` removes the cost today; the rule keeps it removed if fidelity is ever raised. |
| Every graph runs at `default_fidelity="truncate"` | Anything higher prepends a stage recap to every agent prompt — `compact`, the fabro default, prepends *every* completed stage with up to 25 lines of output each. At `summary:low` the preamble was still 23% of every prompt. Nothing here needs it: every prompt names its inputs by path, which is the point of the file-backed contracts. The one exception was `human.gate.text`, which `record_guidance` writes to a file. |
| `\"` is the only backslash in a `.fabro` file | The DOT parser turns `\n` into a real newline, so `printf '%s\n'` becomes `printf '%sn'` and jq's `"\n"` becomes an unterminated string. Get a newline inside an embedded jq program with `([10]\|implode)`. |
| `//` starts a comment; `#` only ever appears inside a quoted string | `#` is a DOT comment. `Resolves #N` and `##` headings inside a string are fine — never strip one to make a grep pass. |
| Inline scripts are POSIX `sh` | No `[[ ]]`, no `pipefail`, no arrays. `sh -n` catches this, but it is blind inside `jq '...'` — compile embedded jq programs separately. |
| Every command node's unconditional edge lands on the workflow's terminal-failure node | A failed node still routes, and it takes its *unconditional* edge. Where that edge is the happy path, fold `\|\| outcome=failed` into the escape edge's condition. |
| Reset per-iteration context keys, and write every key on both branches | A stale key from an earlier loop can satisfy an edge meant for this one. |
| Delete a contract file before the agent that writes it runs | An agent that exits succeeded without writing hands the gate its predecessor's result. |
| Agents do not run `git` | Two deliberate exceptions: `pr-review`'s rebase agent and `backlog`'s `resolve_merge` agent, which need `git add` and `--continue`. |
| A conflicted merge or rebase never crosses a stage boundary | The checkpoint is `git add -A && git commit`, and `git add` marks a conflicted file resolved — so the checkpoint commits conflict markers. The command node aborts to restore a clean tree; a dedicated agent then redoes and resolves the whole thing inside one stage. On 0.362 a checkpoint **commit** failure stops the run (it was best-effort on 0.354), so this rule is now also a run-killer, not just a merge hazard. |
| `backlog` never chooses its own issue: `claim` validates `args.inputs.issue_number` and fails closed | Draft 10 deleted `acquire`, the marker comment and the lowest-ULID arbitration together — the race they patched (two runs implementing jelly-swipe#356 on separate branches) is now prevented upstream, by the scheduler's lease table rather than by the graph. Restoring any selection inside the graph re-opens it. `claim` must keep quitting on an issue that is missing, closed, or not `agent-in-progress` after its idempotent label swap; picking a different issue is the failure the whole design exists to prevent. |
| Anchor hook matchers | They are unanchored regexes tested against `node_id`, `handler_type`, `edge_to`, `edge_from` and `tool_name`. Write `^open_pr$`, not `open_pr`, for any id that prefixes another. For a node that arrives through `import=`, anchor at the end but allow the prefix: `(^|[.])report_merged$`. The running id is prefixed (`review_merge.report_merged`), so a bare `^report_merged$` matches nothing — and a hook that matches nothing is silent, because the runner logs no line for an event with zero matching hooks. |
| `[run.environment.env]` in backlog's `workflow.toml` must never gain an `id` key | It pins all four backlog automations to one environment, and two of the four repos fail CI on the wrong image. It also breaks `fabro validate`, which resolves a non-default id against the CLI's own local catalog and errors. |
| Every terminal report of the imported `review_merge` phase has a hook **in the graph that owns the run** | The phase is spliced into `backlog` and `pr-review` with `import=`, but hooks are per-package: a hook in `pr-review/workflow.toml` does nothing for a `backlog` run. `54c21be` moved the phase into the backlog run and carried no hook across, so `report_merged` and `report_blocked` were silent for every scheduler-dispatched run until 2026-09-19; a blocked merge ends the run `succeeded`, so no failed-run hook, no `fabro-monitor.sh` condition and no scheduler requeue can see it either. Add all three hooks when a new graph imports it, each spelled `(^|[.])<id>$` — a bare `^report_merged$` does **not** match the prefixed `review_merge.report_merged`. The `^…$` form these hooks carried from `e7eedc9` matched nothing, so every report notification was silent; fixed 2026-09-20, after run `01M2XEP62DVGS9JJT9CZQ7HE2Q` checkpointed `review_merge.report_blocked` with no `Running hooks` line. |
| Both auto-merge switches fail closed on a value that is **present and not exactly the enabled one** — empty, `false`, `0`, `TRUE`, malformed. **Absence is not that case**: an absent per-repo `auto_merge` token means **on** (decision 10), and an absent `FABRO_AUTO_MERGE` variable means no run is created at all | A `gh` call that returns nothing, or a broken injection read as "not disabled", merges an unreviewed PR into `main`. On `lawncare-saas` that is also a deploy. The inverse error is just as costly: reading "fails closed" as "an untouched row is disarmed" is wrong — an untouched `pr-review-<repo>` row is **armed**, and three of the four are untouched. |
| `[run.environment.env]` interpolates `{{ vars.X }}` only — never `${X}`, never `{{ env.X }}` | `${X}` reaches the sandbox as literal text. A kill switch spelled that way is pinned off forever, and the shakedown cannot tell it from a correctly disarmed one. |
| The `FABRO_AUTO_MERGE` server variable must exist | An unset `{{ vars.X }}` fails the RunIntent at compile time, so no `pr-review` run is created at all. `provision-server-state.sh` creates it; `off` writes `0` and never deletes. |
| `POST /variables` upserts, so `provision_variable` is create-if-absent | A re-provision that POSTs unconditionally silently re-arms a switch an operator killed mid-incident. It reports drift instead. |
| A profile image must satisfy its repository's `.fabro/ci.sh`, not just its `setup.sh` | jelly-swipe's `ci.sh` gained `cd frontend && npm ci` while `fabro-python:local` had no npm. Under `set -euo pipefail` that is exit 127, `validate` exits 1, and every task walks the four-tier rework ladder at 45 minutes a tier against a failure no code change can fix. `ops/profile-images/build-images.sh` gates on this; a hand-built image does not. |
| `open_pr_prep` refuses to build a PR when the branch tree equals `origin/main` | Nothing downstream of that node reads the working tree: the title comes from the issue title and the body from the first prose paragraph of the issue body, so a branch on which no task landed yields a **well-formed PR asserting the issue was implemented**, and the node's own three `grep -q` assertions pass because they check the derivation, not the diff. The path in is `human_rescue -> [P] Accept partial`, which was written for "some tasks landed, ship what we have" and read identically to "none did". On 2026-09-21 it opened womens-fantasy-sports#1236 and writers-app#949, both 0 files changed; #1236's summary is a sentence lifted verbatim out of issue #1205. The merge phase then made it worse — `review_fix` read the reviewers' "this is empty" verdict and implemented the issue itself, 396/-101 across 4 files. Covered by `ops/test-task-gates.sh`. |
| No agent may edit `.github/workflows/`, and `open_pr_prep` refuses a branch whose commits do | The sandbox pushes over HTTPS with an **OAuth App** token (`gho_`), and GitHub rejects any push whose new commits create or update a workflow file unless that token carries the `workflow` scope. The host token's scopes are `admin:public_key, gist, read:org, repo`, and `repo` does not imply it. Before ADR 0011 D1 the failure was quiet until it was terminal: the post-stage checkpoint push only **warned** (`[git_push_failed]`), so the run continued with the remote branch frozen, and `open_pr` was the first node that cared. Since D1 `backlog` pushes nothing before `open_pr`, so its push is now the first one rejected. Either way `open_pr` — it dies on `git push -u origin HEAD` under `set -e` in ~1s with no routing schema, so the operator sees `unknown error`. Its unconditional edge lands on `human_rescue`, whose `[P] Accept partial` returns to `open_pr_prep` with no attempt counter, so the same deterministic rejection loops. On 2026-09-22 run `01M33SDMZF55NAEV8JV29A8EA4` deleted one clause from a five-line **YAML comment** in womens-fantasy-sports' `test-backend.yml` — seven lines, no workflow semantics, flagged by its own reviewer as a nit — and that single commit rejected 17 of 96 checkpoint pushes, froze the remote branch 3.5h behind the sandbox, and stranded 98 commits and 71 files. The gate reads `git log origin/main..HEAD -- .github/workflows/`, never a two-dot `git diff`: GitHub judges the **commits in the push**, so edit-then-revert is still rejected while a net-diff test would call it clean. Recovery is to push the branch over **SSH**, which carries no such restriction — once the commits are on the remote, later pushes carry only new commits and are accepted. Covered by `ops/test-task-gates.sh`. |
| A root graph's `stall_timeout` exceeds every node timeout in it, including the ones spliced in by `import=` | The stall watchdog **cancels the run**; a node timeout **routes**. Of the two bounds the smaller one silently wins, and nothing reports which. A command node emits only `CommandStarted` and `CommandCompleted` (`handler/command.rs:94`, `:155`) — command output is streamed to a log recorder and never into the event stream — so a node that polls with `sleep` is invisible to the watchdog, which re-arms only from `Emitter::last_activity()` (`pipeline/execute.rs:85-116`) and parks only while the run is blocked on a human. At the 30-minute default (`graph.rs:687`, and no graph set it) `watch_checks`'s 35m was unreachable: seven runs between 2026-09-20 and 2026-09-22 were cancelled at exactly 1800s in `review_merge.watch_checks` with no `report_blocked`, no PR comment and no `ai-review-needs-human` label, each leaving an open unmerged PR and its issue parked on `Review`. `fabro validate` checks neither number. Both root graphs now carry `stall_timeout="60m"`; the tallest node under it is `watch_checks` at 50m, and `coder` at 180m is the only node above it — an agent emits an event per stream delta, so the watchdog only ever sees one that has genuinely gone silent. |
| A root graph that runs an imported phase in a loop sets `loop_restart_signature_limit` above the number of times one gate can fail in a run | Fabro's failure-signature breaker counts **run-wide** and never resets on success (`lifecycle/circuit_breaker.rs:93-103`), and at the default of 3 it ends the run with an engine error: no edge is taken, so no failure sink runs. Every `*_gate` prints one fixed line, and digits are masked, so a gate's signature is identical for every issue. `arch-review` runs the triage phase up to 18 times, and three issues that each needed one repair turn would stop it mid-queue with no summary and the current issue still carrying `triage-in-progress`. It carries `loop_restart_signature_limit=20`. `backlog`'s own exposure is `docs/research_improvements/01` item 1, still open. `fabro validate` checks neither number. |
| Moving a stylesheet class to a different model voids every timeout derived from the old one | Node timeouts here are measured, not guessed, so the measurement's subject is part of the number. `9b7f5ff` moved `.improve` from `glm-5.3` to `{{ inputs.coder_pool }}` — a single-slot llama.cpp box — and left `timeout="15m"`, which the comment above the node justified with "improve max 180s" taken entirely on glm-5.3. Measured output rate is 22.1 tok/s hosted against 11.8 tok/s on a box, and worse than that ratio for a node that re-reads the checkout every turn: run `01M321VB0DC4N187GHA88RM0QB` burned both attempts at exactly 900004ms and 900005ms, still generating, and reached `human_rescue` having never run a coder. `fabro validate` cannot see this — the graph is valid and the class resolves. Re-time the node in the same commit that moves the class, or say in the comment that the number is now unmeasured. |
| No stylesheet class may name `glm-4.7`, and nothing may fall back to it | It is the only model in the server's catalog whose record carries `features.reasoning: false` (`fabro model list --json`); every other model these graphs name is `true`. The z.ai endpoint **returns reasoning blocks from it anyway**, and on the next turn fabro replays that assistant message against its own capability record and refuses locally — 3ms, no request leaves the host: `LLM error: model zai/glm-4.7 does not support reasoning`. It classifies `api_deterministic|zai|invalid_request`, and that category both bypasses the retry budget (`max_retries=1` never engages) and skips `[run.model.fallbacks]`, which only covers errors and rate limits — so the stage dies outright and takes its unconditional edge. It is nondeterministic by nature, firing only when the model happens to emit reasoning, so `.coder-t2` carried it for weeks before run `01M33SDMZF55NAEV8JV29A8EA4` lost `rework_t2` to it on 2026-09-22 with six of seven tasks already landed. `fabro validate` cannot see it: the graph is valid and the class resolves. `.coder-t2` and both `coders-*` fallbacks now name `glm-5.3-flash` — reasoning-capable, same z.ai coding plan, 262144 window against glm-4.7's 202752. Check a replacement with `fabro model list --json` before naming it. |
| `risk` is gate-enforced in `fix_gate`, not merely documented | Without the check the field is optional in practice, PRs quietly stop auto-merging, and the report still says "complete". |
| The merge node re-reads PR `state` from GitHub immediately before merging | The bridge's double-fire marker is per-run and does not stop a second review fired by hand. Two runs reach `merge`; the loser must report "already handled", not fail. |
| The `merge` node waits for `mergeable` to leave `UNKNOWN` and retries a branch-moved rejection | Any push to the head just before `merge` causes this. Until ADR 0011 D1 that was fabro's own checkpoint push (commit + push after every stage); since D1 neither graph pushes checkpoints, so it is now a push by `ci_fix_gate` or `remerge_base`, or by someone outside the run. The push invalidates GitHub's computed merge ref, and a merge attempted before it is recomputed is rejected as `Base branch was modified` even though the base has not moved. A pure race: `jelly-swipe#386` lost it, with the push ~15ms before `merge` started, with `main` unmoved for 35h and no protection rules, while run `01M2TFEHPYXXTQST6VPXREA854` won it with the same 10ms gap. Without the wait and the retry a mergeable PR is reported to a human as a permissions or conflict problem. |
| Any merge-phase node that fails into `mark_needs_human` writes **both** `merge_block_reason` and `needs_human_reason` | The two renderers read different files: `blocked` reads the first, `needs-human` the second, and `mark_needs_human` renders `needs-human`. A reason written to only one of them leaves the other holding whatever a previous stage left there — on #386 that was a placeholder `validate` writes on its SUCCESS path, so the comment said the run stopped before the review was delivered while the same comment listed all three verdicts. A placeholder describing a stopping point the run went on to pass is a false statement waiting to be printed. |
| `backlog` does not push checkpoints (`[run.run_branch] push = false`); every push after `open_pr` is an explicit node push, and `open_pr` widens the fetch refspec for the run branch | Fabro's checkpoint commit is always `--allow-empty`, so a per-stage push started a full CI run for every stage once the PR existed (womens-fantasy-sports#1247: 80 commits, 78 Actions runs), and the push after `watch_checks` moved the head CI had passed, so `merge` timed out waiting. Without the refspec, the single-branch clone has no `refs/remotes/origin/<run branch>` and `deliver`'s bare `--force-with-lease` is rejected as `stale info` — it only ever worked because the checkpoint push had already made it a no-op. Covered by `ops/test-task-gates.sh` (real git). ADR 0011 D1. |
| `gh pr merge` never passes `--delete-branch` (the merge node passes `--delete-branch=false`) | It deletes the **local** head branch as well as the remote one, and the merge is not the run's last stage: fabro's checkpoint publishes the run branch after every later stage, so the next publish fails with `src refspec … does not match any`, retries three times, and reports `failed/publish_failed`. A run whose PR merged and whose issue closed is then recorded as a failure — cost run `01M2TFEHPYXXTQST6VPXREA854` its status on 2026-09-18, eight seconds after PR #1206 merged. Reaping is `fabro-branch-sweep.sh`'s job. |
| No `gh` call is left to infer the branch: `gh pr create` passes `--head "$B"`, and `gh pr view` names the branch or a PR number | The sandbox clone is shallow and **single-branch** (`remote.origin.fetch` is `+refs/heads/main:refs/remotes/origin/main`), so `refs/remotes/origin/fabro/run/<id>` can never be stored. `git push -u` still prints `set up to track` and writes the branch config, but `@{u}` fails — and gh resolves the head remote through exactly that ref, so a bare `gh pr create` aborts with `you must first push the current branch to a remote` while the branch sits on the remote at `HEAD`. Cost run `01M2VHAGXNM9JNEY71085HV82Y` its PR on 2026-09-19 after 41 minutes of finished work. `open_pr` is the only node that ever inferred it; `pr-review`'s `claim` meets the same clone property and widens the refspec instead, because `gh pr checkout` needs a real local branch. Finding 11 in `docs/auto-merge/00-overview-and-contracts.md`. |
| Every merge-phase command node's unconditional edge lands on `mark_needs_human`; expected blocks are the *conditional* edges | A broken merge — a token without `contents: write`, an API 5xx — otherwise lands in the same quiet "not auto-merged" bucket as a risk-4 PR and stays invisible. |
| Commit-body markers are `:start` / `:end`, never `<!-- /fabro:commit-body -->` | A closing marker with a slash forces `\/` into the extraction pattern, and `\"` is the only backslash a `.fabro` file may contain. |
| The subject is the live PR title; the body comes from the marker block | `release-please` turns an agent's `feat:`/`fix:` subject into a release on `jelly-swipe`/`lawncare-saas`, and the derivation never emits `!` or `BREAKING CHANGE:`. |
| A `settings.toml` value that is read at startup is verified by the container's start time, never by `GET /settings` | fabro polls the overlay every 5s and republishes the *reported* settings, but `max_concurrent_runs` is copied out once at startup and read only by the admission loop — so after an in-place edit `/settings` answers the new value while the server keeps enforcing the old one, and the endpoint you would check with is the one endpoint that cannot tell you. Check that `docker inspect -f '{{.State.StartedAt}}' fabro-fabro-1` is later than the overlay's mtime; `ops/README.md` carries the command. |
| A `backlog` run implements at most 8 **decomposed** tasks (`tasks_coded`, counted in `improve_gate` on `ready`), and never labels its remainder issue `agent` | Extra-review follow-ups, and slices split from one (`from_extra: true`), are exempt: they never count, are never moved, and are worked in this run, bounded by `extra_prep`'s two-round cap. `next_task` moves every decomposed task past the budget, `decompose` tasks and their split slices, to `remainder.json`, and `file_remainder` files them as one issue labelled `agent-remainder`. The scheduler adds `agent` + `priority` only once the parent PR merges (ADR 0011 D6, `ops/scheduler/src/fabro_scheduler/remainder.py`). Labelling it `agent` at filing time would let the scheduler's same-repo saturation pass start it on the other box, from a `main` without its parent's work. Covered by `ops/test-task-gates.sh`. |
| An `architecture`-labelled PR is never auto-merged: `open_pr` copies the label from the issue, `merge_gate` check 2b blocks on it, and `file_remainder` copies it to the Remainder issue | An architecture review (ADR 0012) proposes a refactor, triage promotes it, `backlog` implements it and the merge phase would squash it into `main` with no human anywhere in the chain. On `lawncare-saas` that merge is also a deploy. Check 2b fails closed on a `gh` error. Covered by `ops/test-task-gates.sh`. |

## Deploying to the server after a merge to `main`

Manual today; automating it is the plan.

**Workflow changes need no deploy.** Every automation resolves
`.fabro/workflows/**` from `main` at fire time, so a merged graph or prompt is live
on the next run with nothing to copy.

**`ops/` changes do.** Nothing syncs that tree; the host keeps its own copies. After
a merge that touches `ops/` or `backlog/scripts/`, deploy what changed:

```sh
cd ~/Documents/code/fabro-workflows && git pull --ff-only

# the notify script the backlog hooks call, by absolute path, from the container
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'

# the sandbox profile images. Not a file copy: build-images.sh clones each target
# repo on the host, warms that repo's caches from its own lockfiles, and gates the
# result on an offline cache check plus a live run of the repo's .fabro/setup.sh.
# A failed build leaves the previous image tagged and in use.
rsync -a --delete ops/profile-images/ andrew@10.10.0.32:~/profile-images-build/
ssh andrew@10.10.0.32 'cd ~/profile-images-build && ./build-images.sh'

# the coder scheduler. Its compose service builds from ./scheduler, resolved
# relative to the compose file, so the tree has to sit beside it on the host.
# From draft 09 this deploy ARMS the dispatch loop *and* releases leases: the
# container starts taking a box and creating real runs on its own, and a GitHub
# token without `issues: write` is enough to make every dispatch fail. Draft 08's
# "nothing releases a lease until draft 09, so clear the rows first" is spent —
# `reconcile.py` releases on a terminal run and clears stale rows at startup, so
# *Breaking a stuck lease* in ops/README.md is now only the escape hatch. The rsync is
# load-bearing before the build: the host tree is whatever was last copied there,
# and `--build` alone happily rebuilds an older one.
# `up -d scheduler` names one service and never recreates fabro, which matters:
# a fabro restart fails every in-flight run. The tree carries no credential — the
# scheduler reads its own from ~/fabro/scheduler.env (NOT ~/fabro/.env, which
# holds fabro's SESSION_SECRET). Create it once per host, with both variables:
# without GITHUB_TOKEN the queue is empty and the page says so; without
# FABRO_API_TOKEN every dispatch answers 503 and /health reports
# `fabro.configured: false`. The fabro token is the server's own dev token, read
# out of the volume rather than retyped — there is exactly one.
#   ssh andrew@10.10.0.32 'cd ~/fabro && umask 077 && { \
#     printf "GITHUB_TOKEN=%s\n" "$(gh auth token)"; \
#     printf "FABRO_API_TOKEN=%s\n" "$(docker exec fabro-fabro-1 cat /storage/server.dev-token)"; \
#   } > scheduler.env && chmod 600 scheduler.env'
# `--build` is load-bearing: the runtime image carries `git`, which is how the
# scheduler fetches .fabro/ from main at dispatch time. An image built before
# draft 07 has no git and fails inside a dispatch.
rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d --build scheduler'

scp ops/docker-compose.yaml andrew@10.10.0.32:~/fabro/docker-compose.yaml
scp ops/fabro-branch-sweep.sh andrew@10.10.0.32:~/bin/fabro-branch-sweep.sh
scp ops/fabro-sandbox-sweep.sh andrew@10.10.0.32:~/bin/fabro-sandbox-sweep.sh
scp ops/fabro-monitor.sh andrew@10.10.0.32:~/bin/fabro-monitor.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-monitor.sh'

# the auto-merge kill switch. It talks to the API over the network and runs fine from
# this checkout, but an incident that starts with an ssh session should not also need
# a git clone, so it is deployed alongside the sweepers.
scp ops/fabro-auto-merge-switch.sh andrew@10.10.0.32:~/bin/fabro-auto-merge-switch.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-auto-merge-switch.sh'

# the operator's manual backlog escape hatch (draft 10). Same reasoning as the
# auto-merge switch: an incident that starts with an ssh session should not also need
# a git clone. Operator-only; no automation runs it, which is why it lives in ops/.
scp ops/fabro-fire-backlog.sh andrew@10.10.0.32:~/bin/fabro-fire-backlog.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-fire-backlog.sh'

# the automation-schedule switch (draft 13). The four backlog-<repo> schedules are off,
# which is what makes the scheduler the only producer of backlog runs; this is the
# switch that keeps that deliberate, and `on` is the only undo. Operator-only, same
# reasoning as the two above. It talks to the API over the network and needs no restart.
scp ops/fabro-automation-schedule.sh andrew@10.10.0.32:~/bin/fabro-automation-schedule.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-automation-schedule.sh'

# automations, when the provisioning script changed or a row is missing
FABRO_API_URL=http://10.10.0.32:32276/api/v1 FABRO_DEV_TOKEN=<dev token> \
  ./ops/provision-server-state.sh
```

Then confirm the host still matches the repo. Every one of these should print
nothing:

```sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-branch-sweep.sh' | diff - ops/fabro-branch-sweep.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-sandbox-sweep.sh' | diff - ops/fabro-sandbox-sweep.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-monitor.sh' | diff - ops/fabro-monitor.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-auto-merge-switch.sh' | diff - ops/fabro-auto-merge-switch.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-fire-backlog.sh' | diff - ops/fabro-fire-backlog.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-automation-schedule.sh' | diff - ops/fabro-automation-schedule.sh
ssh andrew@10.10.0.32 'cat ~/fabro/docker-compose.yaml'  | diff - ops/docker-compose.yaml
ssh andrew@10.10.0.32 'cat ~/fabro/scheduler/repos.toml' | diff - ops/scheduler/repos.toml
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
```

A compose change needs `cd ~/fabro && docker compose up -d` to take effect — or
`docker compose up -d --build scheduler` for a scheduler code or `repos.toml` change,
which names one service and leaves `fabro` alone. The host
auto-merge switch is a server variable, so flipping it needs no deploy and no
restart; it takes effect on the next run:

```sh
# one repo
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 \
  ./ops/fabro-auto-merge-switch.sh andrewthetechie/<repo> off
# all four
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 \
  ./ops/fabro-auto-merge-switch.sh host off
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro variable get FABRO_AUTO_MERGE'
```

Neither undoes a merge that already happened.

**Upgrading the fabro binary is a separate, deliberate act** — never part of a
routine deploy. The image tag is pinned by `FABRO_VERSION` in `~/fabro/.env`
because an upgrade applies SQLite migrations that the previous binary then refuses
to start against, which makes rollback a database restore rather than a tag change.
`ops/README.md` has the runbook and the current known-bad version.

An upgrade on the 0.354–0.362 line also **drops fabro's run history**: 0.362's
run-history activation rejects every stored run from 0.354 (finding R1), so the
drop and the `codecs` catalog rewrite happen in the cutover. The host reached
0.362.0-nightly.0 on 2026-09-25 (`docs/fabro-upgrade/` holds the series). The next
upgrade must be rehearsed on a copy first — `docs/fabro-upgrade/02-rehearsal.md`
is the template.

## The server

`ssh andrew@10.10.0.32` — a trusted single-tenant box. The compose project is in
`~/fabro`, the API is on `:32276`, and the CLI exists only inside the container:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro events <run> -p'
```

`fabro events -p` shows which edge was selected and why, which is where a wrong-route
bug surfaces. Fire a `pr-review` run against a named PR:

```sh
~/bin/fabro-fire-pr-review.sh andrewthetechie/jelly-swipe 123
```

That helper registers the `pr-review` package, creates the run and **starts** it —
three calls, because `POST /runs` creates a `submitted` run that never executes and
never reports anything.

The obvious-looking curl does **not** work and must not be reconstructed from the API
surface: it was `POST /automations/pr-review-<repo>/runs` with a JSON body carrying
`inputs.pr_number`. That endpoint declares **no request body** — it fires the
automation's enabled API trigger and drops whatever is sent. The `inputs` never
arrive, so compilation then fails on `{{ inputs.pr_number }}` in `validate_input`
and the call returns `422 run_compile_invalid` having created nothing. That is exactly
what the node's deliberate "`pr_number` unbound" validation warning exists to catch.
Binding `[run.inputs] pr_number` would silence the warning and turn a no-input fire
into a review of PR #1. Send no body to that endpoint, or use the helper above.

**Since draft 10, firing `backlog` that way does not work either**, and the paragraph
above is exactly why. `backlog` used to take no inputs — `acquire` chose its own issue
— so the bodyless automation fire was correct for it. It now requires
`issue_number`, deliberately unbound in `workflow.toml` for the same reason
`pr_number` is, so the automation endpoint drops the input it cannot carry and the run
dies at compile with `422 run_compile_invalid`, having created nothing. Use the helper,
which does the three-POST sequence with the input attached:

```sh
~/bin/fabro-fire-backlog.sh andrewthetechie/jelly-swipe 123
```

The `backlog-<repo>` automations still exist and their rows are still where the
per-repo `environment_id` is looked up; what changed is that firing one by hand is no
longer a way to start work. **Their schedules are off** (draft 13, 2026-09-19), so
nothing starts them on a timer either: the coder scheduler is the only producer of a
`backlog` run, and `fabro-fire-backlog.sh` is the escape hatch for when it is down.
`ops/fabro-automation-schedule.sh <automation-id> on|off` is the switch, and it is the
only undo — turning one back on puts a second admission controller on the same two
coder boxes, which is what the scheduler exists to prevent.

`ops/README.md` has the environments and automations tables, which schedules are
enabled, host rebuild, the profile images, and the branch sweeper.

The operational history — every deployment, each E2E result and what it did and did not
prove, and the bugs found along the way — is `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`
on the Mac, mode 600, deliberately outside this public tree. Append a dated section
rather than editing earlier ones; they are a chronological record.

## The two checkouts

`~/.fabro-deploy/fabro-workflows` is the operator's deploy checkout. This one is for
working sessions. They are the same repository, so pull before you start and push when
you finish, or the two will diverge.
