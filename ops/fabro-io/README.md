# fabro-io

The Stage I/O MCP server (ADR 0016, `docs/stage-io/`). One static Rust binary, built once
and copied into every profile image, that runs in the run sandbox and gives each agent
stage two MCP tools: `inputs`, which returns everything the stage declares it needs, and
`submit`, which validates the stage's output and writes it with an Input receipt (`_io`).

The binary holds **no** stage facts. Every path, stage name, schema, and sealed path comes
from the **Stage manifest** (C2), generated into each `workflow.toml` as the
`FABRO_IO_MANIFEST` environment variable. See
`docs/stage-io/00-overview-and-contracts.md` for the contracts (C1–C7) this implements.

## Subcommands

| Command | What it does (exit codes) |
|---|---|
| `stage` | The `io-stage` hook: writes `.io/stage.json` with a fresh 128-bit visit; `mkdir -p /tmp/fabro/.io` (0, or 2 if the binary is older than the manifest's `min_binary` or the manifest is invalid JSON). |
| `inputs [<name> [--part N]]` | Returns the stage's inputs, paged at 49152 bytes, and records what was served in `.io/served.json` (0, or 3 when a required input is missing). |
| `submit --file <path>` | Validates the output against the stage's schema, refuses until every required input was read in full, stamps `_io` and writes the contract atomically (0, 3 on "call inputs first"/incomplete, 4 on a schema violation). |
| `guard` | The `io-guard` pre-tool-use hook: blocks a Sealed path or a path a Sealed glob matches (2), proceeds (0) otherwise, and proceeds with a warning on its own errors (D8). |
| `serve --port N` | The MCP streamable-HTTP server on `127.0.0.1:N`. `list_tools`/`call_tool` read `stage.json` and the manifest on every request, so it is stateless (D3): each stage sees only its own tools. A stage problem is returned as an `is_error` tool result, never a JSON-RPC error. |
| `version` | Prints the crate version. |

Every known stage also gets seven `code_*` tools (`src/code.rs`): `code_def`, `code_show`,
`code_search` (a grep that groups each match under its enclosing function or class),
`code_callers`, `code_callees`, `code_impact` and `code_tests`. Each runs the matching
`fabro-code` verb (ADR 0014) in the repository checkout and returns what it prints. The
server's cwd is `/workspace`, so the checkout is its one child holding `.git` (the indexed
one, if there are several); `FABRO_CODE_ROOT` and `FABRO_CODE_BIN` override both in tests.
A call is cut at 25 s, under the 30 s `tool_timeout`. Wrapper exit 1 ("nothing found") is an
answer; exit 2 (no index) and anything else is an `is_error` result that says to use grep.
They exist because a local model ignores a shell command it is told about and uses the
listed `read_file` instead: two backlog runs on 2026-10-01 made no `fabro-code` call in
`improve` or `coder`.

The sandbox `type = "sandbox"` MCP transport is wired per C6. The `serve` service is
mounted at the HTTP router fallback (any path), because the fabro sandbox client's
`initialize` does not hit `/mcp` (spike check 1).

## Build

```sh
docker run --rm -v "$PWD":/src -w /src rust:1.98.1 sh -c \
  'rustup target add x86_64-unknown-linux-musl && apt-get update && apt-get install -y musl-tools \
   && cargo build --release --target x86_64-unknown-linux-musl'
```

`target/x86_64-unknown-linux-musl/release/fabro-io` is statically linked. `cargo test` runs
offline on a Mac or in the builder stage.
