# A rescue gate names its failure class, and offers only the exits that can work

**Status:** proposed (2026-09-22)
NOT YET IMPLEMENTED. The constraints below are measured against 0.354.0-nightly.0; the
staging is not committed. Nothing in `backlog` changes until it is.

`backlog` has one rescue gate. **27 edges enter `human_rescue` and three leave it** —
`[P] Accept partial`, `[X] Abandon`, and a freeform field. The gate was raised 18 times in the ten days to
2026-09-22, as far back as the container's logs reach; 15 of those were answered, and
the three that were not are the shape ADR 0002 predicts — a gate does not outlive a
restart. It cannot say why it fired, because nothing it renders is capable
of carrying a per-run fact, and the one field that does carry text shows something
unrelated to the failure. Depending on which of the 27 edges the run arrived on, one or
two of the three exits are wrong, and two of them have each caused a live incident.

This ADR records what the gate can and cannot express, why the present shape is not
fixable by rewording it, and the posture that replaces it: **a rescue gate is scoped to a
failure class it can describe in static text, and offers only the exits that work for that
class.**

## The gate renders three fields and controls none of them usefully

Read from the `interview.started` payload of run `01M33SDMZF55NAEV8JV29A8EA4`:

| Field | Source | What it can carry |
|---|---|---|
| `question` | the node's `label` | static text, fixed at authoring time |
| `options` | outgoing edge `label`s | static text, key plus label, no description field |
| `context_display` | the most recent **agent message** | whatever an agent last said, unrelated to the failure |

`context_display` is the field an operator reads, and it is the one with no relationship
to why the run stopped. Three cases, all of them incidents:

- **`01M33SDMZF55NAEV8JV29A8EA4`, 16:55:40.** The gate raised after `open_pr` died on a
  rejected push displayed the code reviewer's summary from 14:10 — "changes_requested with
  two blocking findings." Nothing about `open_pr`, nothing about git. `open_pr` is a
  command node and emits no agent message, so the gate fell back 2.8 hours.
- **`01M310TQEWFMPHZ3XG5T22X09W`.** `prep` failed on a pypi timeout as the second stage, so
  no agent had spoken at all. `context_display` was **empty, length 0**. Answered `[P]` in
  22 seconds. That is womens-fantasy-sports#1236, the empty PR.
- **`01M321VB0DC4N187GHA88RM0QB`.** `improve` timed out. The gate displayed the
  **decompose** agent's "Decomposition complete — 6 ordered tasks", which reads as a
  healthy run. Answered `[P]`. That is writers-app#949, the second empty PR.

In two of the three the operator was shown a success message from an earlier stage while
the run was broken. The field is not merely uninformative; it misleads, and it misleads
hardest on exactly the early failures where `[P] Accept partial` is most damaging.

## Three limits, measured

A throwaway package was validated against the container at 0.354.0-nightly.0. All three
results are from `fabro validate`, not from reading the source.

1. **Long, descriptive option labels are valid.** An edge labelled
   `[P] Accept partial - open a PR from whatever landed, skipping the remaining tasks`
   validates clean. The transition cascade strips `[A]`-style accelerator prefixes before
   matching, and the run above recorded `"answer": "P"`, so the accelerator carries the
   routing and the prose after it is free.
2. **`{{ … }}` in a human node's `label` is literal.** It raises
   `detemplated_attribute`, whose own fix text reads: "move the dynamic value into a
   `prompt`/`goal`/`model_stylesheet`."
3. **`prompt` on a `shape=hexagon` node is inert.** It raises `inert_attribute` — "only
   read by agent, prompt, parallel.fan_in nodes and has no effect here."

Together: **a human gate's question text is permanently static.** There is no attribute on
a hexagon that renders a per-run value, so no amount of rewording makes one gate able to
describe 27 different failures. That is the fact this ADR turns on. Dynamic text must
arrive through some other channel, or the gate must be split until its static text is true.

## The three exits are wrong for most of the 27 ways in

