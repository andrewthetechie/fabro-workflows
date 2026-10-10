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
| `docs/merge-rate/` | The implementation plan for ADR 0011: stop per-stage checkpoint pushes, rerun flaky CI once, `ci_fix_t1` on `glm-5.3-flash`, CI parity in two target repos, an `autofix` stage, and an 8-task budget whose remainder the scheduler queues with a new `priority` label once the parent PR merges. `00-overview-and-contracts.md` first. Tasks 05–07 run in other repositories and task 11 needs the host. **Applied 2026-09-24** in this repository; the target-repository halves of tasks 05 and 06 are PRs there. The budget was then amended so that extra-review follow-ups are exempt (ADR 0011 D6 amendment). |
| `docs/architecture-review/` | The task series for ADR 0012: `arch-review` scans a repository for deepening candidates twice a week, files the strongest as `architecture` issues, and triages the waiting issues toward `agent` through the shared `_shared/triage/` phase. `00-overview-and-contracts.md` first. An `architecture` PR is never auto-merged. **Applied 2026-09-25.** |
| `docs/merge-gate/` | The task series for ADR 0013: two gates in the shared merge phase between `validate` and `deliver`. `hygiene` counts test tampering (deleted tests, added skips, removed or tautological assertions) and code erosion over the PR's added lines, in shell; any tamper counter blocks the auto-merge. `refute` is an agent that sees only the issue, the diff and the counters and tries to prove the work is not done; `refute_gate` computes its verdict, and anything but `pass` blocks. `00-overview-and-contracts.md` first. |
| `docs/code-context/` | The task series for ADR 0014: a **Code index** built by pinned codegraph inside the sandbox at each workflow's entry node, queried by agents through `shell` with the `fabro-code` wrapper (exact names only; every answer is synced to the working tree). The **Repo map** and the **Task dossier** are derived from it. The index is never proof that a name is unused, so grep stays. The wrapper reads `.codegraph/codegraph.db` itself and never calls codegraph's query verbs, which walk guessed edges (ADR 0014 D1 defect 3). `00-overview-and-contracts.md` first. **Applied 2026-09-26** (tasks 01–07); the task 08 measurement is pending. |
| `docs/triage-split/` | The task series for ADR 0015: the shared triage phase gains a `plan` agent that drafts a **Task map** (a visible body section; `backlog`'s `decompose` reads it as a hint and always runs), and splits an issue whose map exceeds 4 tasks into at most 3 ordered **Child issues**. A child with a predecessor is created `agent-held`; the scheduler promotes it when that predecessor closes completed, and closes the `agent-split` parent when every child has landed. `00-overview-and-contracts.md` first. **Applied 2026-09-27.** The map path is verified live; the first live split, promotion and parent close, and the task 07 measurement, are pending. |
| `docs/coder-tweaks/` | The coder tool-use series: steer the local model (DeepSeek on the coder boxes) to the code-index tools without denying `grep` or `read_file`, and stop agents from running mutating git. Six parts: an agent guide in the system prompt (`.codex/instructions.md`), `code_search`, `restore_file`/`baseline_check`, a `git-guard` pre_tool_use hook, an `improve` backstop, and call sites in `task-code.md`. `00-overview-and-contracts.md` first, with the 2026-10-02 baseline. **Tasks 01–07 are implemented in this repository (2026-10-01)**: `ops/fabro-agent-tools.py` measures it (baseline in `baseline-2026-10-02.txt`), and the images need `make deploy-images` for `code_search`, `restore_file`, `baseline_check` and `git-guard` (fabro-io 0.3.2, `baseline_check` under pipefail, and `rg` in every image plus Node 24 in `fabro-ts` arrive with the 2026-10-03 `make deploy-images`). Task 08 was measured on 2026-10-03 over 16 runs (`result-2026-10-03.txt`) and rechecked on 2026-10-08 over 49 (`result-2026-10-08.txt`): git, `improve`, first-edit time and quality targets met, read bytes −38%, the guard verified live, and code-index share (M1, 7.6%) still short. `handoff.md` is the separate follow-up check of `24eb95c` (the `excerpts` node, the coder prompt rules, and the `code_*` tools). |
| `docs/test-env/` | The task series for ADR 0018 (proposed): each target repository declares its **Test environment** (services, preparation, variables) and **Test targets** in `.fabro/test.toml`. `ci.sh` reads it through `test_env=$(fabro-io test-env); eval "$test_env"`, and agents run `run_tests TARGET [paths]` (shell: `fabro-test`), prepared lazily and cached under an inputs fingerprint. `baseline_check` gains targets, and an edit to `.fabro/test.toml` or `.fabro/ci.sh` trips `config_changed`. `00-overview-and-contracts.md` first. **Designed 2026-10-09; nothing implemented.** Tasks 06–07 run in the target repositories, after the images (task 04). |
| `.fabro/workflows/_io/` | The **Stage manifest** and output schemas (ADR 0016): the only source of every `FABRO_IO_MANIFEST` block, generated into each `workflow.toml`. Never hand-edit the generated blocks; `check` fails on drift. |
| `docs/stage-io/` | The task series for ADR 0016: `fabro-io`, a static Rust binary in every profile image, runs as an MCP server in the sandbox and gives each agent stage two tools: `inputs`, which returns every input the **Stage manifest** declares (paged, no line numbers), and `submit`, which checks the output against its schema, stamps an **Input receipt** and writes the contract. Gates check the receipt, and an `io-guard` hook denies **Sealed paths**. `00-overview-and-contracts.md` first. **Applied 2026-09-27 through task 10**; the multi-day post-deploy production measurement is pending. |
| `docs/fabro-upgrade/` | The task series that moved the host from fabro 0.354.0-nightly.0 to **0.362.0-nightly.0**. It was rehearsed on a copy of the live database on 2026-09-25: the upgrade must drop fabro's run history (0.362's run-history activation rejects every run 0.354 stored) and rewrite the catalog to `codecs = [...]` (0.362 refuses `codec` and the protocol adapter ids at startup). `00-overview-and-contracts.md` first. **Applied 2026-09-25.** |
| `docs/factory-roadmap/` | The ranked improvement plan for the whole factory, one design record per item (A1–C4, H): andon cord, proposal gate, anti-oscillation, diff hygiene, cross-family refuter, scorecard, value-based queue, submit tool, shared code context, elastic fleet, scout, memory, upgrades. `00-overview.md` first. Records, not task series. An item becomes its own `docs/<name>/` series when picked up. |
| *(removed series)* | `d5cd750` (2026-09-24) deleted the finished task series: `docs/backlog/`, `docs/pr-review/`, `docs/pr-review-bridge/`, `docs/auto-merge/`, `docs/issue-triage/`, `docs/scheduler/`, `docs/run-history/`, `docs/direct-providers/`, `docs/perf/`, `docs/plan/` and `docs/turn-it-on/`. The live graphs, the ADRs and this file are the record now. Read an old file with `git show d5cd750^:<path>`, or list one series with `git ls-tree -r --name-only d5cd750^ docs/<series>/`. Two facts from them are still open: direct-providers task 02 is not applied, and nothing yet bounds a hung inference request since the LiteLLM ingress went away (ADR 0007). And `docs/perf/00-overview-and-measurements.md` holds the source-verified list of what fabro's docker provider cannot do (no mounts, no service provisioning). |
| `ops/check-routing-schemas.py` | Catches command-node routing-schema mismatches that `fabro validate` accepts and fabro only reports at runtime. Reads the parsed AST, not the DOT text. |
| `ops/check-agent-profiles.py` | The Agent-profile checker (ADR 0017): reads a settings overlay and resolves the effective pebble Agent profile of every enabled provider and model row the way fabro does, failing on anything that is not `openai`. Stdlib `tomllib` only; prints ids, codecs and profiles, never a credential. Runs offline on `ops/settings.toml.example` and in `make verify-host` against the live overlay. |
| `ops/test-task-gates.sh` | Runs `backlog`'s `claim`, `mark_stuck`, `open_pr`, `open_pr_prep` and task-queue nodes (`decompose_gate`, `improve_gate`, `next_task`) against fixtures, extracted verbatim from the graph. The only gate that executes a node's shell. Offline; no host or container. |
| `ops/fabro-io-manifest.py` | The Stage-manifest generator/checker (ADR 0016): reads `.fabro/workflows/_io/manifest.json` and the output schemas, and writes the generated manifest into each workflow's `workflow.toml` `[run.environment.env]` between C2's markers. `check` regenerates in memory and fails on drift or a contract break. Python 3.11+; runs on the Mac and the host, never in a sandbox. |
| `ops/fabro-io/` | The `fabro-io` Rust MCP binary (ADR 0016): the `stage`/`inputs`/`submit`/`guard`/`serve` tools and CLI, plus the six `code_*` MCP tools that run `fabro-code` in the checkout for every agent stage (`src/code.rs`). Built into every profile image by `ops/profile-images/build-images.sh`. |
| `Makefile`, `ops/deploy-host.sh` | `make deploy`: the host deploy after a push that touches `ops/` or `backlog/scripts/` (see *Deploying to the server*). The Makefile only names the script's steps. |
| `ops/fabro-run-status.sh` | LLM-free health check for an in-flight run: alive, where in the graph, making progress, and whether a compaction says the decomposition was oversized. |
| `ops/scheduler/` | The coder scheduler: an external service that owns admission to the two llama.cpp instances, because fabro's own queue is FIFO-on-creation with no priority and no per-pool concurrency (ADR 0005, ADR 0006). Draft 14's 24-hour shakedown window restarts with the container, so its acceptance criteria are pending, not unstarted. Do not trust a start time written here: every `docker compose up -d --build scheduler` re-dates the window. Read it with `docker inspect -f '{{.State.StartedAt}}' fabro-scheduler`. `ops/test-task-gates.sh` covers `claim` and `mark_stuck`, so the shell-level regression that killed the first bring-up (`5fa974d`) now fails offline. The four `backlog-<repo>` schedules are off, so the scheduler is the only producer of `backlog` runs. `ops/fabro-automation-schedule.sh` turns them back on, and `ops/fabro-fire-backlog.sh` is the manual escape hatch. Its LAN page is `http://10.10.0.32:32280/`. The operator quickstart (the page, the three controls, changing repo priority, the two monitor conditions) was `docs/scheduler/OPERATING.md`, removed in `d5cd750`: `git show d5cd750^:docs/scheduler/OPERATING.md`. |
| `docs/research_improvements/` | An audit of all three packages against the Fabro source: what we hand-roll that Fabro already does, four operator-observed gaps traced to Fabro lines, and nine settled dead ends. A plan, not a changelog. `00-overview.md` first: its status table (2026-09-24) records what shipped (the stall watchdog, the rescue-gate label, the `truncate` preamble), the one gap still open in that work (nothing deletes `rescue.md` between tasks), and the priority of the rest. The merge-rate review of the same date is ADR 0011. |
| `.scratch/` | Untracked working notes. |

