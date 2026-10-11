# 10. Validate, hand-fire, go live (operator)

## Outcome

The package validates in the container, and its node and edge counts are a recorded
baseline. Two hand-fired runs (jelly-swipe, then lawncare-saas) have merged real
Dependabot PRs or opened and reviewed a real patch PR, with every check below
confirmed. The four daily schedules are on. After 7 days, `result-<date>.txt` records
what the runs did and what they cost.

## Why

This is D11 and D18: test runs first, then straight to live, with no observation week.
Everything in tasks 02-09 is checked offline. Model resolution, permissions, real
lockfile tooling and Dependabot's behavior can only be checked live.

## Change

All host work runs from the deploy checkout `~/.fabro-deploy/fabro-workflows` at
`origin/main` (AGENTS.md, *The two checkouts*). Record each step's output for step 9.

### 1. Offline gates

`make check`, on the Mac, at `origin/main`. It must be green.

### 2. In the container

```sh
make check-host
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /path/to/dependabot/workflow.fabro'
```

- Record `Workflow: Dependabot (N nodes, M edges)` and every warning. There should be
  none. If there is one, understand it, and either fix it or write down why it is
  deliberate, as the `issue_number` and `pr_number` warnings are documented.
- Add the baseline to `ops/check-host.sh`, and replace the placeholder in AGENTS.md
  *Validating* with it. Commit and push.
- The `fabro parse` parity step in `make check-host` now covers the new graph: it must
  agree with `check-graph-invariants.py`'s reader.
- `fabro model test -m glm-5.3-flash`, `-m glm-5.3` and `-m gpt-6.1-sol` in the
  container: each resolves to the intended provider (invariants-host, *The model
  catalog*).

### 3. Deploy the notify script

`make deploy-notify`, because `discord-notify.sh` changed in task 08. Check it: the
container's copy contains `dependabot-summary`.

### 4. Create the rows, schedules held off

```sh
ops/provision-server-state.sh            # creates the four dependabot-* rows, schedules ENABLED (C10)
for r in jelly-swipe lawncare-saas womens-fantasy-sports writers-app; do
  DRY_RUN=0 ops/fabro-automation-schedule.sh dependabot-$r off
done
```

`fabro-automation-schedule.sh` defaults to `DRY_RUN=1` and only prints the PUT it would send.
Without `DRY_RUN=0` the schedules stay **enabled** (2026-10-10: the first attempt did exactly
that). Both scripts read `FABRO_API_URL` (with `/api/v1`) and `FABRO_DEV_TOKEN`.

Do both in one sitting, **before the next 05:00**, so no scheduled run fires before
the hand-fired ones pass. Then check that the rows list `auto_merge=true`, and that
the schedules are disabled.

### 5. Hand-fire jelly-swipe

It has the most work: 6 version-update PRs, runtime urllib3 alerts and dev npm alerts.
Fire the automation's `api:manual` trigger with no body:

```sh
ssh andrew@10.10.0.32 'curl -fsS -X POST -H "Authorization: Bearer $(docker exec fabro-fabro-1 cat /storage/server.dev-token)" http://127.0.0.1:32276/api/v1/automations/dependabot-jelly-swipe/runs'
```

The path prefix must match what `fire-pr-review.sh` uses. Check it there first. A
bodyless automation fire carries no `workflow_sha` label (ADR 0020). That is a known
gap for every scheduled fire; record it, and do not fix it here.

Follow the run with `ops/fabro-run-status.sh <run>` and
`fabro events <run> -p`. Confirm each item:

