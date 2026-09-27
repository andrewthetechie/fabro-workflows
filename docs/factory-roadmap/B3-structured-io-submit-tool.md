# B3 · Stage I/O: one tool reads a stage's inputs, one tool writes its output

**Status:** decided in ADR 0016 (accepted 2026-09-27) and implemented through task 10 of
`docs/stage-io/` on 2026-09-27; the post-deploy measurement is pending
(`docs/stage-io/result-2026-09-27.txt`).
Supersedes `docs/research_improvements/02-tier-2-structured-output.md` and the first
version of this record (a `fabro-submit` CLI with flags). That version assumed that no MCP
server could run in the sandbox on this deployment. The assumption is out of date (see
"Feasibility").
**Axis:** repeatability. **Effort:** M–L. **Feasibility:** high, subject to one live spike.
**Depends on:** a profile-image rebuild (`ops/profile-images/build-images.sh`).

## Problem

Every agent stage starts by reading files that command nodes wrote under `/tmp/fabro/`.
Every agent stage ends by writing a JSON contract by hand with `write_file`, which a gate
then validates with `jq`. Both ends are done by the model, and the model has these problems
at both ends:

1. **Reading is not deterministic.** The prompt lists the files, and the agent decides
   whether to read them, how much of each to read, and in what order. `read_file` stops at
   2000 lines by default. `refute_diff.patch` may be 300 KB. Nothing proves that a stage
   saw its inputs before it wrote its verdict.
2. **Writing is not deterministic.** The contracts (`tasks.json`, `improve_result.json`,
   `verdict.json`, `standards.json`, `spec.json`, `fix_result.json`, `triage.json`,
   `candidates.json`, `refute.json` and more) are hand-written JSON. Sixteen `*_attempts`
   counters implement "bounce once, then give up". The measured bounce rate is low (about 2
   in 750 gate visits, 2026-09-19..24). It will not stay low as more stages move to local
   boxes (B5), and every new contract (B2's enums, A5's checklist) adds another shape to get
   right.
3. **Hiding a file names it.** `refute.md.j2` tells the Refuter not to open
   `standards.json`, `spec.json` and `fix_result.json`. That sentence is the only place
   in the Refuter's context where those paths appear.
4. **Reading costs turns.** Every read is a tool call, and each extra turn sends the whole
   context again.

The principle behind the item: LLMs write text and code, and code owns every data format
(`00-overview.md`, "The goal these items serve").

## Evidence (measured 2026-09-26)

Three `backlog` runs completed on 2026-09-26 (`01M3E2GZ85KG`, `01M3EF21YHPZ`,
`01M3FE4GP8VH`), 57 agent sessions, `fabro events --json`:

| Measure | Value |
|---|---|
| LLM turns | 1001 |
| Leading turns that only read `/tmp/fabro` inputs | 97 (9.7%) |
| Wall time in those turns | 1296 s, about 5% of the three runs' wall time |
| Hosted reviewers (`review`, `standards`, `spec`, `quality`, merge-phase `standards`, `spec`, `refute`) | 5–6 parallel reads in 1–2 turns, 6–20 s |
| `coder` on a local box | 3–7 turns, 70–206 s per task |
| `review_fix` | 2–7 turns, 14–34 s |
| Whole-file input reads | 255, none cut at the 2000-line cap in these runs |
| Partial input reads | 5, all `diff.patch` read 100–120 lines at a time by choice |
| Refuter reads of other agents' files | 0 in `01M3FE4GP8VH`: the instruction held |

So the saving is real and modest: about one turn per reviewer stage, and three to five per
coder task on a box. The main case is correctness. A deterministic reader always delivers
every input, in full or in declared pages. A gate can prove that the delivery happened,
which today it cannot. The tool also returns text without `read_file`'s `  N | ` line
prefixes, which saves some tokens on a large diff.

## Feasibility: the sandbox MCP transport works on the docker provider (source-verified)

The first version of this record rejected MCP because "`sandbox` needs Daytona". On 0.362
that is no longer true, according to the source. It is not yet proven live:

