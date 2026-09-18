# Collapse `acquire`/`claim` and add the manual-fire script

## Tracer-Bullet Outcome
A `backlog` run works the issue the scheduler chose, and nothing else. The
20-second settle and the ULID arbitration are gone. An operator can still fire one
issue by hand with `ops/fabro-fire-backlog.sh andrewthetechie/jelly-swipe 123`.

## User Story
As the operator, I want the run to stop choosing its own work now that the
scheduler chooses it, so that the claim race that once had two runs implement
`jelly-swipe#356` on separate branches cannot recur — and I want a one-command
escape hatch for when the scheduler is down.

## Description
Two changes that must land together, because the first removes the only way to
fire `backlog` without the second.

The graph stops selecting and starts validating: `issue_number` arrives as a run
input, `acquire` is deleted, and `claim` shrinks to "swap the label if the
scheduler has not already, fetch the issue, fail closed if it is gone".

**The label swap stays in the graph as a fallback.** The scheduler does it at
dispatch (draft 08), but the manual path does not, so `claim` must be idempotent
about it: add `agent-in-progress` if absent, remove `agent` if present, never fail
because either is already in the target state.

## Context Pack
- Source decisions: overview decisions 3 (scheduler passes `issue_number`), 9
  (scheduler sets the label at dispatch), 20 (manual fires move to an `ops/`
  script). Q1(c) of the decomposition grilling.
- Repo facts: the arbitration this deletes is documented in a 30-line comment
  above `claim` recording the live incident it was written for. **Delete the
  mechanism and the comment together** — a comment describing machinery that is
  gone is worse than no comment. ADR 0005 is where the reasoning now lives.
- Non-goals: changing anything after `prep`; changing `pr-review` or
  `issue-triage`; removing the `agent-stuck` / `agent-authored` label vocabulary.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  `.fabro/workflows/backlog/workflow.fabro`,
  `ops/fabro-fire-backlog.sh` (new),
  `ops/README.md`, `AGENTS.md` (baselines + deploy row), `CONTEXT.md`.

- Interfaces and names — the nodes as they exist today.

  `acquire` (line 54), **delete entirely**:

  ```
  acquire [label="Acquire issue", shape=parallelogram,
      script="mkdir -p /tmp/fabro && gh issue list --label agent --state open --json number,title,labels --limit 10 > /tmp/fabro/candidates.json && jq '[.[] | select(([.labels[].name] | contains([\"agent-in-progress\"]) | not))] | sort_by(.number) | .[0:1]' /tmp/fabro/candidates.json > /tmp/fabro/acquire.json && test \"$(jq length /tmp/fabro/acquire.json)\" != \"0\""]
  ```

  `claim` (line 87) keeps its label creation and issue fetch, and **loses**:
  the marker comment `gh issue comment $N --body '<!-- fabro:claim:'$RID' -->'`
  (line 100), the 20-second settle, the re-read, the lowest-ULID arbitration, and
  the `claim_lost` context key.

  The edges today (lines 708-713):

  ```
  start -> acquire
  acquire -> claim
  acquire -> exit                 [condition="outcome=failed"]
  claim -> exit                   [condition="context.claim_lost=true", weight=10]
  claim -> prep                   [condition="outcome=succeeded"]
  claim -> human_rescue
  ```

  Target:

  ```
  start -> claim
  claim -> prep                   [condition="outcome=succeeded"]
  claim -> human_rescue
  ```

  The `claim_lost` edge goes with the arbitration. **Every node named by an edge
  must have its own declaration or validation fails**, so `acquire`'s declaration
  and both its edges go together.

  `claim`'s new script must, in order: read `$ISSUE` from the templated input;
  reject a non-numeric or zero value; create the four labels idempotently as it
  does today; `gh issue edit $ISSUE --add-label agent-in-progress --remove-label agent`
  (idempotent, non-fatal if already in that state); `gh issue view $ISSUE --json number,title,body,labels`
  into `/tmp/fabro/issue.json`; **fail closed** if the issue is closed or does not
  carry `agent-in-progress` afterwards.

