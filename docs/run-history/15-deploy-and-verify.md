# Deploy and verify on the host

## Tracer-Bullet Outcome
The scheduler on `10.10.0.32` runs the new image, `run_history` exists on the live
`scheduler-data` volume with all fourteen columns, `http://10.10.0.32:32280/history`
renders, and the first real release after the deploy writes a row an operator can read.

## User Story
As the operator, I want the run-history page live on the host with its first real row
verified, so that the next time I ask "what has the factory been doing" the answer is a
URL.

## Description
Deploy the scheduler and confirm the four things that a green test suite cannot: that the
table appeared on a database that already existed, that the page serves, that a real
release writes a real row, and that the PR lookup resolves against real GitHub.

Two properties of this deploy are worth stating before it is run.

**It restarts the scheduler container, and that is safe for in-flight runs.** Startup
recovery adopts the leases it already holds rather than forgetting them — the draft-14
re-arm demonstrated exactly this, with two leases adopted and their `dispatched_at`
preserved. It does **not** restart `fabro`; `up -d --build scheduler` names one service,
and a fabro restart would fail every in-flight run.

**It re-dates draft 14's shakedown window**, which is measured from the scheduler
container's `StartedAt`. Note the new time in task 17's log entry.

## Context Pack
- Source decisions: ADR 0008 in full. This is the task that makes the ADR true, and task 16
  is what flips it to `accepted`.
- Repo facts: the deploy sequence is AGENTS.md's, under *Deploying to the server after a
  merge to `main`*. The scheduler tree is rsynced to `~/fabro/scheduler/` because
  `build.context` resolves relative to the compose file, and the rsync is load-bearing
  before the build — `--build` alone happily rebuilds whatever was last copied there. The
  running container is named `fabro-scheduler` (not `fabro-scheduler-1`), and fabro's is
  `fabro-fabro-1`.
- Non-goals: no `fabro` restart, no settings-overlay edit, no automation change, no
  24-hour observation window. The verification here is minutes, because a row appears on
  the next release.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files: none in the repository. This task changes host state only.

- Interfaces and names: the deploy, exactly as AGENTS.md specifies:

  ```sh
  cd ~/Documents/code/fabro-workflows && git pull --ff-only

  rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
    ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/
  ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d --build scheduler'
  ```

  `up -d --build scheduler` names one service and leaves `fabro` alone. Do not run a bare
  `docker compose up -d`.

- Verified external contracts: the four checks, in order. Every one of them has an exact
  expected output.

  **1. The container restarted and is healthy.**

  ```sh
  ssh andrew@10.10.0.32 'docker inspect -f "{{.State.StartedAt}}" fabro-scheduler'
  ssh andrew@10.10.0.32 'docker ps --format "{{.Names}}\t{{.Status}}" | grep scheduler'
  ```

  `StartedAt` must be later than the deploy. Status must read `Up ... (healthy)`.

  **2. The table exists on the live database, with all fourteen columns.**

  This is the check that cannot be made anywhere else: the volume already holds a
  `scheduler.db` with seven tables, and `CREATE TABLE IF NOT EXISTS` is what has to have
  added the eighth. The image has no `sqlite3` binary, so ask Python:

  ```sh
  ssh andrew@10.10.0.32 'docker exec fabro-scheduler python -c "
  import sqlite3
  c = sqlite3.connect(\"/data/scheduler.db\")
  print([r[1] for r in c.execute(\"PRAGMA table_info(run_history)\")])
  "'
  ```

  Expected, exactly:

  ```
  ['run_id', 'coder_pool', 'repo', 'issue_number', 'dispatched_at', 'finished_at',
   'kind', 'reason', 'category', 'requeue_attempt', 'pr_lookup', 'pr_number',
   'pr_url', 'merged']
  ```

  An empty list means the table is missing and the deploy did not take. Do **not** create
  it by hand — find out why `SCHEMA` did not run.

  **3. Both surfaces serve.**

  ```sh
  curl -s -o /dev/null -w '%{http_code}\n' http://10.10.0.32:32280/history
  curl -s http://10.10.0.32:32280/api/history | head -c 200
  ```

  Expected: `200`, and `[]` if nothing has been released since the deploy. Open
  `http://10.10.0.32:32280/history` in a browser and confirm the nav link from the queue
  page reaches it.

  **4. The first real release writes a real row.**

  Wait for a dispatched run to reach a terminal state — `docker logs -f fabro-scheduler`
  shows `reconcile: <repo>#<issue> run <id> terminal (...)` — then:

  ```sh
  curl -s 'http://10.10.0.32:32280/api/history?limit=5' | python3 -m json.tool
  ```

  The row must carry a real `coder_pool` (`coders-a` or `coders-b`), the repo and issue the
  run worked, a `finished_at`, and a `kind`. If that run opened a PR, `pr_lookup` must be
  `found` with a real `pr_number` and `pr_url` — and if it auto-merged, `merged: true`.
  A `pr_lookup` of `failed` on the first real row means the token lacks
  `pull_requests: read`; check with:

  ```sh
  ssh andrew@10.10.0.32 'docker logs --since 10m fabro-scheduler | grep "could not resolve the PR"'
  ```

