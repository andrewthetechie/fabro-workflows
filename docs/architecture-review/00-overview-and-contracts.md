# Architecture review: overview and canonical contracts

**Read this file first.** Every task in this folder assumes the decisions, contracts and
rules in this file. Each task repeats what it needs. If a task and this file disagree,
this file is correct. Stop and report the conflict. Do not guess.

Source decision: `docs/adr/0012-architecture-review.md` (ADR 0012). This series is its
implementation plan.

**When you write or change a `.fabro` file or a `workflow.toml`, invoke the
`/fabro-workflow` skill first.** It has the DOT attribute tables, the node types, the
condition grammar, the `import=` contract and the CLI. This file adds only the facts
that are specific to this deployment.

## What this is

The `backlog` workflow implements GitHub issues that carry the `agent` label. Today only
humans file those issues. This series adds a fourth workflow package, `arch-review`. One
run does these things for one target repository:

1. It scans the codebase for refactors that make shallow modules deep.
2. It files the strongest ones as GitHub issues labelled `architecture` and `needs-triage`.
3. It triages those issues and the other waiting issues in the repository. A ready issue
   gets the `agent` label, so the scheduler queues it for `backlog`.
4. It sends one Discord summary.

It runs twice a week for each repository and when the operator fires it.

## Glossary (from `CONTEXT.md`)

- **Run**: one execution of a workflow graph on the fabro server.
- **Architecture review**: one run of `arch-review` against one target repository.
- **Deepening candidate**: one refactor that a review proposes. Its strength is
  `Strong`, `Worth exploring` or `Speculative`. A candidate is not an issue until the
  review files it.
- **Architecture issue**: a candidate filed as an issue, with the `architecture` label.
  The label is permanent.
- **Remainder issue**: the issue a `backlog` run files for the tasks that it did not
  start because its task budget (8) was spent. The `file_remainder` node files it.
- **Scheduler**: `ops/scheduler/`, a Python service that starts `backlog` runs for
  issues labelled `agent`. It does not start `arch-review` or `issue-triage` runs.
- **Triage phase**: the shared graph `_shared/triage/triage.fabro` (task 01). It triages
  one issue. `issue-triage` imports it once, and `arch-review` runs it once for each issue
  in a queue.

## Operator decisions (settled, do not change them)

| # | Decision |
|---|---|
| 1 | The package is `.fabro/workflows/arch-review/`. Never call it "project improvement". The word `improve` already names nodes in `issue-triage` and `backlog`. |
| 2 | The triage nodes move to `.fabro/workflows/_shared/triage/triage.fabro`. `issue-triage` and `arch-review` both import it with `import=`. `issue-triage` stays as a one-issue package that the operator fires manually. |
| 3 | The triage phase never blocks on a human. There is no `ask_human` gate and no `record_answer` node. For `needs_info` it posts the questions, adds `needs-info`, and a hook sends one Discord message with the issue link. A human answers in a new issue comment. |
| 4 | The triage phase decides the questions that the repository can answer (task 02). For an Architecture issue it decides every question that has a recommended answer and an internal, reversible choice. For a human-filed issue it decides a question only when the code, `CONTEXT.md` or an ADR supports the recommended answer. It writes each decision into the issue body under `## Decisions made during triage`. |
| 5 | A review files only `Strong` and `Worth exploring` candidates, at most **8** per run. Zero is a valid result. |
| 6 | Deduplication uses the slug marker in contract C2. A slug that any `architecture` issue has, open or closed, is never filed again. |
| 7 | A review triages two budgets, in this order: (1) every issue it filed this run, (2) at most **10** other issues: answered `needs-info` issues first, then `needs-triage` issues, oldest first in each group. |
| 8 | A review starts no new triage after the run is **6 hours** old. |
| 9 | If the scan fails, the review files nothing and still triages budget 2. |
| 10 | Claim label `triage-in-progress`. The triage phase skips an issue with a claim that is less than **24 hours** old, and takes over a claim that is older. |
| 11 | Every `arch-review` stage uses the model `high-reasoning`, with the fallback `kimi:kimi-k3`. It never uses a coder box, and the scheduler does not admit it. |
| 12 | Schedules, **enabled**: jelly-swipe `0 4 * * 1,4`, lawncare-saas `0 4 * * 2,5`, womens-fantasy-sports `0 4 * * 3,6`, writers-app `0 5 * * 0,3`. On demand is the automation's `api:manual` trigger. The workflow takes **no inputs**. |
| 13 | The scan reads 90 days of git history: `git fetch --shallow-since="90 days ago" origin main`. |
| 14 | An Architecture issue is never merged automatically. `open_pr` copies `architecture` to the PR. `merge_gate` blocks a PR with that label. `file_remainder` copies it to the Remainder issue. |
| 15 | One Discord summary per review. |
| 16 | `fabro-monitor.sh`: C3 ignores `arch-review` automations. C4 allows `arch-review` runs 9 hours (`ARCH_STUCK_HOURS=9`). |

