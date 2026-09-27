# Stage I/O: overview and canonical contracts

**Status:** tasks 01–10 implemented and pushed to `main` on 2026-09-27; task 11's docs are
done and its post-deploy measurement is pending (`docs/stage-io/result-2026-09-27.txt`).
Proposed 2026-09-26; decisions 1–4, 6 and 8 accepted, 5 and 7 implemented as proposed.
Task 02's spike resolved D9 to the `sandbox` MCP transport (`docs/stage-io/spike-result.md`,
`RESULT: mcp`) — no `shell` fallback is needed. A review on 2026-09-27 found and fixed five
defects before the measurement: the batch `inputs` call cut a page short and recorded it as
served, never named the inputs after one that did not fit, answered an unknown name with an
empty success, and lost `served.json` updates to parallel calls (all in `fabro-io` 0.1.1);
and `rebase_gate` sent a tier-1 receipt miss to `entry_failed`. C3, C1 and C5 below describe
the fixed behaviour. Operator decisions are listed in "Decisions" at the end of this file.

**Read this file first.** Every task in this folder assumes the decisions, contracts and
rules in this file. Each task repeats what it needs. If a task and this file disagree, this
file is correct. Stop and report the conflict. Do not guess.

Source decision: `docs/adr/0016-stage-io-server.md` (ADR 0016). Roadmap record:
`docs/factory-roadmap/B3-structured-io-submit-tool.md`.

**When you write or change a `.fabro` file or a `workflow.toml`, invoke the
`/fabro-workflow` skill first.** This file adds only the facts that are specific to this
change.

## What this is

Agent stages read their inputs from `/tmp/fabro/` with `read_file` and write their output
contract with `write_file`. The model decides how much to read, and it formats the JSON.
This series adds `fabro-io`, a static Rust binary in every profile image. It runs as an MCP
server in the sandbox and gives each agent stage two tools:

```
stage_start hook ── fabro-io stage ──▶ /tmp/fabro/.io/stage.json   (workflow, node, visit)
agent session  ──▶ mcp__io__inputs  ──▶ every declared input, paged, no line numbers
               ──▶ mcp__io__submit  ──▶ schema-checked contract + Input receipt, atomic write
gate           ──▶ checks _io.visit == stage.json visit, then only semantic checks
pre_tool_use   ── fabro-io guard ──▶ denies calls that name a Sealed path
```

The binary holds no stage facts. The **Stage manifest** in this repository holds them. A
generator writes each workflow's part of it into that workflow's `workflow.toml`, so a
manifest change is live on the next fire, like a prompt change.

## Glossary (from `CONTEXT.md`)

- **Stage manifest**: for each agent stage, its inputs, its output contract and its Sealed
  paths. It belongs to the graph, not to the image.
- **Sealed path**: a file in the sandbox that one stage must not read, because it holds
  another agent's opinion.
- **Input receipt**: the stamp on a stage's output that proves the stage received its
  inputs in the current visit.

## Canonical contracts

### C1. Files under `/tmp/fabro/.io/`

| Path | Written by | Read by | Lifetime |
|---|---|---|---|
| `stage.json` | `fabro-io stage` (the `io-stage` hook) | `serve`, `guard`, the CLI, gates | overwritten at the start of each agent stage |
| `served.json` | `inputs` | `submit`, gates of stages that only read | overwritten by the first `inputs` call of each visit |
| `served.lock` | `inputs` (`flock`) | `inputs` | whole run; empty. Serializes the read-modify-write of `served.json`, because fabro runs one turn's tool calls in parallel |
| `manifest.json` | the `io-stage` hook, **only** if task 02 finds that the env carrier does not work | as `FABRO_IO_MANIFEST` | whole run |

`stage.json`: `{"workflow": "<FABRO_WORKFLOW>", "node": "<FABRO_NODE_ID>", "visit": "<32 lowercase hex>", "started": "<RFC 3339>"}`.
`fabro-io stage` creates `/tmp/fabro/.io/` itself (`mkdir -p`). The directory can be the
first thing written under `/tmp/fabro` (see the `claim` lesson in AGENTS.md).

