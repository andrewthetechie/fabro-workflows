# Coder boxes run background work between the coder loop's box stages

**Status:** proposed (2026-10-28). The decisions below are the operator's, from the planning
session of 2026-10-27/28, revised the same day after a review against the repository. The task
series is `docs/plans/more-local-inference/`. This ADR becomes accepted when that series'
Phase 2 exit check passes (task 12).

The two coder boxes (`coders-a`, `coders-b`, both serving qwen3.8-flash-next through
strix-infer) are free to run and have no capacity limit, but they sit idle most of the day.
Since 2026-10-21 the scheduler leased them for 432 hours, and box stages ran for 158 of
those hours. The other 274 were gaps between the coder loop's box stages: hosted stages,
command nodes and CI waits. Their median is 11 minutes, and 253 of the 274 hours fall in
gaps of 10 minutes or more. The hosted models are not free. The z.ai Coding Plan ran out of
weekly credits on 2026-10-27, and OpenRouter and OpenAI share a fixed $100.

So idle box time should do work that needs no hosted model, and that work should raise the
merge rate or lower hosted token use. Only 44 of 176 runs since 2026-10-20 merged, and every
run that did not had already paid for its hosted stages.

## D1. The coder loop always wins the box, one stage at a time

A **coder loop** is a `backlog` or `ralph` run (or a `pr-review` run the scheduler leases). It
keeps absolute precedence on its box. A box runs one fabro stage at a time and never
interleaves two sessions, because interleaving requests thrashes strix-infer's KV cache.

A box changes hands only at a stage boundary. Each stage starts a fresh session, so the
handover costs nothing in the cache. Holding the box across a whole backlog task (coder,
then hosted review, then rework) was considered and rejected: it buys no cache reuse and
gives up the gap.

## D2. Background work is whole fabro runs, and three layers keep it off a busy box

Background work is a **background run**: a run of a box-only package, whose every stage is on
the box and spends no hosted credits. At most one background run is on a box at a time.

Before each box stage, the coder loop runs a blocking `stage_start` hook, `fabro-io
box-claim`, in its own sandbox. It cancels any background run on its box through fabro's run
API, then waits until strix-infer reports the box idle. It does not depend on the scheduler,
and it exits 0 on any error of its own, so it can delay a box stage by seconds but never stop
one.

The claim runs once, at stage start, so it cannot see a background run created just after it.
Two more layers close that gap. The scheduler reads the loop's stage fresh before it starts a
background run, and on every tick it evicts any background run on a box whose loop is in a box
stage. Each background package also runs a `fabro-io box-guard` hook before its own agent
stages, which cancels its own run when the box's loop is in a box stage or the box is busy.

Rejected:
- **A proxy in front of each box** (priority queue, gap-filling, pre-emption). It puts new
  code in the coder loop's path, and the LiteLLM stalls of September are why fabro talks to
  providers directly (ADR 0007).
- **Request-level interleaving** of two runs on one box: KV-cache thrash.
- **A blocking hook that waits for a box another stage holds.** A blocking hook's wait counts
  toward the run's `stall_timeout`; verified on the host on 2026-10-28.
- **A parallel branch inside the coder loop's run.** Fabro's join waits for every branch, so
  the coder loop would wait on the background work.
- **Scheduled automations as the background lane.** An automation is per repository and
  carries no input, so it would need about ten rows firing mostly empty runs.

## D3. The scheduler decides when

The coder scheduler (ADR 0005, 0006) starts a background run only when two things hold. The
coder loop on that box must be outside a box stage, and strix-infer must report the box idle.
It labels every run it starts with its box and lane. Fabro 0.370 to 0.396 adds no cross-run
priority or per-pool concurrency, so the scheduler stays the admission controller.

Background runs ignore the peak-hours pause, because they spend no hosted credits. They never
run on a drained box. One switch, `SCHEDULER_BACKGROUND`, off by default, turns the lane off.

## D4. The PR doctor gets one attempt per pull request

**Retired 2026-10-30.** The operator retired the doctor before its first run. It holds its box
for about 40 minutes, and the measured gaps do not allow that: half of all gap time is in gaps of
30 minutes or more, but no stage outside a rescue gives even odds of 41 free minutes, so the
lane cannot tell which gap to start it in. The canary job waited 23 hours without starting. The
lane keeps only jobs that fit a gap of about 10 to 20 minutes. What follows is the decision as
it stood.

The first background job is the **PR doctor**. It fixes a blocked factory pull request on the
box, using that PR's own merge-phase report. It records the attempt in a marker comment before
it writes anything else, and it removes `ai-review-needs-human` before it pushes, because the
merge queue hands off any PR that carries that label. The scheduler then fires exactly one
hosted review (`pr-review`) as a leased coder loop. If that review does not end merged or
queued, the PR gets `ai-review-needs-human` back, and a human must act. A pull request is
doctored at most once, ever.

## D5. Background output reaches hosted stages through a Stage I/O input

A background job publishes its result as a marker comment, edited in place. The hosted stages
do not read issue comments: the `issue.json` the coder loop fetches has none. The coder loop's
`claim` node therefore copies the newest marker of each kind into one optional Stage I/O
input, `background.md`, which `decompose` and `plan` read. Repository orientation is kept in
one pinned issue per repository, not in a pull request, so it needs no hosted review.

## D6. Out of scope

- **A credit governor** that admits extra hosted runs while z.ai credits are below pace.
- **A fabro upgrade.** Everything here runs on 0.362. A later upgrade must skip 0.370 to 0.386,
  where `stall_timeout` was not enforced (fabro PR #1032).
