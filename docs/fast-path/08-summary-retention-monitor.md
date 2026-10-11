# 08. `merge-queue`: summary and Discord, run retention, and the ops tools that read runs

## Outcome

- `summarize` turns `results.json`, `skip_reason` and `stop_reason` into one summary, and
  the summary is empty for a quiet tick. Discord gets a `merge-queue-summary` message only
  when the tick changed something, and gets a repeated stop message at most once an hour
  per repository.
- A daily host job deletes `merge-queue` runs older than 7 days.
- `fabro-monitor.sh` and every other ops tool that reads a fixed number of runs ignore
  `merge-queue` runs. A stuck `backlog` run is then still found among 576 sweeps a day.

## Why

ADR 0024 D8, P9 and rule 6. Without these changes the queue works on its first day, and
then it floods Discord, grows the run store without limit, and blinds the stuck-run monitor
(`fabro-monitor.sh` reads one page of 20 runs).

## Change

Read 00: C10, rule 6, and the verified facts about Discord, `GET /runs` and the monitor. Read
the spike file, S4 and S5. Read `summarize` and `finish` in `dependabot/workflow.fabro`,
the `dependabot-summary` branch of `backlog/scripts/discord-notify.sh`, `ops/fabro-monitor.sh`
in full, and `ops/README.md` for how the existing sweepers are installed.

### `summarize` (routing, `timeout="1m"`) and `finish`

- `stop_reason` present: the summary is
  `Stopped at <node>: <reason>`, plus any results that were already recorded.
- `skip_reason` present: the summary is empty. A switch that is off and an overlapping sweep
  are normal states, not news. The reason goes to stdout, so the run's events still show
  it. **Exception:** `could not list active runs` is a fault, so it becomes a stop line.
- Otherwise one line per outcome class that has entries, in this order: `Merged #a, #b`,
  `Rebased #c`, `Reran CI on #d`, `Handed off #e (<reason, ≤80 chars>)`, `Failed #f
  (<detail, ≤80 chars>)`. `waiting` and `skipped_cap` entries appear only as a count, and
  only when another line exists, for example `Waiting: 4`.
- Join the lines with ` | `, as `dependabot`'s `summarize` does, and publish
  `{"context_updates":{"mq_summary":"<text>"}}`. Print only that object as JSON.
- `finish` is `echo done`, the hook target, as in `dependabot`.

### Discord

- `workflow.toml`: add `discord-summary` on `checkpoint_saved`, matcher `^finish$`, script
  `/storage/scripts/discord-notify.sh merge-queue-summary`.
- `discord-notify.sh`: add `merge-queue-summary` beside `dependabot-summary`. Read
  `mq_summary` the same way the script reads `dep_summary`. If it is empty, exit 0 with no
  message. If it starts with `Stopped at `, compare it with
  `/storage/state/merge-queue-<repo>.last` (`<epoch> <text>`). Skip it when the text is the
  same and under 3600 s old. Otherwise post it and rewrite the file. Create
  `/storage/state/` if it is missing. The script is POSIX `sh` on Alpine with `wget` and no
  `jq` (its header). Message: `🔀 fabro merge queue${subject}\n<lines>`.
- `merge-queue` runs also fire `discord-failed` from `run_failed`. A command failure routes
  to `summarize`, so a run fails only on a fabro-level error. Leave that hook as it is.

### Retention

Add `ops/fabro-run-prune.sh`. It runs
`docker compose exec -T fabro fabro system prune --workflow merge-queue --older-than 7d --yes`
from `~/fabro` and logs the count and bytes that it freed. If S5 found no `--workflow` flag on
0.362, the script instead lists `merge-queue` runs through `GET /runs` (paged, terminal
statuses only) and deletes each one older than 7 days with `DELETE /runs/{id}`, at most 2000
per pass. Install it as a daily host cron job, the way the existing sweepers are installed.
Add a `make deploy-*` step, or extend the existing one, so the host matches the repo
(`docs/agents/deploying.md`). Never prune a non-terminal run.

### `fabro-monitor.sh`

- **C3** (idle): exclude `merge-queue` from the enabled-schedule count, as `arch-review` is
  excluded. Find the newest run that is not `merge-queue`, paging `GET /runs` with
  `page[limit]=100` up to 5 pages, instead of reading one page of 20.
- **C4** (stuck): read the non-terminal runs with the status filters and paging of
  `list_active_runs`, instead of the newest page. Then a stuck run cannot fall off the
  page. Give `merge-queue` its own limit: a sweep that is not terminal after 30 minutes is
  stuck (`MQ_STUCK_MINUTES=30`).
- Update the header comment's notes on the page size, the run rate and the accepted gap.

### Other tools that read runs

Run `grep -rn '/runs' ops --include='*.sh' --include='*.py'`. For each tool, decide one of:
**exclude** `merge-queue` (any tool that counts, ranks or measures factory runs, such as
`fabro-export-runs.sh`'s index and `fabro-agent-tools.py`), **unaffected** (a tool that reads
one run by id, or the scheduler, which reads only its own leases), or **include** (a tool
whose job is to list every run). Record each decision in a table in this task's commit
message. `ops/fabro-sandbox-sweep.sh` must still reap a `merge-queue` sandbox.

## Acceptance

- `make check` passes.
- `make deploy-notify` and the retention deploy step are listed in task 09's deploy
  sequence. This task changes host scripts and does not deploy them.

## Tests

1. `merge-queue — summarize` in `ops/test-task-gates.sh`:
   - a `results.json` with 2 merged, 1 handed off and 3 waiting gives
     `Merged #1, #2 | Handed off #3 (…) | Waiting: 3`.
   - only `waiting` entries give an empty summary.
   - `skip_reason` `auto-merge is off …` gives an empty summary.
     `could not list active runs` gives a `Stopped at prep:` line.
   - a `stop_reason` from `inventory` gives `Stopped at inventory: …`.
2. `discord-notify.sh`, run with a fake webhook endpoint (a stub `wget` that logs) and a
   temporary `/storage`, as the existing notify tests do, or add such a test if none exists:
   - an empty `mq_summary` produces no `wget` call.
   - the same `Stopped at` text twice within an hour produces 1 call. After 3601 s it
     produces a 2nd call.
   - a `Merged` summary always produces a call.
3. `fabro-monitor.sh` (`ops/tests/`, or a shell fixture): a runs fixture with 30 newer
   `merge-queue` rows and one `backlog` row that has been non-terminal for 5 hours fires C4.
   A fixture whose only recent runs are `merge-queue` rows fires C3 once its other
   conditions hold.
4. `ops/fabro-run-prune.sh` with a stubbed `docker`: the command line has `--workflow
   merge-queue --older-than 7d --yes`. In fallback mode, it never issues `DELETE` for a run
   whose status is not terminal.

## Depends on

07, and 01 (S4 and S5).