## Canonical contracts

### C1. Triage phase entry and exit (task 01 writes it, tasks 01 and 04 use it)

The importing graph must do these things before the phase starts:

- Create `/tmp/fabro/`.
- Write the issue number, digits only, to `/tmp/fabro/issue_number`.

The phase does these things:

- It reads only `/tmp/fabro/issue_number` from the importing graph.
- It writes the outcome word, one line, to `/tmp/fabro/triage_outcome`.
- It publishes the same word as `context.triage_outcome`.

The outcome words:

| Word | Meaning |
|---|---|
| `ready` | Promoted: `agent` added, `needs-triage`/`needs-info`/`triage-in-progress` removed. |
| `needs_info` | Questions posted, `needs-info` added. |
| `not_actionable` | Report posted. The issue stays open. |
| `skipped` | Another run holds a claim that is less than 24 hours old. The phase changed nothing. |
| `released` | The phase failed and removed its own claim. |

The phase has one `start` and one `exit`. Every path goes through a node named `done`.
This is the `import=` contract: an imported file has exactly one exit with exactly one
incoming edge, and the edges at the boundary carry no `condition`.

### C2. Architecture issue body marker (task 03 writes it, task 03 reads it)

The **first line** of an Architecture issue body is exactly this:

```
<!-- fabro:arch-candidate slug=<SLUG> -->
```

- `<SLUG>` matches `^[a-z0-9][a-z0-9-]{2,59}$`. Example: `order-intake-validation`.
- Deduplication extracts slugs with this jq expression on a `gh issue list --json body`
  result:
  `[.[] | .body | capture("fabro:arch-candidate slug=(?<s>[a-z0-9-]+)")? | .s]`

### C3. Labels

| Label | Colour | Who adds it | Meaning |
|---|---|---|---|
| `architecture` | `0E8A16` | `arch-review` `file_issues`, `open_pr`, `file_remainder` | An Architecture issue, or a PR or Remainder issue that came from one. Permanent. |
| `needs-triage` | `D4C5F9` | `arch-review` `file_issues`, humans | Waiting for triage. |
| `ai-generated` | `EDEDED` | `arch-review` `file_issues`, `file_remainder` | An agent wrote the issue. |
| `triage-in-progress` | `FBCA04` | triage phase `claim` | Claim. |
| `needs-info` | `FEF2C0` | triage phase `post_questions` | Waiting for a human answer. |
| `agent` | `5319E7` | triage phase `apply_ready` | Queue item for the scheduler. |

Create every label before use with the idempotent pattern that `claim` uses today:

```sh
gh label create needs-info --color FEF2C0 --description 'Waiting on a human for triage answers' 2>/dev/null || true
```

### C4. Run-local files under `/tmp/fabro/`

| Path | Written by | Read by | Shape |
|---|---|---|---|
| `issue_number` | `issue-triage` `acquire`, `arch-review` `next_issue` | triage phase `claim` | digits |
| `triage_outcome` | triage phase terminal nodes | `arch-review` `next_issue` | one word from C1 |
| `issue.json` | triage phase `claim` | triage phase | `gh issue view --json number,title,body,labels,comments,url` |
| `triage.json`, `triage.md` | triage agent | `triage_gate`, terminal nodes | see task 01 and task 02 |
| `arch/hotspots.txt` | `arch-review` `history` | scan agent | `count path` lines |
| `arch/existing.json` | `arch-review` `history` | scan agent, `file_issues` | `[{number,title,state,stateReason,slug}]` |
| `arch/candidates.json` | scan agent | `scan_gate`, `file_issues` | see task 03 |
| `arch/filed.json` | `file_issues` | `build_queue` | JSON array of issue numbers |
| `arch/scan_status` | `scan_gate` | `summarize` | `ok` or `failed` |
| `queue.json`, `queue_index` | `build_queue`, `next_issue` | `next_issue` | array of numbers; integer |
| `tally.json` | `next_issue` | `summarize` | `{"ready":0,"needs_info":0,"not_actionable":0,"skipped":0,"released":0}` |
| `run_started` | `prep` | `next_issue` | epoch seconds |

### C5. Discord hook context keys (the notify script reads these)

The hook script runs in the fabro server container. It reads the run state from
`GET /api/v1/runs/<id>/state`. **In that JSON, `checkpoints` is an ascending array**
(verified 2026-09-24 on run `01M3BA6RVDY215DM530PHCSGV5`). `grep | head -1` therefore
returns the **oldest** value of a key, and `grep | tail -1` returns the newest. A graph
that loops must use `tail -1`.