`served.json`: `{"visit": "...", "inputs": {"<name>": {"path": "...", "sha256": "...", "parts": N, "served": [1, 2], "status": "ok" | "absent" | "missing"}}}`.
An input is **complete** when `status` is `absent` (optional inputs only) or when `served`
holds every part from 1 to `parts`.

### C2. The Stage manifest

The source is `.fabro/workflows/_io/manifest.json`, with the schemas in
`.fabro/workflows/_io/schemas/<contract>.schema.json` (JSON Schema draft 2020-12, with
`additionalProperties: false` on every object).

```json
{
  "version": 1,
  "min_binary": "0.1.0",
  "phases": {
    "review-merge": {
      "refute": {
        "inputs": [
          {"name": "issues", "path": "/tmp/fabro/review/refute_issues.json", "required": true,
           "about": "The issues this PR closes: number, title, body. May be []. An entry with an error key could not be read."}
        ],
        "output": {"path": "/tmp/fabro/review/refute.json", "schema": "refute"},
        "sealed": ["/tmp/fabro/review/standards.json", "/tmp/fabro/feedback/**"]
      }
    }
  },
  "workflows": {
    "backlog": {"imports": {"review_merge": "review-merge"}, "stages": {}}
  }
}
```

- A stage in a phase is keyed by its id **inside** the phase. The generator prefixes it with
  the import node id (`review_merge.refute`), which it reads from the workflow's `.fabro`
  file, and it fails when the import node is not there.
- `inputs` is ordered. The order is the order of `inputs` output. `required: false` needs
  an `absent` sentence: `{"absent": "improve did not write a dossier for this task"}`.
- `output` is optional. A stage with no `output` gets no `submit` tool.
- `sealed` entries are absolute paths or globs (`*` matches within one path segment, and
  `**` matches across segments).

The generated form, in each `workflow.toml`, between the markers
`# BEGIN fabro-io manifest (generated by ops/fabro-io-manifest.py; do not edit)` and
`# END fabro-io manifest`, under `[run.environment.env]`:

```toml
FABRO_IO_MANIFEST = '''{"version":1,"min_binary":"0.1.0","stages":{"review_merge.refute":{...,"output":{"path":"...","schema":{...inlined...}}}}}'''
```

It is compact JSON with sorted keys, so the output is byte-stable. It must not contain
`'''`. `backlog`'s `[run.environment.env]` must never gain an `id` key (AGENTS.md).

### C3. `inputs`

MCP tool `inputs`. Parameters: `name` (string, optional) and `part` (integer ≥ 1,
optional). CLI: `fabro-io inputs [<name> [--part N]]`.

- With no arguments, it walks every input in manifest order within a **page budget of
  49152 bytes**. Each present input starts with
  `=== <name>: <path> (<bytes> bytes, <lines> lines) ===`, then the `about` line, then
  `part 1 of N` when the input has more than one part, then the text of part 1. The text
  has no line numbers.
- A page is **never cut**. Part boundaries are fixed for a file (they depend on the bytes,
  not on the call), and the batch shows part 1 of an input only when it fits whole in the
  rest of the budget. An input whose part 1 does not fit prints only its header and
  `(not shown: no room left in this result; see MORE below)`, and the walk **continues**:
  a later input that fits is still shown. The first block of a result is always shown
  whole, even when its header takes it past the budget. So what `served.json` records as
  served is exactly what the model received.
- The result ends with one line for every page not yet shown:
  `MORE: inputs(name="<name>", part=<k>)`. An input that was not shown gets a line for
  each of its parts, from 1.
- `inputs(name=X)` walks that one input the same way. `inputs(name=X, part=K)` returns
  only that page's raw bytes, so the parts can be requested in any order.
- A name that the stage does not declare is an error (`is_error`) that lists the stage's
  input names.
- An optional input that is absent prints `=== <name>: <path> (absent) ===` and
  `(absent: <absent sentence>)`. A required input that is absent is listed in the result
  as `MISSING: <name> (<path>)`. The tool result is marked `is_error`, and `served.json`
  records `missing`.
