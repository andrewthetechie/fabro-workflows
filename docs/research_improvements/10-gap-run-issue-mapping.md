# Gap — the web UI cannot show which issue a run is working

**Observation:** opening a run in the Fabro web UI gives no way to tell which issue it
claimed, so mapping issue → run means reading the Discord message or scraping
`/runs/{id}/state`.

**Conclusion: there is no in-run lever. This is a genuine product gap, not something we
have failed to configure.** The workaround is to invert the mapping.

**Status 2026-09-24: mostly solved from outside the run, and one premise is out of date.**
The table below says `backlog` is "fired by automation schedule" with no inputs. Since
scheduler draft 10 that is false. The scheduler creates every `backlog` run with
`args.inputs = {issue_number, coder_pool}` and `labels = {source: "scheduler", issue}`.
Its `/history` page (ADR 0008) maps each issue to its run, PR and outcome, which is the
mapping this gap asked for. The run title is still the generic goal: every scheduler run
in the store is titled "Backlog: decompose the next agent-labeled issue, …", even though
`issue_number` is present at create time. Either the LLM title generator described below
(read from the 0.357 source) does not run on the deployed 0.354, or it does not use the
inputs. This is not verified. Either way, the scheduler builds the `RunIntent` itself, so
the cheap fix is now the deterministic one 03 item 2 proposed for `pr-review`: set
`title` in the scheduler's dispatch, for example
`backlog <repo>#<issue>: <issue title>`. The issue number no longer depends on `claim`,
so the argument in "What this means per workflow" no longer applies to `backlog`.

---

## How a run gets its title

`lib/components/fabro-workflow/src/operations/create.rs:540`:

```rust
let title = explicit_title.unwrap_or_else(|| fabro_types::infer_run_title(record.graph.goal()));
```

When no explicit title is supplied, the server additionally spawns an LLM call to improve
it (`lib/apps/fabro-server/src/run_title_generation.rs`, prompt at
`lib/apps/fabro-server/src/prompts/run_title.md.j2`). That prompt is given exactly three
things:

- the workflow identity,
- the workflow summary (including the graph `goal`),
- `args.inputs` — *"Run inputs (raw values, not redacted)"*.

It says: *"If the run names an identifier — work order, ticket, issue, PR number — put it
next in canonical uppercase."*

**The title is generated once, at create time.** There is no later regeneration and no
mutation route.

## There is no title or label mutation API

Enumerated every route in `lib/apps/fabro-server/src/server/handler/runs.rs:78-102`:

```
/runs                      GET, POST
/runs/resolve              GET
/runs/{id}/questions       GET
/runs/{id}/questions/{qid}/answer   POST
/runs/{id}/state           GET
/runs/{id}/logs            GET
/runs/{id}/settings        GET
/runs/{id}/files           GET
/runs/{id}/commits         GET
```

plus events, graph, steer, interrupt, pair, usage, artifacts and lifecycle routes in
sibling modules. **None mutates the title or the labels.** A `RunTitleUpdated` event
variant exists (`lib/foundation/fabro-types/src/run_event/mod.rs`, `"run.title.updated"`)
but nothing in the HTTP surface emits it.

## Labels exist but are effectively hidden

`fire-pr-review.sh` already sends `args.labels = {source, pr, issue}`. Those render only in
the run **Settings** tab — `apps/fabro-web/app/routes/run-settings.tsx:123`,
`<Row title="Metadata" help="Combined run / workflow / project labels.">` — not on the run
list and not in the run header. Two clicks deep, and not where anyone looks.

---

## What this means per workflow

| Workflow | Fired by | Inputs at create | Can carry the issue? |
|---|---|---|---|
| `pr-review` | `fire-pr-review.sh` | `pr_number`, `auto_merge` | **Yes.** The script builds the intent. |
| `backlog` | automation schedule | none | **No.** Static goal, no inputs, and the issue is unknown until `claim` runs. |
| `issue-triage` | automation schedule | none | **No.** Same. |

For `backlog` and `issue-triage` the issue number does not exist at the only moment the
title can be set. Even a mutation API would not help without one, because the automation
fire builds the intent server-side.

---

## Recommended workarounds

### 1. Fix the half that can be fixed

Give bridge-fired `pr-review` runs a deterministic title. Detail in
`03-tier-3-smaller-items.md` item 2.

### 2. Invert the mapping for the rest

The issue and PR are the durable records; make them point at the run rather than the
reverse. `open_pr` already comments on the issue:

```sh
gh issue comment $N --body 'Agent run complete. PR: '$PR_URL || true
```

Add the run URL to that comment, and add one in `claim` so the link exists from the moment
the issue is claimed rather than only on success. Both need:

- `FABRO_WEB_URL` in `[run.environment.env]` (a `{{ vars.* }}`, non-sensitive), and
- the run id from `basename $(git rev-parse --abbrev-ref HEAD)` — the same derivation
  `trigger_review` already uses, because `FABRO_RUN_ID` is injected into hooks only
  (see `04-confirmed-dead-ends.md` item 7).

This is strictly more useful than a run title: it puts the link where the human already is
— reading the issue — instead of requiring them to find the run first.

Apply the same ULID shape check `trigger_review` uses before interpolating, and keep the
call `|| true`: a comment failure must never fail a run that produced a correct PR.

### 3. File upstream

Two requests, either of which closes this properly:

- `PATCH /api/v1/runs/{id}` accepting `title` and `labels`.
- Or: regenerate the run title when `context_updates` land, so a run that publishes
  `issue_number` re-titles itself.

The second is the better fit for this deployment, because the identifying fact genuinely
does not exist at create time.

---

## What not to do

**Do not put the issue number in the graph `goal`.** The goal is templated with
`{{ inputs.* }}` and `{{ vars.* }}` only, both resolved at run creation, so it cannot carry
a value discovered by `claim`.

**Do not add `[run.inputs] issue_number` to `backlog`'s `workflow.toml`** hoping the
generator picks it up. TOML `[run.inputs]` is static per workflow version — every run would
claim the same number.
