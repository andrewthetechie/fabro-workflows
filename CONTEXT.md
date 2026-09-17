# Fabro Workflows

Three production Fabro workflow packages (`backlog`, `pr-review`, `issue-triage`) and
the ops tooling for the single-tenant host that runs them against four target
repositories.

## Language

**Run**:
One execution of a workflow graph on the fabro server. Holds at most one LLM session at
a time; all three workflows are sequential graphs.
_Avoid_: job, execution, pipeline

**Session**:
One live conversation between fabro and a model endpoint, held by the stage a run is
currently executing. Provider concurrency limits (e.g. GLM 5.3 × 3) count sessions, not
runs.
_Avoid_: connection, request

**Cap**:
`server.scheduler.max_concurrent_runs` (currently 2), the only hard concurrency control.
Runs fired beyond the cap queue; they are not rejected (verified 2026-09-16).
`issue-triage`'s capacity check reads the same number from the other side: it stands
down at `scheduler_slots_used > 1`, because the triage run is itself one of the two.
_Avoid_: limit, throttle

**Quiet-exit**:
A backlog run that finds no `agent`-labeled issue and routes `acquire → exit` in
seconds, having spawned a sandbox and cloned the repo but started no LLM session.

**Automation**:
A server-side row binding a workflow package to a target repo and environment, with
triggers (`api:manual`, `schedule:every-15m`, `schedule:hourly`). Three per repo,
twelve total.
_Avoid_: schedule, cron, job

**Bridge**:
The `trigger_review` stage of `backlog`, which fires a durable `pr-review` run on the PR
the backlog run just opened.

**Coder pool**:
The two local coder models (`coders` in the litellm catalog), each serving one session
at a time. Saturation behavior (queue vs. error into cloud fallback) is an accepted
unknown.

**Double-fire guard**:
The `/tmp/fabro/review_triggered` marker file that prevents a second bridge fire when
`open_pr` is revisited via the human_rescue path.

**User guide**:
`docs/USER-GUIDE.md`, the public, secrets-free operating manual: adding work, watching
runs, expected timings, attention signals. Never carries an IP, path, or token.
_Avoid_: manual, README

**Operator runbook**:
`~/.fabro-deploy/docs/OPERATOR-RUNBOOK.md`, the private companion to the user guide,
holding host addresses, paths, and credential-retrieval commands. Lives outside git.
_Avoid_: playbook, cheatsheet

**Monitor**:
`ops/fabro-monitor.sh`, deployed to `~/bin` on the host: the out-of-band health layer,
cron every 15 minutes. It never alerts on a failed run — failure alerting is hook-owned.
_Avoid_: watchdog, alerting (the hooks alert; the monitor watches)

**Starvation**:
Zero open `agent`-labeled issues across all four target repos — the factory is out of
work. A monitor condition, not a run state.
_Avoid_: idle, empty queue

**Heartbeat**:
The external dead-man's ping the monitor sends every run; its absence means the host
itself is silent, which no in-band or on-host signal can report.
_Avoid_: healthcheck (the compose container already has one, and it is a different thing)

**Canary**:
The first repo whose `backlog` schedule is enabled at turn-on — `jelly-swipe`, because
it has branch protection and no deploy-on-merge.

**Observation window**:
The five-day, human-verified period between canary turn-on and full-fleet expansion,
with a daily checklist and explicit exit and stop conditions.