- Each call writes `served.json` (C1) atomically, under the `served.lock` lock. If
  `stage.json` has a visit that `served.json` does not hold, the call replaces it.
- The CLI prints the same text and exits 0, or 3 when an input is `missing`.

### C4. `submit` and the Input receipt

MCP tool `submit`. Its input schema is the stage's `output.schema`. CLI:
`fabro-io submit --file <path-to-json>`.

The server refuses, returns `is_error` and writes nothing when:

1. `served.json` does not hold the current visit: `call inputs first`.
2. A required input is not complete: `input <name> was not read in full: call inputs(name="<name>", part=<k>)`.
3. The arguments fail the schema: one line for each violation, `<json-pointer>: <message>`.

Otherwise it adds `_io` and writes the file with a temporary name followed by a rename:

```json
"_io": {"stage": "review_merge.refute", "visit": "<visit>", "binary": "0.1.0",
        "inputs": {"issues": "<sha256>", "diff": "<sha256>"}}
```

It returns `written: <path>`. The CLI exits 0 on success, 3 on refusal 1 or 2, and 4 on
refusal 3. The output is written with sorted keys and two-space indentation, so the gate
fixtures can be generated by the binary and compared byte for byte.

### C5. `serve`, `stage` and `guard`

- `fabro-io serve --port 7391` is the MCP streamable-HTTP server on `127.0.0.1:7391`, path
  `/mcp`. It answers `tools/list` for the stage in `stage.json` on each request. When no
  stage matches, it returns no tools. It stays stateless (ADR 0016 D3).
