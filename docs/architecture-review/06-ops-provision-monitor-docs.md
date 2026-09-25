# Provision the `arch-review` automations, adjust the monitor, and update the docs

## Tracer-Bullet Outcome
Running `ops/provision-server-state.sh` creates four `arch-review-<repo>` automations,
each with an `api:manual` trigger and an **enabled** twice-weekly schedule. The monitor
stays quiet about them: C3 ignores `arch-review` schedules, and C4 allows an
`arch-review` run 9 hours instead of 3. `AGENTS.md`, `CONTEXT.md` and `ops/README.md`
describe four workflows and the first enabled schedules. Nothing is deployed in this
task. The operator runs the script in task 07.

## User Story
As the operator, I want the host rebuild script and the monitor to know about
`arch-review`, so that a rebuilt host gets the reviews back and the monitor does not
alarm on a run that is supposed to take hours.

## Description
Read `docs/architecture-review/00-overview-and-contracts.md` first (decisions 12 and 16).
This task edits shell scripts and Markdown only. It needs no `/fabro-workflow` skill
unless you touch a `.fabro` file, and you must not.

1. `ops/provision-server-state.sh`: the edits in *Provisioning edits*.
2. `ops/fabro-monitor.sh`: the edits in *Monitor edits*.
3. `AGENTS.md`, `CONTEXT.md`, `ops/README.md`: the edits in *Doc edits*.

## Context Pack
- Source decisions: ADR 0012 D7 and its Consequences. Overview decisions 12 and 16.
- Repo facts:
  - `provision_automation` builds each row as JSON and POSTs it to `/automations`. It
    never changes an existing row. It reports drift instead. Today every schedule it
    creates is `enabled:false`. Signature today:
    `provision_automation <id> <environment_id> <repo> <workflow> <schedule_id_or_empty> [schedule_expr] [auto_merge]`.
  - `DRY_RUN=1 FABRO_API_URL=http://10.10.0.32:32276/api/v1 FABRO_DEV_TOKEN=<token>
    ./ops/provision-server-state.sh` sends no POST. With the edits below it printed
    `would create: arch-review-jelly-swipe (env=python)` and the three others on
    2026-09-24.
  - Automation rows from `GET /automations` carry `.workflow` (a string such as
    `"backlog"`) and `.triggers[]` with `.type` and `.enabled`. That is the payload
    shape the script POSTs.
  - Run rows from `GET /runs` carry `.workflow.slug` (for example `"backlog"`), verified
    on the live server 2026-09-24.
  - Today C3 alarms when any schedule is enabled and the newest run is more than
    `IDLE_HOURS` (2) old. Every `backlog` and `issue-triage` schedule is off, so C3 is
    inert. An enabled `arch-review` schedule would make it active, and it would alarm
    whenever no run started for 2 hours.
  - C4 alarms on any non-terminal run older than `STUCK_HOURS` (3). A review can run up
    to about 8 hours: 6 hours of triage starts plus the last triage.
  - Every edit below was applied and checked in a scratch copy: both scripts pass
    `sh -n`, and the dry run above succeeded.
- Non-goals: do not deploy anything. Do not run the script without `DRY_RUN=1`. Do not
  change any other monitor condition. Do not change the `backlog`, `pr-review` or
  `issue-triage` rows.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft. The rows do not exist until task 07
  runs the script.

## Implementation Contract
- Expected files: `ops/provision-server-state.sh`, `ops/fabro-monitor.sh`, `AGENTS.md`,
  `CONTEXT.md`, `ops/README.md`.
- Interfaces and names: automation ids `arch-review-jelly-swipe`,
  `arch-review-lawncare-saas`, `arch-review-womens-fantasy-sports`,
  `arch-review-writers-app`. Schedule trigger id `twice-weekly`. Monitor variable
  `ARCH_STUCK_HOURS` (default `9`).
- Verified external contracts: see Repo facts.
- Behavior rules: the schedule table in overview decision 12.
- Error and security rules: no token in any file. The script reads `FABRO_DEV_TOKEN`
  from the environment, as it does today.

### Provisioning edits: `ops/provision-server-state.sh`

Apply each replacement exactly once. Each "Replace" text occurs exactly once in the file.

1. Replace:

```sh
# provision_automation <id> <environment_id> <repo> <workflow> <schedule_id_or_empty> [schedule_expr] [auto_merge]
```

   with:

```sh
# provision_automation <id> <environment_id> <repo> <workflow> <schedule_id_or_empty> [schedule_expr] [auto_merge] [schedule_enabled]
```

2. Replace:

