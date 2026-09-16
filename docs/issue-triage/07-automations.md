# Task 07 — Automations: `provision-server-state.sh` grows to twelve

**Depends on:** 03, 05. **Blocks:** 08, 09. **LLM level:** ordinary.

Four new automation rows, one per repository, plus the server variable the capacity
check depends on. Automations are **state in fabro's own store**, invisible to a
settings backup, which is what `ops/provision-server-state.sh` exists to recreate.

## Part 1 — the rows

| id | target | environment | triggers |
|---|---|---|---|
| `issue-triage-jelly-swipe` | `andrewthetechie/jelly-swipe` | `python` | `api:manual` enabled, `schedule:hourly` **disabled** (`30 * * * *`) |
| `issue-triage-lawncare-saas` | `andrewthetechie/lawncare-saas` | `python-node` | `api:manual` enabled, `schedule:hourly` **disabled** (`35 * * * *`) |
| `issue-triage-womens-fantasy-sports` | `andrewthetechie/womens-fantasy-sports` | `ts` | `api:manual` enabled, `schedule:hourly` **disabled** (`40 * * * *`) |
| `issue-triage-writers-app` | `andrewthetechie/writers-app` | `rust-node` | `api:manual` enabled, `schedule:hourly` **disabled** (`45 * * * *`) |

Environments follow the existing per-repo image mapping. Triage installs nothing, so
the image barely matters — but a run that wants to check whether a reported command
exists is better off in the repository's own image, and a uniform mapping is one fewer
special case in the table operators read.

The schedules are created **disabled**, like `backlog`'s. Task 09 enables all four
after the first live run, which is the rollout: there is no dry-run mode, and the
switch is the trigger.

Five minutes apart, on the hour, and deliberately clear of `backlog`'s residues
2/5/8/11 mod 15. Two triage runs can never overlap anyway — the capacity check sees
the other and quiet-exits — but a five-minute stagger means the second repository's
run is not wasted on a sandbox spawn it will immediately abandon.

## Part 2 — `FABRO_API_URL` as a server variable

`workflow.toml` reads `{{ vars.FABRO_API_URL }}`. **An unset variable fails the
RunIntent at compile time**, so no run is created at all and nothing is reported beyond
the fire's error. Provision it with the existing `provision_variable` helper, which is
create-if-absent for a reason: a re-provision that POSTs unconditionally would
silently overwrite a value an operator changed mid-incident. It reports drift instead.

Its value is the same API base the deploy recipes use, including the `/api/v1` prefix.

`FABRO_API_TOKEN` needs nothing new: it is already a vault entry, injected the same way
`backlog` injects it for the bridge, and it fails closed — missing means every run
aborts at startup before its sandbox exists.

## Part 3 — the drift check

`provision-server-state.sh` already reads back `environment_id`, `target` and the cron
expressions to report drift. Extend the same comparison to the four new rows and to
`FABRO_API_URL`; a missing row is the failure mode that makes triage silently stop on
one repository while three keep working.

## Acceptance

- `FABRO_API_URL=… FABRO_DEV_TOKEN=… DRY_RUN=1 ./ops/provision-server-state.sh` prints
  twelve automations and sends nothing.
- With `DRY_RUN=0` on an already-provisioned host it reports "exists" for all twelve and
  changes nothing — run it twice and diff the output.
- `GET /api/v1/automations` returns twelve rows; the four new ones have their schedule
  trigger present and **disabled**.
- `fabro variable get FABRO_API_URL` returns the value with the `/api/v1` suffix.
- A manual fire creates a run:
  `curl -fsS -X POST -H "Authorization: Bearer $TOK" http://10.10.0.32:32276/api/v1/automations/issue-triage-womens-fantasy-sports/runs`.
  `issue-triage` takes no inputs, so unlike `pr-review` this endpoint both creates and
  starts the run. Do not add `[run.inputs]` to make it look like `pr-review`.
