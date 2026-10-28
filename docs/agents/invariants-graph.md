# Invariants: graphs, scripts, hooks and packages

Read before changing any `.fabro` graph, prompt, hook, or `workflow.toml`, in any of the five workflows or the `_shared/` phases.

Every rule here passes `fabro validate` and fails at runtime, usually silently. Each one cost a live incident or was found before it could. Moved verbatim from `AGENTS.md` on 2026-10-09; the area files are siblings of this one.

## Syntax and stylesheets

| Rule | What happens otherwise |
|---|---|
| Model stylesheet class selectors use `[a-z0-9-]` only: `.rebase-t2`, never `.rebase_t2` | The stylesheet fails to parse at runtime. Checked by `ops/check-graph-invariants.py` R4.
| A per-run model choice goes in the root graph's `model_stylesheet` as `{{ inputs.X \| default('value') }}`, with single quotes, and `X` is never bound in `[run.inputs]` | An unbound input fails the run at compile; a bound one routes every hand-fired run to one box. Checked by `ops/check-graph-invariants.py` R4.
| A class used by a node in `_shared/review-merge/` or `_shared/triage/` has an explicit rule in **both** importing stylesheets | It inherits `*`, so a merge-phase reviewer runs on a local box without a failure. Checked by `ops/check-graph-invariants.py` R3.
| No `#` line inside a `script=` attribute; the prose goes in `//` comments above the node | The prose is paid twice in the next agent's prompt. Checked by `ops/check-graph-invariants.py` R6.
| `\"` is the only backslash in a `.fabro` file | The parser turns `\n` into a real newline, which breaks an embedded jq or printf program. Checked by `ops/check-graph-invariants.py` R6.
| `//` starts a comment; `#` only ever appears inside a quoted string | `#` is a DOT comment. `Resolves #N` and `##` headings inside a string are fine — never strip one to make a grep pass. |
| Inline scripts are POSIX `sh` | No `[[ ]]`, no `pipefail`, no arrays. `sh -n` catches this, but it is blind inside `jq '...'` — compile embedded jq programs separately. |
| Every graph runs at `default_fidelity="truncate"` | Anything higher prepends a stage recap to every agent prompt — `compact`, the fabro default, prepends *every* completed stage with up to 25 lines of output each. At `summary:low` the preamble was still 23% of every prompt. Nothing here needs it: every prompt names its inputs by path, which is the point of the file-backed contracts. The one exception was `human.gate.text`, which `record_guidance` writes to a file. |

## Routing and contracts

| Rule | What happens otherwise |
|---|---|
| A command node declares `output_schema="routing"` exactly when its script prints `context_updates` | Routing goes inert, or the stage fails with no retry. Checked by `ops/check-graph-invariants.py` R8.
| Every command node's unconditional edge lands on the workflow's terminal-failure node | A failed node still routes, and it takes its *unconditional* edge. Where that edge is the happy path, fold `\|\| outcome=failed` into the escape edge's condition. |
| Reset per-iteration context keys, and write every key on both branches | A stale key from an earlier loop can satisfy an edge meant for this one. |
| Delete a contract file before the agent that writes it runs | An agent that exits succeeded without writing hands the gate its predecessor's result. |
| A conflicted merge or rebase never crosses a stage boundary | The checkpoint is `git add -A && git commit`, and `git add` marks a conflicted file resolved — so the checkpoint commits conflict markers. The command node aborts to restore a clean tree; a dedicated agent then redoes and resolves the whole thing inside one stage. On 0.362 a checkpoint **commit** failure stops the run (it was best-effort on 0.354), so this rule is now also a run-killer, not just a merge hazard. A bare `git merge` under `set -e` breaks it too: the failed node leaves the conflict in the tree for the checkpoint. That was `open_pr_prep` until 2026-10-12 (`invariants-backlog.md`, `final_merge`). |

## Agents and hooks

