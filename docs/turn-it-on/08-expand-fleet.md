# Task 08 — Expand the fleet, queue-driven, `lawncare-saas` last

**Depends on:** 07 (exit criteria met, evidence in the log). **Blocks:** 09, 10.
**LLM level:** no.

Turn on the remaining three `backlog` schedules. The order is not cosmetic — it is
driven by two facts: which repos have work, and which repo's merges deploy.

## 1 — Order and timing

Same PUT recipe as task 06, one repo at a time, in this order:

1. **`backlog-womens-fantasy-sports`** — it had a queue on day one (1 `agent`, 10
   `needs-triage`, the latter being promoted by triage all through the window). No
   branch protection, but no deploy-on-merge either; a bad merge here is a revertible
   commit on `main`.
2. **`backlog-writers-app`** — when it has a queue. If nobody filed issues during the
   window, leave the schedule off and say so in the log; enabling a schedule on an
   empty repo buys 96 quiet-exits a day and nothing else.
3. **`backlog-lawncare-saas`** — last, and only when it has a queue, because a merge
   there runs `deploy-main.yml`, `deploy-homelab.yml`, and `docker-main.yml`. Before
   enabling, re-read its `auto_merge` token and the host switch and confirm both are
   *deliberately* in their current states — this is the repo where "armed by absence"
   and "armed on purpose" must stop being distinguishable. If auto-merge should be on
   there, write the explicit `auto_merge=true` token now.

Leave at least one full day between enabling repos — the point of the stagger is that
a problem attributable to one repo's environment (the `ts` image vs the `rust-node`
image vs the `python-node` image) is observed before the next one joins.

## 2 — The concurrency cap: revisited, kept

ADR 0001 said re-enabling the staggered schedules "must be accompanied by revisiting"
`max_concurrent_runs = 3`. This is the revisiting, and the decision is **keep 3**:

- The schedules are staggered three minutes apart precisely so the fleet never fires
  four runs in one minute; the cap's normal state is headroom.
- Over-capacity runs **queue rather than reject** (verified). A delayed backlog run
  costs minutes; nothing is dropped.
- `issue-triage` is designed for exactly this: it reads `scheduler_slots_used` and
  quiet-exits when the host is busy, so contention self-resolves.
- The one real cost is the merge phase holding a slot for up to the 60-minute CI
  budget. With four active repos that is the plausible saturation case — and the
  monitor's C4 (stuck-run, 3h) bounds how badly it can go wrong unnoticed.

Record this paragraph's conclusion in the deployment log so the next revisit starts
from evidence, not from the ADR's caution.

## 3 — Verification after each enable

Per repo, within the first hour of its schedule:

- a run fires at that repo's staggered minute and behaves per its queue (claims, or
  quiet-exits with the queue empty — both correct);
- no monitor alert, no unexpected Discord kind;
- `fabro events <run> -p` on the first claiming run shows the expected acquire route;
- the other repos' behavior is unchanged (a schedule enable touching only its own row —
  read back the other rows if in doubt).

## 4 — Full-fleet steady state

With all enabled schedules live, the expected shape of a day: backlog fires per repo
every 15 minutes and mostly quiet-exits; triage fires hourly at `:30/:35/:40/:45` and
mostly quiet-exits; the bridge fires pr-review per opened PR; merges happen when the
gates pass. The monitor's C3 now guards the whole fleet, C5 fires when everything is
drained, and both are correct behavior, not noise.

One deliberate residual: `pr-review-*` rows remain never-fired-by-cron — the bridge
owns pr-review fires, and nothing in this task changes that.

## Acceptance

- Each enabled row reads back `enabled: true` from the server; each behaved correctly
  in its first hour.
- `lawncare-saas`'s auto-merge state is explicit (a written token, not absence) in
  whichever direction the operator chose.
- The cap-kept-at-3 reasoning is in the deployment log.
- Any repo left disabled is named, with the reason (no queue), in the log.