```sh
#   - auto_merge defaults to true and is written into a pr-review row's description
```

   with:

```sh
#   - schedule_enabled defaults to false. Only the arch-review rows pass true (ADR
#     0012 D7): theirs are the one set of schedules this deployment runs on. Like
#     every other schedule flag it is set only when the row is created; an existing
#     row's flag is the operator's and is never compared or rewritten.
#   - auto_merge defaults to true and is written into a pr-review row's description
```

3. Replace:

```sh
  auto_merge="${7:-true}"
```

   with:

```sh
  auto_merge="${7:-true}"
  schedule_enabled="${8:-false}"
```

4. Replace:

```sh
    *) echo "FAILED: $id auto_merge must be true or false, got '$auto_merge'" >&2; return 1 ;;
  esac
```

   with:

```sh
    *) echo "FAILED: $id auto_merge must be true or false, got '$auto_merge'" >&2; return 1 ;;
  esac
  case "$schedule_enabled" in
    true | false) ;;
    *) echo "FAILED: $id schedule_enabled must be true or false, got '$schedule_enabled'" >&2; return 1 ;;
  esac
```

5. Replace:

```sh
    schedule_json=",$(jq -nc --arg sid "$schedule_id" --arg expr "$schedule_expr" \
      '{type:"schedule",id:$sid,enabled:false,expression:$expr}')"
```

   with:

```sh
    schedule_json=",$(jq -nc --arg sid "$schedule_id" --arg expr "$schedule_expr" --argjson en "$schedule_enabled" \
      '{type:"schedule",id:$sid,enabled:$en,expression:$expr}')"
```

6. Replace:

```sh
provision_automation issue-triage-writers-app rust-node andrewthetechie/writers-app issue-triage hourly "45 * * * *" || FAILED=$((FAILED+1))
```

   with:

```sh
provision_automation issue-triage-writers-app rust-node andrewthetechie/writers-app issue-triage hourly "45 * * * *" || FAILED=$((FAILED+1))

# arch-review (ADR 0012 D7): created with the schedule ENABLED, the only rows that are.
# Two fixed weekdays per repo, 3 or 4 days apart, and never two repos in the same hour
# of the same day. The `true` before it is auto_merge, which only pr-review rows read.
echo "Provisioning arch-review automations..."
provision_automation arch-review-jelly-swipe python andrewthetechie/jelly-swipe arch-review twice-weekly "0 4 * * 1,4" true true || FAILED=$((FAILED+1))
provision_automation arch-review-lawncare-saas python-node andrewthetechie/lawncare-saas arch-review twice-weekly "0 4 * * 2,5" true true || FAILED=$((FAILED+1))
provision_automation arch-review-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports arch-review twice-weekly "0 4 * * 3,6" true true || FAILED=$((FAILED+1))
provision_automation arch-review-writers-app rust-node andrewthetechie/writers-app arch-review twice-weekly "0 5 * * 0,3" true true || FAILED=$((FAILED+1))
```

7. Replace:

```sh
echo "Done. Backlog and issue-triage schedules are created disabled; enable them if this was a rebuild."
```

   with:

```sh
echo "Done. Backlog and issue-triage schedules are created disabled; enable them if this was a rebuild. arch-review schedules are created enabled (ADR 0012)."
```


### Monitor edits: `ops/fabro-monitor.sh`

Apply each replacement exactly once. The `\t` in the `printf` is two characters, a
backslash and `t`, as in the file today.

1. Replace:

```sh
#   C3 dead-scheduler   any schedule enabled AND newest run > IDLE_HOURS old 🔴 4h
#   C4 stuck-run        non-terminal run older than STUCK_HOURS             🔴 4h
```

   with:

```sh
#   C3 dead-scheduler   any schedule enabled AND newest run > IDLE_HOURS old 🔴 4h
#                       (arch-review schedules excluded, ADR 0012)
#   C4 stuck-run        non-terminal run older than STUCK_HOURS             🔴 4h
#                       (ARCH_STUCK_HOURS for an arch-review run)
```

2. Replace:

```sh
#   STUCK_HOURS=3                 C4: non-terminal run older than this
```

   with:

```sh
#   STUCK_HOURS=3                 C4: non-terminal run older than this
#   ARCH_STUCK_HOURS=9            C4: the same limit for an arch-review run, which
#                                 triages up to 18 issues and stops starting new
#                                 ones at 6h (ADR 0012)
```

3. Replace:

```sh
STUCK_HOURS="${STUCK_HOURS:-3}"
```

   with:

