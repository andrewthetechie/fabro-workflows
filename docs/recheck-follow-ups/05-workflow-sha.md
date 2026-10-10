# 05. Stamp `workflow_sha` on every API-created run (#13, item 3)

## Outcome
`GET /runs` and `run.created` show, for every run, the `fabro-workflows` commit its graph
came from.

## Why
Scheduler-dispatched runs show `resolved_sha` as absent, because only automation runs get
one. On 2026-10-09 the graph each run used was worked out by comparing dispatch times with
commit times on `main`. Three fixes landed inside that window, so the answer mattered.

## Change
1. `ops/scheduler/src/fabro_scheduler/fabro.py`, `FabroClient.dispatch`: pass
   `labels={"workflow_sha": sha}` to `build_run_intent`, and add `sha=%s` (first 12) to the
   dispatch log line (C5).
2. `ops/fabro-fire-backlog.sh` and `.fabro/workflows/backlog/scripts/fire-pr-review.sh`:
   after the clone, `SHA=$(git -C "$CLONE_DIR" rev-parse HEAD)`, and add
   `workflow_sha: $sha` to the `labels` object in each jq payload. Keep both scripts POSIX
   `sh` where they are now.
3. `docs/agents/server.md`: one line saying which label carries the graph version.

## Acceptance
- The scheduler's tests pass, with a new test that `dispatch` sends the clone's SHA as
  `labels.workflow_sha` (the clone is already injectable).
- `DRY_RUN=1` on both fire scripts prints a payload whose `labels.workflow_sha` equals
  `git ls-remote https://github.com/andrewthetechie/fabro-workflows main`.
- `make check` passes.

## Tests
`ops/scheduler/tests/`: the dispatch test above, which also asserts that `source` and `issue`
are still sent. The fire scripts: run each with `DRY_RUN=1` and assert the
label with `jq`.

## Depends on
Nothing. The scheduler change is live after `make deploy-scheduler`, and
`fabro-fire-backlog.sh` after `make deploy-scripts` (task 09). `fire-pr-review.sh` is live on
push: `~/bin/fabro-fire-pr-review.sh` is a wrapper that runs the copy on `origin/main`. Its
output contract (the last stdout line is the run id) must not change.
