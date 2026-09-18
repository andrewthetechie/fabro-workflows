# Fabro Issue Triage — Overview & Canonical Contracts

**Read this first.** Every task in this folder assumes the architecture, the contracts
and the findings recorded here. This document is the single source of truth.

## What this is

The front door of the pipeline. `backlog` decomposes `agent`-labelled issues and opens
PRs; `pr-review` reviews and merges them. Nothing puts issues *into* that queue except
a human. `issue-triage` does: it takes one `needs-triage` issue, improves its body,
triages it against the repository, retitles it to Conventional Commits, and either
promotes it to `agent` or asks the human the questions that block it.

It is a third workflow package, not a phase of `backlog`. It runs **only when nothing
else is running**, so it consumes the capacity the other two are not using, and it is
the only workflow here that deliberately blocks on a human.

It is a Fabro translation of the `issue-triage-loop` skill
(`~/.agents/skills/issue-triage-loop`), whose relay is improve → triage → retitle, one
subagent per issue, sequentially. Fabro supplies the sequence; the two prompts in task
04 carry the substance of the `issue-improver` and `issue-triage` skills that relay
invokes.

## Operator decisions (settled — do not re-litigate)

| # | Decision |
|---|---|
| 1 | A **separate package**, `.fabro/workflows/issue-triage/`, with its own four automations. Not a branch of `backlog`'s quiet-exit, which would require reversing ADR 0001's disabled schedules, and not a phase inside `backlog`, which would weld a human-blocking path into the graph that opens PRs. |
| 2 | **One issue per run.** The run acquires exactly one and exits. |
| 3 | **"The coders are not busy" means the host has no other run**, read from `GET /api/v1/system/info` → `runs.scheduler_slots_used`, quiet-exit when it exceeds 1. Not the local GPU pool: the agent stages run on `high-reasoning`, so the contention is slots and quota, not CUDA. |
| 4 | **Triage promotes.** A `ready` issue loses `needs-triage`, gains `agent`, and is `backlog`'s problem on its next fire. Promotion is the value; a workflow that only labels is a worse version of a human reading the issue. |
| 5 | **Triage never closes an issue.** `not-actionable` gets a label and a comment and stays open for a human to close. It is the only irreversible-looking act available and it buys nothing. |
| 6 | **The reporter's original body is archived as a comment** before the first improve write. `decompose` is told to weigh reporter intent, and GitHub's edit history is not readable by an agent. |
| 7 | **Two agent stages, not three.** Retitle folds into the triage contract as a `title` field; every tracker mutation is performed by a command node from a contract file, never by the agent. |
| 8 | **One question batch per run, at most one in-run re-triage.** The `issue-triage` skill forbids incremental questioning and specifies the cross-run loop instead; successive runs supply the rounds. |
| 9 | **The gate waits 30 minutes** and degrades to posting the questions on the issue. See ADR 0002 — a blocked run holds one of three slots and dies on the next deploy. |
| 10 | **All four repos at once.** Only `womens-fantasy-sports` has a queue; the other three quiet-exit until they do. |
| 11 | **No `.fabro/setup.sh`.** Triage clones and reads. Paying up to 20 minutes of dependency install on every run — including the ones that quiet-exit — to serve the minority of issues needing a live reproduction is the wrong trade; an issue that cannot be triaged without running it becomes an open question. |
| 12 | **`high-reasoning`, not `glm-5.3`**, in the stylesheet. Priority with overflow, defined in LiteLLM. See ADR 0003. |
| 13 | **Hourly, staggered `:30 :35 :40 :45`.** A quiet-exit still spawns a sandbox and clones a repo. 24 chances a day per repo is more than a one-issue-per-run loop can absorb. |
| 14 | **"Cannot tell" is not "busy".** An unreachable API or a missing token fails the run loudly rather than quiet-exiting, so a broken capacity check cannot silently disable triage forever. |

## Findings established against the live deployment

Measured 2026-09-16 against the four target repositories, the installed `gh`, and
`context/fabro` at 0.354.0-nightly.0.

### 1. The automation posts as `andrewthetechie` — the same identity as the human

`gh pr view` on `writers-app#879` and `#867` returns
`{"author":"andrewthetechie"}` for the `## AI PR review complete` comments.
`[server.integrations.github] strategy = "token"` means every run acts as the owner's
PAT.

This kills the obvious design for the return path. "Has a human replied since the bot
asked?" **cannot** be answered by comparing comment authors. Every comment this
workflow writes therefore carries an HTML-comment marker, and the return path keys on
the *absence* of a marker in the newest comment. See **Markers** below.

### 2. Scripts and prompts cannot read context; only `stdin_source` can

