# Fabro Backlog Workflow Redesign — Overview & Canonical Contracts

**Read this first.** Every task in this folder assumes the architecture and file
contracts defined here. Tasks are self-contained where it matters, but this document
is the single source of truth for the design.

## Why this exists

A review of the fabro deployment on 10.10.0.32 (after ~12h of prior implementation)
found four P0 bugs — three in the `backlog` workflow in
`andrewthetechie/fabro-workflows`, and one in fabro itself:

1. **Review routing can never fire.** Edge conditions reference flat
   `context.review_approved`, but with a custom `output_schema`, fabro stores the
   validated object only under `output.<node>` and never applies `context_updates`
   (see `lib/components/fabro-workflow/src/handler/agent.rs` — the routing-JSON scan
   is in the `else` branch of `if let Some(schema)`), and fabro conditions cannot
   traverse nested objects (`lookup_flat` is a flat map lookup). Every review falls
   through to `human_rescue`.
2. **Decompose fails deterministically.** Its `output_schema` has `"type": "array"`
   at the root, but fabro's structured-output parser (`find_json_objects`) only
   extracts `{...}` objects — an array can never validate. Every run burns ~500s
   and ~360k tokens on a guaranteed failure.
3. **Plan stage output is invisible to the coder.** Non-schema agent output goes to
   `response.<node>` context keys, which are hidden from later agents' preambles.
   The coder re-plans from scratch (~50 min of wasted exploration per run).
4. **Fabro leaks two git branches into the target repository on every run.**
   `fabro/run/<run_id>` is pushed after every checkpoint and `fabro/meta/<run_id>` is
   pushed by the server process, and there is **no deletion path anywhere in the fabro
   codebase** — no `git push --delete`, no retention, nothing `fabro system prune`
   reaps. Most runs quiet-exit at `acquire`, leaving a run branch that is one empty
   commit with a zero-byte diff against `main`. Measured 2026-09-13: **~300 leaked
   branches** across the four target repositories, growing at ~768/day. Found by the
   operator, root-caused in this review; **mitigated in task 12, not fixed upstream.**

The operator has directed a redesign that replicates the Sandcastle loop
(`run-backlog-v3.mts` in `/Users/andrew/Documents/code/Sandcastle-loop/`) behavior
in fabro primitives instead of patching these individually.

## The core architectural rule: file-backed contracts + jq gates

**No *custom* `output_schema` anywhere. No structured chat output relied upon anywhere.** Command gates carry exactly one reserved value — `output_schema="routing"` — which is what switches the stdout routing scan on.

Every agent stage writes its deliverable to a file under `/tmp/fabro/` in the
sandbox. Every agent stage (except coder/rework) is followed by a tiny **command
gate** that validates the file with `jq` and emits routing state by printing a
single JSON object with `context_updates` to stdout:

```json
{"context_updates": {"some_key": "some_value"}}
```

This is the one mechanism everything routes on, and it has **one prerequisite that
is easy to miss**: the node must also declare

```
output_schema="routing"
```

`"routing"` is a reserved keyword (`handler/structured_output.rs` `ROUTING_KEYWORD`),
not a JSON schema. Without it fabro never scans the command's stdout, the
`context_updates` are silently dropped, and every gate's routing is inert — the run
walks its unconditional edges with no error reported anywhere.

This was learned the expensive way. An earlier draft of this document claimed the
mechanism worked unconditionally and cited `handler/command.rs` test ~795 as proof.
That test is `command_routing_output_schema_applies_routing_fields`, and its **setup
sets `output_schema="routing"`** before asserting the updates apply — the assertion was
read, the setup was not. Task 09's deployment caught it; all 12 emitting nodes now
carry the attribute.

Conditions then read the flat context keys the scan merged in.

Agents:
- **Never** call GitHub (`gh`, API). Deterministic command stages do all GitHub work.
- Agent nodes carry **no** `output_schema` at all — a custom schema there is the
  original P0 bug.
- **Never** run `git commit`/`git push`. Fabro's checkpoint layer commits after each stage.
- **Never** run the full test suite (coder/rework). The `validate` stage runs `.fabro/ci.sh`.
- Read inputs from files under `/tmp/fabro/`, write outputs to files under `/tmp/fabro/`.

