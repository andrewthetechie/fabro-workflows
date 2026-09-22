# Operating the coder scheduler — quickstart

The page is **`http://10.10.0.32:32280/`**. LAN-only, no login (decision 16), so anyone
who can reach the host can cancel a running job. That is the trade, and it is why every
mutation logs what it changed — the log is the whole audit trail.

This file is the operator's short version. `ops/README.md` has the reasoning behind each
control and the failure modes; `00-overview-and-contracts.md` has the 19 decisions and the
11 findings the design rests on.

---

## What it does, in one paragraph

Every 60 seconds it asks GitHub for the open `agent`-labelled issues in the four repos
(conditionally — a `304` costs no rate-limit quota). Every 5 seconds it checks whether a
coder box is free and, if one is, fills it with a `backlog` run: **one run per repo, one
run per box, two boxes** &mdash; and where the queue is not diverse enough for that, a second
run from the same repo so a box never idles hungry (decision 6, *prefer diversity, never
idle a box*). It labels the issue `agent-in-progress` *before* creating the run,
because that label is the receipt a restart rebuilds from. It holds the box for the whole
run — including while the run sits on a human gate — and releases it when the run reaches
`succeeded`, `failed` or `dead`.

Nothing else starts a `backlog` run. The four schedules that used to are off.

---

## The page

| Section | What to look at |
|---|---|
| **Coder pools** | Which box is busy with what, and whether either is drained. Two idle boxes with a non-empty queue for more than a few minutes is a wedge. |
| **Queue** | Dispatch order, top-first. This is the *actual* order the loop will use — the page and the dispatcher call the same `rank()`. |
| **Repos** | `ok` / `stale` / `not yet fetched` per repo. **`stale` means GitHub failed and you are looking at cached items** — it does not mean there is no work. |

Row colours: highlighted = an operator bump, dimmed = past the 4-hour starvation ceiling.

JSON twins, if you would rather curl: `/api/queue`, `/api/pools`, `/api/repos`, `/health`.

---

## Setting repo priority

Priority is a **signed integer and the smallest wins**: `-99` beats `0` beats `7`. It is
required per repo — there is no default — and it is the third tiebreak, after an operator
bump and the starvation ceiling, then issue number ascending.

It lives in one file, `ops/scheduler/repos.toml`:

```toml
[[repo]]
name           = "andrewthetechie/jelly-swipe"
priority       = 0
environment_id = "python"
enabled        = true
```

**Yes, you have to restart the scheduler.** `repos.toml` is baked into the image
(`COPY repos.toml` in the Dockerfile, read via `SCHEDULER_CONFIG=/app/repos.toml`) and
there is no bind mount for it, so a priority change is a rebuild. The config is also read
exactly once, in `main()`, before the port is bound — editing a file in the container and
waiting would do nothing even if one were mounted.

The whole procedure:

```sh
cd ~/Documents/code/fabro-workflows
$EDITOR ops/scheduler/repos.toml

git commit -am "Reprioritise <repo>" && git push          # main is the source of truth

rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d --build scheduler'

curl -fsS http://10.10.0.32:32280/health | jq '.repos'     # confirm it took
```

Four things worth knowing before you do it:

- **`up -d --build scheduler` names one service.** Never `up -d` on its own here — that can
  recreate `fabro`, and a fabro restart fails *every* run in flight.
- **Live runs survive the restart.** Startup recovery *adopts* a lease whose run is still
  going, keeping its original `dispatched_at`; it only releases leases whose runs are
  terminal. Verified live on 2026-09-19: two runs mid-graph, neither paused.
- **The rsync is load-bearing.** `--build` rebuilds whatever is on the host, which is
  whatever was last copied there. Skipping the rsync rebuilds the old tree.
- **Priority does nothing while everything is starved.** Anything waiting longer than `T`
  (4h, `SCHEDULER_CEILING_SECONDS`) jumps ahead of everything that has not, ordered
  oldest-first, and repo priority never reorders that tier — that is what makes "nothing
  waits more than `T`" checkable by looking. After an outage the whole queue can sit in that
  tier, and the page will look like priority is being ignored. It is not.

`enabled = false` keeps the row and takes the repo out of all scheduling. Same rebuild.

---

## The three controls

All on the page as buttons; these are the same calls.

```sh
B=http://10.10.0.32:32280
R=andrewthetechie/jelly-swipe

# Work this issue next. Ordering only — it never pre-empts a running lease, and
# one-run-per-repo still applies, so a bumped issue whose repo is busy goes next
# *for that repo*. Cleared automatically on dispatch: it means "next", not "forever".
curl -fsS -X POST "$B/api/queue/$R/123/bump"          # {"override_rank":-1}

# Stop NEW work going to a box. The run already on it keeps running.
curl -fsS -X POST "$B/api/pools/coders-a/drain"       # {"drained":true}
curl -fsS -X POST "$B/api/pools/coders-a/undrain"     # {"drained":false}

# End the run on a box now. Separate from drain on purpose.
curl -fsS -X POST "$B/api/pools/coders-a/cancel"      # {"cancelled_run_id":"01M..."}
```

