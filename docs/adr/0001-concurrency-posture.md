# Concurrency posture: one global cap, schedules off until staggered, known pinch at GLM 5.3

**Status:** accepted (2026-09-16)

The system's only hard concurrency control is the server's global
`server.scheduler.max_concurrent_runs = 3`; both workflows are sequential graphs, so at
most three LLM sessions are ever live, one per active run. A deliberate backpressure
test on 2026-09-16 (four simultaneous manual `backlog` fires with no `agent`-labeled
issues open) settled the long-open question from the pr-review-bridge docs: **fabro
queues at the cap, it does not reject** — the fourth run was created at 03:37:34.150Z
with the other three, but did not start until 03:37:45.183Z, one second after the first
of the three completed (03:37:44.160Z). Note `lifecycle.queue_position` stayed `null`
even while the run was queued, so the timing pattern — not that field — is the evidence.
A queued `trigger_review` fire is therefore delayed, never dropped.

Against the operator's capacity (2 local coders × 1 session each, GLM 5.3 × 3 concurrent
sessions, assumed **account-wide across the z.ai plan** so glm-4.7 fallbacks share the
same budget) the pinch point is glm-5.3: three active runs can all be in glm-5.3 stages
simultaneously (backlog runs decompose/improve/review/standards/quality there; pr-review
runs everything except the first rebase there), and both workflows' fallback chains route
*into* glm-5.3, so a rate-limit anywhere can push demand to four. `max_concurrent_runs`
stays at 3 anyway: over-cap demand degrades into the documented fallback chains, which is
the accepted cost of keeping throughput. What happens when both local coders are busy
(queue at the inference server vs. error into cloud fallback) is an accepted unknown —
not worth the GPU time to test; either outcome is tolerable.

The four backlog schedules stay **disabled** (operator decision of 2026-09-14 stands);
when re-enabled they will be **staggered** (e.g. `2-59/15`, `7-59/15`, `12-59/15`,
`17-59/15`) rather than all on `*/15`, so the four automations never fire in the same
minute and never spike glm-5.3 with four simultaneous decompose stages. Two active runs
in the same repo on different issues is accepted: the `agent-in-progress` label prevents
same-issue collisions, and the mainline-merge discipline in `next_task`/`open_pr_prep`
handles the branch collision.

## Consequences

- The circular fallback `glm-5.3 → kimi-k3 → glm-5.3` in both workflows is left as-is;
  under sustained account-wide saturation it is the first thing to revisit.
- `trigger_review`'s `on_failure="succeed"` design is validated by the queuing finding:
  a bridge fire at cap waits rather than failing, so the silent-empty-review-queue
  scenario requires an actual API error, which the Discord hook reports.
