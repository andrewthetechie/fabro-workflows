# Fast path and merge queue: handoff after go-live

> Written 2026-10-11, right after go-live. Read `00-overview-and-contracts.md` (the contracts)
> and `10-validate-and-go-live.md` (the task this finishes) first. The operational record is the
> 2026-10-11 sections of `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac.

You have three jobs: check the first day (task 10 step 7), write the 7-day result (step 8, due
**2026-10-18**), and close the series out (step 9). Everything else in task 10 is done. This doc
gives the live state, what to run, and the open faults to watch for.

## Live state (2026-10-11 23:03Z)

| What | State | Where to check |
|---|---|---|
| Fast lane (`backlog`, `route_lane`) | Live on every `backlog` run since `e0b2203` | `fabro events <run> -p`: `route_lane` publishes `lane=fast` or `standard` |
| Merge-phase queue mode (`report_queued`) | Live in `backlog` only (only its `pr_handoff` writes `queue`) | PR comment line 1: `<!-- fabro:merge-queue approved head=… -->` |
| `merge-queue-<repo>` x4 | **Schedules ON** since 23:03:03Z, `*/10` staggered at :00 jelly-swipe, :02 lawncare-saas, :05 womens-fantasy-sports, :07 writers-app | `GET /automations`, `.workflow == "merge-queue"` |
| `max_concurrent_runs` | **8** (the operator chose 8, not the drafted 6) | `docker inspect -f '{{.State.StartedAt}}' fabro-fabro-1` later than the overlay mtime |
| Retention | Host cron `07 5 * * *` runs `~/bin/fabro-run-prune.sh` (merge-queue runs older than 7d) | `~/.local/state/fabro-run-prune.log` on the host |
| First batch | womens-fantasy-sports #1599 #1805 #1812 #1818 #1832 #1842 #1843 #1844 #1846 #1847, labelled `fast-path` + `agent`, no `priority` | `gh issue list -R andrewthetechie/womens-fantasy-sports --label fast-path --state all` |
| Coder pools | Four: `coders-a`, `coders-b`, `coders-c` (spark, vLLM, 1M), `coders-d` (freellm router, model `auto`, capped at 256k) | scheduler `/api/pools` on :32280 |
| Kimi quota fallback | `orfallback:qwen3.8-27b` (OpenRouter) is the next rung after `kimi-k3` | `fabro events <run>` → `agent.route.failover` |

The kill switches (P10): turn the four schedules off with
`DRY_RUN=0 ~/bin/fabro-automation-schedule.sh merge-queue-<repo> off` (it is a **dry run** unless
`DRY_RUN=0`; read the rows back afterwards). `FABRO_AUTO_MERGE` off host-wide, or `auto_merge=false`
on a `pr-review-<repo>` row, makes every sweep skip (`ops/fabro-auto-merge-switch.sh`).

## Task 10 evidence so far

| Step | Result | Run ids |
|---|---|---|
| 1 Gates | `make check` and `make check-host` green; counts in `AGENTS.md` | — |
| 2 Quiet sweep | Passed: 4 s to first node, 8 s total, no `light` env needed | `01M4PEFW4G6YVTVD3QSG4VM06X` |
| 3 One issue end to end | **Substituted.** jelly-swipe#454 passed checks a-e. wfs#1834 was made redundant mid-run by the operator's cluster PR #1918 (merge conflict at `open_pr_prep`, abandoned). lawncare#2764 is still queued. Quiet sweeps passed on the other three repos. | #454 `01M4PFJP7BFA50A1022D7P8DVW`; sweeps `01M4PK7X49…`, `01M4PK816Z…`, `01M4PK84CP…` |
| 4 Strict repository | Passed: #456 merged; #457 BEHIND → rebased (C5 marker, REBASE method, author kept, committer = token user) → merged | `01M4PH696H…`, `01M4PH8978…`, `01M4PHCWR2…` |
| 5 Hand-off | Passed on scratch PR jelly-swipe#453 (closed) | `01M4PEJCY2C51EQT7BFSWAPP2S` |
| 6 Go live | Done 23:03:03Z | — |

First data point for step 8: jelly-swipe#454 took a **15.3 min** coder lease (dispatch to release)
and the run took 15.2 min: coder 3.6, validate 2.5 + 2.2, PR prep 2.1, standards 1.7, the rest under
a minute each. #455 took 12.2 min.

## Step 7: the first day (from 2026-10-12)

Run each check. Write what you found into the deployment log.

1. **Monitor.** `ssh andrew@10.10.0.32 'tail -50 ~/.local/state/fabro-monitor.log'`. You want no false
   C3 (dead scheduler) and no C4 (stuck run). A C4 for a `merge-queue` run means a sweep waited more
   than `MQ_STUCK_MINUTES=30` (`ops/fabro-monitor.sh`). A sweep that waits behind a full cap of 8
   could cause that. If it repeats, raise the limit, don't widen the cap.
2. **Retention.** After 05:07Z, `~/.local/state/fabro-run-prune.log` must show a pass. On day one it
   prunes nothing. `fabro system prune --older-than` accepts only `h` or `d`.
3. **Discord.** Summaries appear only for sweeps that changed something (`Merged #N`,
   `Rebased #N`, `Handed off #N (…)`). The operator has **not yet confirmed** that the `queued`,
   hand-off and merged messages arrived. Ask them.