## Model map (operator-approved 2026-09-13)

| Stage class | Model | Notes |
|---|---|---|
| `.decomp` | `glm-5.3` | decomposition is the highest-judgment stage in the run — matches the model Sandcastle uses for it (changed 2026-09-14 from `long-context`) |
| `.improve` | `glm-5.3` | z.ai coding plan |
| `.coder` (tier 1: `coder`, `rework_t1`) | `coders` | local deepseek, round 1–2 |
| `.coder-t2` (`rework_t2`) | `glm-4.7` | rework rounds 3–4 |
| `.coder-t3` (`rework_t3`) | `kimi-for-coding` | rework round 5 |
| `.coder-t4` (`rework_t4`) | `glm-5.3` | rework round 6 (last stop before rescue) |
| `.review` (`review`, `standards`, `quality`) | `glm-5.3` | fresh eyes ≠ coder-of-record |
| `.review-frontier` (`spec`, `extra_decompose`) | `kimi-k3` | frontier review/planning only, **never coding** |

Rules from the operator: z.ai models before kimi (more zai quota); `kimi-k3` is never
a coder — review and frontier-intelligence tasks only.

`long-context` is no longer used by any stage. Its `[run.model.fallbacks]` entry is
left in `workflow.toml` deliberately: it is inert while nothing selects the model, and
keeps the option available without another edit.

Escalation ladder (Sandcastle: 6 max rounds, tiers at rounds 3 and 5; ours has 4
tiers over 6 rounds):

| Router tick (= rework round − 1) | Round | Model |
|---|---|---|
| 1 | 2 | coders (tier 1) |
| 2–3 | 3–4 | glm-4.7 (tier 2) |
| 4 | 5 | kimi-for-coding (tier 3) |
| 5 | 6 | glm-5.3 (tier 4) |
| ≥6 | — | human_rescue |

`glm-4.7` and `kimi-for-coding` do not exist in the deployment yet — task 01 adds them.

## Canonical state files (all in the sandbox at `/tmp/fabro/`)

| File | Written by | Contract |
|---|---|---|
| `candidates.json` | `acquire` | raw `gh issue list` output |
| `acquire.json` | `acquire` | filtered: 1-elem array `[{number,title,labels}]` |
| `issue.json` | `claim` | full issue: `gh issue view N --json number,title,body,labels,comments` |
| `decomposition.json` | `decompose` (agent) | `{status: "issues"\|"no_work"\|"needs_human_review", summary, issues: [{id,title,body,files[],priority}]}` |
| `tasks.json` | `decompose_gate`, `extra_gate` | `[{id,title,body,files[],priority,source}]` — `source`: `"decompose"` or `"extra-review"` |
| `task_index` | `decompose_gate` (init `0`), `next_task` (incr) | integer |
| `current_task.json` | `next_task`, `improve_gate` | single task object |
| `task_base_sha` | `next_task`, `resolve_merge_gate` | git SHA stamped at task start (after a clean mainline merge; `resolve_merge_gate` re-stamps it once a conflicted merge has been resolved) |
| `pre_merge_sha` | `next_task` (writes), `resolve_merge_gate` (reads) | the run-branch commit before the mainline merge attempt; the `git reset --hard` target if a resolution fails |
| `merge_attempts` | `next_task` (reset `0`), `resolve_merge_gate` (incr) | integer, cap 2: one agent retry after the first failed resolution |
| `round` | `next_task` (reset `0`), `rework_router` (incr) | integer |
| `extra_round` | `prep` (init `0`), `extra_prep` (incr) | integer, cap 2 |
| `<gate>_attempts` | each gate | per-task retry counter (max 2), reset on success |
| `improve_result.json` | `improve` (agent) | `{disposition: "ready"\|"redundant"\|"needs_human", reason, task: {...} (required when ready)}` |
| `review/verdict.json` | `review` (agent) | `{decision: "approved"\|"changes_requested"\|"needs_human_review", summary, findings: [{severity,message}]}` |
| `review/diff.patch`, `review/diffstat.txt`, `review/changed_files.txt` | `prep_review` | diff of `task_base_sha..HEAD` |
| `feedback/rework.md` | `validate`, `review_gate`, `next_task`, `prep_review` | markdown feedback for the next rework attempt |
| `validate_output.log` | `validate` | full setup+ci log |
| `extra/diff.patch`, `extra/diffstat.txt`, `extra/changed_files.txt` | `extra_prep` | whole-branch diff `origin/main...HEAD` |
| `extra/standards.json`, `extra/spec.json`, `extra/quality.json` | extra reviewers | `{decision: "approve"\|"findings"\|"needs_human_review", summary, findings: [{message,files[],suggestion}]}` |
| `extra/followups.json` | `extra_decompose` (agent) | `{status: "issues"\|"no_work"\|"needs_human_review", summary, issues: [{id,title,body,files[],priority}]}` |
| `completed.md` | `integrate` (appends) | per-task completion notes for the PR body |
| `pr_title.txt`, `pr_body.md` | `open_pr_prep` | deterministic PR content |