| Key | Published by | Kind that reads it |
|---|---|---|
| `issue_url` | triage phase `claim` | `triage-question`, `triage-failed` (tail -1) |
| `triage_questions` | triage phase `triage_gate` | `triage-question` (tail -1) |
| `arch_summary` | `arch-review` `summarize` | `arch-summary` (tail -1) |

A value in these keys must not contain `"` or a newline. The publishing node removes
`"` with `tr -d '\"'` (that is how the text appears inside a `.fabro` script) and joins
lines with two spaces, as `triage_gate` does today. The notify script removes any
backslash with `sed 's/[\\"]//g'`. That `sed` is in a `.sh` file, not a `.fabro` file,
so its backslashes are allowed.

## Rules every task must follow

These come from `AGENTS.md`. Each one cost a live incident. `fabro validate` catches
none of them.

1. **`.fabro` files are Graphviz DOT.** A node's shell script is a `script="..."`
   attribute. Inside it:
   - The **only** backslash allowed is `\"`. Never write `\n`, `\t` or `\\`. The DOT
     parser turns `\n` into a real newline. To get a newline in a jq program, write
     `([10]|implode)`. To write lines to a file, use several `echo` commands.
   - Write every `"` in the script as `\"`.
   - Never write a `#` comment in a script. Fabro pastes the whole script into later
     agent prompts. Put explanations in `//` DOT comments above the node.
   - Scripts run under POSIX `sh`: no `[[ ]]`, no `pipefail`, no arrays, no `local`.
2. **Routing.** A command node that prints `{"context_updates":{...}}` **must** declare
   `output_schema="routing"`. A node that prints no routing object **must not** declare
   it. The routing object is the **last** JSON object that the node prints.
3. **Every command node has an unconditional edge to the failure node.** In the triage
   phase that node is `release`. The success path is a conditional edge, usually
   `[condition="outcome=succeeded"]`.
4. **Delete a contract file before the agent that writes it runs.**
5. **Agents never run `git` or `gh`.** Command nodes do.
6. **Stylesheet class names** use `[a-z0-9-]` only.
7. **A class that a node in an imported file uses needs a rule in every importing
   graph's `model_stylesheet`.** The triage phase uses `.improve` and `.triage`.
8. **A root graph's `stall_timeout` is larger than every node timeout in it,** including
   the imported nodes. Use `stall_timeout="60m"` and node timeouts of 50m or less.
9. **Every graph uses `default_fidelity="truncate"`.**
10. **Anchor hook matchers.** An imported node's id has the placeholder as a prefix
    (`triage_phase.post_questions`). Write `(^|[.])post_questions$`, never
    `^post_questions$`.
11. Never put a token, key or webhook URL in a tracked file. This repository is public.
12. `gh issue edit` resolves every label name before it sends. One missing label fails
    the whole call. Create labels first (C3), and use `set -e` in nodes that apply labels.

## How to check your work offline (no server needed)

Run these from the root of the `fabro-workflows` checkout:

```sh
./ops/test-task-gates.sh      # graph shell tests. Before this series: "PASS: 255 checks"
python3.11 -c 'import tomllib,sys; [tomllib.load(open(p,"rb")) for p in sys.argv[1:]]' \
  .fabro/workflows/*/workflow.toml
sh -n ops/fabro-monitor.sh && sh -n ops/provision-server-state.sh
```

Each task that adds checks raises the `PASS` number. `fabro validate` and
`ops/check-routing-schemas.py` need the `fabro` binary, which exists only in the
container on the fabro host. Only the operator runs them (task 07). You cannot see a
routing mismatch offline, so follow rule 2 exactly.

## Task index

The tasks run in order, one at a time.

| # | File | Blocked by |
|---|---|---|
| 01 | `01-shared-triage-phase.md` | none |
| 02 | `02-triage-decides.md` | 01 |
| 03 | `03-arch-review-scan-and-file.md` | none |
| 04 | `04-arch-review-triage-loop.md` | 01, 03 |
| 05 | `05-architecture-never-auto-merges.md` | none |
| 06 | `06-ops-provision-monitor-docs.md` | 04, 05 |
| 07 | `07-validate-and-turn-on.md` (operator, on the host) | 02, 04, 05, 06 |

Task 06 waits for task 05 on purpose. If the schedules were enabled before the merge
block existed, an LLM-proposed refactor could merge into `main` with no human.

## Deploy notes (operator)

- Graph and prompt changes (01 to 05) go live on the next run after they reach `main`.
  The `arch-review` automations do not exist until task 07 runs the provisioning script,
  so nothing fires `arch-review` before then.
- Task 01 changes `discord-notify.sh`, and task 03 changes it again. The hook runs the
  copy in the container, so deploy it after each of those tasks (`AGENTS.md`, "Deploying
  to the server after a merge to `main`").
- Task 06 changes `ops/fabro-monitor.sh` and `ops/provision-server-state.sh`. Copy the
  monitor to `~/bin/` on the host.
