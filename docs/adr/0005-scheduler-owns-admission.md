# The scheduler owns admission; fabro's cap becomes a backstop

**Status:** accepted (2026-09-18)

Fabro has a run queue, and it cannot be steered. Ordering is strict FIFO on run
*creation* time (`lib/apps/fabro-server/src/server.rs:4538`, sorting the `created_at`
captured at `handler/lifecycle.rs:185`), there is no priority field anywhere in
`RunIntent` (`lib/foundation/fabro-types/src/run_intent.rs:10-42`), and
`lifecycle.queue_position` is hardcoded `None` in the only production construction site
(`lib/components/fabro-store/src/run_state.rs:1212`) — the one thing that computes it is
`#[cfg(test)]`. The queue is real, it is observable only as "some runs are `runnable`",
and nothing about it can be influenced from outside.

That is fine for one repo with one schedule. It is not fine for the thing this
deployment actually needs: *N* repos with priorities competing for *N* inference boxes
that each serve one request at a time. So an external scheduler holds the queue and
releases exactly one run at a time, and `server.scheduler.max_concurrent_runs` — one
global integer, with no per-pool, per-label or per-resource variant anywhere in the
codebase (`lib/foundation/fabro-types/src/settings/run.rs`, greps for
`semaphore|pool|capacity|lease|admission` return only intra-run parallel fan-out and
ETag revisions) — is raised to 4 and left as a safety net.

The interesting rejected alternative is parking work in fabro as `submitted` runs.
`POST /runs` creates without starting, `submitted` rows are the only ones a server
restart does *not* fail (`server.rs:3101-3139`), and fabro's own kanban would then show
the backlog for free — a real gain, since the alternative is building a UI. It was
rejected because a `submitted` run has already frozen its `workflow_version_id`,
environment and inputs, and one of those inputs is `coder_pool`. Re-deciding which box
an item goes to — the operator override that motivated this whole design — would mean
deleting and recreating the run. Committing to a box at enqueue time to gain a queue
view is exactly backwards.

The cost is that fabro's cap now guards a resource nobody is contending for, and
`issue-triage`'s `check_capacity` stage, which reads `scheduler_slots_used > 1` and
stands down, becomes *more* timid the more headroom we add. That stage is deleted rather
than retuned: it was compensating for a cap of 2, and triage needs no coder.

The second cost is honest and unresolved: there are now two schedulers in the system,
and only one of them knows about coder instances. A run fired by hand — the manual
`curl` against an automation, or a `pr-review` fired outside the helper — takes a box
the scheduler believes is free. The `coders` model group is retained precisely so those
runs still work, and they will land on whichever box LiteLLM picks. This is tolerated,
not solved.
