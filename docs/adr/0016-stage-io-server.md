# Agents get their inputs from, and give their output to, one MCP server in the sandbox

**Status:** accepted for the `sandbox` MCP transport (2026-09-27). Task 02's spike on the docker provider passed checks 1, 3, 4 and the 2 s startup budget (`docs/stage-io/spike-result.md`, `RESULT: mcp`), so D9's `shell` fallback is not needed. The static Rust binary
(D2), `submit` refusing until every page is served (D6), the manifest in the graph (D4) and
the 48 KB page budget (D5, confirmed by `docs/stage-io/01`) are accepted. Implementation plan: `docs/stage-io/`. Roadmap item
`docs/factory-roadmap/B3-structured-io-submit-tool.md`, which this ADR replaces as the
design. Tasks 01 and 02 are done; the rest is not implemented.

Every agent stage reads its inputs from files under `/tmp/fabro/` and writes a JSON
contract by hand. The model decides what to read and how much of it, and it also formats
the output. We add **`fabro-io`**, one MCP server that runs in the run sandbox. It gives
each agent stage two tools: `inputs`, which returns everything the stage needs, and
`submit`, which takes the stage's output as schema-typed parameters and writes the contract.
The **Stage manifest** in this repository holds every fact that is specific to a stage. The
server holds none of them. Code does the reading, the formatting and the proof that
delivery happened. The model only reasons.

## Evidence

Three `backlog` runs, 57 agent sessions, 1001 LLM turns (B3, "Evidence"):

- 97 turns (9.7%) and 1296 s (about 5% of wall time) went to reading `/tmp/fabro` inputs
  before any other action. The hosted reviewers already read 5–6 files in parallel in 1–2
  turns. `coder` on a local box took 3–7 turns and 70–206 s for each task.
- No input read was cut at `read_file`'s 2000-line default. But nothing prevents that cut,
  and `refute_diff.patch` may be 300 KB. Five reads paged through a diff 100–120 lines at a
  time, by the agent's choice.
- The saving in turns is modest. The case for this ADR is correctness: each input arrives
  in full, and there is proof at the gate that the stage received it.

## D1. One server, two tools, both directions

`inputs` and `submit` are in one server, because they share the stage identity (D3), the
manifest (D4) and the receipt (D6). `submit` refuses until `inputs` has run in the same
visit. That rule is only possible when one process owns both tools.

The server name in `[run.agent.mcps]` is `io`, so the model sees `mcp__io__inputs` and
`mcp__io__submit`. `tools/list` returns only the tools for the current stage. A stage with
no `output` in the manifest gets no `submit`.

## D2. A static Rust binary, built once, copied into every image

Not every profile image has `python3` on `PATH`, and each image has a different runtime.
`fabro-io` is a Rust crate at `ops/fabro-io/`. It uses `rmcp` 1.7.x, the crate and
version that fabro's client uses (`context/fabro/Cargo.lock`), with the streamable-HTTP
server transport and protocol `2025-03-26`, the version that the client requests.
`build-images.sh` compiles it once for `x86_64-unknown-linux-musl`, statically, in a builder
stage. Then each profile Dockerfile copies it to `/usr/local/bin/fabro-io`, the same way
that `fabro-code` is installed. It has no runtime dependencies, so the four images cannot
differ in behavior.

The same binary has the subcommands that the hooks run (`stage`, `guard`), a `serve`
subcommand for MCP, and CLI forms of both tools (D9).

## D3. The stage comes from a sandbox hook. The server keeps no state between calls

MCP servers are configured for the whole run, and the process does not know the node.
Each workflow gets a `stage_start` hook with `matcher = "^agent$"` (the hook matches
`handler_type`), `sandbox = true` and `blocking = true`. The hook runs `fabro-io stage`.
That command writes `/tmp/fabro/.io/stage.json`:
`{"workflow": $FABRO_WORKFLOW, "node": $FABRO_NODE_ID, "visit": <random 128-bit hex>}`.
The hook runs before the handler starts the agent session.

The server reads `stage.json` on every request, and it keeps nothing in memory between
requests. The per-visit state (what was served) is in files under `/tmp/fabro/.io/`. So a
server process that stays alive from an earlier session, or a new process that finds the
port already bound, answers correctly for the current stage. Task 02 measures which of the
two happens.