```sh
STUCK_HOURS="${STUCK_HOURS:-3}"
ARCH_STUCK_HOURS="${ARCH_STUCK_HOURS:-9}"
```

4. Replace:

```sh
    enabled_schedules="$(jq '[.data[].triggers[]? | select(.type == "schedule" and .enabled == true)] | length' "$tmp/autos.json" 2>/dev/null)"
```

   with:

```sh
    # arch-review runs twice a week per repo (ADR 0012), so its enabled schedules
    # must not arm a 2-hour idle alarm meant for the backlog cadence.
    enabled_schedules="$(jq '[.data[] | select(.workflow != "arch-review") | .triggers[]? | select(.type == "schedule" and .enabled == true)] | length' "$tmp/autos.json" 2>/dev/null)"
```

5. Replace:

```sh
    jq -r '.data[] | [(.id // ""), (.timestamps.created_at // ""), (.lifecycle.status.kind // "")] | @tsv' \
```

   with:

```sh
    jq -r '.data[] | [(.id // ""), (.timestamps.created_at // ""), (.lifecycle.status.kind // ""), (.workflow.slug // "")] | @tsv' \
```

6. Replace:

```sh
        while IFS="$(printf '\t')" read -r rid created kind; do
          [ -n "$rid" ] || continue
          e="$(run_epoch "$created" "$rid")"
          [ -n "$e" ] || { warn "run $rid has no usable timestamp; row skipped"; continue; }
```

   with:

```sh
        while IFS="$(printf '\t')" read -r rid created kind slug; do
          [ -n "$rid" ] || continue
          e="$(run_epoch "$created" "$rid")"
          [ -n "$e" ] || { warn "run $rid has no usable timestamp; row skipped"; continue; }
```

7. Replace:

```sh
      stuck_age=0
      while IFS="$(printf '\t')" read -r rid created kind; do
```

   with:

```sh
      stuck_age=0
      stuck_lim="$STUCK_HOURS"
      while IFS="$(printf '\t')" read -r rid created kind slug; do
```

8. Replace:

```sh
        age=$(( now - e ))
        if [ "$age" -gt $(( STUCK_HOURS * 3600 )) ]; then
          stuck_n=$(( stuck_n + 1 ))
          if [ "$stuck_oldest_e" = 0 ] || [ "$e" -lt "$stuck_oldest_e" ]; then
            stuck_oldest_e="$e"; stuck_rid="$rid"; stuck_kind="$kind"; stuck_age="$age"
          fi
```

   with:

```sh
        age=$(( now - e ))
        lim="$STUCK_HOURS"
        [ "$slug" = arch-review ] && lim="$ARCH_STUCK_HOURS"
        if [ "$age" -gt $(( lim * 3600 )) ]; then
          stuck_n=$(( stuck_n + 1 ))
          if [ "$stuck_oldest_e" = 0 ] || [ "$e" -lt "$stuck_oldest_e" ]; then
            stuck_oldest_e="$e"; stuck_rid="$rid"; stuck_kind="$kind"; stuck_age="$age"; stuck_lim="$lim"
          fi
```

9. Replace:

```sh
            "stuck-run — run $stuck_rid status='${stuck_kind:-unknown}' age $(( stuck_age / 3600 ))h (limit ${STUCK_HOURS}h)"
```

   with:

```sh
            "stuck-run — run $stuck_rid status='${stuck_kind:-unknown}' age $(( stuck_age / 3600 ))h (limit ${stuck_lim}h)"
```


### Doc edits

1. `AGENTS.md`, first line of the body: "Three production Fabro workflows and the ops
   tooling for the host that runs them." becomes "Four production Fabro workflows and
   the ops tooling for the host that runs them."
2. `AGENTS.md`, *Layout* table, row `.fabro/workflows/<name>/`: "Three runnable packages
   — `backlog`, `pr-review`, `issue-triage` —" becomes "Four runnable packages —
   `backlog`, `pr-review`, `issue-triage`, `arch-review` —".
3. `AGENTS.md`, *Layout* table: add this row after the `docs/issue-triage/` row:

```
| `docs/architecture-review/` | The task series for ADR 0012: `arch-review` scans a repository for deepening candidates twice a week, files the strongest as `architecture` issues, and triages the waiting issues toward `agent` through the shared `_shared/triage/` phase. `00-overview-and-contracts.md` first. An `architecture` PR is never auto-merged. |
```

