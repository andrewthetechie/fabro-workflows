# Task 10 — Correct `AGENTS.md`, `ops/README.md` and `CONTEXT.md`

**Depends on:** 09. **Blocks:** 11. **LLM level:** ordinary.

Do this **after** the first live run, not before. Half of what goes in these files is
only known once a run has happened, and a document that describes an intention as though
it were a deployment is worse than one that says nothing.

## `AGENTS.md`

- **Opening line.** "Two production Fabro workflows" becomes three.
- **Layout table.** `.fabro/workflows/<name>/` says "Two packages: `backlog`,
  `pr-review`" — add `issue-triage`. Add a `docs/issue-triage/` row pointing at
  `00-overview-and-contracts.md`.
- **Validating.** Add the new baseline beside the other two:
  `IssueTriage (15 nodes, 35 edges)` clean, or whatever task 08 actually recorded.
- **Deployment invariants.** Four new rows, each one a rule that passes `fabro validate`
  and fails at runtime:

| Rule | What happens otherwise |
|---|---|
| Every human gate carries both `timeout` and `human.default_choice`, and the default choice is also an edge target | A gate with no timeout waits forever, holds one of three scheduler slots, and is failed by the next server restart. A timeout with no default choice re-asks the question instead of advancing. A default choice naming a non-target is a dangling route at runtime — Fabro validates none of the three. ADR 0002. |
| Every comment `issue-triage` writes carries an HTML-comment marker, except the one recording a human's answer | The automation posts as the repository owner, so author comparison cannot tell bot from human. The return path reads markers; an unmarked bot comment makes an issue look answered forever, and a marked answer comment makes an answered issue look ignored. |
| `issue-triage`'s capacity check gates on `runs.scheduler_slots_used`, not `runs.active` | `active` also counts `Pending` and `Runnable`. A run queued behind us would keep triage out permanently. |
| A triage title never contains `!` or `BREAKING CHANGE:`, and uses only the ten CI-accepted types | `release-please` runs on two of the four repos; the title seeds the PR title, and an exclamation mark cuts a major version. `style` is not among the types `amannn/action-semantic-pull-request` accepts. |

- **Deploying.** `issue-triage` is workflow-only, so it needs no deploy — but
  `ops/provision-litellm-models.sh` joins the `ops/` list, and `settings.toml` changes
  now require a restart taken while the host is idle. Add both, and add the LiteLLM
  drift check to the "confirm the host still matches the repo" block.

## `ops/README.md`

- **Automations table.** Four new rows, with their environments and their hourly
  crons, and whether the schedules ended up enabled.
- **Contents table.** `provision-litellm-models.sh`.
- **Install steps.** A new step: LiteLLM's `high-reasoning` model and its fallback,
  positioned before the automations step, because a run that fires without it fails at
  its first agent stage. Note that LiteLLM models are Postgres rows created through the
  Admin UI, not config — the same class of invisible state as the automations.
- **Secrets.** Nothing new. `FABRO_API_TOKEN` is reused; say so explicitly, and say that
  reusing it means `issue-triage` inherits the same blunt kill switch and the same
  inability to rotate independently.

## `CONTEXT.md`

The glossary describes the system, so these land only once the system has them. Five
terms:

**Triage run**: One execution of the `issue-triage` graph. Acquires at most one issue,
and quiet-exits in seconds when the host has another run or the queue is empty.
_Avoid_: triage job, triage pass.

**Capacity gate**: `issue-triage`'s first node, which reads
`runs.scheduler_slots_used` from the fabro API and quiet-exits above 1. It is what makes
"trigger when the coders are not busy" a real condition, and it is why at most one
triage run can be blocked on a human at a time.

**Question batch**: The complete set of blocking questions one triage run produces.
One batch per run, never incremental; successive runs are the rounds. A batch posted to
an issue carries the `fabro:triage-questions` marker.

**Marker**: An HTML comment opening a comment this deployment wrote. The only reliable
way to tell an automation comment from a human one, because both are authored by the
repository owner's token.

**Promotion**: The `ready` outcome of a triage run — `needs-triage` off, `agent` on —
which is the only automatic path into `backlog`'s queue.

Also correct the opening line: "Two production Fabro workflow packages" becomes three,
and the `Coder pool` entry's "accepted unknown" is unchanged by this work — say nothing
new about it.

## Acceptance

- Every count, baseline and cron in all three files matches what `fabro validate` and
  `GET /automations` actually return, checked the same day.
- No file claims a schedule is enabled that is disabled, or the reverse.
- `CONTEXT.md` gains vocabulary only. No implementation detail, no contract shapes —
  those live in `docs/issue-triage/00-overview-and-contracts.md`.