Templates render in exactly three attributes, and the only expressions available are
`{{ goal }}`, `{{ inputs.* }}` and `{{ vars.* }}`. There is **no `{{ context.* }}`**,
in a `prompt` any more than in a `script` — and every prompt in this repository today
uses no template variable at all. Data moves between stages in files under
`/tmp/fabro/`.

The single exception is a command node's `stdin_source`, which pipes **one flat
context key** to stdin, resolving `context.NAME` then bare `NAME`. That is the only
channel through which the human's freeform gate answer can reach anything, which is
why task 03 probes it before wiring it.

### 3. The label vocabulary is inconsistent across the four repos

| Label | jelly-swipe | lawncare-saas | womens-fantasy-sports | writers-app |
|---|---|---|---|---|
| `needs-triage` | yes | yes | yes | yes |
| `needs-info` | **no** | yes | **no** | yes |
| `agent` | yes | **no** | **no** | **no** |
| `triage-in-progress` | no | no | no | no |

Four different descriptions for `needs-triage`, and `jelly-swipe` additionally has a
bare `triage` label that nothing reads. Every label this workflow applies is therefore
created if absent with `gh label create ... || true`, the `claim` precedent.

### 4. The only triage queue that exists is `womens-fantasy-sports`

Open `needs-triage`: **11** in `womens-fantasy-sports`, **0** in the other three. The
E2E in task 09 has real input on exactly one repo, and the other three prove the
quiet-exit path.

### 5. There are zero `agent`-labelled issues anywhere, and 24 stranded ones

`backlog`'s `acquire` selects `--label agent`. No repo has an open issue with it, while
10 issues in `lawncare-saas` and 14 in `writers-app` sit under `ready-for-agent`, which
nothing reads. The operator is handling that separately; it is recorded because this
workflow makes `agent` the promotion target and therefore hardens the split.

### 6. `gh issue list --json` exposes `comments`, which is the whole return path

Verified field list includes `comments`, `labels`, `number`, `title`, `body`,
`createdAt`, `updatedAt`, `author`. One `gh issue list` call can therefore select
candidates *and* evaluate their newest comment, with no per-issue round trip.

### 7. `runs.scheduler_slots_used` is the server's own admission counter

**Correction (2026-09-16, live-fire on `womens-fantasy-sports`):** the route is
`GET /api/v1/system/info`, not `GET /api/v1/system` — the bare path 404s (confirmed
against the live server, from the Mac, from inside `fabro-fabro-1`, and from a
container on `fabro_default`, all with a valid dev token). `check_capacity` in Task 03
originally curled the wrong path and failed every single fire with "the fabro API did
not answer", a false negative for finding 14's "cannot tell" case — the API answered
fine, just not at that path. Fixed in `workflow.fabro` and in Task 03's doc.

`GET /api/v1/system/info` returns `runs: { total, active, scheduler_slots_used }`
(`handler/system.rs:60-103`). `scheduler_slots_used` is computed with the identical
predicate the scheduler admits against, while `active` counts `Pending` and `Runnable`
as well. **Gate on `scheduler_slots_used`, not on `active`** — a queued run that will
never start while we hold a slot must not keep triage out.

### 8. A conventional-commits title is CI-enforced on at least two repos

`jelly-swipe` runs `amannn/action-semantic-pull-request@v6` with types
`feat fix docs chore refactor test ci build perf revert`; `lawncare-saas` runs one too.
`style` is **not** in that list. `release-please` runs on both, so `feat:` cuts a minor
and `fix:` a patch. The title this workflow writes is the seed of the PR title
`backlog` later derives, so it obeys the same rule as the auto-merge derivation:
**never `!`, never a `BREAKING CHANGE:` footer.**

## Architecture

`start` fed a `check_capacity` gate until 2026-09-18; it is deleted (ADR 0005,
`9b0e4f1`) and `start` now feeds `acquire` directly. Triage competes for no coder
instance, so it never had to stand down for one.

```
start
             │
        acquire ──(outcome=failed: nothing to do)──→ exit   // quiet, no LLM
             │
        claim                    // labels, claim, fetch, archive original body once
             │
        improve  (.improve) ──→ improve_gate ──(failed)──→ improve   // one retry
             │                        │
        triage   (.triage)  ←─────────┘
             │
        triage_gate ──(failed)──→ triage                             // one retry
             ├── ready ─────────────→ apply_ready          → exit
             ├── not_actionable ────→ apply_not_actionable → exit
             ├── needs_info && answered ─→ post_questions   → exit
             └── needs_info ────────→ ask_human  [hexagon, 30m]
                                          ├─ freeform ────→ record_answer → triage
                                          ├─ [D] Defer ───→ post_questions
                                          ├─ [X] Not actionable → apply_not_actionable
                                          └─ outcome=failed → post_questions
```

