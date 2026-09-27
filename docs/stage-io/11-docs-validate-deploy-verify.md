# 11 · Docs, validation, deploy and the success targets

Read `00-overview-and-contracts.md` first. The docs part runs once, with task 06. The checks
run after each of tasks 06–10. The measurement runs when tasks 07–09 are live.

## Docs

1. **`AGENTS.md`, layout table.** Rows for `docs/stage-io/`, `ops/fabro-io/` (the binary;
   built by `build-images.sh`; holds no stage facts), `.fabro/workflows/_io/` (the Stage
   manifest and schemas; the only source of the `FABRO_IO_MANIFEST` blocks), and
   `ops/fabro-io-manifest.py`.
2. **`AGENTS.md`, "Validating".** Add `ops/fabro-io-manifest.py check` beside the routing
   checker, and `cargo test --manifest-path ops/fabro-io/Cargo.toml` for any change under
   `ops/fabro-io/` or `.fabro/workflows/_io/schemas/`. Say that `fabro validate` checks
   neither.
3. **`AGENTS.md`, deployment invariants.** One row each:
   - The `FABRO_IO_MANIFEST` blocks are generated. A hand edit is overwritten, and `check`
     fails on it.
   - `io-stage` is blocking and it runs in the sandbox for every agent stage. If the
     binary is too old, every agent stage in every run stops. Rebuild the images before
     you raise `min_binary`.
   - A migrated contract is written by `submit` and carries an Input receipt. A gate that
     accepts a contract without one has re-opened the gap that this series closed.
   - A stage's Sealed paths are in the manifest, not in its prompt. The guard is not a
     security boundary.
   - `io-guard`, and any hook for a stage in an imported phase, goes into every graph that
     imports the phase.
   - No two agent stages run in parallel while `stage.json` is the stage identity
     (ADR 0016 D3).
   - The MCP server's absence is silent in fabro. The receipt check is the only thing that
     notices it.
4. **`CONTEXT.md`.** The three terms are already there (Stage manifest, Sealed path, Input
   receipt). Check that they still match the implementation.
5. **`ops/README.md`.** `fabro-io` in the profile-image section. No new host copy: the binary
   is in the images and the manifest is in the graph.
6. **ADR 0016 and B3.** Update the Status lines with the spike result and the commits.

## Offline checks (every push in tasks 05–10)

```sh
ops/fabro-io-manifest.py check
cargo test --manifest-path ops/fabro-io/Cargo.toml
./ops/test-task-gates.sh
python3.11 -c 'import tomllib,sys; [tomllib.load(open(f,"rb")) for f in sys.argv[1:]]' .fabro/workflows/*/workflow.toml
```

Then `fabro validate` in the container and the routing checker, as AGENTS.md shows. The
validate baselines do not change: this series adds no node and no edge.

## Deploy

- Tasks 03–04: `rsync` `ops/profile-images/` **and** `ops/fabro-io/` to the host, and run
  `build-images.sh`. The build needs the crate beside the images tree. Adjust the rsync
  line in AGENTS.md's deploy block, and the path that `build-images.sh` reads it from.
- Tasks 05–10: no deploy. A push to `main` is live on the next fire.
- Order: images first, graph second. A graph whose manifest raises `min_binary` must not be
  pushed before the images that satisfy it are built and tagged.

## Success targets

Measured with `ops/fabro-exploration-share.py` (task 01) after at least 20 visits of each
migrated stage, against the committed baseline:

| Measure | Target |
|---|---|
| Migrated-stage contracts with a valid receipt, of those that reached their gate | 100% (anything less is a bug to fix) |
| `partial_reads` and `capped_reads` for migrated stages | 0 |
| `sealed_reads` for `refute` | 0 |
| Median `lead_turns`, reviewers | 1 or less (baseline 1–2) |
| Median `lead_turns`, `coder` on a box | 1 or less (baseline 3–7) |
| Median `lead_secs`, `coder` on a box | at least 50% below the baseline |
| Gate repairs for each migrated contract | no more than the baseline |
| MCP startup, `agent.session.started` to the first `agent.llm.started` | p95 within 2 s of the baseline |
| Runs stopped by `io-stage` | 0 |

Record the result in `docs/stage-io/result-<date>.txt` and in ADR 0016's Status line. If the
startup target fails and the turn targets pass only for `coder`, the next step is to limit
`[run.agent.mcps.io]` to runs where it pays off. That is not possible for each stage today
(MCP servers are configured for the whole run), so record it as a question for B8 (fabro
upgrades), and do not work around it in the graph.