| Arrived from | `[P] Accept partial` | freeform guidance | `[X] Abandon` |
|---|---|---|---|
| `claim`, `prep`, `decompose`, `decompose_gate` — nothing built | **harmful**; this is the empty-PR incident | **broken**, below | the only sound exit |
| `coder`, `review`, `rework_t*`, the per-task gates | sound | **the best exit** | sound |
| `open_pr_prep`, `open_pr`, `pr_handoff`, `review_merge` | **loops** on a deterministic failure | **incoherent** — a code-editing agent aimed at a publishing fault | the only real exit |

The empty-tree floor added to `open_pr_prep` in `74009b3` contains the consequence of the
first row, but the operator is still asked to guess, with no information, under a clock.

## The freeform exit is the strongest one, mislabelled, and broken early

It is not a vestigial text box. `record_guidance` carries
`stdin_source="human.gate.text"`, writes `/tmp/fabro/feedback/rescue.md`, and routes to
`rework_t4` — the top rung, glm-5.3. `prompts/rework.md.j2` reads that file as its
**priority-1 input, explicitly overriding everything else**. It is the only exit that can
apply human judgement to the work. The gate's label calls it "Type guidance to retry",
which names neither the model, nor the scope, nor the fact that it supersedes the findings.

It is also unsound on early failures, and this is readable from the graph rather than
inferred. `record_guidance -> rework_t4 -> prep_review`, and `prep_review` opens with
`BASE=$(cat /tmp/fabro/task_base_sha)` under `set -e`. `task_base_sha` is written by
`next_task`. A run that failed at `claim`, `prep` or `decompose` has never reached
`next_task`, so guidance there spends a full glm-5.3 stage and then fails `prep_review` —
whose unconditional edge is `validate`, **not** the rescue gate. The run proceeds into CI
and review on a branch with no task context rather than returning to the human.

## A parked gate costs a scheduler slot

Every fact in ADR 0002 applies here and is why a slow, uninformed decision is expensive
rather than merely annoying: a blocked run counts toward scheduler capacity, nothing reaps
it, its sandbox stays up, and a server restart converts it to `run.failed`. One correction
to that ADR: `max_concurrent_runs` is now **4**, not 3, so a parked gate holds a quarter of
capacity. Run `01M33SDMZF55NAEV8JV29A8EA4` held one for 162 minutes, then 20 more, then
indefinitely.

ADR 0002's closing bullet — "`backlog`'s `human_rescue` gate is unbounded" — is **stale**.
The node now carries `timeout="4h"` and `human.default_choice="mark_stuck"`. The bound
exists; what is missing is any basis for the human to answer before it expires.

## Decision

A rescue gate is scoped to a **rescue class**: a set of failures whose sound exits are the
same, and which a static sentence can describe truthfully. A gate offers the exits that
work for its class and omits the rest. Where a per-run fact would change the answer, it is
published as a context key and delivered out of band, not squeezed into the gate.

Three classes in `backlog`, named for what the run has at the moment it stops:

- **Nothing built** — `claim`, `prep`, `decompose`, `decompose_gate`. No accept-partial.
- **Work in progress** — the coder, review, rework and per-task gate nodes. All three exits.
- **Publishing** — `open_pr_prep`, `open_pr`, `pr_handoff`, `review_merge`. No rework guidance.

## What an operator should be able to do

1. Read, without opening the run, which stage stopped and why.
2. Tell from the menu alone what each option will do to the branch, the PR and the issue.
3. Never be offered an option that cannot work from where the run actually is.
4. Know that typing guidance hands instructions to a glm-5.3 coder that supersedes the
   reviewer's findings, before deciding whether to type any.
5. Distinguish "no work exists" from "work exists and publishing failed" — the two cases
   that currently render identically and have opposite right answers.
6. Answer a publishing failure without burning a rework stage to discover it cannot help.
7. Get the same facts in Discord as in the web UI, because Discord is what is read first.
8. Find the reason after the fact, on the issue, when the gate has timed out or been lost
   to a restart.
9. Leave a gate unanswered for four hours and have the default do the least destructive
   thing for that class, rather than one default for all 27 entry points.

## Staging

**Tier 1 — describe the exits.** Rewrite the three edge labels and the gate label as
self-describing text. Limit 1 makes this valid today; it needs no new nodes and no new
mechanism. On the evidence above it alone would likely have prevented both empty PRs.