| Rule | What happens otherwise |
|---|---|
| Agents do not run `git` | Three deliberate exceptions: `pr-review`'s rebase agent, `backlog`'s `resolve_merge` agent and the merge phase's `resolve_base` agent (#37), which need `git add` and `--continue`. Enforced by the `git-guard` `pre_tool_use` hook (docs/coder-tweaks C2): read-only git (`status`, `diff`, `log`, ...) passes, everything else is blocked from `shell` with a reason naming `restore_file` or `baseline_check`. Exempt stages carry `"git": "write"` in `_io/manifest.json` (the generator copies it into each `workflow.toml`'s manifest). `sh -c` and `python` bypass it; it catches mistakes. |
| A blocking hook's script exits 0 when its own program is missing or crashes | fabro treats **any** hook exit but 0 as a block (`parse_decision`: 2 blocks, and every other code blocks too; an unrunnable sandbox hook reports -1). A guard whose binary is missing from an old image, or that panics, would fail every call it matches. `git-guard`'s script is `fabro-io git-guard; [ $? -eq 2 ] && exit 2; exit 0`, and the gate tests (`test_tools.py`) extract it from each `workflow.toml` and runs it with `fabro-io` absent. |
| A blocking hook finishes inside its `timeout` by its own deadline | A blocking sandbox `stage_start` hook that outlives its `timeout` is killed and reports `-1`, and fabro fails the stage and the run (`workflow_error`): verified 2026-10-28 with a 20 s sleep under a 5 s timeout (more-local-inference task 12). A timeout is a block, never a pass. So `box-claim`'s own 45 s deadline (5 s per call) inside its 60 s `timeout` is load-bearing, and a blocking wait also counts toward `stall_timeout`. |
| A sandbox hook prints nothing anyone can read unless it blocks | fabro 0.362 keeps neither stdout (a JSON `proceed` decision or plain text) nor stderr of a hook that does not block: they appear in no event, `fabro logs`, `fabro dump` or server log (verified 2026-10-28). A blocked hook shows only as `✗ Failed hook exited with code N`. `stage_start` hooks run between the `edge.selected` into a stage and its `stage.started`, so their time can be read from the events: `ops/fabro-box-claims.py` reads `box-claim`'s wait that way. |
| A sandbox `stage_start` hook knows its run: `FABRO_RUN_ID` is set, and the `FABRO_HOOK_CONTEXT` file carries `run_id`, `node_id` and `handler_type` | Verified 2026-10-28 (more-local-inference task 12). `FABRO_WORKFLOW` is the **graph** name (`Backlog`), not the package name, so nothing may look a package up by it. A command node gets neither `FABRO_RUN_ID` nor a hook context. |
| A run's `FABRO_API_TOKEN` may cancel another run | `POST /runs/{id}/cancel` from a sandbox with `{{ secrets.FABRO_API_TOKEN }}` answered 202 and the other run ended `failed`/`cancelled` (verified 2026-10-28). It is the operator's API token, so every package that carries it can cancel any run: carry it only where a node or hook needs it. |
| Hook matchers are anchored: `^...$`, or `(^|[.])...$` for a node of an imported phase | An unanchored matcher is silent, or fires on the wrong node. Checked by `ops/check-graph-invariants.py` R5.

## Packages (`workflow.toml`)

| Rule | What happens otherwise |
|---|---|
| `[run.environment.env]` in backlog's `workflow.toml` must never gain an `id` key | It pins all four backlog automations to one environment, and two of the four repos fail CI on the wrong image. It also breaks `fabro validate`, which resolves a non-default id against the CLI's own local catalog and errors. |
| `[run.environment.env]` interpolates `{{ vars.X }}` only — never `${X}`, never `{{ env.X }}` | `${X}` reaches the sandbox as literal text. A kill switch spelled that way is pinned off forever, and the shakedown cannot tell it from a correctly disarmed one. |

## Timeouts and the failure-signature breaker

| Rule | What happens otherwise |
|---|---|
| A root graph's `stall_timeout` exceeds the timeout of every command node in it, spliced nodes included | A command node outlives the watchdog. Checked by `ops/check-graph-invariants.py` R2.
| A root graph that imports a phase, or has a cycle through a command node, sets `loop_restart_signature_limit` to at least 20 | The breaker stops the run on a few repeats of one signature. Checked by `ops/check-graph-invariants.py` R1.
| Moving a stylesheet class to a different model voids every timeout derived from the old one | Node timeouts here are measured, not guessed, so the measurement's subject is part of the number. `9b7f5ff` moved `.improve` from `glm-5.3` to `{{ inputs.coder_pool }}` — a single-slot llama.cpp box — and left `timeout="15m"`, which the comment above the node justified with "improve max 180s" taken entirely on glm-5.3. Measured output rate is 22.1 tok/s hosted against 11.8 tok/s on a box, and worse than that ratio for a node that re-reads the checkout every turn: run `01M321VB0DC4N187GHA88RM0QB` burned both attempts at exactly 900004ms and 900005ms, still generating, and reached `human_rescue` having never run a coder. `fabro validate` cannot see this — the graph is valid and the class resolves. Re-time the node in the same commit that moves the class, or say in the comment that the number is now unmeasured. |

## Stage I/O (ADR 0016)