## Run shape (graph summary — full graph in task 02)

```
acquire → claim → prep → decompose ⇄ decompose_gate
  gate →(issues)→ next_task   →(no_work)→ close_noop → exit
                         ↓ (tasks remain; pops task, refreshes mainline, stamps base sha)
              mainline conflict → resolve_merge → resolve_merge_gate
                 gate →(merge_ok)→ improve          ↓ (failed twice)
                 gate →(merge_attempts=1)→ resolve_merge      → human_rescue
                      improve ⇄ improve_gate →(ready)→ coder
                         ↓ redundant → next_task
                      coder → prep_review → validate → review ⇄ review_gate
                         ↑           ↓ failed            ↓ approved
                         |     rework_router ←(changes_requested)
                         |       ↓ tick → rework_t1..t4 (by round) ─┘
                         └───────┘   (≥6 → human_rescue)
                      integrate → next_task
  next_task →(all done)→ extra_prep → standards ⇄ gate → spec ⇄ gate → quality ⇄ gate
    → extra_decompose ⇄ extra_gate →(new follow-ups appended to tasks.json)→ next_task
                                   →(none)→ open_pr_prep → open_pr → exit
  extra review runs at most 2 rounds (extra_round counter).
  human_rescue hexagon: [R]→rework_t4 (guidance freeform)  [P]→open_pr_prep  [X]→mark_stuck → exit
```

## Sandcastle behavior → fabro mapping (for prompt tasks)

| Sandcastle | Fabro |
|---|---|
| host fetches issue, writes input files | `acquire`/`claim` command stages write `/tmp/fabro/*.json` |
| structured-result MCP + validation retry | agent writes JSON file + `jq` gate with per-task attempt counter (max 2) |
| coder round 1 / rework rounds with feedback files | `coder` / `rework_t1..t4` reading `/tmp/fabro/feedback/rework.md` |
| escalation ladder (rounds 3, 5) | `rework_router` counter + tier nodes + round conditions |
| no-progress fingerprints (3× → stuck) | **simplified**: 6-round cap per task → human_rescue |
| reviewer maxAttempts=2 | gate attempt counter per task |
| validation gate (host commands) | `validate` runs `./.fabro/setup.sh && ./.fabro/ci.sh` |
| diff-too-large guard (2MB) | `prep_review` size check → rework feedback |
| pre-coder rebase guard | `next_task` merges `origin/main`; a conflict is aborted there (leaving the tree would commit markers) and handed to the dedicated `resolve_merge` agent + `resolve_merge_gate`: one retry, then `human_rescue` |
| accumulation branch advance per child | the run branch **is** the accumulation branch (single sandbox, sequential tasks; checkpoints commit per stage) |
| subtask improvement vs current accumulation | `improve` stage per task (disposition ready/redundant/needs_human) |
| extra review: code-quality + two-axis (standards/spec) → follow-up issues | `standards` + `spec` + `quality` reviewers → `extra_decompose` → follow-ups appended to `tasks.json`; max 2 rounds |
| terminal labels `Review`/`agent-stuck`, `agent-authored` PRs | `open_pr` (label swap + `Review` + comment) / `mark_stuck` |
| human rescue gate | `human_rescue` hexagon (Retry-with-guidance / Accept-partial / Abandon) |

