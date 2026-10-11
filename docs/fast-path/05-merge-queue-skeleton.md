# 05. The `merge-queue` package: skeleton, `prep`, the guard and the switches

## Outcome

`.fabro/workflows/merge-queue/` exists with a `workflow.toml` and a `workflow.fabro`. The
graph is `start → prep → inventory → decide → act → summarize → finish → exit`. `prep` is
complete: it resets the run's files, reads the repository, the base branch and the token's
login, reads both auto-merge switches, and ends the sweep early when an older `merge-queue`
run for the same repository is still active. `inventory`, `decide`, `act` and `summarize` are
placeholders that tasks 06-08 replace. Each placeholder prints one line and exits 0.

## Why

ADR 0024 D1, D3 rule 6, and D8 (the overlap and push rows). Nothing fires this package until
task 09 creates its automations, so the placeholders are safe on `main`. The package
gets its own task because its `workflow.toml` settings decide whether 576 runs a day push
branches, open PRs or take an agent slot.

## Change

Read 00: P10, C7, rules 4-6, the `merge-queue` graph shape, and the verified facts about
switches, `GET /runs` and the run id. Read 01's spike file (S4 gives the field paths of a
run row). Read `dependabot/workflow.toml` and `dependabot/workflow.fabro`'s `prep`, which are
the model.

### `workflow.toml`

- `[workflow] graph = "workflow.fabro"`.
- `[run.run_branch] enabled = true`, `push = false`. A `#` comment in the TOML says
  why: 576 runs a day must not push 576 branches, and `enabled = true` is what gives
  the run its id through the branch name.
- **No `[run.pull_request]` section**, and no model, fallbacks, `[run.agent.mcps.io]`,
  `FABRO_IO_MANIFEST`, `FABRO_AGENT_GUIDE`, `io-stage`, `git-guard` or `io-guard`. The graph
  has no agent stage. Read `ops/fabro-io-manifest.py` first. If `generate` or `check` require
  every package in `WF_ORDER`, add `merge-queue` there with no stages and confirm that
  `check` passes. Otherwise leave it out and add a comment at `WF_ORDER` that says why.
- `[run.integrations.github.permissions]`: copy `dependabot`'s block and add what the
  queue needs: `contents = "write"` (merge, rebase), `pull_requests = "write"` (labels,
  comments), `issues = "write"` (the issue's `Review` label), `actions = "write"` (rerun) and
  `checks = "read"`. Remove anything it does not use.
- `[run.environment.env]`: `FABRO_API_TOKEN = "{{ secrets.FABRO_API_TOKEN }}"`,
  `FABRO_API_URL = "{{ vars.FABRO_API_URL }}"` and
  `FABRO_AUTO_MERGE = "{{ vars.FABRO_AUTO_MERGE }}"`. No `id` key, and `{{ vars.X }}` only
  (invariants-graph, *Packages*).
- One hook now: `discord-failed` on `run_failed`, as in `dependabot`. Task 08 adds the
  summary hook.

### `workflow.fabro`

- `goal`: `Merge queue: merge the PRs the review-merge phase approved (label merge-on-green) once their checks are green, rebase the ones behind their base, and hand the rest to a human`.
- `default_fidelity="truncate"` and `stall_timeout="15m"`. The longest node is `act` at 8m,
  and R2 needs the stall timeout above every command timeout. No stylesheet. The graph has
  no cycle and imports nothing, so R1 does not apply. Write that in a comment.
- Timeouts: `prep` 2m, `inventory` 3m, `decide` 1m, `act` 8m, `summarize` 1m, `finish` 1m.
- Every command node's unconditional edge goes to `summarize`, as in `dependabot`
  (`docs/dependabot/00`, D24). `summarize -> finish -> exit`.

### `prep` (routing)

All files go under `/tmp/fabro/mq/`. `rm -rf /tmp/fabro/mq && mkdir -p /tmp/fabro/mq` first.

1. `REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)` and
   `BASE=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)`.
   `LOGIN=$(gh api user --jq .login)`. `RUN=$(basename "$(git rev-parse --abbrev-ref HEAD)")`.
   If any one is empty, write `stop_reason` and exit 1.
2. Write `env.json`: `{"repo","base","login","run","now": <date +%s>,"merge_cap": 5}`.
3. **The switches.** Host: `FABRO_AUTO_MERGE` exactly `1`. Per repository: the
   `pr-review-<repo>` row's `auto_merge=` token, read exactly as `pr_handoff` does (absent
   means on, any other present value means off, an unreadable API means off). Both on gives
   `switch=on`. Otherwise write the reason, for example
   `auto-merge is off for this repository (pr-review row)`, to `skip_reason`.
4. **The guard.** List the active runs with the paging of `list_active_runs`
   (`ops/scheduler/src/fabro_scheduler/fabro.py`). Keep rows whose workflow is
   `merge-queue` and whose target repository is `REPO` (S4's field paths), other than
   `RUN`. If one of them was created before this run, write
   `an older merge-queue run (<id>) is still active` to `skip_reason`. An API error writes
   `could not list active runs` to `skip_reason`. A sweep that cannot see the others does
   not act.
5. Print `{"context_updates":{"sweep":"run"}}` when `skip_reason` is absent, else
   `{"context_updates":{"sweep":"skip"}}`.

### Edges

```
start -> prep
prep -> inventory      [condition="outcome=succeeded && context.sweep=run"]
prep -> summarize
inventory -> decide    [condition="outcome=succeeded"]
inventory -> summarize
decide -> act          [condition="outcome=succeeded"]
decide -> summarize
act -> summarize
summarize -> finish
finish -> exit
```

`prep`'s skip path and its failure path both reach `summarize` through the unconditional
edge. `summarize` tells them apart by `skip_reason` and `stop_reason` (task 08).

## Acceptance

- `make check` passes, including `check-graph-invariants.py` on the new package.
- `make check-host`: `fabro validate` passes with no warning. Record
  `MergeQueue (<n> nodes, <m> edges)` in `AGENTS.md`'s baseline paragraph and in
  `ops/check-host.sh`'s expected counts.
- No live run in this task. Task 09 creates the rows with their schedules disabled, and task 10
  hand-fires the first run through a row's `api:manual` trigger, as `docs/dependabot/10` did.

## Tests

Add `merge-queue — prep` to `ops/test-task-gates.sh`, with `gh` and `curl` stubs:

1. Both switches on, no other run: `sweep=run`, and `env.json` has every key.
2. `FABRO_AUTO_MERGE=0`: `skip`, and the reason names the host switch.
3. The row's description says `auto_merge=false`: `skip`. With no token: `run`. With the
   API stub failing: `skip`.
4. An older active `merge-queue` run for the same repository: `skip`, and the reason names
   its id. An older run for **another** repository, or a newer one for this repository: `run`.
5. `gh api user` returns nothing: exit 1, and `stop_reason` is written.

## Depends on

01 (S4).