The node id is the prefixed id for imported phases (`review_merge.refute`). The manifest
keys stages by that full id.

**Constraint.** There is one `stage.json` for each sandbox. Two agent stages that run in
parallel would overwrite each other's identity. No graph runs agents in parallel today.
C3 (parallel reviewers) must first solve this, for example with a hook payload that carries
a session id.

## D4. The manifest travels with the graph, not with the image

Graphs are live on the next fire after a push to `main`. Images are rebuilt each night. If
the stage facts were compiled into the binary, a prompt change would need an image rebuild
before the push, and the two would drift.

- The source of truth is `.fabro/workflows/_io/manifest.json`, plus one JSON Schema file
  for each output contract in `.fabro/workflows/_io/schemas/`.
- `ops/fabro-io-manifest.py` puts together each workflow's subset of the manifest (its own
  stages and the stages of the phases it imports), with the schemas inlined. It writes the
  result into that workflow's `workflow.toml` as `FABRO_IO_MANIFEST` under
  `[run.environment.env]`, in a TOML literal string between marker comments. `--check`
  fails when a `workflow.toml` does not match the source. It goes in the pre-push checks
  beside `check-routing-schemas.py`.
- The manifest has a `min_binary` version. `fabro-io stage` refuses (the hook exits 2,
  and fabro blocks the stage) when the installed binary is older. The failure is then
  immediate and it names the cause.

Task 02 verifies that a `[run.environment.env]` value reaches both a sandbox hook and a
sandbox MCP server. If it does not, the stage hook writes the manifest to
`/tmp/fabro/.io/manifest.json` from its own `script`. Only the carrier changes.

## D5. `inputs` is deterministic and complete

- It returns the declared inputs in manifest order. Each input starts with the header
  `=== <name>: <path> (<bytes> bytes, <lines> lines) ===`. The text has no line numbers.
- The page budget is 48 KB for each call. An input that does not fit is cut at a line
  boundary. The header then says `part 1 of N`, and the result ends with the exact call
  that returns the next part (`inputs(name="diff", part=2)`).
- An optional input that is absent is printed as `(absent: <reason from the manifest>)`.
  A required input that is absent makes the tool return an error that names it. The
  receipt records the error, so the gate can see it.
- Each call writes `/tmp/fabro/.io/served.json` with the visit id and, for each input, the
  parts served and the file's sha256.

## D6. `submit` validates, stamps and writes atomically

- The tool's input schema is the output contract's JSON Schema, so the model gets typed
  parameters. The server validates the arguments against that schema again and returns
  each violation in-session, with its JSON path.
- On success the server adds an **Input receipt**,
  `"_io": {"stage": ..., "visit": ..., "inputs": {<name>: "<sha256>"}, "binary": ...}`.
  It writes to a temporary file and renames it into place, so a gate never reads half a
  contract.
- It refuses with `call inputs first` when `served.json` does not hold the current visit.
  It also refuses when a required input was not served in full, that is, when a page was
  never requested.

A contract written by hand has no valid receipt, because the model never sees the visit
id. The gate rejects that contract and gives the usual one repair: "write it with the
submit tool".

## D7. Gates check the receipt, and keep only their semantic checks