## Writing, changing, or diagnosing a workflow

**Invoke the `/fabro-workflow` skill first.** It carries the DOT attribute tables,
node types, condition grammar, the transition cascade, and the CLI. Treat it as the
source of truth for how Fabro behaves; this file covers only what is specific to this
deployment.

Then read the ADRs that the graph's comments cite, and the live series for the part you
change (`docs/merge-rate/`, `docs/merge-gate/`, `docs/architecture-review/`,
`docs/code-context/`). The original build series are in git history (see *removed series*
in the layout table). Each workflow is built on
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

Baselines as of 2026-10-02 (ADR 0013 added four merge-phase nodes; ADR 0015 added `plan`, `plan_gate` and `apply_split`; the `rework_t1 -> rework_router` escalation edge added one backlog edge; `excerpts` added one backlog node and a net three edges; `drop_followup` added one backlog node and three edges, and `decompose_gate -> mark_stuck` one more; `rescue_brief` and `partial_remainder` added two backlog nodes and three edges), against **fabro 0.362.0-nightly.0** (review+merge shared and imported; ADR 0011 added
`autofix` and `file_remainder`):
`Backlog (69 nodes, 162 edges)` with exactly one warning — `issue_number` unbound in
`claim` (draft 10's deliberate fail-closed input, the same shape as `pr_number`) — and
`PrReview (34 nodes, 73 edges)` with exactly one warning — `pr_number` unbound in
`validate_input` — and `IssueTriage (16 nodes, 36 edges)` clean, and `ArchReview (24 nodes, 54 edges)` clean. Both include the 13 nodes of `_shared/triage/`. Backlog and PrReview both include the ~25
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

The Agent profile (ADR 0017) is checked the same way, against the tracked template — and
in `make verify-host`, against the live overlay:

```sh
python3.11 ops/check-agent-profiles.py ops/settings.toml.example
python3.11 -m unittest discover -s ops/tests
```

The Stage manifest (ADR 0016) is generated, never hand-edited. `fabro validate`
is blind to a manifest that has drifted from `_io/manifest.json`, so a pre-push
check regenerates it in memory and fails on any drift or contract break:

```sh
python3.11 ops/fabro-io-manifest.py check
```

It also fails when a stage id is not an agent node, when an `imports` entry names a
node that is not in the graph, when a live stage's prompt names one of its Sealed
paths, and when a stage that is not yet live names a `/tmp/fabro/` path outside its
inputs/output/`also`. Python 3.11+ (macOS ships 3.9, which the `tomllib` check below
also needs). The schema side is checked in Rust: `cargo test` in `ops/fabro-io/`
compiles every `_io/schemas/*.schema.json` under draft 2020-12. The manifest is JSON
inside a TOML literal, so it needs no escaping — `check` also fails if it ever
contains `'''`.

Neither gate runs the command nodes' shell. `backlog`'s task queue is several hundred
bytes of `jq` spread across `decompose_gate`, `improve_gate` and `next_task`, and a
cursor off-by-one there silently skips a task rather than failing — the same class of
bug, one layer down. `ops/test-task-gates.sh` extracts those `script` attributes from
the graph verbatim, rebases `/tmp/fabro` onto a scratch directory and runs them against
fixtures:

```sh
./ops/test-task-gates.sh      # 814 checks on the Mac, 902 in a profile image; offline
```

It needs only `jq`, `awk` and `git` — no `python3`, which `fabro-ts` and `fabro-python-node` do not ship — so it belongs in the same pre-push hook and also runs inside every profile image, the one way to test the counters against the sandbox's own mawk and jq (`jq 1.6` in `fabro-python-node`). It covers
the task-queue gates, `open_pr`, and — since 2026-09-19 — `claim`, `mark_stuck` and the
shared review-merge graph's `merge`. Since 2026-09-21 it also covers `open_pr_prep`'s
empty-diff floor, and that section is the one place here that uses the REAL `git`: it
builds an actual repo and clones it over `file://`, because what is under test is whether
`git diff --quiet origin/main HEAD` tells the truth about a branch carrying nothing but
checkpoint commits. A stub would only test the stub. It restores `$ORIG_PATH` first —
`next_task` prepends an `exit 0` `git` and never takes it off, so every later
`SAVED_PATH="$PATH"` captures that stub too. Since 2026-09-26 it also covers the merge phase's diff-hygiene
counters, each with a real-git fixture, and `refute_prep`, `refute_gate`, `merge_gate`
checks 13, 13b and 14, and the review comment's Refuter and hygiene sections (ADR 0013). It also runs
the code-index lines of every workflow's entry node (`backlog` `prep`, `pr-review` `claim`,
`arch-review` `prep`, `issue-triage` `acquire`), each with and without `fabro-code` on
`PATH`. Since 2026-10-01 it also covers `excerpts`, which copies the code the task dossier
cites into `task-code.md` for the coder: ranges, merging, the byte cap, and single lines
with and without `fabro-code`. Since 2026-10-02 it also covers `rework_router`'s
exemption lookup, `drop_followup` (real git), and the `needs_human_review` route from
`decompose_gate` to `mark_stuck` with the decomposer's reason, and the rescue gate's
`rescue_brief` summary and `partial_remainder`'s move of the unfinished tasks. Inside a profile image, where `codegraph` exists, it also runs the `fabro-code`
wrapper against a real-git fixture; on the Mac that section prints `SKIP`.
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
| A provider name fabro does not know built-in declares `display_name`; a known one may be extended by a bare model table | Fabro knows `zai`, `litellm` and `moonshot`, so `[llm.providers.zai.models."glm-5.3"]` alone is valid. It does not know `kimi`, `box-a`, `box-b` or `spark`, so each needs a full provider table **including `display_name`**. Omitting it fails the whole `[llm]` layer with `catalog layer "settings [llm]" is invalid TOML: missing field display_name` — and the failure is close to silent. The server rejects the reload and **keeps the previous catalog**, so it stays healthy and `GET /settings`, `fabro model list` and `fabro doctor` all keep answering from the good copy. Only a **worker** builds its catalog from the file, so every new run dies at its first node with `Worker exited before emitting a terminal run event: exit status: 1`, in about 0.2s. Runs already in flight finish normally, which hides it further. On 2026-09-20 that cost 14 hours and drained all four repos: the scheduler requeued each instant failure three times, then released the lease leaving the issue on `agent-in-progress`, until the queue was empty and the page read "no open work". After any overlay edit, check `docker logs --since 1m fabro-fabro-1 \| grep -cE "Rejected reloaded|Failed to reload"` prints `0`: fabro logs the first string when it rejects a parsed overlay and the second when the file will not parse at all (serve.rs:906), so the one-pattern grep misses a syntax error. |
| A provider table uses `codecs = [...]` (default `["openai-chat"]`), never `codec` or a protocol adapter id (`openai-compatible`, `openai`, `anthropic`, `gemini`) | A cold start refuses to boot (0.362's catalog loader rejects `codec`; finding R2), and a hot reload is rejected and the old catalog kept — the silent 2026-09-20 failure. The `Rejected reloaded` check is the same one in the `display_name` row above. |
| Every enabled provider resolves to the **openai** Agent profile: a provider whose first codec is not `openai-chat` declares `metadata.agent.profile = "openai"` (ADR 0017) | Pebble picks the Agent profile per session — the model row's `metadata.agent.profile`, then the provider's, then the profile the provider's **first codec** implies (`anthropic-messages` → anthropic, `gemini-generate` → gemini, an `adapter` of `bedrock` → anthropic, anything else → openai) — and **only the openai profile loads `<checkout>/.codex/instructions.md`**, the file that carries the Agent guide (docs/coder-tweaks C1). So a provider on another codec runs every one of its stages with no guide and a different tool vocabulary (`git-guard` matches `^shell$`; the `fabro-io` tools and the guide name the openai-profile tools), and **nothing reports it** — the run succeeds. It cost 163 of 1179 sessions in the 2026-10-08 recheck: every `kimi-k3` `spec` and `extra_decompose`, because `kimi` is the only overlay provider on `codecs = ["anthropic-messages"]`. Pin at the **provider**, never per model row, so a model added later is covered; a model row's own pin **overrides** the provider's, which is the one way this comes back. `ops/check-agent-profiles.py` catches it offline and in `make verify-host`. It reads the overlay only, and **no live surface reports a per-model profile** (`fabro model list --json`, `fabro doctor` and `GET /settings`, checked on 0.362.0-nightly.0 on 2026-10-08), so a row that merely extends a lithos **built-in** provider is invisible to it and prints a NOTE; verify those per run from `agent.memory.loaded` in `fabro events --json`. |
| Every model id a stylesheet names must resolve to the **intended** provider, not merely to a provider | Two providers may carry the same id, and a **built-in one wins** over an overlay-defined one. Storing `KIMI_API_KEY` for the new `kimi` provider also flips fabro's built-in `moonshot` to `configured`, so an unqualified `kimi-k3` resolved to `moonshot` and failed `Invalid Authentication` — a coding-plan key is not valid for moonshot's endpoint. It hit `backlog`'s `.review-frontier` (`spec`, `extra_decompose`). A stylesheet **cannot** qualify its way out: `kimi:kimi-k3` is `Unknown model`, because `provider:model` is only valid as a `[run.model.fallbacks]` target — which is why the fallbacks were fine and only the stylesheet broke. The fix is `[llm.providers.moonshot] enabled = false` in the overlay. `fabro model list` shows the collision; only `fabro model test -m <id>` shows which side won. An unconfigured provider (`venice` carries `kimi-k3` and `glm-5.3` with no key) never competes. |
| Do not store a key for `openrouter`, `fireworks`, `vercel` or `venice` without re-running `fabro model test` for every stylesheet id | Those new built-ins also carry `glm-5.3`/`kimi-k3` (finding R5), and a configured built-in wins over an overlay provider — the `moonshot` lesson again, see the row above. |
| `[run.git.author]` is set in the server overlay (`andrews-ai-agent`) | Without it fabro derives the identity from the GitHub token user and fails the run at setup if the lookup fails (0.362 behaviour change 4), and factory commits become indistinguishable from the operator's. |
| A Child issue with a predecessor is created `agent-held`, never `agent`, and only the scheduler promotes it | The same-repo saturation race of ADR 0011 D6, one level down: child 2, promoted by a `backlog` run straight from a `main` that lacks child 1, would implement against the wrong tree. `apply_split` creates the first child `agent` and every later child `agent-held`, and no child inherits a queue-state label (`agent`, `agent-in-progress`, `agent-stuck`, `agent-held`, `agent-split`, `agent-remainder`, `priority`) from its parent; `agent-held` is invisible to the queue until the scheduler's promoter adds `agent` and `priority` when the predecessor lands. Covered by `ops/test-task-gates.sh`. |
| `apply_split` finds existing children through the sub-issues API, never through issue search | GitHub search is eventually consistent, so a retried split that searched for "already titled" children would miss a just-created child and duplicate it. The sub-issues API is strongly consistent; `apply_split` reuses a child whose title already appears there and links new ones with `POST /issues/{P}/sub_issues`. Its entries are **plain issue objects** (`.number`, `.title`, `.state_reason` at the top level), with no wrapper key: the first build read `.sub_issue.*` in both `apply_split` and the scheduler, and its tests mocked the same wrong shape, so the dedupe never matched and the parent closer always saw an empty list. The read fails closed — an unreadable list read as empty would create every child again — and a failed link POST counts only when a re-read shows the child already linked, because an unlinked child is invisible to both the dedupe and the closer. Covered by `ops/test-task-gates.sh` and `ops/scheduler/tests/test_split.py`. |
| A Split parent is never labelled `agent` | `backlog` would implement the whole issue, and each child separately. `apply_split` labels the parent `agent-split` and never adds `agent`, so the queue's `agent`-labelled fetch never sees it; the scheduler closes it when every child lands. |
| A class used by a node in `_shared/review-merge/` or `_shared/triage/` has an explicit rule in **both** importing stylesheets | The phase is spliced into `backlog` and `pr-review`, whose `*` rules differ — `coders-a` and `glm-5.3`. A class with no rule silently inherits `*`, so one forgotten in `backlog` runs a merge-phase reviewer on a **local box** instead of failing: the auto-merge gate is quietly downgraded and nothing reports it. `fabro validate` checks stylesheet syntax, never coverage. Six classes carry this today (`merge-standards`, `merge-spec`, `merge-fix`, `ci-fix`, `rebase-t2`, and `rebase`, which only `pr-review`'s own rebase agent still uses); the triage phase's `.plan` has a rule in both `issue-triage` and `arch-review`. |
| A `#` comment inside a `script=` attribute is paid **twice** in the next agent's prompt | Fabro writes a command node's whole script into `outcome.notes` (`handler/command.rs:198`) and its preamble prints the same script again as `- Script:`, at every `summary:*` fidelity. Measured 2026-09-19 on run `01M2X2MVPC2NXY1BW5CVKPFF3S`: 13.7 KB of 89 KB of agent prompt was that one duplication, and `claim` was 54% comments. Put the prose in `//` DOT comments above the node, where it costs nothing. `truncate` removes the cost today; the rule keeps it removed if fidelity is ever raised. |
| Every graph runs at `default_fidelity="truncate"` | Anything higher prepends a stage recap to every agent prompt — `compact`, the fabro default, prepends *every* completed stage with up to 25 lines of output each. At `summary:low` the preamble was still 23% of every prompt. Nothing here needs it: every prompt names its inputs by path, which is the point of the file-backed contracts. The one exception was `human.gate.text`, which `record_guidance` writes to a file. |
| `\"` is the only backslash in a `.fabro` file | The DOT parser turns `\n` into a real newline, so `printf '%s\n'` becomes `printf '%sn'` and jq's `"\n"` becomes an unterminated string. Get a newline inside an embedded jq program with `([10]\|implode)`. |
| `//` starts a comment; `#` only ever appears inside a quoted string | `#` is a DOT comment. `Resolves #N` and `##` headings inside a string are fine — never strip one to make a grep pass. |
| Inline scripts are POSIX `sh` | No `[[ ]]`, no `pipefail`, no arrays. `sh -n` catches this, but it is blind inside `jq '...'` — compile embedded jq programs separately. |
| Every command node's unconditional edge lands on the workflow's terminal-failure node | A failed node still routes, and it takes its *unconditional* edge. Where that edge is the happy path, fold `\|\| outcome=failed` into the escape edge's condition. |
| Reset per-iteration context keys, and write every key on both branches | A stale key from an earlier loop can satisfy an edge meant for this one. |
| Delete a contract file before the agent that writes it runs | An agent that exits succeeded without writing hands the gate its predecessor's result. |
| Agents do not run `git` | Two deliberate exceptions: `pr-review`'s rebase agent and `backlog`'s `resolve_merge` agent, which need `git add` and `--continue`. Enforced by the `git-guard` `pre_tool_use` hook (docs/coder-tweaks C2): read-only git (`status`, `diff`, `log`, ...) passes, everything else is blocked from `shell` with a reason naming `restore_file` or `baseline_check`. Exempt stages carry `"git": "write"` in `_io/manifest.json` (the generator copies it into each `workflow.toml`'s manifest). `sh -c` and `python` bypass it; it catches mistakes. |
| Every edge into `human_rescue` passes through `rescue_brief`, and `[P] Accept partial` passes through `partial_remainder` | The gate shows only its fixed label and `response.<last_stage>`, which fabro sets for agent stages alone. `rescue_brief` writes that key and `last_stage` itself, from `failure_signature` and the run's files, so the gate says why the run stopped and what `[P]`, `[X]` and guidance do in that run. Before it, run `01M3Y86Z013N2V1QDNNG591MBQ` showed "Verdict submitted." for a task no agent could finish (a `.github/workflows/` edit). An edge straight to `human_rescue` brings that back silently. `[P]` without `partial_remainder` opens a PR that says `Resolves #N` and records the unfinished task and every unstarted one nowhere; with it they go to `remainder.json` and `file_remainder` files them as one held issue. `rescue_brief` prints only its one-line routing object: fabro scans stdout and stderr together for brace-balanced objects, and echoed finding text could merge with it into an invalid candidate. Its `[P]` and `[X]` texts mirror `open_pr_prep`'s refusals and `mark_stuck`; change them together. Covered by `ops/test-task-gates.sh`. |
| A blocking hook's script exits 0 when its own program is missing or crashes | fabro treats **any** hook exit but 0 as a block (`parse_decision`: 2 blocks, and every other code blocks too; an unrunnable sandbox hook reports -1). A guard whose binary is missing from an old image, or that panics, would fail every call it matches. `git-guard`'s script is `fabro-io git-guard; [ $? -eq 2 ] && exit 2; exit 0`, and `ops/test-task-gates.sh` extracts it from each `workflow.toml` and runs it with `fabro-io` absent. |
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
| A root graph that runs an imported phase in a loop sets `loop_restart_signature_limit` above the number of times one gate can fail in a run | Fabro's failure-signature breaker counts **run-wide** and never resets on success (`lifecycle/circuit_breaker.rs:93-103`), and at the default of 3 it ends the run with an engine error: no edge is taken, so no failure sink runs. Every `*_gate` prints one fixed line, and digits are masked, so a gate's signature is identical for every issue. `arch-review` runs the triage phase up to 18 times, and three issues that each needed one repair turn would stop it mid-queue with no summary and the current issue still carrying `triage-in-progress`. It carries `loop_restart_signature_limit=20`. `backlog`'s own exposure is `docs/research_improvements/01-tier-1-single-attribute-fixes.md` item 1, still open. `fabro validate` checks neither number. |
| Moving a stylesheet class to a different model voids every timeout derived from the old one | Node timeouts here are measured, not guessed, so the measurement's subject is part of the number. `9b7f5ff` moved `.improve` from `glm-5.3` to `{{ inputs.coder_pool }}` — a single-slot llama.cpp box — and left `timeout="15m"`, which the comment above the node justified with "improve max 180s" taken entirely on glm-5.3. Measured output rate is 22.1 tok/s hosted against 11.8 tok/s on a box, and worse than that ratio for a node that re-reads the checkout every turn: run `01M321VB0DC4N187GHA88RM0QB` burned both attempts at exactly 900004ms and 900005ms, still generating, and reached `human_rescue` having never run a coder. `fabro validate` cannot see this — the graph is valid and the class resolves. Re-time the node in the same commit that moves the class, or say in the comment that the number is now unmeasured. |
| No stylesheet class may name `glm-4.7`, and nothing may fall back to it | It is the only model in the server's catalog whose record carries `features.reasoning: false` (`fabro model list --json`); every other model these graphs name is `true`. The z.ai endpoint **returns reasoning blocks from it anyway**, and on the next turn fabro replays that assistant message against its own capability record and refuses locally — 3ms, no request leaves the host: `LLM error: model zai/glm-4.7 does not support reasoning`. It classifies `api_deterministic|zai|invalid_request`, and that category both bypasses the retry budget (`max_retries=1` never engages) and skips `[run.model.fallbacks]`, which only covers errors and rate limits — so the stage dies outright and takes its unconditional edge. It is nondeterministic by nature, firing only when the model happens to emit reasoning, so `.coder-t2` carried it for weeks before run `01M33SDMZF55NAEV8JV29A8EA4` lost `rework_t2` to it on 2026-09-22 with six of seven tasks already landed. `fabro validate` cannot see it: the graph is valid and the class resolves. `.coder-t2` and both `coders-*` fallbacks now name `glm-5.3-flash` — reasoning-capable, same z.ai coding plan, 262144 window against glm-4.7's 202752. Check a replacement with `fabro model list --json` before naming it. |
| `risk` is gate-enforced in `fix_gate`, not merely documented | Without the check the field is optional in practice, PRs quietly stop auto-merging, and the report still says "complete". |
| The merge node re-reads PR `state` from GitHub immediately before merging | The bridge's double-fire marker is per-run and does not stop a second review fired by hand. Two runs reach `merge`; the loser must report "already handled", not fail. |
| The `merge` node waits for `mergeable` to leave `UNKNOWN` and retries a branch-moved rejection | Any push to the head just before `merge` causes this. Until ADR 0011 D1 that was fabro's own checkpoint push (commit + push after every stage); since D1 neither graph pushes checkpoints, so it is now a push by `ci_fix_gate` or `remerge_base`, or by someone outside the run. The push invalidates GitHub's computed merge ref, and a merge attempted before it is recomputed is rejected as `Base branch was modified` even though the base has not moved. A pure race: `jelly-swipe#386` lost it, with the push ~15ms before `merge` started, with `main` unmoved for 35h and no protection rules, while run `01M2TFEHPYXXTQST6VPXREA854` won it with the same 10ms gap. Without the wait and the retry a mergeable PR is reported to a human as a permissions or conflict problem. |
| Any merge-phase node that fails into `mark_needs_human` writes **both** `merge_block_reason` and `needs_human_reason` | The two renderers read different files: `blocked` reads the first, `needs-human` the second, and `mark_needs_human` renders `needs-human`. A reason written to only one of them leaves the other holding whatever a previous stage left there — on #386 that was a placeholder `validate` writes on its SUCCESS path, so the comment said the run stopped before the review was delivered while the same comment listed all three verdicts. A placeholder describing a stopping point the run went on to pass is a false statement waiting to be printed. |
| `backlog` does not push checkpoints (`[run.run_branch] push = false`); every push after `open_pr` is an explicit node push, and `open_pr` widens the fetch refspec for the run branch | Fabro's checkpoint commit is always `--allow-empty`, so a per-stage push started a full CI run for every stage once the PR existed (womens-fantasy-sports#1247: 80 commits, 78 Actions runs), and the push after `watch_checks` moved the head CI had passed, so `merge` timed out waiting. Without the refspec, the single-branch clone has no `refs/remotes/origin/<run branch>` and `deliver`'s bare `--force-with-lease` is rejected as `stale info` — it only ever worked because the checkpoint push had already made it a no-op. Covered by `ops/test-task-gates.sh` (real git). ADR 0011 D1. |
| `gh pr merge` never passes `--delete-branch` (the merge node passes `--delete-branch=false`) | It deletes the **local** head branch as well as the remote one, and the merge is not the run's last stage: fabro's checkpoint publishes the run branch after every later stage, so the next publish fails with `src refspec … does not match any`, retries three times, and reports `failed/publish_failed`. A run whose PR merged and whose issue closed is then recorded as a failure — cost run `01M2TFEHPYXXTQST6VPXREA854` its status on 2026-09-18, eight seconds after PR #1206 merged. Reaping is `fabro-branch-sweep.sh`'s job. |
| No `gh` call is left to infer the branch: `gh pr create` passes `--head "$B"`, and `gh pr view` names the branch or a PR number | The sandbox clone is shallow and **single-branch** (`remote.origin.fetch` is `+refs/heads/main:refs/remotes/origin/main`), so `refs/remotes/origin/fabro/run/<id>` can never be stored. `git push -u` still prints `set up to track` and writes the branch config, but `@{u}` fails — and gh resolves the head remote through exactly that ref, so a bare `gh pr create` aborts with `you must first push the current branch to a remote` while the branch sits on the remote at `HEAD`. Cost run `01M2VHAGXNM9JNEY71085HV82Y` its PR on 2026-09-19 after 41 minutes of finished work. `open_pr` is the only node that ever inferred it; `pr-review`'s `claim` meets the same clone property and widens the refspec instead, because `gh pr checkout` needs a real local branch. Finding 11 in `docs/auto-merge/00-overview-and-contracts.md` (removed in `d5cd750`; `git show d5cd750^:docs/auto-merge/00-overview-and-contracts.md`). |
| Every merge-phase command node's unconditional edge lands on `mark_needs_human`; expected blocks are the *conditional* edges | A broken merge — a token without `contents: write`, an API 5xx — otherwise lands in the same quiet "not auto-merged" bucket as a risk-4 PR and stays invisible. |
| Commit-body markers are `:start` / `:end`, never `<!-- /fabro:commit-body -->` | A closing marker with a slash forces `\/` into the extraction pattern, and `\"` is the only backslash a `.fabro` file may contain. |
| The subject is the live PR title; the body comes from the marker block | `release-please` turns an agent's `feat:`/`fix:` subject into a release on `jelly-swipe`/`lawncare-saas`, and the derivation never emits `!` or `BREAKING CHANGE:`. |
| A `settings.toml` value that is read at startup is verified by the container's start time, never by `GET /settings` | fabro polls the overlay every 5s and republishes the *reported* settings, but `max_concurrent_runs` is copied out once at startup and read only by the admission loop — so after an in-place edit `/settings` answers the new value while the server keeps enforcing the old one, and the endpoint you would check with is the one endpoint that cannot tell you. Check that `docker inspect -f '{{.State.StartedAt}}' fabro-fabro-1` is later than the overlay's mtime; `ops/README.md` carries the command. |
| A `backlog` run implements at most 8 **decomposed** tasks (`tasks_coded`, counted in `improve_gate` on `ready`), and never labels its remainder issue `agent` | Extra-review follow-ups, and slices split from one (`from_extra: true`), are exempt: they never count, are never moved, and are worked in this run, bounded by `extra_prep`'s two-round cap. `next_task` moves every decomposed task past the budget, `decompose` tasks and their split slices, to `remainder.json`, and `file_remainder` files them as one issue labelled `agent-remainder`. The scheduler adds `agent` + `priority` only once the parent PR merges (ADR 0011 D6, `ops/scheduler/src/fabro_scheduler/remainder.py`). Labelling it `agent` at filing time would let the scheduler's same-repo saturation pass start it on the other box, from a `main` without its parent's work. Covered by `ops/test-task-gates.sh`. |
| An `architecture`-labelled PR is never auto-merged: `open_pr` copies the label from the issue, `merge_gate` check 2b blocks on it, and `file_remainder` copies it to the Remainder issue | An architecture review (ADR 0012) proposes a refactor, triage promotes it, `backlog` implements it and the merge phase would squash it into `main` with no human anywhere in the chain. On `lawncare-saas` that merge is also a deploy. Check 2b fails closed on a `gh` error. Covered by `ops/test-task-gates.sh`. |
| The diff-hygiene counters read `.fabro/hygiene.json` from `origin/<base>`, never from the PR head, and `config_changed` counts a PR that touches it | A PR that could edit the rules that judge it would loosen them: raise `erosion_threshold`, drop a test path, and pass its own check. Nothing overrides a hygiene block (ADR 0013 D2): the override is a human merge. The counters' patterns are bracket expressions (`[.]`, `[(]`) because `\"` is the only backslash a `.fabro` file may hold, and they run under **mawk**. The fixtures in `ops/test-task-gates.sh` are their contract. `merge_gate` check 13b refuses a HEAD whose **tree** differs from the one the counters read (the SHA always differs: every checkpoint commit is `--allow-empty`), because `refute` is an agent with write access between `hygiene` and `deliver`. |
| `ci_fix_gate` runs `/tmp/fabro/hygiene.sh` again before it pushes, and `hygiene` is the only node that writes that file | `ci_fix` changes code after `merge_gate` has approved the diff, and a CI fixer is the stage most likely to skip a failing test. A second copy of the counter program in `ci_fix_gate` would drift from the first (ADR 0013 D3). |
| `refute` sees no other agent's output, `refute_gate` computes its verdict, and a missing verdict blocks | The Refuter's value is independence: a prompt that hands it `standards.json`, `spec.json` or `fix_result.json` turns it into a fourth reviewer that agrees with the other three. An agent-written `verdict` key is ignored. The verdict is `pass` only when every criterion is `met` and there is no defect. A timed-out Refuter goes straight to `deliver`, and check 14 blocks on the absent `refute_verdict` rather than letting a provider outage through. `.refute` needs a rule in **both** stylesheets. Since `9c76544` it runs on `gpt-6.1-sol` (openai), a family unlike the coder's and the reviewers' (ADR 0013 D6), with `openai:gpt-6-luna` as the fallback in both `workflow.toml`s; that fallback must never be GLM. |
| The `FABRO_IO_MANIFEST` blocks in each `workflow.toml` are **generated** | A hand edit is overwritten by `ops/fabro-io-manifest.py generate`, and `check` fails on drift. Edit `.fabro/workflows/_io/manifest.json`, never the block. |
| `io-stage` is blocking and it runs in the sandbox for every agent stage | If the shipped `fabro-io` binary is older than the manifest's `min_binary`, every agent stage in every run stops. Rebuild the profile images before you raise `min_binary`, and keep the image and `min_binary` in step. |
| A migrated contract is written by `submit` and carries an Input receipt | The gate rejects a fixed-format contract written by hand or without a receipt. A gate that accepts a contract without one has re-opened the gap this series closed, and the fix is the C7 receipt check, not a longer prompt. |
| The `FABRO_AGENT_GUIDE` block in each `workflow.toml` is **generated** from `_io/agent-guide.md`, and an entry node never overwrites a tracked `.codex/instructions.md` | Pebble loads `.codex/instructions.md` into the system prompt next to `AGENTS.md` (32,768 bytes across all files, then cut), which is the only place we control that sits above its `rg` advice. `ops/fabro-io-manifest.py generate` writes the block between its own markers; `check` fails on drift, a guide over 3,000 bytes, or `'''` in it. Each entry node (backlog `prep`, pr-review `claim`, arch-review `prep`, issue-triage `acquire`) writes the file and adds it to `.git/info/exclude`, so a checkpoint never commits it, and skips it with one stderr line when the variable is empty or the repository tracks the file. The install never fails the node and prints nothing to stdout (`claim` routes on stdout). Covered by `ops/test-task-gates.sh`. |
| A stage's Sealed paths live in the manifest, not in its prompt | The `io-guard` hook denies them. It is a guardrail, not a security boundary: the sandbox runs in a container with repository write access, and an agent can read history or logs. |
| `io-guard`, and any hook for a stage in an imported phase, goes into every graph that imports the phase | Like the review-merge report hooks, a hook in one `workflow.toml` does nothing for a run of a graph that imports the phase elsewhere. `io-guard` is wired into the two graphs that run `refute`. |
| No two agent stages run in parallel while `stage.json` is the stage identity | The `.io/stage.json` staging area is shared state; parallel runs of two stages would fight over it. ADR 0016 D3. |
| The MCP server's absence is silent in fabro | Fabro logs just one event when an agent tool times out; nothing surfaces that `inputs` did not answer. The receipt check is the only thing that notices, which is why every migrated gate carries it. |

## Deploying to the server after a merge to `main`

`make deploy` does all of it: every step below except the fabro `compose up`, then
the host-matches-repo diffs. It refuses a checkout that is dirty or not at
`origin/main` (`ALLOW_DIRTY=1` overrides). It rebuilds the scheduler and the profile
images only when their inputs changed since the last successful build. That is judged
by an rsync itemize plus a git-tree-hash stamp in `~/.fabro-deploy-stamps/` on the
host, because every scheduler rebuild re-dates the draft-14 window (`FORCE=1`
rebuilds anyway, `SKIP_IMAGES=1` skips the images). Single steps are
`make deploy-scheduler`, `deploy-images`, `deploy-scripts`, `deploy-notify`,
`deploy-compose`, `provision` and `verify-host`. `make compose-up CONFIRM=1` is the
only target that restarts fabro. The logic lives in `ops/deploy-host.sh`. The
commands below are what it runs, and the escape hatch when it cannot.

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
# A failed build leaves the previous image tagged and in use. Since ADR 0016 the
# build also runs `cargo test` and the musl `cargo build` for the fabro-io crate,
# which must sit beside the build tree at ~/fabro-io (build-images.sh resolves it as
# $HERE/../fabro-io). Its `cargo test` also compiles every Stage-manifest output
# schema, which must sit beside it at ~/fabro-io-schemas; the build fails without
# them rather than skip the test. All three trees are synced (profile-images first,
# then the crate, because build-images.sh reads it to name the expected fabro-io
# version, then the schemas). `--exclude target` keeps the Mac's build dir behind.
rsync -a --delete ops/profile-images/ andrew@10.10.0.32:~/profile-images-build/
rsync -a --delete --exclude target ops/fabro-io/ andrew@10.10.0.32:~/fabro-io/
rsync -a --delete .fabro/workflows/_io/schemas/ andrew@10.10.0.32:~/fabro-io-schemas/
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
