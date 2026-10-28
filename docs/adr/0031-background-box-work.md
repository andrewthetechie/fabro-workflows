# Coder boxes run background work between the coder loop's box stages

**Status:** proposed (2026-10-28). The decisions below are the operator's, from the planning
session of 2026-10-27/28. The task series is `docs/plans/more-local-inference/`. This ADR
becomes accepted when that series' Phase 2 exit check passes (task 12).

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

## D2. Background work is whole fabro runs, cancelled by the coder loop

Background work is a **background run**: a run of a box-only package, whose every stage is on
the box and spends no hosted credits. At most one background run is on a box at a time.

Before each box stage, the coder loop runs a blocking `stage_start` hook, `fabro-io
box-claim`, in its own sandbox. It cancels any background run on its box through fabro's run
API, then waits until strix-infer reports the box idle. It does not depend on the scheduler,
and it exits 0 on any error of its own, so it can delay a box stage by seconds but never stop
one.

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
run on a drained box. One switch, `SCHEDULER_BACKGROUND`, turns the lane off.

## D4. The PR doctor gets one attempt per pull request

The first background job is the **PR doctor**. It fixes a blocked factory pull request on the
box, using that PR's own merge-phase report. The scheduler then fires exactly one hosted
review (`pr-review`). If that review does not end merged or queued, the PR gets
`ai-review-needs-human`, and a human must act. A pull request is doctored at most once, ever.

## D5. Out of scope

- **A credit governor** that admits extra hosted runs while z.ai credits are below pace.
- **A fabro upgrade.** Everything here runs on 0.362. A later upgrade must skip 0.370 to 0.386,
  where `stall_timeout` was not enforced (fabro PR #1032).