**Tier 2 — split the gate by rescue class.** Three hexagons, each with a true static
label, its own exit set, and its own `timeout` / `human.default_choice`. This removes the
`prep_review` landmine structurally rather than patching it: the guidance exit stops
existing on the classes where it cannot work.

**Tier 3 — publish the reason.** A small command node ahead of each gate emitting
`context_updates` for the stopping stage, the failure text and the task counts, consumed by
`discord-notify.sh`. The pattern is already proven in this repository: `issue-triage`'s
`triage_gate` publishes `triage_questions`, and the hook greps it back out of
`/runs/<id>/state` and puts it in the ping. Today the rescue ping carries no reason at all.

**Tier 4 — make it outlive the run.** Post the same reason as an issue comment, on ADR
0002's principle that a question must survive the run that asked it.

## What must be verified before Tier 3

`context_display` is modelled here as "the most recent agent message" from three
observations, not from the fabro source. If that model holds, a cheap `shape=tab` prompt
node ahead of the gate would populate it and fix the web UI as well as Discord; if it does
not, Tier 3 is Discord-only and the gate UI stays blind. **One run settles it, and it
should be settled before any work is planned around the gate UI.** Also unverified: whether
the web UI truncates long option labels (validation passes; rendering was not observed),
and whether `review_target` on a hexagon would surface the PR or issue as a primary link.

## Testing

The seam already exists and nothing new should be introduced. `ops/test-task-gates.sh`
extracts `script` attributes from the graph verbatim, rebases `/tmp/fabro` onto a scratch
directory and runs them against fixtures — it already covers `open_pr_prep` with a real
`git`, and it is where a Tier 3 diagnose node's script belongs. A good test here asserts
observable output: the exit status, and the text the operator would actually read. Tests
that assert which jq expression produced it are testing the implementation.

What is and is not reachable offline:

- **Tier 1** is graph structure. `fabro validate` plus `ops/check-routing-schemas.py` are
  the gates; the accelerator-prefix routing is fabro's own and needs no test here.
- **Tier 2** is routing. `fabro validate` catches unreachable nodes and bad conditions;
  `human.default_choice` naming a node that is **not** an edge target is not validated by
  fabro (ADR 0002), so each new gate's default needs checking by eye or by a small
  structural assertion.
- **Tier 3** is a command node, so it is testable in full at the existing seam: the class
  fixtures are cheap, and the assertions are on the emitted `context_updates`.
- **The gate render itself is not offline-testable.** It is fabro runtime behaviour. The
  limits above were established by validating a throwaway package, which is the most that
  can be done without a live run.

## Out of scope

- Fabro's own gate UI. Nothing here proposes a change to fabro; the posture is built from
  what the current binary does.
- `issue-triage`'s `ask_human`, which is already single-class, bounded and truthful, and is
  the shape this ADR generalises.
- `pr-review`'s `mark_needs_human`, which reports rather than asks.
- The unbounded `human_rescue -> open_pr_prep` retry. Splitting the classes makes the
  publishing loop cheap and legible but does not add a counter; that is separate.
- Making a rescue unnecessary. The rework ladder, task sizing and the stall watchdog are
  the levers there.

## Consequences

- A new rescue class means a new gate, not a new edge into an existing one. The cost of
  the old shape was that adding an edge was free and silently widened the menu's inaccuracy.
- Three gates mean three `human.default_choice` values to keep correct, and fabro validates
  none of them against the graph. The publishing class in particular must not default to a
  choice that discards a finished branch.
- `CONTEXT.md` gains **Rescue class**, and **Rescue gate** needs an entry; "the rescue gate"
  stops being a well-defined phrase once there are three.
- `discord-notify.sh` is executed by hooks from `/storage/scripts/`, so any Tier 3 change to
  it is a deploy, not a merge.
- Tier 1 is separable and carries none of the above. It can ship alone.
- Until Tier 2 lands, `[P] Accept partial` on an early failure remains possible and is
  contained only by `open_pr_prep`'s empty-tree floor — which refuses *after* the operator
  has already chosen wrongly.
