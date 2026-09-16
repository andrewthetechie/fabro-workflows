# Task 03 — The heartbeat (dead man's switch)

**Depends on:** 02. **Blocks:** 06 (soft — see below). **LLM level:** no.

Everything else in this stage shares a failure mode: the monitor, the cron that runs
it, and the Discord webhook it posts through all live on the host they watch. A dead
host is silent. The heartbeat closes that: an external service expects a GET every ~15
minutes and alerts when it stops coming.

Decision 8: optional but recommended. Task 06 (turn-on) **soft-gates** on it — do this
first unless the operator consciously accepts that a host failure during the
observation window looks exactly like a quiet factory.

## 1 — Human step: create the check

healthchecks.io free tier is the recommendation, but the contract is "a URL that
expects a ping on a schedule and alerts on absence" — any dead-man's service works,
and the script does not know or care which. Create a check with:

- **Period: 15 minutes** (the monitor's cron), **grace: 15 minutes** — one missed tick
  is a blip (cron hiccup, Docker restart), two is a problem.
- Alert channels: whatever reaches the operator when Discord-on-the-host might be
  dead — email at minimum.

The ping URL is a **capability secret**. It goes in `~/fabro/.env` on the host, never
in this repo, never in `.env.example` (which lists key *names* only — adding
`FABRO_HEARTBEAT_URL=` there with an empty value is correct and encouraged):

```sh
ssh andrew@10.10.0.32 'echo "FABRO_HEARTBEAT_URL=<ping url>" >> ~/fabro/.env'
```

And into the cron line's environment — the cron entry from task 02 sources nothing, so
the URL must be on the crontab line itself or in a file the script reads. Prefer the
script reading `~/fabro/.env` directly (it already lives beside the compose file;
one canonical home for host-local secrets). If task 01 did not implement that read,
patch `ops/fabro-monitor.sh` now, redeploy, re-run the drift check.

## 2 — Prove it, both directions

- **Ping arrives:** `DRY_RUN=0 ~/bin/fabro-monitor.sh`, then confirm the check's log
  shows a ping at that minute.
- **Absence alerts:** pause is not a real proof — the real proof is that the service
  shows the check as late after two missed ticks. Do not leave the monitor stopped to
  find out; instead set the check's grace to 1 minute, wait for the late
  notification, restore grace to 15. That proves the alerting path without leaving a
  hole.
- **Failure signal:** force an evaluation failure (e.g. `FABRO_MONITOR_REPOS` is fine
  but point `FABRO_HOST` at nothing in a one-off run) and confirm the `/fail` ping
  arrives and renders as a failure in the service, not as success.

## 3 — Record it

- `ops/README.md`: one paragraph in the monitor section — the heartbeat exists, the
  URL lives in `~/fabro/.env` as `FABRO_HEARTBEAT_URL`, the service name, the
  period/grace. No URL.
- The operator runbook (task 05) gets the recovery path: where the check lives, how to
  rotate the URL, what the alert means.

## Acceptance

- Pings arrive on the monitor's cron cadence; the check renders green.
- A forced gap produces a late/down alert through a channel that does not depend on
  the host.
- A `/fail` ping renders as failure.
- No URL in any tracked file — `git grep -i healthchecks` and
  `git grep FABRO_HEARTBEAT_URL` find only `.env.example`'s empty key and prose
  references.
