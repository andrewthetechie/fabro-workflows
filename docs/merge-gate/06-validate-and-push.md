# Validate on the host, push once, and read the first runs

## Tracer-Bullet Outcome
Tasks 01 to 05 go to `main` as one push, after the offline gates and `fabro validate`
pass in the container. The next `backlog` and `pr-review` runs execute the four new
nodes, and the first merge-phase run on each coder box is read end to end. The results
are written to the deployment log.

## User Story
As the operator, I want the gates live with evidence that each new node ran and routed
as designed, so that a blocked PR can be trusted to mean what its comment says.

## Description
This task runs on the Mac and on the fabro host (`ssh andrew@10.10.0.32`). Read
`docs/merge-gate/00-overview-and-contracts.md` first. It needs tasks 01 to 05, committed
locally and not pushed.

### 1. Offline gates (on the Mac)

```sh
cd ~/Documents/code/fabro-workflows
./ops/test-task-gates.sh                     # PASS: 402 checks
grep -c '\\[^"]' .fabro/workflows/_shared/review-merge/review-merge.fabro   # 0
python3.11 -c 'import tomllib,sys; [tomllib.load(open(p,"rb")) for p in sys.argv[1:]]' .fabro/workflows/*/workflow.toml
```

The `grep` counts backslashes other than `\"` in the shared graph. It must print `0`.

### 2. `fabro validate` and the routing checker (in the container)

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
scp ops/check-routing-schemas.py andrew@10.10.0.32:/tmp/check-routing-schemas.py
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && for w in backlog pr-review issue-triage arch-review; do docker compose exec -T fabro fabro validate /tmp/check/workflows/$w/workflow.toml; done'
ssh andrew@10.10.0.32 'cd ~/fabro && python3 /tmp/check-routing-schemas.py /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro'
```

Expected: `Backlog (65 nodes, 151 edges)` with only the `issue_number` warning,
`PrReview (34 nodes, 73 edges)` with only the `pr_number` warning, `IssueTriage (13
nodes, 26 edges)` and `ArchReview (21 nodes, 44 edges)` clean, and `MISMATCHES: 0`. Stop
on any difference.

Then confirm the model still resolves to `zai`:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro model test -m glm-5.3'
```

### 3. Push

```sh
git pull --rebase
git push origin main
```

Every automation reads `main` at fire time, so the next run uses the new graph. There is
nothing to copy to the host and nothing to restart.

### 4. Read the first runs

For the first `backlog` run on each coder box that reaches the merge phase, and the
first `pr-review` run:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro events <run> -p' | grep -E 'hygiene|refute|merge_gate|ci_fix_gate'
```

Confirm these, and record each one:

1. `review_merge.hygiene` ran and `review_merge.refute_prep` followed it.
2. `review_merge.refute` ran on `glm-5.3` (or `kimi-k3` through the fallback, which
   decision 2 accepts), and `review_merge.refute_gate` published `refute_verdict`.
3. The PR comment has the `Refuter` and `Diff hygiene` rows and both sections.
4. If the PR was blocked, the `Not auto-merged` reason is the Refuter's or the
   counters', and it matches the checklist or samples in the same comment.

Read two numbers from these runs: how long `refute` took (fabro's stage duration), and
the verdict. A `refute` that reaches its 15m timeout is a sizing problem to note, not a
fault.

### 5. If it goes wrong

- A run dies at `hygiene` or `refute_prep` with no PR comment: read the stage's stderr
  with `fabro logs <run> -n 200`. Both nodes take the edge to `mark_needs_human`, which
  labels the PR `ai-review-needs-human`.
- Every PR blocks: expected for a while (decision 9). If it is still true after about ten
  merge-phase runs, and the checklists are wrong rather than strict, tell the operator.
  The operator can change `.refute`'s model or prompt. There is no switch to make the
  Refuter report-only, and adding one is a new decision, not a fix.
- To take a gate out entirely, revert the task's commit and push. Graph changes need no
  deploy.

### 6. Record it

Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` (mode 600,
outside the repository): the commit, the validate output, the first runs with their
verdicts and `refute` durations, and any PR blocked by the new checks. Then finish
task 05 edit 5 if its date needs correcting.

## Context Pack
- Source decisions: overview decisions 9 and 11.
- Repo facts: `AGENTS.md` "Validating" and "Deploying to the server after a merge to
  `main`". Imported node ids carry the `review_merge.` prefix in events and hooks.
- Non-goals: no hook, Discord or monitor change. A blocked merge is already reported by
  the existing `report_blocked` hooks in both `backlog` and `pr-review`.

## Delivery Strategy
- Shape: Operator runbook
- Valid-state scope: `main`, live.

## Implementation Contract
- Expected files: none in the repository beyond tasks 01 to 05. The deployment log
  outside it.

## Acceptance Criteria
- [ ] The offline gates, `fabro validate` and the routing checker match the expected
      output above before the push.
- [ ] One push carries tasks 01 to 05.
- [ ] One `backlog` run per coder box and one `pr-review` run show all four new nodes
      in `fabro events`, and their PR comments show both new sections.
- [ ] The deployment log has a dated section.

## Test Expectations
The checks in steps 1, 2 and 4.

## Dependencies
- Blocked by: 01, 02, 03, 04, 05
- Blocks: none

## Labels
`ops`

## Estimate
Small (plus waiting for the runs)

## Risk
4 - the push changes the gate in front of every unattended merge into `main` on four
repositories, and it starts blocking on the next run.

## Validator Stopping Point
Step 4 is recorded for three runs.