- fabro's `sandbox` MCP transport starts the server inside the run sandbox and connects
  through the sandbox's port route. The route is passed to every agent session with no
  provider check (`lib/components/fabro-workflow/src/handler/llm/pebble.rs:618`,
  `lib/components/fabro-sandbox/src/driver_sandbox.rs:1007`).
- The docker provider has a port route. The sandbox-driver rev that fabro pins,
  `07600aa`, has `crates/sandbox-driver-docker/src/forward.rs`: `preview_url(port)` opens a
  loopback listener in the worker and bridges each connection into the container through
  `docker exec` and bash's `/dev/tcp`. Every profile image has bash.
- The "Daytona only" statements are in `docs/public/agents/mcp.mdx` and in the web
  preview API (`fabro-server/src/server/handler/sandbox.rs:640`). Neither is on the MCP path.

The limits of the transport, from the same source:

- MCP servers are configured for the whole run (`[run.agent.mcps]`), so every agent sees
  the same tools. The server starts once for each agent session.
- A server that fails to start is logged and skipped, and the agent runs without its
  tools. The design must fail closed at the gate.
- A `stage_start` hook with `sandbox = true` runs in the sandbox before the stage's
  handler starts, and it receives `FABRO_NODE_ID`. That is how the server learns which
  stage it serves.
- A blocking `pre_tool_use` hook refuses one tool call (`ToolErrorKind::Denied`), and the
  agent continues (`lib/components/fabro-hooks/src/bridge.rs:92`).

## Design (ADR 0016)

One server, **`fabro-io`**, runs in the sandbox with the `sandbox` MCP transport. It is a
static Rust binary (`rmcp`, the crate that fabro's own client uses), built once for
`x86_64-unknown-linux-musl` and copied into all four profile images. The binary is
generic. The **Stage manifest** in this repository holds every fact that is specific to a
stage: its inputs, its output contract and schema, and its **Sealed paths**. The manifest
travels with the graph, so a manifest change is live on the next fire, like a prompt.

For each agent stage the server offers two tools:

- **`inputs`** returns every input that the manifest declares for this stage, in a fixed
  order, as text with no line numbers. An input larger than the page budget is returned in
  numbered pages (`inputs(name, part)`). An optional input that is absent is reported as
  absent. A missing required input is an error.
- **`submit`** takes the stage's output as schema-typed parameters. The JSON Schema is the
  tool's input schema. The server validates the output, stamps an **Input receipt** on it,
  and writes it atomically. It refuses when `inputs` has not been called in this visit.

The gates keep their semantic checks. They drop the shape checks and the repair counters
as each contract migrates, and they fail closed on a missing or stale receipt. That check
also catches a server that did not start. A `pre_tool_use` hook denies any tool call that
names a Sealed path, and the prompts stop naming those paths.

## Migration order

1. `refute`: it has Sealed paths, a single output, and a gate built in ADR 0013.
2. The reviewers: `review`, `standards`, `spec`, `quality`, the merge-phase `standards`
   and `spec`.
3. Inputs for the implementers: `coder`, `rework_*`, `improve`, `decompose`, `review_fix`,
   `ci_fix_*`, `resolve_merge`, `rebase_agent_*`. This is where most turns are saved.
4. The remaining outputs: `fix_result`, `decomposition`, `improve_result`, `triage`,
   `improve` (triage), `candidates`, `followups`, `ci_fix_result`, `rebase_result`.

## Verification

- `cargo test` for the server, offline, on the Mac and in the builder stage.
- A gate fixture for each migrated contract in `ops/test-task-gates.sh`. The fixtures are
  generated by `fabro-io` itself, so they are byte-identical to what the server writes.
- The same measurement script before and after (`ops/fabro-exploration-share.py`, extended
  with the input-read metrics above), and the targets in `docs/stage-io/10`.

## Open questions (answered in ADR 0016 or in the series)

1. How does the server know the stage? A sandbox `stage_start` hook writes it (ADR 0016 D3).
2. What happens when images and graph drift? The binary is generic, and the manifest
   travels with the graph. The manifest names the smallest binary version it needs, and
   the entry node checks the version (D4).
3. What if the spike fails? The same binary answers `fabro-io inputs` and
   `fabro-io submit` through `shell`. Only the schema-typed parameters are lost (D9).
