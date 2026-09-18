# A coder instance is leased for a whole run, not per stage

**Status:** accepted (2026-09-18)

A `backlog` run spends most of its life not touching a coder. On run
`01M2TFEHPYXXTQST6VPXREA854`: `decompose` 9.7m, `improve` 4.7m and 3.1m, `review` 4.6m,
and the entire review-and-merge phase, all on hosted models (`glm-5.3`, `kimi-k3`). Only
`coder`, `rework_t*` and `review_merge.ci_fix_t1` resolve to `coders`. Holding one of
two llama.cpp boxes for the whole run therefore idles it for the majority of that run.

We hold it anyway.

The alternative — acquire a lease on entering a coder-class stage, release it at the
stage boundary — is strictly better for utilisation and was the first preference. It was
deferred for two reasons, one practical and one that turned out to be decisive.

The practical one: fabro has no mechanism for "this stage waits for an external
resource". A stage either runs or it does not. Implementing per-stage leasing means the
scheduler holding a run in `submitted` until a box frees, or interposing a gate node
that polls the scheduler — the first re-introduces the frozen-inputs problem ADR 0005
rejects, the second puts a blocking network call inside every coder stage of every run.

The decisive one came from asking when a lease would be *released*. The natural answer
is "when the run blocks on a human gate" — the one long wait that certainly needs no
coder, and since commit `644cfdd` it can last four hours. But a human can answer that
gate in ten seconds, and `human_rescue`'s freeform edge routes straight back into work
that needs a coder. A released lease would then have to be re-acquired mid-run, behind
whatever else the scheduler has since dispatched, with the run holding a fabro slot the
entire time it waits. Per-stage leasing is not "the same design with a shorter lease";
it requires the run to be suspendable, and it is not.

So a lease runs from dispatch to the run's terminal state, and the cost is accepted:
with two boxes and one-run-per-repo, peak concurrency is two runs, and each spends real
time holding a box it is not using.

This is deferred, not rejected. The contract that keeps the door open is that a lease is
already modelled as `(box, repo, issue, run_id, dispatched_at)` — a relationship between
a box and a *unit of work*, not a property of the run. Narrowing the unit from "run" to
"stage" later changes when `release()` is called; it does not change what a lease is.
The thing that would foreclose it is optimising the rest of the system on the assumption
that one run equals one box for its lifetime — in particular, sizing the box count to
the repo count. Do not do that.
