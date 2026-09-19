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
`server.scheduler.max_concurrent_runs`, fabro's only concurrency control and a single
global integer — there is no per-pool or per-label variant. Runs fired beyond it queue;
they are not rejected (verified 2026-09-16). It is **4** as of 2026-09-18, raised from 2
when the **Scheduler** took over admission (ADR 0005), and it is now a backstop rather
than the thing shaping behaviour: it stops a runaway, it does not allocate. Still 4, and
still in force, on 2026-09-19: the container's `StartedAt` (23:13:39Z on the 18th) is later
than the settings overlay's mtime (21:47:03Z), which is the only way to check a value the
server copies out once at startup. Not to be confused with a **Coder lease**, which is
the control that actually matters.
_Avoid_: limit, throttle

**Quiet-exit** *(retired)*:
Was a backlog run that found no `agent`-labeled issue and routed `acquire → exit` in
seconds, having spawned a sandbox and cloned the repo but started no LLM session. Draft
10 collapsed `acquire`/`claim`: a backlog run now works exactly the `issue_number` it is
given and fails closed rather than choosing (or skipping) work, so there is no
quiet-exit path left. Kept here only so the word is not reused for something else.

**Manual fire**:
The operator's escape hatch when the **Scheduler** is down: `ops/fabro-fire-backlog.sh
<owner/repo> <issue_number>`, deployed to `~/bin/fabro-fire-backlog.sh`. It creates one
`backlog` run for exactly that issue with `coder_pool: "coders"` (the both-boxes group,
never a pinned box — a hand fire holds no lease). The graph's `claim` swaps the labels
as a fallback for what the scheduler does at dispatch, so a manual run needs no lease.
**It does not currently work**: from 2026-09-19 a `backlog` run dies at `claim`, the manual
path included, because that node writes into `/tmp/fabro` and nothing creates the
directory — the dated block in `docs/scheduler/00-overview-and-contracts.md` carries it.
_Avoid_: manually, by hand (the adverb, not the noun)

**Automation**:
A server-side row binding a workflow package to a target repo and environment, with
triggers (`api:manual`, `schedule:every-15m`, `schedule:hourly`). Three per repo,
twelve total.
_Avoid_: schedule, cron, job

**Bridge** *(retired)*:
Was the `trigger_review` stage of `backlog`, which fired a separate `pr-review` run on
the PR it had just opened. Commit `54c21be` moved review-and-merge into the backlog run
itself through the shared `_shared/review-merge/` import, so the stage no longer exists
and no second run is created. `backlog` now runs `open_pr -> pr_handoff -> review_merge
-> exit`. Kept here only so the word is not reused for something else.

**Coder instance**:
One llama.cpp server holding the `deepseek-v4-flash-0731-iq3-xxs` GGUF — `10.10.0.29`
and `10.10.0.56` today. Each has exactly **one slot**, so whoever holds it blocks
everything else for a whole turn. Measured ceiling 17.4 tok/s.
_Avoid_: coder agent, box, GPU, worker (a fabro worker is a different thing)

**Coder pool**:
The set of coder instances, and the LiteLLM model group `coders` that fronts all of
them. After the split there are three groups: `coders-a` and `coders-b` address one
instance each, `coders` addresses both and is the fallback for anything unpinned.
Saturation is no longer an accepted unknown: two concurrent runs on two single-slot
instances with no affinity spend roughly half their turns queued behind each other,
measured at ~1.7x on 2026-09-18.

**Coder lease**:
A scheduler-held claim on one **Coder instance** for one **Queue item**, recorded as
`(box, repo, issue, run_id, dispatched_at)`. Runs from dispatch to the run's terminal
state and is released by nothing else — not by a human gate, not by a hosted-model
stage. See ADR 0006.
_Avoid_: lock, pin, reservation

**Queue item**:
One open, `agent`-labelled issue that is neither `agent-in-progress` nor `agent-stuck`.
Becomes exactly one `backlog` run, which may decompose into many tasks and produces one
PR. The unit the queue orders and the web UI reorders.
_Avoid_: job, task (a task is what `decompose` produces *inside* a run)

**Scheduler**:
`ops/scheduler/`, a service in the `~/fabro` compose project that owns admission to the
coder instances: it inventories work from GitHub, orders it, and creates one fabro run
at a time. Distinct from fabro's own scheduler, which only promotes `runnable` runs FIFO
and cannot be steered.
_Avoid_: dispatcher, orchestrator, queue manager

**Drain**:
Marking a coder instance ineligible for new dispatch while letting its current run
finish. Deliberately *not* cancellation — cancelling the run on a box is a separate,
explicitly-labelled control, because the word "drain" should never destroy work.
_Avoid_: disable, cordon

**Override**:
An operator's "dispatch this next" mark on one **Queue item**, set from the web UI and
held in the scheduler's database. It outranks both the **Starvation ceiling** and repo
priority, it is cleared the moment the item is dispatched, and it is never a permanent
priority — repo priority lives in `repos.toml` and is reviewed policy, not a click. Not
a **Manual fire**, which starts a run immediately and takes no queue position.
_Avoid_: priority (that is the repo's), pin, jump

**Starvation ceiling**:
`T`, default 4h. Any queue item waiting longer than `T` jumps the front regardless of
priority. The whole anti-starvation mechanism — chosen over a continuous aging score
because "nothing waits more than T" is verifiable by looking at the queue.
_Avoid_: aging, decay

**Double-fire guard** *(retired)*:
Was the `/tmp/fabro/review_triggered` marker file that stopped `trigger_review` firing a
second time when `open_pr` was revisited through the `human_rescue` path. Nothing writes or
reads it any more: the **Bridge** is gone and no node in any graph names the marker, so a
reopen of `open_pr` has nothing left to guard. Kept here only so the word is not reused for
something else.

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
work. A monitor condition, not a run state. Note this is the *opposite* sense to the
**Starvation ceiling**, which is about a queued item never being picked: here there is
nothing to pick.
_Avoid_: idle, empty queue

**Heartbeat**:
The external dead-man's ping `fabro-monitor.sh` is *able* to send once per run
(`FABRO_HEARTBEAT_URL`), whose absence would mean the host itself is silent — which no
in-band or on-host signal can report. **Declined and not configured** (turn-it-on task 03,
2026-09-16; the crontab sets no such variable), so host death currently looks like silence
and the operator runbook carries the manual disambiguation. Corrected 2026-09-19: the
mechanism exists in the script, the signal does not exist in production.
_Avoid_: healthcheck (the compose container already has one, and it is a different thing)

**Canary** *(retired)*:
Was to be the first repo whose `backlog` schedule is enabled at turn-on — `jelly-swipe`,
because it has branch protection and no deploy-on-merge. No canary turn-on ever happened:
the four `backlog` schedules have been off since 2026-09-14 and draft 13 turned them off for
good (2026-09-19), and the **Scheduler** replaced the staggered-schedule rollout with a
four-repos-at-once cutover (scheduler overview decision 18). Kept here only so the word is
not reused for something else.

**Observation window** *(retired)*:
Was the five-day, human-verified period between canary turn-on and full-fleet expansion,
with a daily checklist and explicit exit and stop conditions. Superseded: there was no
canary and no schedule-driven expansion to gate, so the period never began. Its successor
is draft 14's 24-hour scheduler shakedown — a measurement window with a written recipe
rather than a daily checklist, and as of 2026-09-19 an unstarted one.
