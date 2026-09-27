# 05 · The Stage manifest, its schemas, and the generator

Read `00-overview-and-contracts.md` first. The contract for this task is C2.

## The source: `.fabro/workflows/_io/`

`_io/` has no `workflow.toml`, like `_shared/`, so no automation treats it as a package.

1. **`manifest.json`.** Write an entry for **every** agent stage in the four workflows and
   the two shared phases, 24 in all: 13 in `backlog`, 2 in `pr-review`, 1 in `arch-review`,
   6 in `review-merge` and 2 in `triage` (find them with `grep -n 'prompt="@'`). Take
   the inputs from each prompt's "Inputs" or "Read these" table, in the prompt's order, and
   put the table's description into `about`, in one sentence. The prompt remains the
   authority for what an input means. Take `sealed` for `refute` from `refute.md.j2`'s "Do
   not read anything else" paragraph: `standards.json`, `spec.json`, `fix_result.json`,
   `ci_fix_result.json`, `/tmp/fabro/feedback/**`.
2. **`"live": true | false`** on each stage. It is `false` for every stage in this task.
   The generator emits only live stages. Tasks 07–10 each turn stages on. So this task
   changes no run.
3. **`schemas/`.** One schema for each output contract. Write each one from the gate that
   reads the contract today: every `jq` shape check becomes a schema rule, and a check that
   JSON Schema cannot express stays in the gate (write it in the schema's `description`).
   Start with `refute.schema.json`. Its source is `refute_gate`'s first `jq` program. Write
   the others when their stage migrates (tasks 08 and 10). `additionalProperties: false`
   everywhere, and `_io` is not in the schema (the server adds it).

## The generator: `ops/fabro-io-manifest.py`

Python 3.11+ standard library only. It runs on the Mac and on the host, never in a sandbox.

- `generate`: for each of the four workflows, collect its own live stages and the live
  stages of each phase that it imports. The import node ids come from the workflow's `.fabro`
  file: a node with `import="../_shared/<phase>/<phase>.fabro"`. Prefix the phase's stage
  ids with the import node id. Inline each `output.schema` from `schemas/`. Write compact
  JSON with sorted keys into the workflow's `workflow.toml`, between C2's markers, under
  `[run.environment.env]`. If the markers are missing, insert them at the end of that table.
- `check`: generate in memory and fail (exit 1, with a unified diff) if any
  `workflow.toml` differs. It also fails when:
  1. a stage id is not an agent node (`shape=box` or no shape, with a `prompt`) in its graph;
  2. an `imports` entry names a node that is not in the graph;
  3. a live stage's prompt contains any of its Sealed paths, or a path that one of its
     sealed globs matches;
  4. a stage that is **not** live has a prompt that names a `/tmp/fabro/` path that is not
     one of its inputs, its output, or a path listed in the stage's `"also"` array
     (paths the prompt names for other reasons, for example `/tmp/fabro/feedback/` in
     `rework`). This keeps the manifest right before each stage migrates;
  5. the JSON text contains `'''`.
- The manifest is JSON inside a TOML literal string, so it needs no escaping. Check the
  result with `python3.11 -c 'import tomllib; ...'` on each `workflow.toml`.

Schema validity is checked in Rust, where the validator lives: add a `cargo test` in
`ops/fabro-io/` that compiles every file in `.fabro/workflows/_io/schemas/` with the
`jsonschema` crate.

## Acceptance

- `ops/fabro-io-manifest.py check` passes. The four `workflow.toml` files have the markers
  and an empty `stages` object, because no stage is live.
- `fabro validate` on all four workflows gives the baselines in AGENTS.md. The routing
  checker passes. `ops/test-task-gates.sh` passes.
- Add `ops/fabro-io-manifest.py check` to the pre-push list in AGENTS.md, "Validating"
  (task 11 writes the text; add it to your own checklist now).
