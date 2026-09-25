# Validate on the host and turn the reviews on (operator)

## Tracer-Bullet Outcome
The operator checks the merged series on the fabro host, deploys the two changed host
files, creates the four `arch-review` automations, and fires one review by hand. The
review files Architecture issues, triages the queue, and Discord shows one summary plus
one message per `needs-info` issue, each with the correct issue link. The schedules are
then live, and ADR 0012 is marked accepted.

## User Story
As the operator, I want one checked turn-on with a manual first run, so that the first
scheduled review at 04:00 is not also the first real test.

## Description
This is an operator task. It needs the host (`ssh andrew@10.10.0.32`), the fabro dev
token, and Discord. A local model cannot do it. Read
`docs/architecture-review/00-overview-and-contracts.md` and `AGENTS.md` (*Validating*,
*Deploying to the server after a merge to `main`*) first. If you change any `.fabro`
file while fixing a problem, invoke the `/fabro-workflow` skill first.

Run the steps in order. Stop at the first step whose result differs from the expected
one, and fix the cause before you continue.

### 1. Offline gates (on the Mac)

```sh
cd ~/Documents/code/fabro-workflows && git pull --ff-only
./ops/test-task-gates.sh                       # expect: PASS: 321 checks
python3.11 -c 'import tomllib,sys; [tomllib.load(open(p,"rb")) for p in sys.argv[1:]]' \
  .fabro/workflows/*/workflow.toml             # expect: no output, exit 0
sh -n ops/fabro-monitor.sh && sh -n ops/provision-server-state.sh \
  && sh -n .fabro/workflows/backlog/scripts/discord-notify.sh && echo ok
```

### 2. `fabro validate` and the routing checker (in the container)

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
scp ops/check-routing-schemas.py andrew@10.10.0.32:/tmp/check-routing-schemas.py
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
for w in backlog pr-review issue-triage arch-review; do
  ssh andrew@10.10.0.32 "cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/$w/workflow.toml"
done
ssh andrew@10.10.0.32 'cd ~/fabro && python3 /tmp/check-routing-schemas.py \
  /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro'
```

Expected (measured 2026-09-24 against the target files of tasks 01 to 05):

| Workflow | Line | Warnings |
|---|---|---|
| backlog | `Backlog (61 nodes, 143 edges)` | exactly one: `issue_number` unbound in `claim` |
| pr-review | `PrReview (30 nodes, 65 edges)` | exactly one: `pr_number` unbound in `validate_input` |
| issue-triage | `IssueTriage (13 nodes, 26 edges)` | none |
| arch-review | `ArchReview (21 nodes, 44 edges)` | none |

The routing checker prints `MISMATCHES: 0`.

### 3. Deploy the host files

The notify script (tasks 01 and 03) and the monitor (task 06) are host copies. Use the
commands in `AGENTS.md`:

```sh
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'
scp ops/fabro-monitor.sh andrew@10.10.0.32:~/bin/fabro-monitor.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-monitor.sh'
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh          # expect: no output
ssh andrew@10.10.0.32 'cat ~/bin/fabro-monitor.sh' | diff - ops/fabro-monitor.sh   # expect: no output
```

### 4. Create the automations

The schedules are created **enabled**. The first one fires at the next 04:00 or 05:00
that matches its days. Do this step when you can finish step 5 before then, or turn the
schedules off first and on again after step 5.

```sh
DRY_RUN=1 FABRO_API_URL=http://10.10.0.32:32276/api/v1 FABRO_DEV_TOKEN=<dev token> \
  ./ops/provision-server-state.sh      # expect: "would create:" for the four arch-review ids
FABRO_API_URL=http://10.10.0.32:32276/api/v1 FABRO_DEV_TOKEN=<dev token> \
  ./ops/provision-server-state.sh      # expect: "created: arch-review-..." four times, no DRIFT
```

### 5. One manual review

Fire `arch-review-jelly-swipe` from the automation's manual trigger in the fabro web UI.
Alternatively, send a POST with **no body**. This workflow takes no inputs, so the
bodyless automation fire is correct for it:

```sh
curl -fsS -X POST -H "Authorization: Bearer <dev token>" \
  http://10.10.0.32:32276/api/v1/automations/arch-review-jelly-swipe/runs
```

Watch it with `ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro
events <run> -p'` and `./ops/fabro-run-status.sh <run>`. Confirm each item:

