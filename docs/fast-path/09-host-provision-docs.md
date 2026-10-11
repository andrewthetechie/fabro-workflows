# 09. Host capacity, the automations, and the docs

## Outcome

- The host runs `max_concurrent_runs = 6`.
- Four `merge-queue-<repo>` automations exist, each with an `api:manual` trigger and a
  **disabled** schedule trigger (P4's expressions). Task 10 enables the schedules.
- The notify script, the retention job and the monitor from task 08 are deployed, and the
  host matches the repo.
- `AGENTS.md`, the invariants files, `docs/agents/server.md`, `ops/README.md`,
  `docs/USER-GUIDE.md` and `docs/README.md` describe the fast lane and the queue.

## Why

ADR 0024 D1 and D8, P4 and P8. Changes to `.fabro/workflows/**` are live on push, but
everything in this task is server state or `ops/`, and needs a deploy.

## Change

Read `docs/agents/deploying.md`, `docs/agents/invariants-host.md`, `ops/README.md` (the cap,
the scheduler's drain control, the sweepers) and `ops/provision-server-state.sh`. Read the
spike file, S6 and S7.

### 1. The cap: 4 → 6

S7 must show room for two more sandboxes of the largest environment. If it does not, stop
and report. Do not raise the cap.

1. Edit `ops/settings.toml.example`: `max_concurrent_runs = 6`. Update its comment to cite
   ADR 0024 D8.
2. Pick a drain window. Drain both coder pools on the scheduler page, so no new `backlog`
   run starts. Wait until `GET /runs` lists no active run (the restart fails every in-flight
   run). `dependabot` runs at 05:00-06:00 host time, so avoid that window.
3. Edit the overlay at `/storage/.home/settings.toml` in the container, then
   `docker compose restart fabro`.
4. Verify the enforced value as `invariants-host.md` requires: the container's `StartedAt`
   is later than the overlay's mtime (`ops/README.md` has the command). `GET /settings`
   alone proves nothing.
5. Undrain the pools.

### 2. The environment

Use each repository's own environment (`python`, `python-node`, `ts`, `rust-node`), as the
`dependabot` rows do, unless S6 showed that a tick spends more than 60 s before its first
node. In that case, create one environment `light` from the smallest image, with `cpu=1`
and `mem=1GB`, and record the decision and the S6 numbers in 00 and in ADR 0024 D8.

### 3. The automations

In `ops/provision-server-state.sh`:

- Add `merge-queue` to the list of workflows in the `provision_automation` comment.
- Add four rows after the `dependabot` block, each with an `api:manual` trigger and a
  schedule that is created **disabled** (`schedule_enabled` false):
  `merge-queue-jelly-swipe` `0-59/10 * * * *`, `merge-queue-lawncare-saas` `2-59/10 * * * *`,
  `merge-queue-womens-fantasy-sports` `5-59/10 * * * *`, `merge-queue-writers-app`
  `7-59/10 * * * *`. The row description is `merge-queue sweep for <repo>`. The switches
  live on `pr-review-<repo>` (P10), so this row carries no `auto_merge` token. Say so in a
  comment.
- The drift check (`check_triggers`) compares the expressions. It does not compare the
  enabled flag (its own comment explains why).

Run `make provision`. Confirm the four rows with
`GET /automations | jq '.data[] | select(.workflow == "merge-queue")'`.

### 4. Deploy

From a clean checkout at `origin/main`: `make deploy-notify`, the retention step from task
08, then `make verify-host`. Confirm that the host's `discord-notify.sh` and
`fabro-run-prune.sh` match the repo, and that the cron entry exists.

### 5. Docs

- `AGENTS.md`: "Six production Fabro workflows". The layout row lists `merge-queue`, and
  says that `_shared/review-merge/` has a queue mode that only `backlog` uses. Add the
  baselines from tasks 02-05. Add one line under *The server* on how a `merge-queue` run
  fires (its schedules and the `api:manual` trigger).
- `docs/agents/invariants-backlog.md`: rows for the lane (C2, the `next_task` reset, the
  `!=fast` edge conditions, the demotion order of task 04, and `fast-path` never on a
  split child).
- `docs/agents/invariants-merge.md`: a *Queue mode* section with rules 1 and 2 of 00, and
  the `report_queued` order (comment, then label).
- A new `docs/agents/invariants-merge-queue.md` with rules 3-6, the C7 chain, the "never
  `--admin`" rule from task 07, and the monitor exclusion. Add it to `AGENTS.md`'s list of
  invariants files.
- `docs/agents/server.md`: one paragraph on `merge-queue` runs (who fires them, the guard,
  the pause: disable the schedules with `ops/fabro-automation-schedule.sh`, or turn a
  switch off).
- `ops/README.md`: the package, `fabro-run-prune.sh`, the new monitor behaviour and the cap.
- `docs/USER-GUIDE.md`: how to send an issue down the fast path (add `fast-path` with
  `agent`), what `merge-on-green` on a PR means, and how to stop one PR (remove the label,
  or push a commit, which cancels the approval).
- `docs/README.md`: update this series' row.
- ADR 0023 and ADR 0024: keep **proposed** until task 10 has the first live merge.

## Acceptance

- The container's `StartedAt` is after the overlay edit, and the run that task 10 makes
  first sees the cap of 6.
- `make provision` reports no drift. The four rows exist, with their schedules disabled.
- `make check` passes. The host matches the repo for every deployed file.
- A dated section is appended to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` (the cap
  change, the drain window, the rows, the deploy), with no secret in it.

## Tests

`make check`. The provision script has no offline test. Its acceptance is the live drift
check above.

## Depends on

02-08, and 01 (S6 and S7).