4. `AGENTS.md`, *Validating*: in the paragraph that begins "Baselines as of 2026-09-24",
   replace "`IssueTriage (14 nodes, 32 edges)` clean" with "`IssueTriage (13 nodes, 26
   edges)` clean, and `ArchReview (21 nodes, 44 edges)` clean. Both include the 10 nodes
   of `_shared/triage/`". These numbers were measured with `fabro validate` on
   2026-09-24 against the target files of tasks 01 to 04. Task 07 confirms them.
5. `CONTEXT.md`, first paragraph: "Three production Fabro workflow packages (`backlog`,
   `pr-review`, `issue-triage`)" becomes "Four production Fabro workflow packages
   (`backlog`, `pr-review`, `issue-triage`, `arch-review`)".
6. `CONTEXT.md`, term **Automation**: "triggers (`api:manual`, `schedule:every-15m`,
   `schedule:hourly`). Three per repo, twelve total." becomes "triggers (`api:manual`,
   `schedule:every-15m`, `schedule:hourly`, `schedule:twice-weekly`). Four per repo,
   sixteen total. The `arch-review` schedules are the only enabled ones (ADR 0012)."
7. `ops/README.md`, the table row for `provision-server-state.sh`: "Recreates the twelve
   **automations** (three per repo: `backlog`, `pr-review`, `issue-triage`)" becomes
   "Recreates the sixteen **automations** (four per repo: `backlog`, `pr-review`,
   `issue-triage`, `arch-review`)".
8. `ops/README.md`, the paragraph that begins "Nothing in this deployment fires on a
   cron. The four `issue-triage` schedules are disabled as well": replace the whole
   paragraph with:

```
The four `arch-review` schedules are the only enabled schedules in this deployment
(ADR 0012). Each repository runs twice a week on two fixed weekdays, 3 or 4 days
apart, and no two repositories fire in the same hour of the same day: jelly-swipe
`0 4 * * 1,4`, lawncare-saas `0 4 * * 2,5`, womens-fantasy-sports `0 4 * * 3,6`,
writers-app `0 5 * * 0,3`. `arch-review` uses only hosted models, so the coder
scheduler does not admit it. `fabro-monitor.sh` excludes these schedules from C3 and
gives an `arch-review` run 9 hours under C4 (`ARCH_STUCK_HOURS`). The four
`issue-triage` schedules stay disabled: `arch-review` triages each repository's
waiting issues, and `issue-triage` is the one-issue manual fire. Turn a review off
with `ops/fabro-automation-schedule.sh arch-review-<repo> off`.
```

## Acceptance Criteria
- [ ] `sh -n ops/provision-server-state.sh` and `sh -n ops/fabro-monitor.sh` exit 0.
- [ ] A dry run of the provisioning script (command in Repo facts) prints
      `would create:` for the four `arch-review-<repo>` ids. Only the operator has the
      token; if you do not, state that this check is left for task 07.
- [ ] The gate expression counts an enabled `arch-review` schedule as zero (see Test
      Expectations).
- [ ] `grep -n 'Three production\|twelve' AGENTS.md CONTEXT.md ops/README.md` finds no
      line about the workflow or automation count.

## Test Expectations
The monitor and the provisioning script have no harness. Run these checks from the
repository root:

```sh
sh -n ops/provision-server-state.sh && sh -n ops/fabro-monitor.sh && echo syntax-ok
# prints: syntax-ok

echo '{"data":[{"workflow":"arch-review","triggers":[{"type":"schedule","enabled":true}]},{"workflow":"backlog","triggers":[{"type":"api","enabled":true},{"type":"schedule","enabled":false}]}]}' \
  | jq '[.data[] | select(.workflow != "arch-review") | .triggers[]? | select(.type == "schedule" and .enabled == true)] | length'
# prints: 0

jq -nc --arg sid twice-weekly --arg expr "0 4 * * 1,4" --argjson en true \
  '{type:"schedule",id:$sid,enabled:$en,expression:$expr}'
# prints: {"type":"schedule","id":"twice-weekly","enabled":true,"expression":"0 4 * * 1,4"}

./ops/test-task-gates.sh
# still passes, with the same count as before this task
```

## Dependencies
- Blocked by: 04 (the package that the rows fire), 05 (the merge block)
- Why blocked: an enabled schedule fires `arch-review`, which must include triage (04).
  The issues it promotes must not auto-merge (05) before any schedule can fire.
- Blocks: 07

## Labels
`chore`, `ops`, `priority:medium`

## Estimate
Small

## Risk
2 - scripts and docs only. The provisioning script creates rows only when the operator
runs it, and never changes an existing row.

## Validator Stopping Point
Both scripts pass `sh -n`, the jq checks print the values above, and
`./ops/test-task-gates.sh` still passes.
