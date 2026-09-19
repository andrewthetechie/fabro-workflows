# Compaction is not a cost; task size is

Written 2026-09-18 from run `01M2TFEHPYXXTQST6VPXREA854` (`backlog` on
womens-fantasy-sports#1196, PR #1206 merged), read from
`/api/v1/runs/<id>/events` paginated on `since_seq` and `/runs/<id>/stages`.
Every number here is measured.

Read `00-overview-and-measurements.md` first. This extends it with one finding
that inverts an obvious-looking conclusion, and records the change made because
of it.

## The claim this replaces

A review of the run attributed roughly two hours to a single conversation
compaction, on the grounds that the compaction landed inside the longest stage
of the run — a `coder` stage that spanned 15:55 → 19:10 — and proposed
compacting earlier or more aggressively.

The compaction cost **5 minutes 16 seconds**, once, in an 8h29m run. That is
1.0% of wall. The two hours belong to something else.

## What that stage actually was

`/stages` reports only the **latest attempt** of a stage, which is why the 3h15m
looked like one long run of work. The event stream shows two attempts:

| | |
|---|---|
| attempt 1 | 15:55:40 → 18:55:40, killed at `wall_time_ms: 10800004` — the node's `timeout="180m"` |
| attempt 2 | 18:55:45 → 19:10:45, 14 min, succeeded |

Inside attempt 1: 199 LLM calls, 208 tool calls, and **169.2 min of the 180 min
was active generation** (94%). The last events before the kill are an ordinary
tool → LLM cadence at 18:54:58. It was not stalled, not looping, and not
recovering from the compaction — it was mid-work when the wall clock fired.

```
18:24:15  agent.warning  context_window  209881 / 262144  (80%)
18:24:15  agent.compaction.started
18:29:31  agent.compaction.completed     276 -> 7 turns      (+5m16s)
18:55:40  stage.failed   "handler timed out after 10800000ms"  will_retry: true
18:55:45  stage.started  attempt 2 of 2
19:10:45  stage.completed
```

The compaction fired 2h29m into the stage and 31 min before the timeout.

## There is no idle time to reclaim

`stage.failed` records `inference_time_ms: 0`, so the run totals **exclude the
timed-out attempt entirely**. Measuring inference from the events instead:

```
run wall                509.2 min
run inference (API)     264.8 min   <- excludes attempt 1
measured inference      434.2 min   over 977 calls
difference              169.4 min   ~= the 169.2 min measured inside attempt 1
```

So the run's apparent "61% active, 3.3h idle" is an artifact of that exclusion.
With tool time added the run was **~94% busy generating tokens**. Real dead wall
was about 32 minutes:

| loss | cost | recoverable? |
|---|---|---|
| `glm-5.3` hang on `standards` v2 | 15m00s | yes — known, `00-` item 2 |
| `watch_checks` (GitHub Actions) | 16m30s | no — CI floor |
| compaction | 5m16s | not worth it |

The `standards` hang is the documented signature exactly: normal work until
20:15:46, then `agent.llm.started` and **total event silence** until the node
timeout at 20:30:36. Zero tokens produced.

## Why "compact earlier" is the wrong lever

- **There is no knob.** No compaction attribute exists on agent nodes
  (`fidelity`, `max_tokens`, `reasoning_effort` and the rest do not touch it),
  and nothing in the runtime settings surface exposes one.
- **More compactions cost more.** A compaction is one summarisation pass over
  the whole conversation. It took 5 min *because* the box runs at 17.4 tok/s.
  Firing at 60% instead of 80% buys two or three of them at nearly the same unit
  cost.
- **The trigger is correctly placed.** Both coder boxes report
  `n_ctx: 262144` (`10.10.0.29:8000/props`, `10.10.0.56:8000/props`), so the 80%
  threshold is against a real window, not a wrong default.

## What the compaction is worth: a sizing alarm

276 turns and 209,881 tokens on one task is evidence the task was too large for
one stage. The decomposer had already said so, in prose, in its own summary:

> 1. `fix-squads-copy-no-actionable-422` — the standalone, reachable-now fix for
>    **parent criterion 2**
> 2. `resolve-squad-world-in-one-module` — the consolidation for **criteria 1, 3,
>    4, 5, 6**

One criterion → 46 min. Five criteria → ~194 min. Across the 20 retained
coder/rework stages on this server the median is 24 min and p90 is 47 min, so
that task was about **4x p90**. (That sample only counts latest attempts, so the
real tail is worse than it shows.)

This is the third recorded overrun of this same task; the other two are in
`workflow.fabro` beside `coder`, from run `01M2SBJG92TX0DBRKY5881VM34`.

## Why fabro has this problem and Sandcastle did not

Sandcastle's coder had **no wall clock** — `--idle-timeout` (fails only on
silence) and a livelock watchdog (5 identical tool calls, unchanged worktree).
Both measure progress, so an oversized task simply ran longer and finished. Size
was a latency property, never a failure mode.

