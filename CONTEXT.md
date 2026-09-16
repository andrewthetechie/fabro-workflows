# Fabro Workflows

Two production Fabro workflow packages (`backlog`, `pr-review`) and the ops tooling for
the single-tenant host that runs them against four target repositories.

## Language

**Run**:
One execution of a workflow graph on the fabro server. Holds at most one LLM session at
a time; both workflows are sequential graphs.
_Avoid_: job, execution, pipeline

**Session**:
One live conversation between fabro and a model endpoint, held by the stage a run is
currently executing. Provider concurrency limits (e.g. GLM 5.3 × 3) count sessions, not
runs.
_Avoid_: connection, request

**Cap**:
`server.scheduler.max_concurrent_runs` (currently 3), the only hard concurrency control.
Runs fired beyond the cap queue; they are not rejected (verified 2026-09-16).
_Avoid_: limit, throttle

**Quiet-exit**:
A backlog run that finds no `agent`-labeled issue and routes `acquire → exit` in
seconds, having spawned a sandbox and cloned the repo but started no LLM session.

**Automation**:
A server-side row binding a workflow package to a target repo and environment, with
triggers (`api:manual`, `schedule:every-15m`). Two per repo, eight total.
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
