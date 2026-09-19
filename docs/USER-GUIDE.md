# Fabro factory — user guide

The operating manual for the automation factory. It answers the three questions
that matter: **how do I add work, how do I tell it's running and when it'll be
done, and how do I get notified when something needs attention.**

This file is public — no host addresses, paths, or credentials. Its private
companion, the **operator runbook**, carries those; where this guide says "see
the operator runbook", that's where they live.

## What this factory does

Three workflows, four repositories, one pipeline:

```
issue labeled needs-triage
  → issue-triage   (improves the issue, asks blocking questions, retitles
                    to Conventional Commits, promotes the label)
  → issue labeled agent
  → backlog        (decomposes the issue, implements each task, reviews and
                    fixes, opens a PR, and squash-merges it once CI is green)
  → issue closed
```

**A scheduler decides when a `backlog` run starts.** It is a small service beside
fabro that inventories the open `agent`-labelled issues across the four repositories
every minute, orders them (operator bump, then anything waiting longer than four hours,
then repo priority, then issue number), and starts **one `backlog` run at a time**: one
run per repository, one run per coder box, and there are two boxes. It takes the issue
itself as the run's input, so a run works exactly the issue the scheduler picked, and it
marks that issue `agent-in-progress` at the moment it starts the run. There is nothing
left on a timer — the schedules that used to fire `backlog` are off, deliberately, so
there is exactly one thing deciding what gets worked next. The scheduler's own page shows
the ranked queue, which box is busy with what, and the three operator controls (bump an
issue to the front, pause a box, cancel the run on one); `ops/README.md` has the calls
behind each one and when to reach for them.

Auto-merge is **armed**. A well-specified issue can travel from label to merged
with no human touching anything. That is the fact that shapes every section
below: the system's default is forward motion, and every mechanism in this
guide is about deliberately interrupting it.

## Adding work

**The taught path:** file an issue on any of the four repos and label it
**`needs-triage`**. There is no required body format — under-specified is fine,
that is what triage is for. Triage improves the body, retitles the issue to a
Conventional-Commits title, and either promotes it to `agent` or asks you the
blocking questions.

**The expert shortcut:** when you already know exactly what you want, label the
issue **`agent`** directly and skip triage.

Once work is moving, the labels mean:

| Label | Meaning |
|---|---|
| `agent-in-progress` | Claimed — the scheduler set it when it started the run for this issue. Do not edit the issue; it is mid-flight. |
| `agent-authored` | The PR is automation-written. **A human commit pushed to that branch vetoes auto-merge** — deliberate, so automation never merges code a human touched. |
| `ai-review-complete` | The review run finished and found the PR merge-eligible. |
| `ai-review-needs-human` | The review run wants a human to look before merging. |
| `agent-stuck` | The run gave up and left a comment saying why. |

**Editing a PR mid-flight** (supported, and read live at merge time):

- The squash commit's *body* comes from the marker block in the PR
  description — edit inside the block to correct the commit message.
- The commit's *subject* is the live PR title — fix a bad title by editing
  the title.

## Telling it's running, and when it'll be done

### Discord is the primary signal

The factory narrates itself into one Discord channel. Hook messages (fired
from inside runs) say `fabro …`; monitor messages (health checks, fired by a
host cron job *about* the runs) say `fabro monitor: …`. Full vocabulary in
the notification section below.

### Watching a live run

- The **fabro web UI** shows runs live; the run's page is where a rescue gate
  or triage question waits for your answer.
- For why a run routed the way it did — which edge fired and why — the
  `fabro events <run> -p` incantation answers; the repository's `AGENTS.md`
  carries the exact command.
- GitHub itself: `gh issue list --label agent-in-progress` shows claimed
  work; open PRs labeled `agent-authored` are in the pipeline.

### Durations — ranges, not promises

Measured from the deployment log; the fleet is young and these are early
numbers, not percentiles. Queue position and cadence answer "when will my
issue be done" better than a stopwatch:

| Stage | Measured | Expectation |
|---|---|---|
| issue-triage | no full triage run measured yet | minutes when there is real work, plus a **30-minute question gate** (answer in the web UI, or the questions post to the issue) |
| backlog | 13.9 min and 45.5 min wall for two full runs | tens of minutes |
| pr-review | 6.3–26.8 min across four happy-path runs (one 40-min outlier was a transient provider failure, not a bug) | minutes, plus a **60-minute CI/merge budget** (checks are watched for up to 35 min; the merge phase stops at 60) |