- Verified external contracts:

  `args.inputs` values reaching a POSIX `case` guard must be **JSON numbers**, not
  strings — `fire-pr-review.sh` records this for `pr_number`, whose graph does
  `case "$PR" in *[!0-9]*)`. `issue_number` is guarded the same way, so the
  scheduler sends a number (draft 07).

  Templating: `{{ inputs.issue_number }}` is legal **only** in a node's `script`,
  a node `prompt`, the graph `goal`, or the root `model_stylesheet`. `script`
  receives **value substitution only** — no `{% if %}`, no filters. An unbound
  input fails the run at compile with `422 run_compile_invalid`, which is the
  desired fail-closed behaviour and is exactly why draft 20's script exists.

  `pr-review`'s `validate_input` is the working precedent for exactly this shape
  (a required numeric input, guarded with `case`); copy its structure.

- Behavior rules:
  - `ops/fabro-fire-backlog.sh <owner/repo> <issue_number>` performs draft 07's
    three-POST sequence with `args.inputs = {issue_number: <number>, coder_pool:
    "coders"}` and `environment_id` looked up from the repo's `backlog-<repo>`
    automation row, exactly as `fire-pr-review.sh` looks up its own.
  - It uses `coders` (the both-boxes group), **not** a pinned box: a hand fire has
    no lease and must not pretend to hold one.
  - Same output contract as `fire-pr-review.sh`: last stdout line is the bare run
    id; last stderr line is a one-line reason on every failure path.
  - `DRY_RUN` defaults to `1`.
- Error and security rules: `claim` must fail closed on a missing, closed or
  unlabelled issue rather than picking a different one — silently working the
  wrong issue is the failure this whole draft exists to prevent.

## Acceptance Criteria
- [ ] `grep -c 'acquire' .fabro/workflows/backlog/workflow.fabro` is 0.
- [ ] `grep -cE 'claim_lost|fabro:claim:' .fabro/workflows/backlog/workflow.fabro` is 0.
- [ ] Backlog validates in the container with **fewer nodes and edges than the
      recorded 60/140 baseline**, clean; `MISMATCHES: 0`; `AGENTS.md`'s baselines
      updated to the new counts.
- [ ] A scheduler-dispatched run works exactly the dispatched issue.
- [ ] A run created with **no** `issue_number` fails at compile with
      `422 run_compile_invalid` and creates nothing.
- [ ] `ops/fabro-fire-backlog.sh andrewthetechie/jelly-swipe <n>` starts a run
      that works issue `<n>`.
- [ ] A fire against a closed issue fails with a one-line reason and opens no PR.
- [ ] Two simultaneous scheduler dispatches never select the same issue — the
      lease table's `coder_pool` primary key and one-run-per-repo make it
      structurally impossible.

## Test Expectations
No unit-test framework for the graph; the validator is fabro's own, plus
`sh -n` on the new script and two live fires.

1. `sh -n ops/fabro-fire-backlog.sh` exits 0.
2. Container validation exactly as `AGENTS.md` prescribes (rsync → docker cp →
   `fabro validate` per package → `check-routing-schemas.py`). Expected:
   `Validation: OK` on all three, only the deliberate `pr_number` warning on
   `pr-review`, `MISMATCHES: 0`.
3. Live negative case, the important one:

   ```sh
   jq -n --arg v "$VERSION_ID" '{workflow_version_id:$v,
     target:{kind:"git",repo:"andrewthetechie/jelly-swipe",branch:"main"},
     args:{inputs:{}}, environment_id:"python"}' \
   | curl -sS -o /tmp/r.json -w '%{http_code}\n' -X POST \
       -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
       --data-binary @- http://10.10.0.32:32276/api/v1/runs
   ```

   Expected literal output: `422`, and `jq -r '.errors[0].code' /tmp/r.json`
   prints `run_compile_invalid`.

## Dependencies
- Blocked by: "Lease state machine and the dispatch loop"
- Why blocked: this makes `issue_number` mandatory, so the scheduler must already
  be supplying it or every `backlog` run breaks.
- Blocks: "Turn off the four `backlog` automation schedules"

## Labels
`feature`, `workflows/backlog`, `ops`, `priority:high`

## Estimate
Medium

## Risk
4 - after this, no `backlog` run starts without an explicit issue. A mistake here
stops the factory rather than corrupting it, which is the better direction, but it
is still a full stop.

## Validator Stopping Point
Backlog validates clean at its new baseline, `MISMATCHES: 0`, a dispatched run
works the right issue, a no-input run 422s, and the manual script fires one.