**Drain never destroys work; cancel does.** Prefer draining a misbehaving box to
cancel-cycling it. A cancel requeues its issue (the run exits before the graph's own label
work, so without the requeue the issue would keep `agent-in-progress` and vanish from the
queue), does **not** release the box immediately — the 15-second poll does that when fabro
reports the run terminal — and does **not** un-drain the box.

---

## Monitoring

`fabro-monitor.sh` runs from cron every 15 minutes and owns all alerting; the scheduler
alerts on nothing itself, deliberately — something outside it has to notice when it is dead.

```sh
ssh andrew@10.10.0.32 'DRY_RUN=1 ~/bin/fabro-monitor.sh'     # all 8 conditions, sends nothing
ssh andrew@10.10.0.32 'tail -20 ~/.local/state/fabro-monitor.log'
```

The two that are about the scheduler:

| Condition | Fires when | What it means |
|---|---|---|
| 🔴 `scheduler-down` | `/health` does not answer 200 in 5s | The container is stopped or wedged. Suppresses `scheduler-wedged`, which is unanswerable without it. |
| 🔴 `scheduler-wedged` | Queue non-empty **and** every box idle **and** none drained, for `WEDGE_MINUTES` (20) | It is up and not dispatching. A box is idle between runs by design, which is why one sample is never enough. |

These two are the dead-scheduler alarm now. The older `dead-scheduler` (C3) gates on "some
automation has an enabled schedule" and reports `inert` — correct, since draft 13 turned
the four `backlog` schedules off.

Day-to-day, without ssh:

```sh
curl -fsS http://10.10.0.32:32280/health | jq '{github:.github.configured, fabro:.fabro.configured}'
curl -fsS http://10.10.0.32:32280/api/pools | jq -c '.[] | {coder_pool, drained, run:.lease.run_id}'
curl -fsS http://10.10.0.32:32280/api/queue | jq 'length'
```

`github.configured: false` means the queue will be empty and stay empty.
`fabro.configured: false` means every dispatch answers 503. Both are read from
`~/fabro/scheduler.env`, and neither value is ever echoed.

Logs:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose logs -f scheduler'
# dispatch decisions, releases and requeues only:
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose logs scheduler | grep -E "dispatch:|reconcile:|recover:"'
```

---

## When something is wrong

**Nothing is being dispatched, queue is non-empty.** Check `/api/pools` first: a lease with
a `run_id` means a box is legitimately busy, possibly on a run parked at a human gate for up
to four hours (a lease is held for the *whole* run, gates included — decision 1). Check
`drained`. If both boxes are genuinely idle with work queued, read the logs for the
dispatch failure — a `403` on a label write means the GitHub token lost `issues: write`.

**The scheduler is down and work has to move.** The manual escape hatch takes no lease and
fires at the unpinned `coders` group:

```sh
ssh andrew@10.10.0.32 'DRY_RUN=0 ~/bin/fabro-fire-backlog.sh andrewthetechie/jelly-swipe 123'
```

Do not run it while the scheduler is up unless you know the issue is not queued — the
scheduler would see a receipt it has no lease for. It handles that (a live fabro run counts
as working an issue, exactly so the hand-fire path is safe), but there is no reason to lean
on it.

**A box is stuck on a lease whose run is gone.** Cancel first, then delete the row only if
the poll does not free it:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T scheduler python -c "
import sqlite3; c = sqlite3.connect(\"/data/scheduler.db\")
c.execute(\"DELETE FROM leases WHERE coder_pool = ?\", (\"coders-a\",)); c.commit()"'
```

Deleting a lease does **not** stop its run. Doing it while the run is live puts two
single-slot runs on one slot, which is the exact contention this service exists to remove.

**An issue is wearing `agent-in-progress` and nothing is working it.** The scheduler repairs
this itself now — the receipt scan runs at startup and every 10 minutes after, and requeues
anything that looks orphaned in two consecutive scans. Wait 20 minutes before intervening.
To force it, restart the container.

---

## Things that surprise people

- **A `304` from GitHub is not "no work".** It means the cached list is still current. The
  page says `stale` only when a fetch actually failed.
- **A run on a human gate still holds its box.** Answer the gate, or cancel the run.
- **`waited` is measured from when *this scheduler* first saw the issue**, not from when the
  issue was created, and it is never reset by a refresh. A requeue preserves it on purpose,
  so a failed item does not go to the back of the ceiling tier.
- **A bump does not pre-empt anything.** It reorders the queue. One-run-per-repo still holds.
- **Changing `repos.toml` needs a rebuild; flipping auto-merge does not.** The auto-merge
  kill switch is a fabro server variable and takes effect on the next run.
