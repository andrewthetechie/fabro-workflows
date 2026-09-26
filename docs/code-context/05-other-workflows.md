# 05 · Build the index in `pr-review`, `arch-review` and `issue-triage` too

Read `00-overview-and-contracts.md` first. The contracts for this task are C1, C2 and C4,
and the operator decision is 4. Invoke `/fabro-workflow` before you edit a graph.

## Why

The operator's rule is "use the index wherever we can" (ADR 0014 D3). All four workflows run
in the same per-repository profile images, so `fabro-code` is present in each of them. Each
graph builds the index in the first node where its checkout exists. The shared phases then
find it, whichever graph imported them.

## The fragment

The fragment is the same in each graph. It has no `#` and no backslash, and all of its output
goes to stderr or `/dev/null`:

```sh
grep -qx '.codegraph/' .git/info/exclude 2>/dev/null || echo '.codegraph/' >> .git/info/exclude
if command -v fabro-code >/dev/null; then fabro-code index >/dev/null 2>&1; fabro-code map >/dev/null 2>&1 || true; else echo 'failed: fabro-code not in image' > /tmp/fabro/code_index; fi
```

## Where it goes

| Graph | Node | Position | Why there |
|---|---|---|---|
| `pr-review/workflow.fabro` | `claim` | after `git rev-parse HEAD > /tmp/fabro/run_base_sha`, and before the routing `echo` | Before `gh pr checkout` the tree is `main`, not the PR. `claim` declares `output_schema="routing"`, so the fragment must print nothing to stdout. |
| `arch-review/workflow.fabro` | `prep` | before `echo ready` | `scan` is the first agent. `/tmp/fabro/arch` already exists by that point. |
| `issue-triage/workflow.fabro` | `acquire` | after its `mkdir -p /tmp/fabro` | `acquire` is the first node, and the triage phase's agents follow. `acquire` does not run under `set -e`. |

`pr-review`'s `rebase_check` and rebase agent change the tree after `claim`. That needs no
second build, because every query syncs first (C2 rule 1).

## Tests

For each of the three nodes, `ops/test-task-gates.sh` extracts the script and runs it
against a fixture, as it does for `backlog`'s nodes. With a stub `fabro-code` that prints to
stdout, the node's stdout is unchanged. For `claim`, stdout is still exactly one routing
object. With no `fabro-code` on `PATH`, the node still reaches its normal end.

## Acceptance

- `fabro validate` gives the same baselines: `PrReview (34 nodes, 73 edges)` with the one
  `pr_number` warning, `IssueTriage (13 nodes, 26 edges)` clean, and `ArchReview (21 nodes, 44 edges)` clean.
- The routing checker is clean. `./ops/test-task-gates.sh` passes.