- `fabro-io stage` writes `stage.json`. It exits 2 with
  `{"decision":"block","reason":"fabro-io <v> is older than the manifest's min_binary <m>; rebuild the profile images"}`
  on a version mismatch, and 2 with a reason when `FABRO_IO_MANIFEST` is not valid JSON.
  The decision is printed on **stdout**: fabro parses a hook's decision from stdout only,
  and without it reports "hook exited with code 2". It exits 0 in every other case,
  including a node that the manifest does not list and a `stage.json` it cannot write (it
  then removes the previous stage's `stage.json`, so every receipt check fails closed).
- `fabro-io guard` reads `FABRO_HOOK_CONTEXT`, takes `tool_input`, and collects every
  string value in it, recursively. It blocks (exit 2 and a `block` decision with
  `reason: "<path> is sealed for this stage"`) when a string contains a Sealed path, or
  a path that a sealed glob matches. It proceeds (exit 0) in every other case, including
  when there is no manifest entry. A guard that cannot read its input proceeds and prints
  a warning. It is a guard against mistakes, and it must not stop a stage (ADR 0016 D8).

### C6. The `workflow.toml` wiring (all four workflows)

```toml
[run.agent.mcps.io]
type = "sandbox"
command = ["fabro-io", "serve", "--port", "7391"]
port = 7391
startup_timeout = "20s"
tool_timeout = "30s"

[[run.hooks]]
id = "io-stage"
event = "stage_start"
matcher = "^agent$"
blocking = true
sandbox = true
timeout = "10s"
script = "fabro-io stage"

[[run.hooks]]
id = "io-guard"
event = "pre_tool_use"
matcher = "(^|[.])refute$"
blocking = true
sandbox = true
timeout = "5s"
script = "fabro-io guard"
```

`timeout` is a duration string (fabro 0.362's hook field; an earlier draft of C6
wrote `timeout_ms`, which `fabro validate` rejects with `unknown field`).
`io-guard`'s matcher grows as more stages get Sealed paths. It is tested against the node
id and the tool name, so it must never match a tool name. Task 06 checks the `startup_timeout`
value against the spike's measurement.

### C7. The receipt check in a gate

For a stage that migrates both reads and output, before its semantic checks:

```sh
V=$(jq -r '.visit // empty' /tmp/fabro/.io/stage.json 2>/dev/null)
RV=$(jq -r '._io.visit // empty' $F 2>/dev/null)
if [ -z \"$V\" ] || [ \"$RV\" != \"$V\" ]; then
  (the stage's existing one-repair branch, with the message:
   'no valid Input receipt: write the result with the submit tool, not write_file')
fi
```

For a stage that migrates only reads, the same check reads
`jq -r '.visit' /tmp/fabro/.io/served.json`, and it also checks that every required input
has `status == "ok"` and is complete.

Shown here as it appears inside a DOT `script=` attribute, so `\"` is the only backslash.
No `#` comment goes in a script.

## Rules this series inherits

From `AGENTS.md`, the deployment invariants. These rules apply most often here:

- `\"` is the only backslash in a `.fabro` file. Inline scripts are POSIX `sh`.
- Delete a contract file before the agent that writes it runs. The receipt does not replace
  this rule. It adds a second check.
- Every command node's unconditional edge lands on the terminal-failure node. A gate that
  gains the receipt check keeps its existing edges.
- Hooks are configured per package. A hook for an imported phase's stage goes into
  **every** graph that imports the phase, and its matcher is written `(^|[.])<id>$`.
- `output_schema="routing"` stays on every gate that prints `context_updates`.
- A class move voids measured timeouts. This series moves no class. A stage that gets
  slower because of the MCP startup is re-timed in task 11.
- The Refuter sees no other agent's output (ADR 0013). The manifest's `sealed` list for
  `refute` is that rule, now enforced as well as stated.

## Attempt counters after task 10

Task 10 asked which `*_attempts` counters still have a shape branch once every contract goes
through `submit`. None counts a pure shape error any more that the schema could not also
catch: a valid receipt proves the schema passed. Every contract counter below still counts
a receipt miss (C7). What else each one counts, and why it stays (recorded 2026-09-27):

| Counter | Gate | Still counted besides the receipt | Why it remains |
|---|---|---|---|
| `refute_attempts` | `refute_gate` | nothing | The receipt is the only check left; the verdict is computed, never repaired. |
| `standards_attempts`, `spec_attempts` | phase and `backlog` gates of those names | nothing | As above. The two graphs keep separate files for separate gates. |
| `quality_attempts` | `quality_gate` | nothing | As above. |
| `review_attempts` | `review_gate` | `changes_requested` with no `blocking` finding | Semantic: which findings block is judgement, not shape. |
| `fix_result_attempts` | `fix_gate` | `fixed` with empty `fixes_applied`; an `error` own finding cited nowhere; `risk` not an integer 0–5 | Cross-field and AGENTS.md-mandated: `risk` stays gate-enforced even though the schema requires it. |
| `decompose_attempts` | `decompose_gate` | `status` enum; each issue's `id`/`title`/`body` non-empty strings and `files` an array; `issues` non-empty when `status=issues` | The queue logic below the check reads those fields, and task 10 forbade changing it; the non-empty-when-`issues` rule is conditional on `status`. Redundant with the schema otherwise. |
| `extra_decompose_attempts` | `extra_gate` | the same checks on `followups.json` | Same reason: it feeds the same queue. |
| `improve_attempts` (`backlog`) | `improve_gate` | `disposition` enum; a `ready` task needs a non-empty body; a `split` needs 2–4 tasks, no bad slice and no id collision with the queue | Id collisions and the queue-relative rules need `tasks.json`, which a schema cannot see. |
| `improve_attempts` (triage) | `_shared/triage` `improve_gate` | `status` enum; `improved` needs a non-empty body | Conditional on `status`. |
| `triage_attempts` | `triage_gate` | Conventional-Commit title, no `!` or `BREAKING CHANGE`, label charset, `triage.md` present, questions consistent with `readiness`, decision shape | `triage.md` is prose outside the contract; the title and label rules protect `release-please`; the rest is cross-field. |
| `arch/scan_attempts` | `scan_gate` | candidate content rules | Cross-field rules over the candidate list. |

Four counters are not contract repair counters, and task 10 does not touch them:
`rebase_attempts` and `merge_attempts` are the rebase and mainline-merge **tier ladders**,
driven by checks read from git (a receipt miss counts as a failed tier; see `rebase_gate`),
and `fix_attempts` and `gh_fix_attempts` bound the CI-fix loop.

After a receipt miss, every gate with a repair turn gives C7's hint ("write the result
with the submit tool, not write_file"). The gates that fold the receipt into their
`invalid` state (`decompose`, `extra`, both `improve`, `fix`, `scan`, `triage`) set `RM=1`
on a receipt miss and choose the hint from it, so a stamped but invalid contract still gets
the gate's own "rewrite it exactly per the contract" message.

## Tasks

| # | File | Where | Depends on |
|---|---|---|---|
| 01 | `01-measure.md` | `ops/fabro-exploration-share.py` | — |
| 02 | `02-spike.md` | `ops/fabro-io/` (skeleton), host | — |
| 03 | `03-fabro-io.md` | `ops/fabro-io/` | 02 |
| 04 | `04-profile-images.md` | `ops/profile-images/` | 03 |
| 05 | `05-manifest.md` | `.fabro/workflows/_io/`, `ops/fabro-io-manifest.py` | 03 |
| 06 | `06-wiring.md` | four `workflow.toml` | 04, 05 |
| 07 | `07-refute.md` | `refute` prompt, `refute_gate`, guard | 06 |
| 08 | `08-reviewers.md` | six reviewer prompts and gates | 07 |
| 09 | `09-implementer-inputs.md` | implementer prompts and gates | 08 |
| 10 | `10-remaining-outputs.md` | the remaining contracts | 09 |
| 11 | `11-docs-validate-deploy-verify.md` | `AGENTS.md`, `CONTEXT.md`, host | 01–10 |

Tasks 07–10 each ship on their own, in order. Each is one commit for each stage (ADR 0016
D10), and task 11's checks are run after each of them, not only at the end.

## Decisions

1. **A static Rust binary, built once in a builder stage and copied into every image**
   (ADR 0016 D2). Operator suggestion, 2026-09-26.
2. **One server for both directions**, and `submit` refuses until `inputs` has served every
   page of every required input, a large diff included (D1, D6). Accepted 2026-09-26.
3. **The manifest travels with the graph**, generated into `workflow.toml`, and is never
   compiled into the binary (D4). Accepted 2026-09-26.
4. **Page budget 48 KB** for each `inputs` call (C3). Accepted 2026-09-26, to be confirmed
   by task 01's measurement of input sizes. **Confirmed 2026-09-26 (task 01):** the largest
   `/tmp/fabro` inputs exceed one page and need the paging mechanism — `review/diff.patch`
   p90 = 54842 B (> 49152, max 93431), `review/refute_diff.patch` p90 = 49273 B (> 49152,
   max 57143), `extra/diff.patch` p90 = 62318 B (max 96791). The budget stays 49152; those
   diffs spread across parts (C3, `MORE:` paging). Evidence is in
   `docs/stage-io/baseline-2026-09-26.txt`.
5. **The guard is a guard against mistakes, not a boundary**, and it proceeds on its own
   errors (D8, C5). Proposed; implemented as proposed on 2026-09-27 (task 03, `guard.rs`).
6. **MCP transport accepted; no `shell` fallback.** Task 02's spike resolved D9 to the `sandbox` MCP transport — `RESULT: mcp` in `spike-result.md`. All decision-gating checks passed (transport, hook order, process lifetime; startup ≈ 0.3 s ≤ 2 s); the missing-binary case is silent as designed, so C7's receipt check is authoritative. Accepted 2026-09-27.
7. **Success targets:** the table in task 11. Proposed; the measurement against them is
   pending (`docs/stage-io/result-2026-09-27.txt`). Receipts from before the 2026-09-27
   `inputs` fixes carry `"binary": "0.1.0"`; count only `0.1.1` visits toward the targets.
8. **`coder`'s receipt is reported, never routed on.** `coder` has no gate: both
   `succeeded` and `partially_succeeded` go to `autofix`. A missing receipt is reported in
   the first node after `autofix` that judges the task, and a 180-minute stage is never
   sent back for it (task 09). Accepted 2026-09-26.