For a migrated stage, the gate checks that `_io.visit` equals `stage.json`'s `visit`
(command nodes do not fire the `^agent$` hook, so the file still holds the agent's visit).
It then applies the checks that JSON Schema cannot express: a Conventional Commits
title, "every error finding is cited", cross-field rules. The shape checks move to the
schema. The `*_attempts` counter stays, because a missing receipt is the one failure left
that can be repaired, and it covers an MCP server that did not start. That failure is
otherwise silent: fabro logs it and runs the agent without the tools.

A stage that migrates only its reads (phase 3 in D10) has no `submit`. Its gate checks that
`served.json` holds the current visit, with the required inputs complete.

## D8. Sealed paths: removed from the prompt, and denied by a hook

The manifest lists the **Sealed paths** of each stage. The prompts stop naming them. A
`pre_tool_use` hook with `sandbox = true`, scoped by node id to the stages that have Sealed
paths (`(^|[.])refute$` first), runs `fabro-io guard`. It reads `tool_input` from
`FABRO_HOOK_CONTEXT`, and it blocks any call whose arguments contain a Sealed path or a
glob that matches one. A blocked call fails only that call. The agent receives the reason
and continues.

This is a guard against mistakes, not a security boundary. An agent with `shell` can build
a path that no string match detects. The Refuter's independence depends on it not wanting
to look. The guard only makes sure that it does not look by accident, and that the prompt
does not suggest it.

## D9. Fallback: the same binary through `shell`

If task 02 shows that the `sandbox` transport does not work on the docker provider, or that
its startup cost for each session is more than the turns it saves, the prompts call
`fabro-io inputs [name] [--part N]` and `fabro-io submit --file <json>` through `shell`.
The manifest, the receipt, the gates and the guard do not change. The schema-typed
parameters are lost: the model writes JSON to a scratch file and `submit` validates it. The
field-level errors in-session remain.

## D10. Migration: one stage for each commit, prompt, gate and manifest together

1. `review_merge.refute`: reads and output. It is the stage with Sealed paths.
2. Reviewers: `review`, `standards`, `spec`, `quality`, `review_merge.standards`,
   `review_merge.spec`. Reads and output.
3. Reads only, for the implementers: `coder`, `rework_t1..t4`, `improve`, `decompose`,
   `review_merge.review_fix`, `review_merge.ci_fix_t1..t2`, `resolve_merge`,
   `rebase_agent_t1..t2`, `scan`, triage `improve` and `triage`.
4. The remaining outputs: `fix_result`, `decomposition`, `improve_result`, `followups`,
   triage `improve.json` and `triage.json`, `candidates`, `ci_fix_result`, `rebase_result`.

A stage switches all at once. Its prompt, its gate and its manifest entry change in the same
commit, and the gate does not accept the old form.

## Considered and rejected

- **B3's first design, `fabro-submit` with flags through `shell`.** It covered only output.
  Multi-line text in flags is unreliable, and the shell quoting is fragile. It is kept only
  as the D9 fallback, with a JSON file in place of flags.
- **A command node that writes one brief file, read with one `read_file`.** It needs no new
  binary. But `read_file` still stops at 2000 lines and adds line numbers. The model still
  chooses whether to read and how much. Nothing proves delivery unless the brief carries a
  nonce for the model to copy, which is one more thing the model can get wrong.
- **Context values in the preamble.** A command node can emit file contents as
  `context_updates`, but only `compact` and `summary:*` fidelity print context. They also
  print every completed stage, and that is the cost `truncate` removed (AGENTS.md). Prompt
  templates render at run creation, so they cannot carry runtime files.
- **fabro's `output_schema="@schema.json"`.** It validates only the final response text,
  with one in-session repair. The model must emit the whole contract as prose at the end.
  That is acceptable for three fields and a bad choice for `fix_result.json`.
- **A stdio or HTTP MCP server on the host.** It runs outside the sandbox and cannot read
  `/tmp/fabro` without `docker exec` into a container whose id it does not get.
- **The manifest compiled into the binary.** It would make each prompt change wait for an
  image rebuild (D4).
- **Python or Node for the server.** Not every image has Python on `PATH`, and the four
  images have different Node and Bun versions. A static binary behaves the same in all
  four (D2).

## Consequences

- Every agent stage pays the MCP startup: the process start, the forward's bash probe, and
  the handshake. Task 02 measures it. The budget is 2 s. More than that cancels the saving
  on the hosted reviewers.
- Every agent stage runs one extra sandbox hook (`fabro-io stage`). A failing stage hook
  blocks the stage, because command hooks do not fail open. That is correct for a binary
  that is too old. It also means a broken binary stops every agent stage in every run, so
  the image build tests `fabro-io stage` before it tags an image.
- All four `workflow.toml` files gain the `io` MCP table, two hooks and the generated
  `FABRO_IO_MANIFEST`. Hooks are configured per package, so a stage in an imported phase
  needs the hooks in every graph that imports the phase (the rule from the report hooks in
  AGENTS.md).
- The `docs/public` statement that the sandbox transport needs Daytona is not correct for
  0.362. If task 02 confirms it, a fabro upgrade (B8) that changes the docker forward can
  break every migrated stage. The receipt check makes that failure loud, and D9 is the way
  back.