Fifteen nodes. Every command node's unconditional edge lands on `release`, which
removes the claim and ends the run.

### Why a separate package and not a `backlog` branch

`backlog`'s quiet-exit is a perfect idle signal — a repo with no `agent` work — and
using it was the first design. It requires re-enabling the four `backlog` schedules
that ADR 0001 deliberately disabled, and it puts a 30-minute human gate inside the
workflow that force-pushes branches and opens pull requests. A `check_capacity` node
read the same idea off the server's own counter with neither cost.

That node is gone (ADR 0005). The argument above still holds against the
quiet-exit-as-idle-signal design; what changed is the conclusion it led to. Gating
triage on a global run count was only ever right while the cap was 2 and triage held
one of the two slots — at a cap of 4 the same check stands down for two coder runs
triage does not compete with. Admission is the coder scheduler's job now, and triage
is deliberately outside it (scheduler decision 13).

### Why the merge of "retitle" into triage

The relay's third leg reads the improved body and the triage comment and writes a
title. Both are already in the triage agent's context when it writes its artifact, so
a third session buys nothing but a third chance to fail. `triage.json.title` is
validated by `triage_gate` against the Conventional Commits pattern and applied by the
`apply_*` nodes.

## Canonical state files

All under `/tmp/fabro/`. An agent writes JSON; a command node validates it with `jq`
and emits `context_updates` that decide routing. Delete a contract file before the
agent that writes it runs — an agent that exits succeeded without writing hands the
gate its predecessor's result.

| File | Written by | Contract |
|---|---|---|
| `acquire.json` | `acquire` | the one selected issue: `{number, title, reason}` where `reason` is `needs-triage` or `answered` |
| `issue.json` | `claim`, refreshed by `record_answer` | `gh issue view --json number,title,body,labels,comments,url` |
| `improve.json` | `improve` | `{status: improved\|unchanged, title, body, reason}` |
| `improve_attempts` | `improve_gate` | `0`, `1` or `2` |
| `triage.json` | `triage` | `{readiness, classification, confidence, title, labels[], questions[], summary}` |
| `triage.md` | `triage` | the issue-ready Markdown artifact, posted verbatim as a comment |
| `triage_attempts` | `triage_gate` | `0`, `1` or `2` |
| `human_answer.md` | `record_answer` | the freeform gate text, straight from stdin |
| `bot_login` | `claim` | `gh api user --jq .login`, recorded for the log only — **not** used for detection, see finding 1 |

`readiness` is `ready`, `needs_info` or `not_actionable`. The skill spells the middle
one `needs-info`; the contract uses an underscore because it also travels as a context
value in an edge condition.

## Markers

Every comment this workflow writes opens with an HTML comment. They render invisibly,
they are the only reliable way to tell this workflow's comments from a human's
(finding 1), and they are the mechanism behind the return path.

| Marker | On |
|---|---|
| `<!-- fabro:triage-original -->` | the archived original body, posted once per issue |
| `<!-- fabro:triage-report -->` | the triage artifact on the `ready` and `not_actionable` paths |
| `<!-- fabro:triage-questions -->` | the question batch on the `needs_info` path |

**The comment recording a human's gate answer carries no marker, deliberately.** It is
the human's words, and if the run then dies the issue must still look answered to the
next `acquire`. A marker there would strand it.

`#` is a DOT comment character, but these strings live inside quoted attribute values,
where it is fine. Do not strip one to make a grep pass.

## Selection

`acquire` takes the lowest-numbered open issue matching either:

- label `needs-triage`; or
- label `needs-info` **and** the newest comment contains none of the markers above —
  a human has replied since the questions were posted.

`triage-in-progress` is not excluded. Decision 3 guarantees no other triage run
exists while `acquire` is running, so a claim label it finds was abandoned by a
restart and is reclaimed. A human replying by **editing** the bot's comment rather
than adding one is not detected; say so in the questions comment.

## Context keys

Every key is written on **every** branch of its node. A stale key from an earlier
visit must never satisfy a condition meant for this one.

| Node | Keys |
|---|---|
| `acquire` | none — it publishes nothing and routes on `outcome` alone |
| `claim` | `issue_number`, `issue_url`, `answered` |
| `improve_gate` | `improve_status` (published for the log; no edge reads it) |
| `triage_gate` | `triage_readiness`, `question_count`, `triage_questions`, `answered` |
| `record_answer` | `answered` (always `true`) |
| `apply_ready` / `apply_not_actionable` / `post_questions` | `triage_outcome` |

`triage_questions` is not decoration: it is what lets the Discord hook put the actual
questions and their recommended defaults in the notification instead of a bare run
link. It is built with `jq -nc --arg` so that agent-authored text containing quotes
cannot break the JSON, and truncated to 1200 characters for Discord's 2000-character
limit.

