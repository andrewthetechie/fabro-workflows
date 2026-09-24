# Validate the series in the fabro container and record the new baselines

> **Operator task.** It needs ssh access to the fabro host `andrew@10.10.0.32`, where
> the only `fabro` binary lives. A local model without that access should stop here and
> hand over.

## Tracer-Bullet Outcome
All three workflow packages validate in the fabro container with the expected node and
edge counts and no new warnings. The routing checker reports no mismatch. Both offline
suites pass. `AGENTS.md` records the new `Backlog` baseline, so the next change is
measured against the right numbers.

## User Story
As the operator, I want the whole series proven against the real `fabro` binary before
it reaches `main`, because a push to `main` is live on the next run in four real
repositories.

## Description
Run the checks below from the fabro-workflows checkout, with tasks 01, 02, 03, 04 and 10
applied (08 and 09 for the scheduler). Compare each result with the expected output.
Then update the baseline sentence in `AGENTS.md`.

## Context Pack
- Source decisions: `AGENTS.md`, sections "Validating" and "Deploying to the server after
  a merge to `main`".
- Expected results. These were measured on 2026-09-24 against a scratch copy with
  tasks 01, 02, 03, 04 and 10 applied:
  - `Backlog (61 nodes, 143 edges)`: 59 → 61 nodes (`autofix`, `file_remainder`) and
    138 → 143 edges (+3 out of `autofix`, +2 out of `file_remainder`). Exactly one
    warning, `issue_number` unbound in `claim`, which is deliberate.
  - `PrReview (30 nodes, 65 edges)`: unchanged. Exactly one warning, `pr_number` unbound
    in `validate_input`, which is deliberate.
  - `IssueTriage (14 nodes, 32 edges)`: unchanged, no warnings.
  - `ops/check-routing-schemas.py`: `MISMATCHES: 0`.
  - `ops/test-task-gates.sh`: `PASS: 240 checks` (184 + 7 + 14 + 9 + 26).
  - `ops/scheduler`: `404 passed` (389 + 5 + 10).
- The baseline sentence in `AGENTS.md` today, verbatim:
  ```
  Baselines as of 2026-09-19 (review+merge shared and imported):
  `Backlog (59 nodes, 138 edges)` with exactly one warning — `issue_number` unbound in
  ```
- Non-goals: deploying. The deploy steps are in `00-overview-and-contracts.md` ("Deploy
  notes") and in `AGENTS.md`. This task only proves the tree is valid.

## Delivery Strategy
- Shape: Normal tracer bullet (verification plus one doc edit)
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files: `AGENTS.md` (the baseline sentence only).
- Commands, run from the root of the fabro-workflows checkout:
  ```sh
  # 1. Offline suites
  ./ops/test-task-gates.sh | tail -1                       # PASS: 240 checks
  ( cd ops/scheduler && uv run pytest -q | tail -1 )       # 404 passed
  python3.11 -c 'import tomllib,sys; [tomllib.load(open(p,"rb")) for p in sys.argv[1:]]; print("toml ok")' \
    .fabro/workflows/*/workflow.toml                       # toml ok

  # 2. Container validate (AGENTS.md "Validating")
  rsync -a --delete .fabro/ andrew@10.10.0.32:/tmp/check/
  scp ops/check-routing-schemas.py andrew@10.10.0.32:/tmp/check-routing-schemas.py
  ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
  for w in backlog pr-review issue-triage; do
    ssh andrew@10.10.0.32 "cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/$w/workflow.toml" \
      2>&1 | grep -E '^(Workflow|warning|Validation)'
  done

  # 3. Routing schemas (needs the container's parser, so it runs on the host)
  ssh andrew@10.10.0.32 'cd ~/fabro && python3 /tmp/check-routing-schemas.py \
    /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro' | tail -1
  ```
- Optional: confirm that `ci-fix` resolves to `glm-5.3-flash` at run time. `fabro preflight`
  makes a real model call and times out when a coder box is busy, and `AGENTS.md`
  says to read a timeout there as congestion.
- **`AGENTS.md` edit**, only after every result matches. Replace
  ```
  Baselines as of 2026-09-19 (review+merge shared and imported):
  `Backlog (59 nodes, 138 edges)` with exactly one warning — `issue_number` unbound in
  ```
  with
  ```
  Baselines as of <today's date> (review+merge shared and imported; ADR 0011 added
  `autofix` and `file_remainder`):
  `Backlog (61 nodes, 143 edges)` with exactly one warning — `issue_number` unbound in
  ```
  Also, in the "Neither gate runs the command nodes' shell" paragraph, change
  `./ops/test-task-gates.sh      # 184 checks, offline — no host, container or network`
  to `./ops/test-task-gates.sh      # 240 checks, offline — no host, container or network`.
- Verified external contracts: the command lines are copied from `AGENTS.md`
  ("Validating").
- Behavior rules: any difference from the expected results stops this task. Report the
  exact output. Do not edit `AGENTS.md` to match an unexpected number.
- Error and security rules: none. No token is involved.

## Acceptance Criteria
- [ ] `PASS: 240 checks`, `404 passed`, `toml ok`.
- [ ] `Workflow: Backlog (61 nodes, 143 edges)`, `Workflow: PrReview (30 nodes, 65 edges)`,
      `Workflow: IssueTriage (14 nodes, 32 edges)`, each `Validation: OK`, with exactly
      the two deliberate warnings and no others.
- [ ] `MISMATCHES: 0`.
- [ ] `AGENTS.md` shows the new baseline and check count.

## Test Expectations
- This task is itself the test. The expected literals are listed under Acceptance
  Criteria.
- If only some tasks are applied, the numbers change. Each task file states what it adds.

## Dependencies
- Blocked by: 01, 02, 03, 04 and 10 (the graph changes being validated)
- Why blocked: the expected counts and check totals assume all five are applied.
- Blocks: None. After this, deploy in this order: scheduler (08 and 09) first, then
  merge the graph changes to `main`.

## Labels
`test`, `ops`, `priority:high`

## Estimate
Small

## Risk
1 - This task only runs checks and edits one sentence.

## Validator Stopping Point
Every expected result above has been observed, and `AGENTS.md` is updated.