Cadence and queue:

- **The scheduler picks what runs next, and it starts one run at a time**: one run
  per repository, and one per coder box. Two boxes, so at most two runs are doing
  coder work at once — that is the point of the thing, not a shortage. Everything
  else waits in the scheduler's queue, and waiting is normal.
- **An issue can wait a while.** Nothing waits longer than **four hours** before it
  jumps to the front of the queue regardless of repo priority — that ceiling is the
  anti-starvation rule, so a low-priority repo's issue cannot be skipped forever.
- `issue-triage` is **not** queued: it needs no coder box and stays on its own
  hourly schedule (per repo, staggered).

## Getting notified when something needs attention

### From the runs (hooks)

| Message | Meaning | How fast |
|---|---|---|
| ❓ `fabro issue triage needs answers` | Triage can't proceed without you. **30-minute gate**, then the questions post publicly on the issue. | fast — answer at the run's web UI page |
| 🟡 `fabro needs a human decision` | A rescue gate; the run holds until you answer at the web UI. | fast |
| 🔴 `fabro run failed` | A run died. The run's page and events log say where. | eventually |
| 🟠 `fabro issue triage released a claim without finishing` | Triage gave up mid-issue; read the comment it left. | eventually |
| `agent-stuck` label + comment | Same idea, on the issue itself. | eventually |
| ✅ `fabro opened a PR` | Informational — work moved forward. | — |
| 🚀 `fabro squash-merged a PR` | Informational — done. | — |

### From the monitor (`fabro monitor: …`)

A cron job on the host evaluates eight health conditions every 15 minutes. It
never reports run *failures* (the hooks own those); it reports the things that
prevent runs from happening at all. While a condition persists it re-alerts
(4h; starvation weekly, disk 24h); when it clears you get one ✅ resolved.

| Message | Meaning | Do this |
|---|---|---|
| 🔴 `container-down` | The fabro container is not running or not healthy. | See the operator runbook — server recovery. |
| 🔴 `api-unreachable` | The API doesn't answer (or the token was rejected — the message says which). | Runbook; a 401 means the token rotated. |
| 🔴 `dead-scheduler` | Schedules are on but nothing has run in 2h. With every schedule off (the current state) this condition is **inert** — `scheduler-down` and `scheduler-wedged` below are what watch the scheduler now. | Runbook — the scheduler is wedged. |
| 🔴 `stuck-run` | A run has sat non-terminal for 3h. | Open the run; cancel or let it ride. |
| 🟡 `starvation` | Zero `agent` issues on all four repos. | The queue is empty: file issues labeled `needs-triage` (or `agent`). |
| 🟠 `disk-pressure` | The host disk is ≥ 85% full. | Runbook — run the sweepers. |
| 🔴 `scheduler-down` | The scheduler's health endpoint stopped answering. | Runbook — the scheduler is down or its container is stopped. |
| 🔴 `scheduler-wedged` | Work is queued and every coder box has sat idle for 20 minutes. | Runbook — the scheduler is running but not dispatching. |

### The silence signals

- **Nothing is being dispatched.** Either the scheduler is not running
  (`scheduler-down`) or it is running and every box has sat idle with work queued
  (`scheduler-wedged`) — the monitor says which. Both are the conditions that matter
  now that no `backlog` schedule exists to notice on its own; the older `dead-scheduler`
  condition is inert while the schedules are off.
- No Discord noise at all is **ambiguous**: it can mean everything is healthy,
  or it can mean the host itself is down — the monitor, its cron, and the
  Discord webhook all live on that host, and there is deliberately no external
  heartbeat. The operator runbook has the two-minute check that disambiguates.

## Turning things off

Pointers, not duplication — the kill switches and how to use them live in
`ops/README.md`:

- **Auto-merge kill switches** — per-repo and host-wide; `ops/README.md` →
  "Auto-merge kill switches". Flipping one takes effect on the next run and
  never undoes a merge that already happened.
- **Schedule toggles** — enable/disable a repo's automations; same file.
- The bluntest switch: the `FABRO_API_TOKEN` vault entry is what the run's PR
  handoff reads the server's own API with (the per-repo merge switch lives there).
  Breaking it fails every backlog run closed — nothing opens, nothing merges. For
  when you want the factory *stopped*, not just unmerged; how, in the operator
  runbook.