| # | Check |
|---|---|
| a | `inventory`: `alert_count` and `dep_pr_count` match `gh` at fire time. |
| b | The loop works at most 5 PRs. Each one has a C5 entry, and each `merged` PR is really merged on GitHub with Dependabot's title. |
| c | `dep_next`'s checkout assertion never fails. After the loop, the run branch's tree equals `origin/main` at loop start (`git diff` in the run's last checkpoint shows no foreign files). |
| d | `pick_group` picks `pip:uv.lock:urllib3`, unless a merged PR fixed it. |
| e | The tracking issue exists with the marker, the labels and the alert table. |
| f | `patch` runs on `glm-5.3-flash`: check `agent.llm.started`'s model, and `"profile":"openai"` in `agent.memory.loaded`. Record its duration. |
| g | `patch_gate` accepts a submitted contract with a receipt. The diff touches `uv.lock` (and `pyproject.toml` only if needed), and nothing else. |
| h | The PR has `agent-authored`, `security` and `dependencies`. `linked_issues.json` is non-empty. `spec` and `refute` received the issue. |
| i | `review_merge` ends `merged`, or blocked with a reason that is about the change, not the plumbing. Record which, and why. |
| j | Discord shows the patch PR's `merged`/`blocked` message with `#<issue>`, then the `dependabot-summary` message, with matching contents. |
| k | Record the durations of `review_fix` and `merge-standards` on `glm-5.3-flash` against their timeouts (task 07 marked them unmeasured). |

If any check fails, fix it in a new commit, push, and fire again. Each fire is cheap,
and no schedule is on.

### 6. Hand-fire lawncare-saas

This run checks the npm path: `package-lock.json`, dev scope, and 5 alerts in one group.
Check (d)-(i) again. In particular, the diff adds no lockfile, and
`package-lock.json` resolves brace-expansion at or above `5.0.12`.

### 7. Turn the schedules on

```sh
for r in jelly-swipe lawncare-saas womens-fantasy-sports writers-app; do
  DRY_RUN=0 ops/fabro-automation-schedule.sh dependabot-$r on
done
```

writers-app (cargo) and womens-fantasy-sports (merges only) go live without a
hand-fired run (D18). Watch their first scheduled runs the next morning with the same
checklist.

### 8. Record

- Add a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`: run ids,
  merges, the patch PR, and anything fixed along the way.
- Update the status line in 00, the `docs/README.md` row, and ADR 0022's status
  (proposed → accepted, with the date).

### 9. Measure after 7 days: `result-<date>.txt`

Export the week's `dependabot` runs with `ops/fabro-export-runs.sh --workflow dependabot`,
then record:

- runs per repository, and the status and wall time of each (median, max);
- Dependabot PRs by C5 outcome, and the merge rate;
- patch groups: PRs opened, merged, blocked (with each `merge_block_reason`), and
  blocked before a PR (`patch_blocked`);
- `patch`, `review_fix` and `merge-standards` durations against their timeouts;
- whether any alert closed by a patch was still open after 3 days (D22);
- **the concurrency cost:** `backlog` and `pr-review` runs created 05:00-09:00, and
  their queue delay (create → first stage) against the same window in the week before
  go-live (ADR 0022, *Consequences*). If the delay is material, propose spreading the
  schedules wider.

Open issues for anything that needs a change, and link them from the result file.

## Acceptance

- Steps 1-8 done, with their outputs recorded.
- Both hand-fired runs pass checks (a)-(k). Any check that could not apply (for
  example, no PR to merge) is marked with its reason.
- The four schedules are enabled. `ops/provision-server-state.sh` re-run with
  `DRY_RUN=1` reports no drift.
- `result-<date>.txt` is committed after 7 days.

## Rollback

From the gentlest to the bluntest:

1. `ops/fabro-auto-merge-switch.sh dependabot-<repo> off`: it still validates and
   opens PRs, and merges nothing.
2. `ops/fabro-automation-schedule.sh dependabot-<repo> off`: no more runs for that
   repository.
3. `FABRO_AUTO_MERGE=0` (host): stops every auto-merge in every workflow.
4. Revert the package's commits on `main`: the next fire fails at compile.

## Depends on

09. Needs ssh to the host and the fabro container.