4. **The batch.** For each of the 10 issues, find its run (scheduler history page, or
   `GET /runs` filtered client-side on `.labels.issue`). Record: lane, outcome (`queued`, `watch` =
   over the ceiling, demoted, rescued), coder-lease time, and whether the queue merged the PR.
   When all 10 have landed, the operator may add the next batch (see *Next batches* below).

## Step 8: the 7-day result (due 2026-10-18)

Write `docs/fast-path/result-2026-10-18.txt`, answering every bullet of task 10 step 8. Sources:

- `ops/fabro-export-runs.sh --since 2026-10-11T21:39Z --workflow backlog <dir>`. It skips
  `merge-queue` runs unless you name that workflow. **Stream** its events; never export raw event
  dumps for the whole window (that filled the disk once).
- The scheduler's run history (SQLite at `/data/scheduler.db` in the scheduler container, or its
  history page) for lease durations.
- `gh` for queue times: from the `approved` marker comment's `createdAt` to the PR's `mergedAt`.
- The merge-queue hand-off comments (`<!-- fabro:merge-queue handoff …`) for the reasons, split by
  the `retryable:` prefix.

The comparison that matters: coder-lease hours per closed issue, fast lane vs the standard lane, for
diffs of 200 lines or fewer, over the same 7 days. #23's baseline is p50 1.3 h for ≤ 20 lines and
1.6 h for 21-200 lines. Also report any queue merge a human reverted, and any merged PR whose `main`
CI went red afterwards.

Account for these when you read the numbers:

- The draft-14 scheduler shakedown window restarted twice on 2026-10-11 (21:58Z and ~22:33Z,
  scheduler deploys for coders-c and coders-d).
- The pools went from 2 to 4 that day, so standard-lane throughput also changed. Compare
  **per-issue** lease time, not totals.
- coders-d (freellm `auto`) routes each request to a different free model. Report fast-lane runs
  on coders-d separately if their outcomes differ.

## Step 9: close out

- Mark ADR 0023 and ADR 0024 **accepted**, or amend them with what the result showed. ADR 0024's D8
  table already says the cap is 8.
- Update 00's status line and the `docs/README.md` row.
- Comment on fabro-workflows#23 with the result and a link to it.
- Append a dated section to the deployment log.

## Open faults and loose ends

