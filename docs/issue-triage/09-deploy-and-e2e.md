# Task 09 — Deploy, then a real run on `womens-fantasy-sports`

**Depends on:** 08. **Blocks:** 10, 11. **LLM level:** smarter model recommended — this
is the task that reads a live run's events and decides whether the design survived
contact.

Pushing to `main` deploys: every automation resolves `workflow_source` to
`andrewthetechie/fabro-workflows@main` at fire time, so the graph is live on the next
fire with nothing to copy. `ops/` and `discord-notify.sh` are not; deploy those by hand.

## Order

1. **LiteLLM first** (task 01). A graph that is live before `high-reasoning` exists
   fails every run at `improve`.
2. **`settings.toml` and the restart** (task 02) — while `scheduler_slots_used` is 0. A
   restart fails every in-flight run, including any `backlog` run parked on
   `human_rescue`.
3. **`discord-notify.sh`** (task 06), and diff the host copy.
4. **Merge to `main`.** The graph, the prompts and the TOML go live.
5. **Automations** (task 07), schedules still disabled.
6. **One manual fire**, below.
7. **Enable all four schedules** only after step 6 passes.

## The first fire

`womens-fantasy-sports` is the only repository with a queue — 11 open `needs-triage`
issues as of 2026-09-16 — so it is the only one that can prove anything:

```sh
curl -fsS -X POST -H "Authorization: Bearer $TOK" \
  http://10.10.0.32:32276/api/v1/automations/issue-triage-womens-fantasy-sports/runs
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro events <run> -p'
```

`fabro events -p` shows which edge was selected and why. Read the routing decision, not
just the outcome: a wrong-route bug in this graph looks like a successful run that
labelled nothing.

## What the first run must demonstrate

| # | Check |
|---|---|
| 1 | `check_capacity` emitted `host_busy=false` and routed to `acquire`. Fire a second run while the first is going and confirm the second quiet-exits at `check_capacity` with `host_busy=true`. |
| 2 | `acquire` picked the **lowest-numbered** open `needs-triage` issue. |
| 3 | The original body is on the issue as a comment carrying `fabro:triage-original`, posted **before** the body changed. |
| 4 | The issue body was rewritten and the title was **not** — the title changes once, at the end. |
| 5 | The final title is Conventional Commits, from the ten accepted types, with no `!`. |
| 6 | Whichever terminal path ran left the labels correct: `needs-triage` gone, `triage-in-progress` gone, and `agent` / `needs-info` applied as appropriate. |
| 7 | Exactly one triage comment, carrying its marker. |
| 8 | On a `needs_info` run: the Discord ping arrived carrying the actual questions and the run link, **before** the gate blocked. |
| 9 | Answer the gate in the web UI with a real answer and confirm the run re-triages once, then terminates without blocking a second time. |
| 10 | Separately, let one gate time out. It must route to `post_questions`, post the batch and exit — not retry the question, not fail the run. |
| 11 | Reply to that issue as a human, wait for the next fire, and confirm `acquire` re-selects it because the newest comment carries no marker. |
| 12 | Three quiet-exits: fire the other three repositories and confirm each exits at `acquire` in seconds, having cloned but started no LLM session. |

Checks 9, 10 and 11 are the ones that cannot be proven any other way: they are the
whole human-in-the-loop design, and each has a failure mode that looks like success.

## If a run strands a claim

`triage-in-progress` left on an issue is recovered automatically by the next
`acquire` — decision 3 guarantees no other triage run exists while it is running, so
any claim label it sees is abandoned. Confirm that once, deliberately, by killing a run
mid-flight and watching the next one reclaim the issue. Do not add a sweeper before
seeing whether that mechanism works.

## Rollback

There is none for issues already edited. The kill switch is the four schedule triggers:
disable them and triage stops at the next hour, with any in-flight run finishing. The
bluntest switch is the `FABRO_API_TOKEN` vault entry, which also stops `backlog`.

An issue this workflow damaged is repaired from the `fabro:triage-original` comment,
which is exactly why it is posted before the first write.

## Acceptance

All twelve checks, with the run ids, pasted into the deployment log entry task 11
writes. A check that was not run is recorded as not run.