- Behavior rules:
  - Run the offline suite before the rsync. `cd ops/scheduler && uv run pytest` must be
    green on the machine the deploy is launched from.
  - The rsync comes **before** the build, always.
  - Do not restart `fabro`. Do not run a bare `docker compose up -d`.
  - Do not hand-create `run_history`. If check 2 fails the deploy is wrong, and a
    hand-made table would hide it and risk a column mismatch that no test can catch.
  - If check 4 shows `pr_lookup: "failed"` on every row, stop and fix the token scope
    before task 16 — a history whose PR column is permanently unknown is worse than no
    column, because it looks like data.

- Error and security rules: the scheduler reads its credentials from
  `~/fabro/scheduler.env`, which is not in this repository and is not touched here. Do not
  echo `GITHUB_TOKEN` or `FABRO_API_TOKEN` during verification; the checks above read only
  the database and the HTTP surface.

  **Rollback** is a rebuild of the previous commit, because nothing here migrates
  destructively: the new table is additive and the old code simply ignores it. There is no
  need to drop `run_history` to roll back.

  ```sh
  cd ~/Documents/code/fabro-workflows && git checkout <previous-sha> -- ops/scheduler
  rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
    ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/
  ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d --build scheduler'
  ```

## Acceptance Criteria
- [ ] `cd ops/scheduler && uv run pytest` is green before the rsync.
- [ ] `fabro-scheduler`'s `StartedAt` is later than the deploy, and status is `(healthy)`.
- [ ] `fabro-fabro-1`'s `StartedAt` is **unchanged** — fabro was not restarted.
- [ ] `PRAGMA table_info(run_history)` on `/data/scheduler.db` lists the fourteen columns
      in order.
- [ ] `GET /history` returns `200` and renders in a browser.
- [ ] `GET /api/history` returns `200` and valid JSON.
- [ ] The nav link on the queue page reaches `/history`, and the one on `/history` reaches
      the queue.
- [ ] At least one row appears after a real release, carrying a real `coder_pool`, repo,
      issue and `kind`.
- [ ] If that run opened a PR, `pr_lookup` is `found` with a real `pr_number` and `pr_url`.
- [ ] `docker logs --since 10m fabro-scheduler | grep -c "could not resolve the PR"` prints
      `0`.
- [ ] Any in-flight lease at deploy time was **adopted**, not released: its
      `dispatched_at` in `GET /api/pools` is unchanged across the restart.

## Test Expectations
There is no test runner for this task; the acceptance criteria above are the test, and each
carries its exact command and expected output.

One thing to capture for task 17 while it is easy, because it is what the shakedown window
is measured from:

```sh
ssh andrew@10.10.0.32 'docker inspect -f "{{.State.StartedAt}}" fabro-scheduler'
```

And the pool state immediately before and after the deploy, to evidence the adoption
criterion:

```sh
curl -s http://10.10.0.32:32280/api/pools | python3 -m json.tool
```

## Dependencies
- Blocked by: `Record the run fabro lost`, `Guard: the orphan path writes no row`,
  `Resolve the PR at release`, `Add GET /api/history`, `Add sortable header links`,
  `Add nav links between the queue and the history`, `Add derived final-state labels`
- Why blocked: this deploys the whole feature. Every one of those supplies a behavior the
  acceptance criteria check on the host.
- Blocks: `Correct the docs and accept ADR 0008`, `Append the deployment-log section`

## Labels
`chore`, `scheduler`, `priority:high`

## Estimate
Small

## Risk
3 - A container restart on the service that owns admission to both coder instances. The
restart itself is routine and recovery adopts live leases, but this is the first time the
new release path runs against a real database and real GitHub, and a fault in it holds a
coder lease.

## Validator Stopping Point
Every acceptance criterion above passes on the host, with at least one real row visible at
`http://10.10.0.32:32280/history`.
