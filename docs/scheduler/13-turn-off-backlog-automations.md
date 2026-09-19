# Turn off the four `backlog` automation schedules

## Tracer-Bullet Outcome
No `backlog` run starts except through the scheduler. The four automation rows
survive with their API triggers intact, so `provision-server-state.sh` still
detects drift and a manual fire is still possible.

## User Story
As the operator, I want exactly one thing deciding when `backlog` runs, so that a
schedule and the scheduler cannot both dispatch work for the same repo onto the
same two boxes.

## Description
The cutover. Pure server-state change: `PUT` each `backlog-<repo>` automation with
its schedule trigger disabled. **Not** a delete — decision 9 keeps the rows.

`pr-review-<repo>` and `issue-triage-<repo>` rows are untouched.

## Context Pack
- Source decisions: overview decision 9 (disable schedules, keep rows). ADR 0005
  names two admission controllers as the thing this prevents.
- Repo facts: `~/.fabro-deploy/docs/OPERATOR-RUNBOOK.md` carries the exact
  GET+PUT+If-Match recipe, reproduced below because this repository is the
  implementer's only context. `ops/provision-server-state.sh` creates the twelve
  rows and reads `environment_id` for drift detection; it must keep working.
- Non-goals: deleting rows; touching `pr-review` or `issue-triage` rows; changing
  `workflow_source`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files: `ops/fabro-automation-schedule.sh` (new), `ops/README.md`,
  `AGENTS.md` (deploy + drift rows).

- Interfaces and names:
  `ops/fabro-automation-schedule.sh <automation-id> <on|off>`, `DRY_RUN` defaulting
  to `1`, same shape as `ops/fabro-auto-merge-switch.sh`.

- Verified external contracts: **fabro has no `PATCH` on automations.** It is
  GET + full-body PUT with `If-Match` on the row's `revision`. Exact recipe, from
  the operator runbook:

  ```sh
  TOK=$(ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/server.dev-token')
  API=http://10.10.0.32:32276/api/v1
  ID=backlog-jelly-swipe
  curl -fsS -H "Authorization: Bearer $TOK" "$API/automations" > /tmp/autos.json
  REV=$(jq -r --arg id "$ID" '.data[] | select(.id==$id) | .revision' /tmp/autos.json)
  jq -c --arg id "$ID" '
    [.data[] | select(.id==$id)][0]
    | .triggers |= map(if .type=="schedule" then .enabled = false else . end)
    | {name, description, environment_id, target, workflow, triggers}
      + (if (.workflow_source // null) == null then {} else {workflow_source} end)' \
    /tmp/autos.json > /tmp/row.json
  curl -fsS -X PUT -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
    -H "If-Match: \"$REV\"" --data-binary @/tmp/row.json "$API/automations/$ID"
  ```

  The `jq` projection is **load-bearing**: a PUT that omits `workflow_source` when
  the row has one silently unpins the workflow source. The conditional
  `+ (if ... end)` preserves it only when present.

  Trigger shapes (`lib/components/fabro-automation/src/model.rs:168-224`):
  `{"type":"api","id":"manual","enabled":true}` and
  `{"type":"schedule","id":"<id>","enabled":<bool>,"expression":"<5-field UTC cron>"}`.
  `POST /automations/{id}/runs` returns `409 automation_api_trigger_disabled`
  if no **enabled** API trigger remains — so the API trigger must stay enabled.

  The four ids are `backlog-jelly-swipe`, `backlog-lawncare-saas`,
  `backlog-womens-fantasy-sports`, `backlog-writers-app`.

- Behavior rules: idempotent — a row whose schedule triggers are already disabled
  is reported and skipped, not re-PUT. An `If-Match` mismatch (`412`) must fail
  loudly, never be retried blindly with a fresh revision: something else changed
  the row.
- Error and security rules: the dev token comes from the environment. This
  repository is public — no token in the script or its output.

