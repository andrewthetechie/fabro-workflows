# Invariants: graphs, scripts, hooks and packages

Read before changing any `.fabro` graph, prompt, hook, or `workflow.toml`, in any of the four workflows or the `_shared/` phases.

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
| A conflicted merge or rebase never crosses a stage boundary | The checkpoint is `git add -A && git commit`, and `git add` marks a conflicted file resolved — so the checkpoint commits conflict markers. The command node aborts to restore a clean tree; a dedicated agent then redoes and resolves the whole thing inside one stage. On 0.362 a checkpoint **commit** failure stops the run (it was best-effort on 0.354), so this rule is now also a run-killer, not just a merge hazard. |

## Agents and hooks

| Rule | What happens otherwise |
|---|---|
| Agents do not run `git` | Two deliberate exceptions: `pr-review`'s rebase agent and `backlog`'s `resolve_merge` agent, which need `git add` and `--continue`. Enforced by the `git-guard` `pre_tool_use` hook (docs/coder-tweaks C2): read-only git (`status`, `diff`, `log`, ...) passes, everything else is blocked from `shell` with a reason naming `restore_file` or `baseline_check`. Exempt stages carry `"git": "write"` in `_io/manifest.json` (the generator copies it into each `workflow.toml`'s manifest). `sh -c` and `python` bypass it; it catches mistakes. |
| A blocking hook's script exits 0 when its own program is missing or crashes | fabro treats **any** hook exit but 0 as a block (`parse_decision`: 2 blocks, and every other code blocks too; an unrunnable sandbox hook reports -1). A guard whose binary is missing from an old image, or that panics, would fail every call it matches. `git-guard`'s script is `fabro-io git-guard; [ $? -eq 2 ] && exit 2; exit 0`, and `ops/test-task-gates.sh` extracts it from each `workflow.toml` and runs it with `fabro-io` absent. |
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
| `io-stage` is blocking and it runs in the sandbox for every agent stage | If the shipped `fabro-io` binary is older than the manifest's `min_binary`, every agent stage in every run stops. Rebuild the profile images before you raise `min_binary`, and keep the image and `min_binary` in step. |
| A migrated contract is written by `submit` and carries an Input receipt | The gate rejects a fixed-format contract written by hand or without a receipt. A gate that accepts a contract without one has re-opened the gap this series closed, and the fix is the C7 receipt check, not a longer prompt. |
| The `FABRO_AGENT_GUIDE` block in each `workflow.toml` is **generated** from `_io/agent-guide.md`, and an entry node never overwrites a tracked `.codex/instructions.md` | Pebble loads `.codex/instructions.md` into the system prompt next to `AGENTS.md` (32,768 bytes across all files, then cut), which is the only place we control that sits above its `rg` advice. `ops/fabro-io-manifest.py generate` writes the block between its own markers; `check` fails on drift, a guide over 3,000 bytes, or `'''` in it. Each entry node (backlog `prep`, pr-review `claim`, arch-review `prep`, issue-triage `acquire`) writes the file and adds it to `.git/info/exclude`, so a checkpoint never commits it, and skips it with one stderr line when the variable is empty or the repository tracks the file. The install never fails the node and prints nothing to stdout (`claim` routes on stdout). Covered by `ops/test-task-gates.sh`. |
| A stage's Sealed paths live in the manifest, not in its prompt | The `io-guard` hook denies them. It is a guardrail, not a security boundary: the sandbox runs in a container with repository write access, and an agent can read history or logs. |
| `io-guard`, and any hook for a stage in an imported phase, goes into every graph that imports the phase | Like the review-merge report hooks, a hook in one `workflow.toml` does nothing for a run of a graph that imports the phase elsewhere. `io-guard` is wired into the two graphs that run `refute`. |
| No two agent stages run in parallel while `stage.json` is the stage identity | The `.io/stage.json` staging area is shared state; parallel runs of two stages would fight over it. ADR 0016 D3. |
| The MCP server's absence is silent in fabro | Fabro logs just one event when an agent tool times out; nothing surfaces that `inputs` did not answer. The receipt check is the only thing that notices, which is why every migrated gate carries it. |

## Test environment (ADR 0018)

| Rule | What happens otherwise |
|---|---|
| A `ci.sh` reads its Test environment as `test_env=$(fabro-io test-env); eval "$test_env"`, never `eval "$(fabro-io test-env)"` | `eval "$(cmd)"` ignores a failing `cmd` under `set -euo pipefail`: `bash -c 'set -euo pipefail; eval "$(false)"; echo continued'` prints `continued`. A failed `fabro-io test-env` (a bad `test.toml`, a failed preparation) would then run the checks with no environment and no error. The two-step form exits 1 (verified 2026-10-09). |
