# Task 06 — Turn on: preflight, then five schedules

**Depends on:** 02. Soft-depends on 03 (heartbeat — do it first unless a silent host
during the observation window is consciously accepted). **Blocks:** 07. **LLM level:** no.

The factory goes live: four `issue-triage` schedules plus the canary
`backlog-jelly-swipe`. The other three backlog schedules stay off until task 08.
Auto-merge stays armed (decision 12). This is a small number of API calls — the care
is in the preflight and the verification, not the flip.

## 1 — Preflight (all must pass; any failure stops the task)

Run the validation suite in the container — the AGENTS.md recipe, all **three**
workflows:

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && for w in backlog pr-review issue-triage; do
  docker compose exec -T fabro fabro validate /tmp/check/workflows/$w/workflow.toml; done'
```

Expected baselines: `Backlog (38 nodes, 86 edges)` clean; `PrReview (28 nodes, 64
edges)` with exactly the one deliberate `pr_number` warning; `IssueTriage (15 nodes,
35 edges)` — record whatever it reports; a new warning on it is a finding, not a
blocker, unless it is an undefined-input on a compile path (the `pr_number` lesson).

Then verify, against the API:

- all five environments exist (`GET /api/v1/environments`);
- the three vault secrets are set (`fabro secret list` — `FABRO_API_TOKEN` is the
  bridge's hard dependency);
- `fabro variable get FABRO_AUTO_MERGE` prints `1` — armed deliberately (decision 12),
  not by accident;
- the monitor is live: one cron tick in `~/.local/state/fabro-monitor.log`, and
  `DRY_RUN=0 IDLE_HOURS=0 ~/bin/fabro-monitor.sh` **still sends no C3 alert** — the
  enabled-schedule gate is what holds it back, and this is the last chance to prove
  that before the gate opens;
- queues as expected: `jelly-swipe` has `agent` work; the four repos' label state is
  recorded for the log.

## 2 — Enable the five schedules

The schedule `enabled` flag is the operator's to set — `provision-server-state.sh`
deliberately never touches it, so this is by hand. Per row, the `If-Match` discipline
the auto-merge switch already implements: `GET` the row, flip the one flag, `PUT` the
whole body back with the `revision` as `If-Match`. Enable, in this order (triage first
— it is low-blast-radius and starts refilling queues immediately):

1. `issue-triage-jelly-swipe` (`30 * * * *`)
2. `issue-triage-lawncare-saas` (`35 * * * *`)
3. `issue-triage-womens-fantasy-sports` (`40 * * * *`)
4. `issue-triage-writers-app` (`45 * * * *`)
5. `backlog-jelly-swipe` (`2-59/15 * * * *`) — the canary

A helper one-liner with `curl` + `jq` is fine; a new ops script is not (this action
happens twice ever — here and in task 08). Verify after each: `GET` the row back and
read `enabled: true` off the server's own copy, not off the payload you sent.

## 3 — First-hour verification

The moment the canary schedule is enabled the clock starts. Within the first hour,
confirm:

- a `backlog` run fired on jelly-swipe at a `:02/:17/:32/:47` minute and **claimed an
  issue** — `agent-in-progress` appears on exactly one issue, the lowest-numbered one;
- triage runs fired at `:30`-ish and quiet-exited or claimed per their queues
  (`womens-fantasy-sports` should have real triage work; the other three quiet-exit);
- Discord shows the expected kinds and nothing unexpected — no `failed`, no monitor
  alert;
- C3 stayed quiet (check the monitor log), then prove C3 would fire now if the
  scheduler died: `DRY_RUN=0 IDLE_HOURS=0 ~/bin/fabro-monitor.sh` **must** alert, once.
  Clear the stamp afterward by removing the state-file line — the condition is not
  actually resolved, it was forced; do not leave a lie in the state file, and record
  the forced fire in the log so the ✅-without-a-stamp on the next clean run is not
  confusing. (Implementation note for the operator: deleting the `C3=` line is the
  correct reset; the next run re-evaluates from scratch.)

## 4 — Stop conditions for the first hour

Disable `backlog-jelly-swipe` (same PUT recipe, flag back to `false`) and treat it as
a workflow bug, not an ops event, if:

- the backlog run fails and the failure is in the graph, a prompt, or a contract — not
  in the issue's content;
- two runs claim the same issue, or a run claims an issue already
  `agent-in-progress`;
- anything fires on a repo whose schedule was not enabled;
- the monitor produces an alert that is wrong (false positive is a monitor bug — fix
  the monitor, not the factory).

A failure that is *the issue's fault* (underspecified, infeasible, CI red for content
reasons) is the system working: the run goes to `needs-human` or the PR gets blocked,
and the observation window (task 07) begins anyway.

## Acceptance

- Five rows read back `enabled: true`; the other three backlog rows still `false`.
- First backlog run claimed exactly one jelly-swipe issue; first triage runs behaved
  per their queues.
- C3 proven live-firing with the knob, then reset honestly.
- A dated entry in the deployment log (task 10 formalizes the window's entries; this
  one is written now): what was enabled, what the first hour did, the preflight
  numbers.