| Rule | What happens otherwise |
|---|---|
| The `FABRO_IO_MANIFEST` blocks in each `workflow.toml` are **generated** | A hand edit is overwritten by `ops/fabro-io-manifest.py generate`, and `check` fails on drift. Edit `.fabro/workflows/_io/manifest.json`, never the block. |
| `io-stage` is blocking and it runs in the sandbox for every agent stage | If the shipped `fabro-io` binary is older than the manifest's `min_binary`, every agent stage in every run stops. Rebuild the profile images before you raise `min_binary`, and keep the image and `min_binary` in step. A package that declares `[run.agent.mcps.io]` without the hook has no visit for any stage, so every receipt gate reads every agent stage as a miss; `ralph` was first written that way. Checked by `ops/check-graph-invariants.py` R11, with `git-guard`, and `io-guard` on every stage the manifest gives Sealed paths. |
| A migrated contract is written by `submit` and carries an Input receipt | The gate rejects a fixed-format contract written by hand or without a receipt. A gate that accepts a contract without one has re-opened the gap this series closed, and the fix is the C7 receipt check, not a longer prompt. |
| The `FABRO_AGENT_GUIDE` block in each `workflow.toml` is **generated** from `_io/agent-guide.md`, and an entry node never overwrites a tracked `.codex/instructions.md` | Pebble loads `.codex/instructions.md` into the system prompt next to `AGENTS.md` (32,768 bytes across all files, then cut), which is the only place we control that sits above its `rg` advice. `ops/fabro-io-manifest.py generate` writes the block between its own markers; `check` fails on drift, a guide over 3,000 bytes, or `'''` in it. Each entry node (backlog `prep`, pr-review `claim`, arch-review `prep`, issue-triage `acquire`) writes the file and adds it to `.git/info/exclude`, so a checkpoint never commits it, and skips it with one stderr line when the variable is empty or the repository tracks the file. The install never fails the node and prints nothing to stdout (`claim` routes on stdout). Covered by the gate tests (`test_entry_nodes.py`). |
| A stage's Sealed paths live in the manifest, not in its prompt | The `io-guard` hook denies them. It is a guardrail, not a security boundary: the sandbox runs in a container with repository write access, and an agent can read history or logs. |
| `io-guard`, and any hook for a stage in an imported phase, goes into every graph that imports the phase | Like the review-merge report hooks, a hook in one `workflow.toml` does nothing for a run of a graph that imports the phase elsewhere. `io-guard` is wired into every graph that runs `refute`, and `ralph` adds `standards` and `spec`. |
| No two agent stages run in parallel while `stage.json` is the stage identity | The `.io/stage.json` staging area is shared state; parallel runs of two stages would fight over it. ADR 0016 D3. |
| An `output` in the manifest makes `submit` available; it does not oblige the stage to submit. `task_blocked` (coder and rework tiers, #17) is the one contract a stage writes only to block, and its gate reads "no file" as "carry on" | A gate that treated a missing `task_blocked.json` as an error would stop every task that implemented normally. `fabro-io submit` refuses only a stage with no `output` (`ops/fabro-io/src/serve.rs`), so no binary change came with it. |
| The MCP server's absence is silent in fabro | Fabro logs just one event when an agent tool times out; nothing surfaces that `inputs` did not answer. The receipt check is the only thing that notices, which is why every migrated gate carries it. |

## Box stages and the claim (more-local-inference C1-C4)

The manifest below is the *Stage* manifest (`_io/manifest.json` and the generated
`FABRO_IO_MANIFEST` blocks), not the graph. A **box stage** is a stage that runs on the run's
coder box; every other stage runs on a hosted model. `fabro-io box-claim` and `box-guard`
read these values at run time, and the scheduler reads them to decide when a box is free.

| Rule | What happens otherwise |
|---|---|
| Every stage in the Stage manifest states `box`: `false`, `true`, or `"plan-arm"` (R13's other half, enforced by `ops/fabro-io-manifest.py check`) | A stage nobody decided is a stage nobody can reason about. The generator refuses a missing or malformed value, and a stage of an imported phase carries a map with exactly one bool per importing workflow, because an imported phase renders with the importer's stylesheet. |
| The `box` values and the stylesheets agree in both directions; every agent node has a `box`; a `"plan-arm"` stage's `{% if %}` condition is exactly `(inputs.issue_number \| default(0) \| int) // 2 % 2 == 1`; and no agent node resolves to a literal `coders-*` model, through its class or through the `*` rule (R13) | The stylesheets decide where a stage actually runs, so a stage marked `false` that a class sends to `coders-a` runs on box-a with no claim and no lease — the run silently steals a box. `ops/fabro-io/src/boxclaim.rs` hard-codes the arm arithmetic, so a stylesheet that spells the condition differently disagrees with the hook that is supposed to protect it. |
| Only `backlog`, `ralph` and `pr-review` carry the `box-claim` hook, and each carries it exactly as C4 gives it: `stage_start` on `^agent$`, blocking, sandboxed, `timeout = "60s"`, `script = "fabro-io box-claim; exit 0"` (R15) | In a background package the claim would cancel the background runs on its own box, its own run included, so the lane would cancel itself and never finish a job. The exact shape matters too: unanchored, or unblocked, it either never fires or does not wait for the box. The trailing `exit 0` is the hook rule below. |
| `claim` in each of the three writes `/tmp/fabro/box` (R15), and no other node may depend on it existing | `box-claim` runs as a `stage_start` hook, before any command node, so a file is the only way it can learn which box this run holds. An empty or missing file means "no box known" and the claim does nothing. |
| `fabro-io`'s manifest structs ship **before** any manifest that uses them (D18) | Its types are `deny_unknown_fields`, so a `workflow.toml` carrying `box` fails to load in an older binary — and with it `submit`, `serve`, `guard` and `git-guard` in **every** run. Push the binary and `make deploy-images` first, then the commit that raises `min_binary` or adds the field. |
| `FABRO_BOX_HEALTH` exists on the host before a `{{ vars.FABRO_BOX_HEALTH }}` reaches `main` | An unset server variable fails the RunIntent at compile time, so **no** `backlog`, `ralph` or `pr-review` run is created at all. `ops/provision-server-state.sh` creates it, create-if-absent, and task 12 runs the provisioning before the push. |

## Background packages

A **background package** is a box-only package the scheduler may run in a coder loop's gap
(more-local-inference, ADR 0031). Every agent stage of one runs on the run's coder box, at most
one such run sits on a box at a time, and any of them may be cancelled at any moment. `box-noop`
is the first; later tasks add `pr-doctor`, `issue-precheck`, `context-pack` and two more. The
checker's `BACKGROUND_PACKAGES` list names them, and R16 holds every rule below.

| Rule | What happens otherwise |
|---|---|
| No `[run.model.fallbacks]` key ends in `-bg`, and no `-bg` name appears as a fallback **target** (R14) | A `-bg` name is a box's own model row. A fallback on it, or to it, is the one thing that lets a background stage reach a hosted model and spend the credits the lane exists to save. It is silent: a fallback is not an error. |
| Every model a background package names — the `*` rule, every class rule, and therefore every agent stage — is `{{ inputs.coder_pool \| default('coders-?-bg') }}` (R16) | The scheduler sets `coder_pool` to `coders-a-bg` or `coders-b-bg`; a hosted name makes a "background" run spend z.ai credits, and a literal `coders-a` pins every run to box-a whatever the lease. |
| Only the three coder loops (`backlog`, `ralph`, `pr-review`) run `fabro-io box-claim`, and each runs it exactly as C4 gives it: `stage_start` on `^agent$`, blocking, sandboxed, 60 s, `fabro-io box-claim; exit 0` (R15) | A background run with the claim would cancel the background runs on its own box — its own run included — so the lane would cancel itself and never finish anything. |
| Every background package runs `fabro-io box-guard` before each of its agent stages, as C10 gives it: `stage_start` on `^agent$`, blocking, sandboxed, 30 s, `fabro-io box-guard; [ $? -eq 2 ] && exit 2; exit 0` (R16) | The coder loop's claim runs once, at stage start, and cannot see a background run created after it. The guard is the third layer that closes that gap: without it a background stage and a coder stage can interleave requests on one box, which is the KV-cache thrash D1 exists to prevent. |
| A background package's `claim` node writes `/tmp/fabro/box` with the `-bg` suffix removed (R16) | `box-guard` reads that file to know which box it is on. Without it the guard logs and proceeds, so the run keeps the box a coder loop needs. |
| A background package's run branch never pushes on its own: `[run.run_branch]` sets `enabled = false` (`box-noop`) or `push = false` (a job that pushes explicitly in its last node) (R16) | Left to fabro's default, or to the target repository's `.fabro/project.toml`, a run branch pushes an `--allow-empty` checkpoint commit after every stage: a GitHub write in a run that may be cancelled anywhere, and a branch push that can start CI on the self-hosted runners. `box-noop` was written without the section, and the Phase 1 review caught it before the lane was turned on. |
| A background job is safe to cancel: it writes to GitHub only in its last node, and that node writes its marker first (D9) | A cancel can land in any stage. A job that wrote on the way through would leave half a comment, a label, or a push behind, and the scheduler would requeue it into a duplicate. |
| The Stage manifest of every background package carries `coder_box_stages` (C1), generated by `ops/fabro-io-manifest.py` | `box-guard` judges *another* run's stage with it. Without it every coder stage reads as "not a box stage" and the guard never fires. |

## Test environment (ADR 0018)

| Rule | What happens otherwise |
|---|---|
| A `ci.sh` reads its Test environment as `test_env=$(fabro-io test-env); eval "$test_env"`, never `eval "$(fabro-io test-env)"` | `eval "$(cmd)"` ignores a failing `cmd` under `set -euo pipefail`: `bash -c 'set -euo pipefail; eval "$(false)"; echo continued'` prints `continued`. A failed `fabro-io test-env` (a bad `test.toml`, a failed preparation) would then run the checks with no environment and no error. The two-step form exits 1 (verified 2026-10-09). |