- [ ] `history` printed a hot-file count greater than 0.
- [ ] Each filed issue has the labels `architecture`, `needs-triage`, `ai-generated`, and
      its first body line is `<!-- fabro:arch-candidate slug=... -->`. There are at most 8.
- [ ] The events show `triage_phase.claim` once per queued issue, and no `ask_human`.
- [ ] For each `needs_info` outcome, the run log has a `Running hooks` line for
      `triage_phase.post_questions`, and Discord shows the message with **that** issue's
      number and link, not the first issue's. This is the check for the `tail -1` fix
      (contract C5).
- [ ] Discord shows one summary line that starts with `🧭 fabro architecture review on
      andrewthetechie/jelly-swipe`, and its counts match the issues on GitHub.
- [ ] Issues triaged `ready` carry `agent`. Any with a decision have a `## Decisions made
      during triage` section.
- [ ] `DRY_RUN=1 ~/bin/fabro-monitor.sh` on the host prints `ok    C3 dead-scheduler:
      inert, no enabled schedules`.

If the run misbehaves, turn every review off, then fix the cause:

```sh
for r in jelly-swipe lawncare-saas womens-fantasy-sports writers-app; do
  FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 ./ops/fabro-automation-schedule.sh arch-review-$r off
done
```

### 6. The merge block, when the first Architecture PR opens

This happens hours or days later, when the scheduler dispatches an `architecture` issue.

- [ ] The PR carries `agent-authored` and `architecture`.
- [ ] The merge phase ends `blocked`. The PR comment names the Architecture issue
      reason, and Discord shows the `blocked` message.

### 7. Record it

- `docs/adr/0012-architecture-review.md`: change the status line to
  `**Status:** accepted (<date>). Replaces ADR 0002 for issue-triage. ADR 0002 still
  applies to backlog's human_rescue gate.`
- `docs/adr/0002-bounded-human-gates.md`: remove "(proposed)" from its status line.
- `AGENTS.md`, the `docs/architecture-review/` row: append "**Applied <date>.**"
- `ops/README.md`, the *Automations* table: add the four `arch-review-<repo>` rows as
  read back from `GET /automations`, with trigger `schedule:twice-weekly` enabled.
- Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` (outside this
  repository): the run id of the manual review, what it filed and triaged, and the
  Discord results.

## Context Pack
- Source decisions: ADR 0012, all of it. Overview decisions 12, 14, 15, 16.
- Repo facts: every command above comes from `AGENTS.md`, or from the check that tasks
  03 to 06 recorded. The baselines in step 2 were measured on 2026-09-24 with the target
  files of this series in a scratch copy. The live tree then had
  `IssueTriage (14 nodes, 32 edges)`.
- Non-goals: do not enable the `backlog` or `issue-triage` schedules. Do not upgrade
  fabro. Do not restart the fabro container: nothing here needs it, and a restart fails
  every run in flight.

## Delivery Strategy
- Shape: Operator validation and turn-on. It is not a tracer bullet: it is the
  integration check for tasks 01 to 06, like `docs/merge-rate/11-validate.md`.
- Valid-state scope: Default branch, plus the host state it creates.

## Implementation Contract
- Expected files: `docs/adr/0012-architecture-review.md`,
  `docs/adr/0002-bounded-human-gates.md`, `AGENTS.md`, `ops/README.md`.
- Interfaces and names: None new.
- Verified external contracts: `POST /automations/{id}/runs` fires the automation's
  enabled `api` trigger and ignores a body (`AGENTS.md`).
- Behavior rules: stop at the first unexpected result.
- Error and security rules: the dev token goes on the command line or in the
  environment, never in a file in this repository.

## Acceptance Criteria
- [ ] Steps 1 and 2 give the expected results.
- [ ] Step 3's two `diff` commands print nothing.
- [ ] Four `arch-review-<repo>` automations exist with enabled schedules.
- [ ] Every step 5 item is checked.
- [ ] ADR 0012 is marked accepted.

## Test Expectations
The steps are the tests. The only automated suites are the ones in step 1.

## Dependencies
- Blocked by: 02, 04, 05, 06
- Why blocked: the reviews must not start before triage decides (02), the loop exists
  (04), the merge block exists (05), and the rows and monitor changes exist (06).
- Blocks: None

## Labels
`chore`, `ops`, `priority:high`

## Estimate
Medium

## Risk
3 - it turns on the first scheduled workflow in this deployment, which promotes issues
into the `backlog` queue on four repositories. The off switch is one command per
repository.

## Validator Stopping Point
Step 5's checklist is complete, and the ADR is marked accepted.