| # | Item | What to do |
|---|---|---|
| 1 | **lawncare#2764** (fast-path, priority) has not dispatched. The scheduler's diversity pass skips a repo with a run in flight, even for `priority` and Overrides, and lawncare had two (#2731, #2732, both Overridden). | Check it ran. If it is still waiting days later, that is a real gap in the scheduler, not the fast lane: record it. |
| 2 | **Leftover labels on closed issues**: jelly-swipe #454 and #455 keep `fast-path` and `priority`; wfs#1834 has `agent-in-progress`/`agent-stuck`, `priority` and `fast-path` | Harmless (the scheduler reads open issues only). Remove them if the operator wants a clean board. |
| 3 | `open_pr_prep` fails hard on a conflicting `git merge origin/main` in **both** lanes (wfs#1834). The fast lane is more exposed when a human fixes the same issue in parallel. | **Fixed 2026-10-12** after it recurred on wfs#1747 (run `01M4S16BRA0W1J3W32S6ENJBXH`, three trips through the gate on committed conflict markers). The merge moved to a new `final_merge` node, which hands a conflict to `resolve_merge` the way `next_task` does. `open_pr_prep` refuses committed markers, and `rescue_brief` names a work-already-on-main stop. |
| 4 | A transient `gh run view` failure in `inventory` leaves a failing check's `attempt` null, which `decide` treats as "cannot be rerun": a **non-retryable** hand-off. | Watch the hand-off reasons. If it shows up, retry the read in `inventory` or make that reason `retryable:`. |
| 5 | Markers count by the token's login (C7), and that login is the operator's own (`andrewthetechie`). A marker comment the operator types by hand counts. | Accepted. Mention it in the ADR if it matters. |
| 6 | The operator fixes this issue class by hand in "cluster" PRs (#1917, #1918). They stopped that agent at go-live. | Before labelling the next batch, check for open cluster PRs touching the same files. |
| 7 | **coders-d** (freellm) is unstable by design: a hung free model is not an error, so the stage times out instead of falling back. Its first `fabro model test` failed once (Network), then passed four times. | Count coders-d runs that end in a timeout or rescue. If they're frequent, drain it from the scheduler page. |
| 8 | **qwen3.8-27b** is the fallback after kimi-k3. It has not yet been seen in a live failover. `kimi-for-coding` (rework tier 3, the same Kimi quota) still falls back to glm-5.3. | Grep `agent.route.failover` for `orfallback`. Check spend on the OpenRouter key ($100 credit). Ask the operator before adding qwen3.8-27b to `kimi-for-coding`. |
| 9 | Do not store `OPENROUTER_API_KEY` (or a key for `fireworks`, `vercel` or `venice`): a built-in provider takes over unqualified `kimi-k3` (invariants-host). | Use a custom provider, as `orfallback` does. |

## Next batches

The operator said to grow the batch only after the first one lands. Rules that held for batch 1:

- One repository per batch, issues of one rule (#23's kind), no `priority` label.
- One file per issue, and no two issues in the same file, or their PRs conflict (DIRTY → hand-off).
- Verify each issue is open and its site still exists in `main`, e.g. that
  `biome-ignore … refactor tracked in #N` is still there. A stale issue makes a no-op or conflicting run.
- Check for the operator's own cluster PRs first (item 6).

## Commands

```sh
# Fire one repo's sweep by hand (no body); read its result
ssh andrew@10.10.0.32 'curl -fsS -X POST -H "Authorization: Bearer $(docker exec fabro-fabro-1 cat /storage/server.dev-token)" http://127.0.0.1:32276/api/v1/automations/merge-queue-<repo>/runs'
ssh andrew@10.10.0.32 'T=$(docker exec fabro-fabro-1 cat /storage/server.dev-token | tr -d "[:space:]"); curl -s -H "Authorization: Bearer $T" http://127.0.0.1:32276/api/v1/runs/<run>/state' | grep -o '"plan_[a-z]*":[0-9]*\|"mq_summary":"[^"]*"' | sort -u

# A run's route, stage by stage
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro events <run> -p'

# Coder leases now
curl -s http://10.10.0.32:32280/api/pools | jq -c '.[] | {coder_pool, lease}'
```
