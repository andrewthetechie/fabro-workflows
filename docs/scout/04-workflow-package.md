# Task 04: the `scout` package, and `config_changed` gains `scout.toml`

Read `00-overview-and-contracts.md` first (D13-D17; contracts C8, C9). **Invoke
`/fabro-workflow`**, then read `docs/agents/invariants-graph.md` in full. Open
`.fabro/workflows/merge-queue/workflow.fabro` and `workflow.toml` side by side: they are the
template for every node shape, for `stop()`, for `summarize`, and for the hooks.

## Goal

A runnable package whose shell does only I/O, wired to the `fabro-io scout` subcommands from
tasks 01-03. Also, a PR that edits `.fabro/scout.toml` is never auto-merged.

## Rules that bite in this graph

These all pass `fabro validate` and fail at runtime or in `make check`:

- The only backslash allowed in a `.fabro` file is `\"` (R6). So: no `printf '%s\n'` (use
  `echo`, or `printf '%s'` followed by `echo`), and no jq string interpolation `"\(.x)"` (use
  `"text " + (.x | tostring)`).
- No line inside `script=` starts with `#` (R6). Put the prose in `//` comments above the node.
- POSIX `sh` only: no `[[`, no arrays, no `pipefail`, no `local`.
- `output_schema="routing"` on exactly the nodes that print `context_updates`: `prep` and
  `summarize` (R8). Nothing else prints a JSON object to stdout. Send other output to stderr.
- `stall_timeout` must be greater than every node's timeout (R2).
- Every hook matcher is anchored (R5).

## Steps

1. **`.fabro/workflows/scout/workflow.fabro`**: `digraph Scout`, with the graph attributes,
   nodes, timeouts and the 11 edges exactly as C9 gives them. Add a `goal`:
   `"Scout: run this repository's Ratchet rules and read its flaky CI jobs, then file each new finding as an agent issue (ADR 0027)"`.
   Above every node, write a `//` comment that says what it reads, writes and routes on, in
   the style of `merge-queue`'s. Write the `ratchet -> flaky` comment C9 asks for.
   - `prep`: `set -e` like `merge-queue`'s `prep`, with the `stop()` function.
     `env.json` holds `{repo, sha, now}`.
   - `ratchet`: no `set -e`. Capture `setup.sh`'s exit code with `RC=$?`. Write the skip JSON
     with `jq -n --arg r "setup.sh failed (exit $RC)" '{status:"skipped", reason:$r, entries:[]}'`.
   - `flaky`: compute `SINCE` from `config.json`'s `flaky.window_days`. For the log loop, read
     the job ids with `jq -r --argjson m "$MIN" '[.jobs[] | select(.count >= $m)][0:10][] | .occurrences[0:3][] | .failed_job_id' $S/flaky.json`.
   - `plan`: pass `--sha` from `env.json` and `--repo` from `env.json`.
   - `file`: read the actions with `jq -c '.actions[] | select(.kind == "file" or .kind == "regression")' $S/plan.json`
     into a file, then loop with `while IFS= read -r A; do ... done < file`. Write each body
     with `printf '%s' "$A" | jq -r .body > $S/body.md`. Build the `--label` arguments from
     `.labels[]` by appending to the positional parameters with `set --`. Append each write-log
     entry with `jq` into `$S/filed.json`, starting from `[]`.
   - `summarize`: copy `merge-queue`'s `BS=$(awk 'BEGIN{printf "%c", 92}')` trick to strip
     backslashes.
2. **`.fabro/workflows/scout/workflow.toml`** as C9 describes. Copy `merge-queue`'s comments
   and adjust them: one line per permission saying what it serves, and a header saying the
   package has no agent stage, so it has no model, no MCP server and no generated blocks.
3. **`ops/fabro-io-manifest.py`**: extend the `WF_ORDER` comment to
   `merge-queue and scout are deliberately absent: neither has an agent stage, ...`.
   Do not add `scout` to `WF_ORDER`.
4. **`config_changed`** in `.fabro/workflows/_shared/review-merge/review-merge.fabro`:
   - line 489: change the pattern to `'[.]fabro/(hygiene[.]json|test[.]toml|ci[.]sh|scout[.]toml)'`;
   - line 167: the report sentence becomes `This pull request changes .fabro/hygiene.json, .fabro/test.toml, .fabro/ci.sh or .fabro/scout.toml, so it is not auto-merged.`;
   - line 438's comment: add `.fabro/scout.toml` and `(ADR 0027 D14)`.
   Check that no other copy of the pattern exists:
   `grep -rnF 'test[.]toml|ci[.]sh' .fabro ops` should list only line 489.
5. **`ops/test-task-gates.sh`**:
   - After case 13a, add **13b**: a PR that adds `.fabro/scout.toml` trips `config_changed` and
     lands in `blocking`, and `.fabro/scout.toml.example` does not. Copy 13a's shape.
   - Add a **`scout`** section at the end, before the summary, in the style of
     `merge-queue: prep`. Stub `gh`, `fabro-io` and `date` in `$WORK/scout-stubs/bin`. Drive
     the stubs with environment variables. Extract each node with
     `extract_from "$REPO_ROOT/.fabro/workflows/scout/workflow.fabro" <node>`, rewrite
     `/tmp/fabro` to the case dir, check `sh -n`, and run the node in a scratch git repo. The cases:
     1. `prep` OK: it routes `scout=run`, and `env.json`, `config.json` and `issues.json` exist
        (`issues.json` is a JSON array).
     2. `prep` when the issues list fails: exit 1, and `stop_reason` reads `could not list scout issues`.
     3. `prep` when `fabro-io` exits 2: `stop_reason` names `make deploy-images`.
     4. `ratchet` with no entries: the stub `.fabro/setup.sh`, which touches a marker file,
        is **not** run, and the output is `status: ok`.
     5. `ratchet` when `setup.sh` exits 3: exit 0, `status: skipped`, reason `setup.sh failed (exit 3)`.
     6. `flaky` fetches jobs only for runs with `run_attempt >= 2` (the stub logs each URL),
        and a failed log read leaves no `logs/<id>.txt`.
     7. `flaky` when the runs list fails: exit 1, and `stop_reason` reads `could not read Actions runs`.
     8. `file` with `filing: false` calls no `gh issue create`.
     9. `file` with `filing: true` and two actions: two creates, each with every label
        (check the stub's log), and `filed.json` holds two `filed` entries with the parsed numbers.
     10. `file` when the re-list already shows the second fingerprint: `filed`, then `raced`.
     11. `file` when the first create fails: one `failed` entry, and no second create.
     12. `summarize` with `stop_reason`: the line starts `Stopped at prep:`, and the last JSON
         carries `context_updates.scout_summary` with no `"` in it.

## Done when

- `make check` passes (the full one, with `ops/test-task-gates.sh`), including
  `check-graph-invariants.py` on the new package.
- You counted by hand: 9 nodes and 11 edges. Write `Scout (9 nodes, 11 edges)` in your report.
  Task 05 records it.
- One commit: `feat(scout): the scout package; config_changed covers scout.toml (scout 04, #32)`.
