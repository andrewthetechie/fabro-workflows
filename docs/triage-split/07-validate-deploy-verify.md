# 07 · Validate, deploy the scheduler first, then push the graphs, and watch

Read `00-overview-and-contracts.md` first. This task needs the host.

## Order

1. Run the offline gates: `./ops/test-task-gates.sh`, `uv run pytest` in `ops/scheduler/`,
   and the TOML check.
2. Container validation and the routing checker, for all four graphs (AGENTS.md
   "Validating"). The new baselines are the ones from task 04.
3. **Deploy the scheduler first**: rsync `ops/scheduler/`, then
   `docker compose up -d --build scheduler` (AGENTS.md, "Deploying"). This dates the
   draft-14 shakedown window again. Confirm in `docker logs fabro-scheduler` that the
   promoter and the closer run each minute with no errors, and do nothing while no
   `agent-held` or `agent-split` issue exists.
4. Commit to `main`, and push the graph, prompt and doc changes. `issue-triage` and
   `arch-review` pick them up on their next fire.

## First runs

Fire `issue-triage` by hand against a real `needs-triage` issue that looks large, before
the next scheduled `arch-review`:

- `plan` wrote `plan.json`, and `plan_gate` published `plan_status`.
- On `map`: the issue body has exactly one `## Proposed task map`, and the issue is `agent`.
- On `split`:
  - the children exist and are listed as sub-issues;
  - the first child is `agent` and the others are `agent-held`, with correct markers;
  - the parent is `agent-split` and not `agent`;
  - the Decisions section records the split.
- Let the first child's `backlog` run merge. Within a minute, the scheduler promotes the
  second child (`agent` and `priority`).
- After the last child merges, the parent closes as completed.

## Measure

When at least 10 issues have left triage through `plan`, compare with the ADR 0011 and
ADR 0014 baselines:

- **Split rate**: the share of `ready` issues that were split.
- **Child PR size and auto-merge rate**, against unsplit issues from the same period.
- **`decompose` median minutes**, on issues with a map against issues without one.
- **Map id reuse**: the share of `tasks.json` ids that appear in the issue's map.

Record the result as a dated line in ADR 0015's status.
