# Delete `issue-triage`'s capacity check and raise the run cap to 4

## Tracer-Bullet Outcome
An `issue-triage` run proceeds to triage work while two coder runs are in flight,
instead of standing down because the host "looks busy".

## User Story
As the operator, I want triage to stop declining work it does not compete for, so
that raising the run cap for the scheduler does not make triage progressively more
timid.

## Description
`issue-triage` gates itself on fabro's global run count. That was correct when the
cap was 2 and a triage run was itself one of the two. Under overview decision 2
the cap becomes 4, and the same check then stands down whenever *any* two runs
exist — including two coder runs triage never contends with, because every triage
stage resolves to `high-reasoning` (hosted z.ai), not `coders`.

Delete the node and its edges. Deleting it also removes `FABRO_API_TOKEN` and
`FABRO_API_URL` as hard startup dependencies of triage.

## Context Pack
- Source decisions: overview decision 13 (delete, do not retune) and decision 2
  (cap → 4). ADR 0005 records why.
- Repo facts: `CONTEXT.md` defines **Cap**; ADR 0001 "Concurrency posture"
  describes the old posture and should be read but not edited (ADRs are a
  chronological record — supersede, never rewrite).
- Non-goals: any change to what triage *does* once running; any change to the
  `backlog` or `pr-review` graphs; changing the cap by editing a file in this repo
  (the cap is server state, see below).

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files: `.fabro/workflows/issue-triage/workflow.fabro` (delete the node
  and re-route its edges); `ops/README.md` and `CONTEXT.md` prose updates.
- Interfaces and names: the node to delete, verbatim from the graph today
  (`check_capacity`, at line 25):

  ```
  check_capacity [label="Capacity check", shape=parallelogram,
      output_schema="routing", timeout="2m",
      script="... S=$(jq -r '.runs.scheduler_slots_used // empty' /tmp/fabro/system.json) ...
      if [ $S -gt 1 ]; then jq -nc '{context_updates:{host_busy:\"true\"}}'
      else jq -nc '{context_updates:{host_busy:\"false\"}}' fi"]
  ```

  Every edge naming `check_capacity` must go, and whatever edge fed it must now
  feed its `host_busy=false` successor directly. Find them with:

  ```sh
  grep -n 'check_capacity\|host_busy' .fabro/workflows/issue-triage/workflow.fabro
  ```

  **Every node named by an edge must have its own declaration or validation
  fails** — so remove declaration and edges together.

- Verified external contracts: the cap is **server state, not a file in this
  repo**. `ServerSchedulerSettings` has exactly one field, `max_concurrent_runs`
  (`lib/foundation/fabro-types/src/settings/server.rs:268-271`); the container
  reads it from `/storage/.home/settings.toml`:

  ```toml
  [server.scheduler]
  max_concurrent_runs = 2      # -> 4
  ```

  Change it in the container's settings overlay and restart, then confirm:

  ```sh
  curl -fsS -H "Authorization: Bearer $TOK" \
    http://10.10.0.32:32276/api/v1/system/info | jq '.runs'
  ```

  `ops/settings.toml.example` is the tracked copy of that overlay and must be
  updated to match, or the next host rebuild silently reverts the cap.

- Behavior rules: after this change a triage run never consults the fabro API
  before starting work.
- Error and security rules: None new. Removing the check removes a failure path
  that exited non-zero when `FABRO_API_TOKEN` was unset.

## Acceptance Criteria
- [ ] `grep -c 'check_capacity' .fabro/workflows/issue-triage/workflow.fabro` is 0.
- [ ] `grep -c 'host_busy' .fabro/workflows/issue-triage/workflow.fabro` is 0.
- [ ] Container validation reports **IssueTriage with 2 fewer nodes than the 15
      recorded in AGENTS.md, and clean** — no warnings.
- [ ] `/system/info` reports the new cap.
- [ ] `ops/settings.toml.example` shows `max_concurrent_runs = 4`.
- [ ] `AGENTS.md`'s validation baselines are updated to the new node/edge counts.

## Test Expectations
No unit-test framework; the validator is fabro's own, run in the container. Exact
sequence from `AGENTS.md`:

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/issue-triage/workflow.toml'
ssh andrew@10.10.0.32 'cd ~/fabro && python3 /tmp/check-routing-schemas.py /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro'
```

Expected: `Validation: OK` with no `warning:` lines, and `MISMATCHES: 0`.

Then one live triage run fired while two other runs are active, which must reach
its first agent stage rather than exiting early.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: None

## Labels
`enhancement`, `workflows/issue-triage`, `priority:medium`

## Estimate
Small

## Risk
2 - removes a safety valve while the scheduler does not yet exist, so fabro can
briefly run 4 concurrent runs against 2 coder boxes. Accepted: there are no
enabled automation schedules at the time of writing, so nothing fires unattended.

## Validator Stopping Point
`fabro validate` clean on all three workflows, `MISMATCHES: 0`, and
`/system/info` reporting the raised cap.
