# 06 · Wire the server and the hooks into all four workflows

Read `00-overview-and-contracts.md` first. The contract for this task is C6. Invoke
`/fabro-workflow` before you edit a `workflow.toml`.

## Change

In `backlog`, `pr-review`, `arch-review` and `issue-triage` `workflow.toml`:

1. The `[run.agent.mcps.io]` table from C6. If task 02 chose `shell`, leave it out.
2. The `io-stage` hook from C6, in every workflow. It runs for every agent stage, including
   stages that are not live. For those stages it writes `stage.json` and nothing reads it.
3. The `io-guard` hook from C6, only in `backlog` and `pr-review` (the two graphs that import
   `review-merge`, where `refute` is). Its matcher is `(^|[.])refute$`.
4. If the spike showed that a retry does not start a new visit: a `stage_retrying` hook,
   `sandbox = true`, `matcher = "^agent$"`, `script = "fabro-io reset"`.
5. Above each hook and above the MCP table, a `#` comment that says why, as the Discord hooks
   do: what it does, why `sandbox = true`, why its matcher is written that way, and
   `docs/stage-io/` for the contracts.

Check `startup_timeout` against the spike's check 2. Keep it at least 5 times the measured
p90, and 20 s or more.

## Nothing is live yet

No stage is live after task 05, so the server lists no tools and the guard finds no entry.
This task changes only the cost that every agent stage pays: one hook and one server start.
Measure that cost here, before any stage depends on it.

## Verification

1. `fabro validate` on all four workflows: the AGENTS.md baselines, unchanged.
2. `python3.11 -c 'import tomllib; ...'` on each `workflow.toml`.
3. `ops/fabro-io-manifest.py check`, the routing checker, `ops/test-task-gates.sh`.
4. Push, then watch the first run of each workflow (`fabro events <run> -p`):
   - every agent stage shows the `io-stage` hook with `proceed`;
   - `agent.tools.available` lists no `mcp__io__*` tool (no stage is live), and there is
     no MCP warning;
   - the time from `agent.session.started` to the first `agent.llm.started`, compared with
     the baseline from task 01, is 2 s or less more for each stage.
5. If an agent stage is blocked by `io-stage`, revert this commit first and diagnose second.
   A blocking hook that fails stops every run.
