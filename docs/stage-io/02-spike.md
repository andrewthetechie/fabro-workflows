# 02 · Spike: does a sandbox MCP server work on the docker provider?

Read `00-overview-and-contracts.md` first. This task decides between the MCP transport and
the `shell` fallback (ADR 0016 D9). Every later task says what changes under each result.

## Why

The only evidence that the `sandbox` transport works on the docker provider is source
reading (B3, "Feasibility"). fabro's own docs say Daytona only. Seven facts that the design
depends on are unverified. Each one is cheap to check on the host and expensive to discover
in production.

## Build the skeleton

Create the crate `ops/fabro-io/` with the layout that task 03 fills in:

- `Cargo.toml`: edition 2024, `rmcp` 1.7.x with the `server` and
  `transport-streamable-http-server` features, `tokio`, `serde_json`, `axum` (or the HTTP
  stack that `rmcp`'s example server uses). `[profile.release]` with `lto = true`,
  `strip = true`, `panic = "abort"`.
- Two subcommands only:
  - `stage`: C5. For the spike, it also appends one line to `/tmp/fabro/.io/spike.log`:
    the time, `FABRO_NODE_ID`, and whether `FABRO_IO_MANIFEST` is set and its byte length.
  - `serve --port N`: an MCP server with one tool, `whoami`. It returns the contents of
    `stage.json`, the server's pid, its start time, and whether `FABRO_IO_MANIFEST` is set
    in the server's own environment. Log each request to `spike.log`.
- Build it for `x86_64-unknown-linux-musl`:
  `docker run --rm -v "$PWD":/src -w /src rust:1.98.1 sh -c 'rustup target add x86_64-unknown-linux-musl && apt-get update && apt-get install -y musl-tools && cargo build --release --target x86_64-unknown-linux-musl'`.
  Check `file target/x86_64-unknown-linux-musl/release/fabro-io` says "statically linked".

## Run it on the host

Do not change a production image or a production workflow. Make a scratch image
`FROM fabro-ts:local` with the binary at `/usr/local/bin/fabro-io`, tagged
`fabro-ts:io-spike`, and a scratch environment that uses it. Write a scratch workflow in
`/tmp/check/` with: a command node, two agent nodes in sequence (the second one with
`max_retries=1` and a prompt that fails the first attempt on purpose, for example by making
it exit before it writes a required file), and a final command node that prints `spike.log`.
Give it C6's MCP table and `io-stage` hook, and a `[run.environment.env]` with
`FABRO_IO_MANIFEST = '''{"version":1}'''`. The agent prompts say: "Call the `whoami` tool
once, then stop." Use a hosted model, so no coder box is held. Fire it with the three-call
sequence (register, create, start) that `~/bin/fabro-fire-*.sh` use.

## The seven checks

Record each answer, with the event or log line that proves it, in
`docs/stage-io/spike-result.md`:

1. **Transport.** Does `mcp__io__whoami` appear in `agent.tools.available`, and does the
   call return? If not, record the `agent.warning` or worker log line, and stop: D9 applies.
2. **Startup cost.** The time from `agent.session.started` to the first `agent.llm.started`,
   with and without the MCP table (run the scratch workflow twice). The budget is 2 s
   (ADR 0016, "Consequences").
3. **Hook order.** Does `stage.json` hold the node of the current stage when `whoami`
   answers? That is, does the `stage_start` hook finish before the session starts?
4. **Process lifetime.** Does the second agent stage get a new server pid, or the pid of the
   first stage's server? If it gets the old one, does `whoami` still report the second
   stage? (It must, because `serve` reads `stage.json` on each request.)
5. **Env carrier.** Is `FABRO_IO_MANIFEST` set in the hook's environment and in the
   server's environment? If it is not set in either, D4's alternative applies: the hook
   writes `/tmp/fabro/.io/manifest.json`. Record which.
6. **Retries.** On the retried second stage, does `stage_start` fire again, so that the
   retry gets a new visit? Record either answer. Task 03 depends on it: if it does not fire
   again, `inputs` must also reset `served.json` when `stage_retrying` fires. Check whether a
   `stage_retrying` sandbox hook can do that.
7. **Failure is silent.** Change the command to a binary that does not exist and run again.
   Does the agent run without the tool, as the source says? Record exactly what the events
   show. This is the case that C7's receipt check exists for.

## Result

`spike-result.md` ends with one line: `RESULT: mcp` or `RESULT: shell`, and the reason. Use
`mcp` only if checks 1, 3 and 4 pass and check 2 is 2 s or less. Update Decision 6 in the
overview, and the "Status" lines of the overview and of ADR 0016.

Delete the scratch image and environment afterwards. Keep the crate. Task 03 continues it.