## Golden rules for every task

1. **Never print or commit secrets.** Keys live in `~/.fabro-deploy/env` (Mac, chmod 600)
   and the fabro vault. Plan files, prompts, TOML, and the workflow repo stay secret-free.
2. The workflow repo is **public** (`andrewthetechie/fabro-workflows`). Nothing sensitive.
3. Work staging dir: `~/.fabro-deploy/fabro-workflows/` (currently NOT a git repo;
   the deploy task re-inits and pushes).
4. fabro DOT quirks: `#` is rejected as a **comment** (use `//`) but is fine and
   necessary **inside** quoted strings (`Resolves #N`, `## ` headings) — verified on
   the container's fabro; multi-line double-quoted
   strings are fine; only `"` needs escaping (`\"`) inside them; avoid backslashes in
   shell/jq code (write jq filters to avoid `\`).
5. `workflow.toml` is parsed strictly at automation fire (422 on bad TOML); keys
   containing dots MUST be quoted (`"glm-4.7" = [...]`). `fabro validate` does NOT
   catch TOML errors — check TOML separately.
6. Undefined template variables in prompts promote to hard errors at run admission.
   Prompts may use `{{ goal }}` only; everything else comes from files.
7. The fabro server is `0.354.0-nightly` in the container; the host CLI is `0.254.0`
   and resolves `@schemas`/`@prompts` differently. Always validate with the
   **container's** fabro: `cd ~/fabro && docker compose exec -T fabro fabro validate <path>`.
8. Sandbox profile images have: `git`, `jq`, `gh`, `bash`. The fabro server container
   is Alpine (no bash, no curl; `/bin/sh` + `wget`) — relevant only for host-side
   hook scripts.
9. Profile images run command stages as `sh` — write POSIX sh, no bashisms
   (`pipefail`, `[[ ]]`, arrays are out).
10. **Stylesheet class selectors accept `[a-z0-9-]` only — no underscores.**
    `stylesheet.rs` `parse_selector` stops a `.class` at the first character outside
    that set (ids and shape selectors *do* allow `_`, classes do not), so `.coder_t2`
    fails with `expected '{' after selector`. All class names are hyphenated:
    `.coder-t2`, `.coder-t3`, `.coder-t4`, `.review-frontier`.
11. **Branch hygiene.** `workflow.toml` sets `[run.meta_branch] push = false` (task 07)
    because nothing reads the pushed metadata branch. `[run.run_branch] push` stays on
    so a server crash does not lose in-progress work; the daily sweeper in task 12 reaps
    the run branches instead. Never add a `[run.pull_request]` block — the workflow
    opens its own PR, and that block would also make `run_branch.push = false`
    a hard config error if the operator ever wants it.

## Task index

| # | Task | Needs smarter LLM? |
|---|---|---|
| 01 | Add `glm-4.7` + `kimi-for-coding` to LiteLLM + fabro | no (exact commands) |
| 02 | Write the new `workflow.fabro` | **yes — recommended** (escaping + validation iteration) |
| 03 | Prompts: decompose, improve | no |
| 04 | Prompts: coder, rework | no |
| 05 | Prompt: task reviewer | no |
| 06 | Prompts: standards, spec, quality, extra_decompose | no |
| 07 | workflow.toml + notify script + dead-file cleanup | no |
| 08 | Validate the workflow (container `fabro validate` + TOML) | no (escalate if stuck) |
| 09 | Deploy + E2E test on jelly-swipe | **yes — recommended** (live judgment) |
| 10 | Post-deploy cleanup | no |
| 11 | Update FABRO-DEPLOYMENT-LOG.md | no |
| 12 | Purge leaked `fabro/*` branches + install the daily sweeper | no (script is given verbatim) |

Tasks are designed to run sequentially. 02–07 can technically go in any order
(contracts are fixed here), but do them in order anyway. 08 needs 02–07. 09 needs 08+01.
10 and 11 need 09. **12 is independent of 02–11** — it fixes a separate fabro bug and
can be done at any point; doing it after 09 means the E2E run's own branches are in
scope, doing it before means the ~300 already-leaked branches are gone before the
redesign goes live. Task 11 records the results of 09, 10 **and** 12, so do 11 last.