`triage_gate` also strips `"` from the value before publishing it. The hook does not
parse the run state, it greps it — `'"triage_questions":"[^"]*"'` — and a `\"` inside
the serialized string ends the match early, silently dropping the rest of that question
and every question after it. Sanitising at the producer is the only place one fix
covers every consumer.

## Rules specific to this workflow

The inherited golden rules all still apply — `output_schema="routing"` on every
command node that prints `context_updates`, a failed node still routes down its
*unconditional* edge, `\"` is the only backslash, `//` is the comment, POSIX `sh`
only, stylesheet class selectors are `[a-z0-9-]`, anchored hook matchers. Five more
matter here:

1. **The gate carries both `timeout` and `human.default_choice`, and the default
   choice is also a real edge target.** Fabro does not validate the pair. Without the
   timeout the gate waits forever and dies on the next deploy; without the default
   choice a timeout re-asks the question rather than advancing. ADR 0002.
2. **Never author a comment without a marker, except the human's answer.** Finding 1
   means an unmarked bot comment permanently poisons the return path for that issue.
3. **Both `needs_info` edges out of `triage_gate` exist, and the `answered=true` one
   is declared first.** Otherwise a re-triage that is still short of answers blocks a
   second time in the same run, which decision 8 forbids.
4. **Never emit `!` or `BREAKING CHANGE:` in a title**, and use only the ten
   Conventional Commits types finding 8 lists. `style` is not among them.
5. **The agent never calls `gh`.** It writes `improve.json` / `triage.json` / `triage.md`
   and nothing else; every mutation is a command node. The `issue-triage` skill states
   the same boundary for itself — *"Issue-tracker access is strictly read-only"*.

## Known risks, recorded deliberately

| Risk | Detail |
|---|---|
| A 30-minute gate holds one of the cap's slots | ~~Bounded by ADR 0002 and by the capacity gate, which makes at most one blocked triage run possible.~~ **Half of this bound is gone.** `check_capacity` was deleted on 2026-09-18 (ADR 0005, `9b0e4f1`), so nothing now limits triage to one blocked run: several can hold gates at once, up to the cap. ADR 0002's 30-minute timeout is the whole of what remains, and it is what keeps the hold bounded in *time* rather than in *number*. Triage is deliberately never queued by the coder scheduler (scheduler decision 13), so the scheduler does not re-establish the missing half either. |
| An improve pass can degrade an issue | The agent rewrites the body in place. Decision 6's archive comment is the only copy of what the reporter wrote, and it is posted *before* the first write for exactly that reason. |
| A human editing the bot's question comment is invisible | The return path reads the newest comment, not edits. The questions comment says "reply, do not edit". |
| Promotion is unsupervised | A `ready` verdict puts the issue in `backlog`'s queue with no human in the loop. The gate exists for questions, not for approval, and decision 4 is deliberate. `backlog` schedules are disabled today, so today the promotion is a label and nothing more. |
| A run that dies mid-triage strands `triage-in-progress` | Recovered by the next `acquire` (Selection). The window is one hour. |
| LiteLLM is a single point of failure for the model | `high-reasoning` lives in LiteLLM's Postgres, not in git. A rebuilt proxy without it fails every run at `improve`. Task 01's provisioning script is the mitigation. |
| Triage quality decides what the pipeline builds | The body this workflow writes is the body `decompose` reads. A confidently wrong improve pass propagates into child issues and into code. This is the argument for `high-reasoning` rather than the local coders, and it is why `improve_gate` refuses an agent's output rather than trusting it. |

## Task index

| # | Task | Needs smarter LLM? |
|---|---|---|
| 00 | This document | — |
| 01 | LiteLLM: the `high-reasoning` model, its fallback, and `provision-litellm-models.sh` | no |
| 02 | Fabro's model catalog: `settings.toml` entry and the restart | no |
| 03 | The workflow graph | **yes** |
| 04 | The two prompts and their contracts | **yes** |
| 05 | `workflow.toml`: permissions, branches, hooks | no |
| 06 | `discord-notify.sh`: the `triage-question` and `triage-failed` kinds | no |
| 07 | Automations: `provision-server-state.sh` grows to twelve | no |
| 08 | Validate | no |
| 09 | Deploy and E2E on `womens-fantasy-sports` | **yes** |
| 10 | Correct `AGENTS.md`, `ops/README.md`, `CONTEXT.md` | no |
| 11 | Update the deployment log | no |

Do them in order. 03 needs 01, 02 and 04. 05 needs 03. 06 needs 03's context keys.
08 needs 01-07. 09 needs 08. 10 and 11 need 09.
