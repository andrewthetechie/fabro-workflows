# 04 · Build the index in `prep`, and render the map again in `next_task`

Read `00-overview-and-contracts.md` first. The contracts for this task are C1 and C4. Invoke
`/fabro-workflow` before you edit the graph.

## Change to `.fabro/workflows/backlog/workflow.fabro`

**`prep`**: after `./.fabro/setup.sh`, add these lines. Setup must run first, because it can
create or remove files that the index should see.

```sh
grep -qx '.codegraph/' .git/info/exclude 2>/dev/null || echo '.codegraph/' >> .git/info/exclude
if command -v fabro-code >/dev/null; then fabro-code index; fabro-code map || true; else echo 'failed: fabro-code not in image' > /tmp/fabro/code_index; fi
```

`prep` runs with `set -e`. Without the `command -v` guard, an image built before task 02
would exit 127 on this line and fail every run's `prep`. The images are rebuilt by hand,
so they can lag behind `main`. `fabro-code index` never exits non-zero (C2), and `map` has
`|| true`. So an index failure cannot fail `prep`. That is ADR 0014 D3: the index makes
agents faster, but correctness does not depend on it.

**`next_task`**: after the mainline-merge `if … fi`, and before the final `echo` of the
routing object on **both** branches, render the map again:

```sh
fabro-code map >/dev/null 2>&1 || true
```

(A missing `fabro-code` here is harmless. `next_task` does not run under `set -e`, and
`|| true` absorbs the 127.)

It must not print to stdout. Fabro scans a routing node's stdout for `context_updates`, and
extra lines there are a risk. On the merge-failed branch the tree is clean at the pre-merge
state, so that map is correct for the tree that `resolve_merge` starts from.

Also add `rm -f /tmp/fabro/task-context.md` next to the existing
`rm -f /tmp/fabro/improve_result.json`. Task 07 needs it, and it belongs to the same
delete-before-write group.

Put any explanation in `//` comments above the nodes, never as `#` in the script (AGENTS.md
invariant).

## Tests

In `ops/test-task-gates.sh`, extend the `next_task` fixture: after it runs,
`/tmp/fabro/task-context.md` is absent, and stdout still parses as exactly one routing
object. Stub `fabro-code` on `PATH` with a script that prints to stdout, to prove that
`next_task` discards that output.

Add a `prep` fragment test for the exclude line. Run it twice, and the result is still
exactly one `.codegraph/` line. Add a second fragment test with **no** `fabro-code` on
`PATH`, under `set -e`: the fragment exits 0 and writes `failed: fabro-code not in image`.

## Acceptance

- `fabro validate` gives the same baselines as before: `Backlog (65 nodes, 151 edges)`, with the one
  `issue_number` warning.
- The routing checker is clean. `./ops/test-task-gates.sh` passes.
- `sh -n` passes on both edited scripts.
