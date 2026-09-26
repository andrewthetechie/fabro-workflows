# 04 · Cutover: stop, back up, drop run history, rewrite the overlay, start 0.362

## Outcome
The production fabro server runs `0.362.0-nightly.0`, healthy, with its secrets, variables,
environments and automations intact and an empty run history. All four workflow packages
validate as finding R4, every stylesheet model resolves and answers, and the coder scheduler
is running again and dispatching `backlog` runs.

## Why
This is the upgrade itself. Tasks 01–03 exist so this task is a repeat of a rehearsed
sequence. Decision 3 says no drain and no canary phase: stop, cancel, work, start.

## Read first
`00-overview-and-contracts.md`, all of it. `~/fabro-archive/0354/REVERSAL.md` on the host
(task 01). Task 02's report: it must say all acceptance criteria passed. If it does not, do
not start.

## Preconditions (check, do not assume)
- Task 01's archive exists and `sha256sum -c SHA256SUMS` passes in `~/fabro-archive/0354/`.
- Task 02 passed, and you have its `VACUUM` timing.
- Task 03 is deployed: `docker inspect fabro-scheduler --format '{{json .Mounts}}'` has no
  `docker.sock`.
- `git pull` in this checkout, and `main` has no commit deleting `[run.meta_branch]` (that is
  task 05, after this one).

## Steps (host: `ssh andrew@10.10.0.32`, `cd ~/fabro`)

Keep a timestamped log of each step's output for your report.

### 1. Stop the scheduler
```sh
docker compose stop scheduler
```
Nothing new is dispatched from here on. Issues it holds stay labelled `agent-in-progress`,
and its receipt scan requeues them when it restarts in step 11.