## Acceptance Criteria
- [ ] All four `backlog-*` rows have every `schedule` trigger `enabled: false`.
- [ ] All four still have an enabled `api` trigger with id `manual`.
- [ ] `workflow_source` on each still resolves to
      `andrewthetechie/fabro-workflows@main`.
- [ ] The eight `pr-review-*` and `issue-triage-*` rows are byte-identical before
      and after.
- [ ] `ops/provision-server-state.sh` reports no drift.
- [ ] Over one hour with the scheduler stopped, zero `backlog` runs are created.
- [ ] Re-running the script reports "already off" and PUTs nothing.

## Test Expectations
No unit-test framework — POSIX `sh` against live server state. `sh -n` plus a
before/after comparison.

```sh
curl -fsS -H "Authorization: Bearer $TOK" "$API/automations" \
| jq -r '.data[] | select(.id|startswith("backlog-")) |
         "\(.id)\t\([.triggers[] | select(.type=="schedule") | .enabled] | @csv)\t\([.triggers[] | select(.type=="api") | .enabled] | @csv)"' \
| sort
```

Expected after, for each of the four rows: the schedule column reads `false`
(or is empty if a row has no schedule trigger) and the api column reads `true`.

Capture the same command's output **before** the change into the deployment log,
so the cutover is reversible by inspection.

## Dependencies
- Blocked by: "Collapse `acquire`/`claim` and add the manual-fire script"
- Why blocked: once schedules are off, the scheduler is the only producer of
  `backlog` runs — so the graph must already require `issue_number` and the
  manual escape hatch must already exist.
- Blocks: "Shakedown and deployment-log entry"

## Labels
`chore`, `ops`, `priority:high`

## Estimate
Small

## Risk
3 - server state with no undo beyond re-enabling. Mitigated by capturing the
before state and by the rows surviving, so re-enabling is the same script with
`on`.

## Validator Stopping Point
The `jq` query shows all four schedule triggers disabled and all four API triggers
enabled, `provision-server-state.sh` reports no drift, and an hour passes with no
unattended `backlog` run.

## Corrected during implementation, 2026-09-19

**All four schedules were already `enabled: false`, so this draft's cutover PUT
nothing.** The rows have been disabled since 2026-09-14 (operator decision, recorded
in `ops/README.md`), and draft 13 as written assumes they are on. Verified live before
touching anything:

| id | schedule `enabled` | api `enabled` |
|---|---|---|
| backlog-jelly-swipe | false | true |
| backlog-lawncare-saas | false | true |
| backlog-womens-fantasy-sports | false | true |
| backlog-writers-app | false | true |

What that changes: the *state* this draft's acceptance criteria describe already held,
and `ops/fabro-automation-schedule.sh <id> off` answered `already off` on all four —
which is the draft's own idempotency rule doing its job, not a failure. The task's real
deliverable is therefore the switch and the documentation that makes the state
deliberate: without them the four rows are off by an accident of history, and the next
host rebuild (which creates them disabled) is the only thing that would re-establish
it. The write path — PUT, `If-Match`, the `workflow_source` projection, the `412`
refusal — was exercised against a scratch automation row instead, created and deleted
for the purpose; the detail is in the deployment log's 2026-09-19 (task 13) section.

Two smaller facts confirmed live while implementing, both of which the draft states
and neither of which is checked by anything offline:

- `POST /automations/{id}/runs` really does answer `409` with
  `code: automation_api_trigger_disabled` when the row has no enabled API trigger —
  reproduced on a scratch row carrying `api:manual enabled:false`. That is why the
  script rewrites only `schedule` triggers and sends `api` back exactly as read.
- `DELETE /automations/{id}` requires `If-Match` too: without it the answer is
  **428 Precondition Required**, with it `204`. Worth knowing for anything that has to
  clean up a row.

The "over one hour with the scheduler stopped, zero `backlog` runs are created"
criterion was not observable in-session and is recorded as pending in the deployment
log. Its substance is checkable and was checked: every `backlog-*` schedule trigger
reads `false`, and the scheduler container is stopped, so nothing in the deployment can
create a `backlog` run.