The decomposer prompt is a faithful port of that component
(`docs/backlog/06-prompts-extra-review.md` → `extra-issue-decomposer-prompt-prd.md`),
and every splitting rule in it is about cohesion and independence. One rule —
"a single cohesive change is still one implementation task" — actively pushes
against splitting, which is correct in a runtime with no ceiling and wrong under
`timeout="180m"`.

## What changed

1. **`covers` is now a field** on `decomposition.json`, `extra/followups.json`
   and `tasks.json`: the distinct parent requirements a task delivers.
   `decompose_gate` and `extra_gate` both normalise it (a missing or wrong-typed
   value becomes `[]`) and both warn above three, but neither **rejects** —
   rejection costs a full retry round and some issues carry one genuinely fat
   criterion. Both prompts say plainly that the field is unenforced and that
   omitting it turns the size check off, rather than implying a gate that would
   catch it.
2. **The merge rule is bounded.** "Merge overlapping work when one change
   resolves it together" now carries "provided the merged task still fits the
   size budget".
3. **`improve` can `split`.** It runs per task, immediately before the coder,
   against the checkout as it actually is, so it can judge what work genuinely
   remains. It returns 2–4 ordered slices; `improve_gate` splices them into
   `tasks.json` **in place** at the task's own slot and rewinds `task_index`, so
   `next_task` resumes on the first slice. Appending — the way `extra_gate` does
   — would run them out of order.
4. **Two independent loop guards.** Slices are stamped `source: "split"` and can
   never be split again, and `split_rounds` caps the run at 2. Either alone
   coerces the disposition to `ready` and leaves `current_task.json` untouched,
   so a refused split still gets implemented rather than failing the run.
5. **`ops/fabro-run-status.sh`** attributes each compaction to its stage and
   flags implementation stages as oversized. Its old `grep -c 'compaction'` was
   unanchored and would have counted agent prose. It also reads `covers` off the
   sandbox's `tasks.json`, which is the signal that arrives in time to matter: a
   compaction only fires ~2h29m into a 3h stage, whereas an oversized *current*
   task is visible the moment it is selected.
6. **`ops/test-task-gates.sh`** runs the four queue gates against fixtures,
   offline. 71 checks. It runs each extracted script under `sh`, not `bash`, and
   `sh -n`s it first, because AGENTS.md requires POSIX `sh` inline and a harness
   running them under bash would hide the bashism it exists to catch. The cursor
   arithmetic is the part worth pinning: an off-by-one in the splice silently
   skips a task instead of failing.
7. **`next_task` deletes `improve_result.json`.** AGENTS.md: "Delete a contract
   file before the agent that writes it runs". This graph had no such delete
   anywhere, which `split` made materially worse — a stale result no longer just
   mis-improves one task, it can splice the *previous* task's slices into the
   queue. The remaining contract files still have the pre-existing exposure.

## What was not changed, and why

- **`coder`'s `timeout="180m"`.** Attempt 1 was roughly 93% done when it was
  killed — attempt 2 emitted only 5,930 output tokens yet the diff grew from
  4 files/+207 to 15 files/+996, so it inherited nearly everything from the
  uncommitted worktree. A larger ceiling would have finished around 194 min
  either way. `max_retries=1` resuming in the same sandbox is what made the run
  ship, and `allow_partial` never had to fire.
- **The monitor's treatment of post-merge `failed`.** The run's
  `failed/publish_failed` was real but is already fixed by `a75d69c`
  (`--delete-branch=false`); teaching the monitor to expect it would suppress a
  signal that should no longer fire.
