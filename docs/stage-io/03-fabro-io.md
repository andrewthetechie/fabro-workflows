# 03 · `fabro-io`: the binary

Read `00-overview-and-contracts.md` first. The contracts for this task are C1 to C5. Read
`docs/stage-io/spike-result.md` too: it says which transport the prompts use, and whether a
retry starts a new visit.

## Scope

Extend the skeleton from task 02 at `ops/fabro-io/` into the full binary. The binary holds
**no** stage facts: no paths, no stage names, no schemas. It reads all of them from the
manifest (C2) in `FABRO_IO_MANIFEST`, or from `/tmp/fabro/.io/manifest.json` if the spike
chose that carrier. If you find yourself writing `refute` or `/tmp/fabro/review` in Rust
outside a test, stop: it belongs in the manifest.

## Modules

| Module | Does |
|---|---|
| `manifest` | Parse and validate the generated manifest. Reject unknown keys. Compare `min_binary` with `env!("CARGO_PKG_VERSION")` (semver). Find the stage entry for a node id. |
| `stage` | The `stage` subcommand (C5). A new 128-bit visit id from `getrandom`, written as 32 lowercase hex characters. Atomic write. `mkdir -p /tmp/fabro/.io`. |
| `pages` | Split a file into pages of at most 49152 bytes, at line boundaries, deterministically. A single line longer than the budget is split at a UTF-8 character boundary, and the header of that part says `(line split)`. |
| `inputs` | C3. Builds the text and writes `served.json`. |
| `submit` | C4. Validates with the `jsonschema` crate (draft 2020-12), checks `served.json`, stamps `_io`, and writes with sorted keys (`serde_json` with the `preserve_order` feature **off**) and two-space indentation, through a temporary file and a rename in the same directory. |
| `guard` | C5. Reads the hook context. Glob matching with `globset`. Proceeds on any error of its own. |
| `serve` | C5. The `rmcp` streamable-HTTP server. `list_tools` and `call_tool` each read `stage.json` and the manifest again. The `submit` tool's `input_schema` is the stage's schema, with `_io` removed from `properties` if the schema lists it. |
| `cli` | `stage`, `serve`, `guard`, `inputs`, `submit`, `version`. Exit codes as in C3 to C5. `64` on a usage error. |

A tool call that fails returns a result with `is_error: true` and a text body. The server
never answers with a JSON-RPC error for a stage problem. fabro passes a tool error to the
model, and the model can act on it. A protocol error could end the session.

## If the spike chose `shell`

`serve` still ships, but nothing starts it. The prompts use `fabro-io inputs` and
`fabro-io submit --file`. Put the tool description text that the MCP server would send
(what the tool returns, the paging rule, the refusal messages) in `fabro-io --help`, so that
both forms say the same thing.

## If a retry does not start a new visit

(From the spike, check 6.) Add a `reset` subcommand that removes `served.json`. Task 06
wires it to a `stage_retrying` hook. Do not add it if the spike showed that `stage_start`
fires again on a retry.

## Tests (`cargo test`, offline, on the Mac and in the builder stage)

Each test uses a temporary directory in place of `/tmp/fabro` (take the root from
`FABRO_IO_ROOT`, default `/tmp/fabro`; only the tests set it):

1. `stage` writes a valid `stage.json`, and a second call changes the visit.
2. `stage` with `min_binary` above the crate version exits 2 with the C5 message.
3. `stage` with a node that is not in the manifest exits 0.
4. `inputs` with three inputs (one optional and absent, one of 120 KB, one small) gives the
   headers in manifest order, one `(absent: ...)` line, `part 1 of 3` for the large one, and
   `MORE:` lines. Requesting parts 3, then 2 gives the same bytes as a straight split.
5. The concatenation of all parts is byte-identical to the file.
6. A required input that is absent gives `MISSING:`, `is_error`, and `missing` in
   `served.json`. The CLI exits 3.
7. `submit` before `inputs` refuses with `call inputs first`, and writes nothing.
8. `submit` after `inputs` with an incomplete large input refuses and names the next part.
9. `submit` with a schema violation lists each violation's JSON pointer, and writes nothing.
10. `submit` success: the file has `_io` with the visit and the sha256 of each input, and
    it is byte-identical to the committed fixture `ops/fabro-io/tests/fixtures/refute.json`.
    This test is the reason task 07's gate fixtures can be generated.
11. A new `stage` visit makes an older `served.json` count as not served.
12. `guard` blocks `read_file` of a sealed path, `shell` `cat` of it, a `glob` whose pattern
    matches it, and a nested string. It proceeds for a path that is not sealed, for a node
    with no entry, and for a context file that cannot be read.
13. `serve`: an in-process client (the `rmcp` client) lists `inputs` and `submit` for a stage
    with an output, only `inputs` for a stage without one, and nothing for an unknown node.
    A `submit` call through the client gives the same file as the CLI.

## Acceptance

`cargo test` passes on the Mac. `cargo build --release --target x86_64-unknown-linux-musl`
gives a static binary. `fabro-io version` prints the crate version. The crate's `README.md`
lists the subcommands and points to this series for the contracts.
