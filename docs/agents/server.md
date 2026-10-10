# The server: firing runs and the coder scheduler

Moved verbatim from `AGENTS.md` on 2026-10-09. `AGENTS.md`, *The server*, has ssh and the CLI.

## Firing a run by hand

The graph a run used is its `workflow_sha` label: the `fabro-workflows` commit the run's clone came from. The scheduler, `fabro-fire-backlog.sh` and `fire-pr-review.sh` all set it (ADR 0020, #13), so a recheck reads it from `fabro-export-runs.sh`'s index rather than comparing dispatch times with commit times.


```sh
~/bin/fabro-fire-pr-review.sh andrewthetechie/jelly-swipe 123
```

That helper registers the `pr-review` package, creates the run and **starts** it —
three calls, because `POST /runs` creates a `submitted` run that never executes and
never reports anything.

The obvious-looking curl does **not** work and must not be reconstructed from the API
surface: it was `POST /automations/pr-review-<repo>/runs` with a JSON body carrying
`inputs.pr_number`. That endpoint declares **no request body** — it fires the
automation's enabled API trigger and drops whatever is sent. The `inputs` never
arrive, so compilation then fails on `{{ inputs.pr_number }}` in `validate_input`
and the call returns `422 run_compile_invalid` having created nothing. That is exactly
what the node's deliberate "`pr_number` unbound" validation warning exists to catch.
Binding `[run.inputs] pr_number` would silence the warning and turn a no-input fire
into a review of PR #1. Send no body to that endpoint, or use the helper above.

**Since draft 10, firing `backlog` that way does not work either**, and the paragraph
above is exactly why. `backlog` used to take no inputs — `acquire` chose its own issue
— so the bodyless automation fire was correct for it. It now requires
`issue_number`, deliberately unbound in `workflow.toml` for the same reason
`pr_number` is, so the automation endpoint drops the input it cannot carry and the run
dies at compile with `422 run_compile_invalid`, having created nothing. Use the helper,
which does the three-POST sequence with the input attached:

```sh
~/bin/fabro-fire-backlog.sh andrewthetechie/jelly-swipe 123
```

The `backlog-<repo>` automations still exist and their rows are still where the
per-repo `environment_id` is looked up; what changed is that firing one by hand is no
longer a way to start work. **Their schedules are off** (draft 13, 2026-09-19), so
nothing starts them on a timer either: the coder scheduler is the only producer of a
`backlog` run, and `fabro-fire-backlog.sh` is the escape hatch for when it is down.
`ops/fabro-automation-schedule.sh <automation-id> on|off` is the switch, and it is the
only undo — turning one back on puts a second admission controller on the same two
coder boxes, which is what the scheduler exists to prevent.

## The coder scheduler (`ops/scheduler/`)

The coder scheduler: an external service that owns admission to the two llama.cpp instances, because fabro's own queue is FIFO-on-creation with no priority and no per-pool concurrency (ADR 0005, ADR 0006). Draft 14's 24-hour shakedown window restarts with the container, so its acceptance criteria are pending, not unstarted. Do not trust a start time written here: every `docker compose up -d --build scheduler` re-dates the window. Read it with `docker inspect -f '{{.State.StartedAt}}' fabro-scheduler`. `ops/test-task-gates.sh` covers `claim` and `mark_stuck`, so the shell-level regression that killed the first bring-up (`5fa974d`) now fails offline. The four `backlog-<repo>` schedules are off, so the scheduler is the only producer of `backlog` runs. `ops/fabro-automation-schedule.sh` turns them back on, and `ops/fabro-fire-backlog.sh` is the manual escape hatch. Its LAN page is `http://10.10.0.32:32280/`. The operator quickstart (the page, the three controls, changing repo priority, the two monitor conditions) was `docs/scheduler/OPERATING.md`, removed in `d5cd750`: `git show d5cd750^:docs/scheduler/OPERATING.md`.

`ops/README.md` has the environments and automations tables, which schedules are
enabled, host rebuild, the profile images, and the branch sweeper.