### 2. Cancel every non-terminal run
Read the dev token inside the host (`docker exec fabro-fabro-1 cat /storage/server.dev-token`)
into a shell variable, never into a file in the repo. List runs and cancel each one that is not
`succeeded`/`failed`/`dead`:
```sh
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:32276/api/v1/runs?page%5Blimit%5D=100" \
  | jq -r '.data[] | select(.lifecycle.status.kind as $k | ($k != "succeeded" and $k != "failed" and $k != "dead")) | .id'
# for each id:
curl -s -X POST -H "Authorization: Bearer $T" "http://127.0.0.1:32276/api/v1/runs/$ID/cancel"
```
Check the field path against one real response before trusting the `jq` filter
(`AGENTS.md` records that the run list's shapes surprised earlier readers). Page through with
`meta.has_more` if it is set. Record every cancelled run id, its workflow and its issue label.
This includes runs parked on `human_rescue` and any `arch-review`/`issue-triage` run.

Wait until `docker ps --filter name=fabro-run- --filter status=running` is empty (fabro
stops a sandbox when its run ends), up to 3 minutes. `docker stop` any stragglers.

Known after-effects, all accepted: open PRs of cancelled runs stay open, issues keep
`agent-in-progress` (requeued in step 11), and a `triage-in-progress` claim expires after 24 h
(ADR 0012 D6).

### 3. Stop fabro
```sh
docker compose stop fabro
```

### 4. Stopped-state volume tarball
```sh
docker run --rm -v fabro_fabro-storage:/s -v ~/fabro-archive/0354:/a alpine:3 \
  tar -czf /a/fabro-storage.tar.gz -C /s .
cd ~/fabro-archive/0354 && sha256sum fabro-storage.tar.gz >> SHA256SUMS && ls -la
```
This is the reversal point in `REVERSAL.md`. Do not continue until it exists and is non-empty.

### 5. Drop run history (contract C3)
In a helper container with the volume mounted and `apk add sqlite`, run C3 against
`/s/db/fabro.sqlite3`. `run_session_records` does not exist on this 0.354 database, so do not
reference it. Then `VACUUM` (task 02 measured how long it takes). Verify:
`SELECT (SELECT count(*) FROM runs),(SELECT count(*) FROM run_events),(SELECT count(*) FROM automations),(SELECT count(*) FROM secrets);`
prints `0|0|16|5`. If the automation or secret count differs from task 02's copy, stop and
explain before continuing.

### 6. Rewrite the overlay (contracts C1 and C2)
In a helper container: copy `/s/.home/settings.toml` to `/s/.home/settings.toml.0354`, apply
C1's `sed`, append C2's block, then `diff` the two. The diff must equal task 02's diff.
`chown 1000:1000` the file and keep its mode.

### 7. Repin and start 0.362
```sh
cd ~/fabro
sed -i 's/^FABRO_VERSION=.*/FABRO_VERSION=0.362.0-nightly.0/' .env
```
Also replace the comment above that line (it explains the 0.357 pin) with one line:
`# 0.362.0-nightly.0 since <date>: docs/fabro-upgrade/ in fabro-workflows.`
```sh
docker compose pull fabro && docker compose up -d fabro
sleep 45 && docker compose ps
docker compose logs --tail 60 fabro | grep -v -i 'slatedb\|slow statement'
docker exec fabro-fabro-1 fabro --version        # 0.362.0-nightly.0 (a192bce ...)
```
Expect `Activated SQLite run history source_runs=0 … target_runs=0`, then `API server
started`, and `healthy` in `ps`. `docker logs --since 2m fabro-fabro-1 | grep -c "Rejected
reloaded"` prints `0`.

### 8. Server state readback
With the token: `GET /api/v1/automations` (16 rows, and the four `arch-review-*` rows'
schedule triggers `enabled`, the rest as before), `GET /api/v1/environments` (5),
`docker compose exec -T fabro fabro variable get FABRO_AUTO_MERGE` (present, unchanged value),
and the overlay itself (`grep -A2 '^\[run.git.author\]' /storage/.home/settings.toml`).
`GET /api/v1/settings` cannot confirm C2: on 0.362 it returns only the `server.*` layer.
The readback is on the first run, in step 11.

### 9. Graph gates (`AGENTS.md`, "Validating")
From the Mac: `rsync -a --delete .fabro/ andrew@10.10.0.32:/tmp/check/`, then on the host
`docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check`.
- `fabro validate` of all four `workflow.toml`: output equals finding R4.
- `scp ops/check-routing-schemas.py andrew@10.10.0.32:/tmp/` then
  `cd ~/fabro && python3 /tmp/check-routing-schemas.py /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro`:
  exit 0.

### 10. Every stylesheet model answers
```sh
for m in coders-a coders-b long-context glm-5.3 glm-5.3-flash kimi-k3 kimi-for-coding; do
  docker compose exec -T fabro fabro model test -m "$m"
done
```
Each must pass **and** name the provider from finding R5. `coders-a`/`coders-b` are idle now
because every run was cancelled, so a timeout here is a real fault, not congestion. Also run
`docker compose exec -T fabro fabro preflight` once for `arch-review` (it needs no inputs) as
an end-to-end check of the stylesheet, catalog and environment resolution.

### 11. Start the scheduler
```sh
docker compose up -d scheduler
sleep 30; curl -s http://127.0.0.1:32280/health
docker logs --since 1m fabro-scheduler | tail -40
```
`/health` must report `fabro.configured: true`. The startup reconcile finds every lease's run
gone (the history was dropped) and releases it. The receipt scan finds the
`agent-in-progress` issues from step 2 and requeues them. Within a few minutes
`GET /api/pools` shows new leases, with new run ids, on both boxes. Confirm one new run is
visible in fabro (`GET /api/v1/runs/<id>`) and advancing past `claim` and `prep`
(`ops/fabro-run-status.sh <id>` from the Mac).
Confirm C2 on that run: `GET /api/v1/runs/<id>/settings` contains
`"author":{"name":"andrews-ai-agent","email":"andrews-ai-agent@users.noreply.github.com"}`,
and `fabro events <id> -p` prints `Git identity: andrews-ai-agent <…> explicit`.

### 12. Ops tooling spot-check on 0.362
- `ops/fabro-run-status.sh <new run id>` prints stage and progress (it uses `fabro events -p`
  and `GET /runs/{id}`).
- Run the monitor once by hand on the host (`~/bin/fabro-monitor.sh`, the way its cron does)
  and confirm no false alarm. Its API reads (`/system/info` `.runs.total`, `/automations`,
  `/runs`) are unchanged in 0.362.
- `discord-notify.sh` is unchanged and still at `/storage/scripts/` (it lives in the volume,
  which survived). It fires on the first real hook.

## If the server does not start
Read the error. A catalog or settings message is fixed in the overlay (steps 6–7) and fabro
restarted. A migration or activation message means C3 missed a table: fix it on the database
and restart. Only if no data or config change gets 0.362 running (decision 8) do you follow
`REVERSAL.md`. Record exactly why in the report.

## Acceptance criteria
- Steps 7–11 show all expected outputs. Both coder boxes hold a new lease on 0.362 within
  10 minutes of step 11.
- `~/fabro-archive/0354/fabro-storage.tar.gz` exists, and its checksum is recorded.
- `~/fabro/.env` pins `0.362.0-nightly.0`.

## Report
The cancelled-run list, the `VACUUM` duration and new database size, the overlay diff, the
startup log excerpt, the validate/routing/model-test outputs, and the first two new run ids per
box (task 06 watches them).
