# Unmet criteria: overview and canonical contracts

**Status:** built 2026-10-13 (tasks 01-05 and the task 06 docs). Issue #17. The task 06 live triage trial is pending.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct. **Before you edit a
`.fabro` file, a prompt or a `workflow.toml`, invoke the `/fabro-workflow` skill** and read
`docs/agents/invariants-graph.md`, `docs/agents/invariants-backlog.md` and
`docs/agents/invariants-merge.md`. Read ADR 0016 before you add a contract.

The vocabulary is in `CONTEXT.md`: **Contradiction**, **Unmet criterion**, **Substitution**.
Do not call any of them a "blocker": `blocked` already means a refused merge
(`review_merge_outcome=blocked`) and a held dependency (`Blocked by`).

## What happened

lawncare-saas#2723 asked for a verification screen with a "Resend email" action, and its own
non-goals said "No backend changes". Resending needs a secret-bearing Auth0 call, so it needs a
backend. The two cannot both hold. Every stage let it through:

- **Triage** marked it `ready` with no questions. Nothing compares criteria with non-goals.
- **The coder** shipped a button that only re-checks verification. `coder.md.j2` told it to:
  "implement the closest form that meets its intent and name the difference in your completion
  note".
- **The extra review** turned the gap into a follow-up task, "make the continue action honest",
  which relabelled the button instead of reporting the criterion unmet. The per-task reviewer
  and both spec reviewers approved.
- **The Refuter** marked the criterion `not_met` with a defect, and `merge_gate` refused the
  auto-merge (PR #2787). The PR was merged by hand on 2026-10-11, and #2790/#2791 are the
  follow-up pair.

The coder's `<blocked>` marker is read by nothing. The coder has no output contract, and no
gate parses its reply, so a blocked task reaches `validate` and `review` with an empty diff.

## Decisions (settled with the operator 2026-10-13; do not re-open)

| # | Decision |
|---|---|
| U1 | Scope is this repository's workflows. The `~/.claude/skills` issue-reviewer and issue-improver are out of scope. |
| U2 | **Triage owns the check.** The `triage` stage of `_shared/triage/` names the surface each acceptance criterion needs (a backend, an external API, a secret, a migration) and checks it against the non-goals. A conflict is a Contradiction: `needs_info` with one question quoting both sides, never a `decision`. `issue-triage` and `arch-review` both get it through the import. |
| U3 | **The gate enforces it.** `triage.json` gains a required `contradictions` array (C1). `triage_gate` rejects `ready` while it is non-empty. |
| U4 | The triage-phase `improve` never resolves a Contradiction by rewriting the criterion or the non-goal. It leaves both for `triage`. |
| U5 | **No Substitution.** The "closest form" permission leaves `coder.md.j2`. A criterion that cannot be met as written ends in a block. |
| U6 | The coder and the four rework tiers block through a contract, used only to block (C2). A stage that does not block submits nothing and runs exactly as before. The `<blocked>` text marker goes. |
| U7 | `kind: criterion` returns the issue to triage (C4): a `fabro:triage-questions` comment, `agent`/`agent-in-progress` swapped for `needs-info`, no PR, the branch discarded (tasks already reviewed included). `kind: scope` goes to the existing `rescue_brief -> human_rescue`. |
| U8 | `decompose` gains the status `contradiction` (C3), routed to the same hand-back. Its other `needs_human_review` causes keep `mark_stuck`. |
| U9 | The hand-back publishes `triage_questions` before it starts, so the existing `triage-question` Discord message fires through a new hook. No `discord-notify.sh` change and no `make deploy-notify`. |
| U10 | The hand-back publishes `return_reason` (ADR 0020 rule: one line, at most 300 bytes, built with `jq -nc --arg`), and ends at `exit` succeeded. The scheduler is unchanged: run history shows "succeeded, no PR". |
| U11 | Both spec reviewers report a criterion not delivered as written as a finding that quotes it. "Accepted deviation" and "do not re-litigate" never cover an acceptance criterion. The Refuter is unchanged. |
| U12 | `extra_decompose`, `review_fix` and `refute_fix` implement an unmet criterion or leave it unaddressed. They never relabel or reword the substitute. Prompt rules only. |
| U13 | The review-merge report gains an **Unmet criteria** section under the axis table (C5), rendered from the Refuter's `criteria`. Always present. It reads "None" when every criterion is `met`. |
| U14 | The coder's `<already_satisfied>` marker is also read by nothing. Out of scope; it gets its own issue. |
| U15 | No new ADR. Rules go into `invariants-backlog.md`, `invariants-graph.md` and `invariants-merge.md`. |

## Facts the tasks rely on (verified 2026-10-13 at `5a11441`)

- `fabro-io submit` refuses only when the stage has **no** `output` in the manifest
  (`ops/fabro-io/src/serve.rs:181`, `submit.rs:38`). Declaring an output does not make the
  stage write one: the gate decides what a missing file means. So C2 needs no `fabro-io`
  change and no `min_binary` bump.
- `.io/stage.json` changes only on agent stages, so a command node right after `coder` reads
  the coder's visit there.
- `issue-triage`'s `acquire` (`workflow.fabro:40-43`) and `arch-review`'s queue
  (`workflow.fabro:141-149`) re-triage a `needs-info` issue only when its newest comment
  carries no `fabro:triage-` marker. A comment that starts with
  `<!-- fabro:triage-questions -->` therefore waits for a human reply. A second block costs at
  most one run per human answer.
- `discord-notify.sh triage-question` reads the run context key `triage_questions`
  (`backlog/scripts/discord-notify.sh:113`). A `stage_start` hook fires before the node runs,
  so the node *before* the hand-back must publish the key.
- The scheduler requeues only infra-shaped failures and never touches the labels of a
  succeeded run (`ops/scheduler/src/fabro_scheduler/reconcile.py:1-60`). Its receipt scan
  re-queues an orphaned `agent-in-progress`, so the hand-back must remove that label.
- `backlog` pushes nothing before `open_pr` (`[run.run_branch] push = false`), so "discard the
  branch" means: end the run before `open_pr`.

## Contracts

### C1. `triage.json` gains `contradictions`

`_io/schemas/triage.schema.json`, required, an array (may be empty):

```json
"contradictions": [
  { "criterion": "the UI offers a Resend email action",
    "non_goal": "No backend changes",
    "needed_surface": "a backend endpoint that calls the Auth0 resend API with a secret" }
]
```

All three strings are `minLength: 1`. `triage_gate`: a `ready` verdict with a non-empty
`contradictions` is invalid (the normal two-attempt rule applies). `needs_info` and
`not_actionable` may carry entries. Each entry should also be a question, but the gate does
not match questions to entries.

### C2. The block contract: `/tmp/fabro/task_blocked.json`

New schema `_io/schemas/task_blocked.schema.json`, the `output` of the manifest stages
`coder`, `rework_t1` to `rework_t4`:

```json
{ "kind": "criterion" | "scope",
  "criterion": "the acceptance criterion, quoted",   // non-empty for kind=criterion
  "non_goal": "the non-goal or constraint it conflicts with, quoted, or empty",
  "reason": "one or two sentences" }
```

All four keys required. The gate requires `criterion` non-empty for `kind=criterion`.

New command node **`task_block_gate`** between those stages and `autofix`:

- No file, or a file whose `_io.visit` is not the current `.io/stage.json` visit:
  `task_block=none`, delete any stale file, route to `autofix`.
- `kind=criterion`: write `/tmp/fabro/return.json` (C4), publish `task_block=criterion`
  and `triage_questions`, route to `return_to_triage`.
- `kind=scope`: write `/tmp/fabro/task_block_scope.md`, publish `task_block=scope`, take the
  unconditional edge to `rescue_brief`, which names the reason.
- An invalid file (bad `kind`, empty `reason`, `criterion` without a criterion): treated as
  `scope`, so a human sees it. Never as `none`.

`next_task` deletes `task_blocked.json` at the start of every task, and the gate deletes it
after reading it.

### C3. `decomposition.json` gains `contradiction`

`status` enum adds `contradiction`. A new optional `contradictions` array has the C1 entry
shape. `decompose_gate`: `contradiction` requires `issues: []` and a non-empty
`contradictions`. Otherwise it is invalid. On `contradiction` it writes `/tmp/fabro/return.json`
(each item's `why` is its `needed_surface`),
publishes `decomp_status=contradiction` and `triage_questions`, and routes to
`return_to_triage`.

### C4. The hand-back: `return.json` and `return_to_triage`

`/tmp/fabro/return.json`, written only by `task_block_gate` and `decompose_gate`:

```json
{ "source": "coder" | "decompose",
  "summary": "the decomposer's summary, or the coder's reason",
  "items": [ { "criterion": "...", "non_goal": "...", "why": "..." } ] }
```

`return_to_triage`, a command node with `output_schema="routing"`:

1. Writes `stuck_reason.md` first, so the unconditional fallback to `mark_stuck` carries the
   reason.
2. Creates `needs-info` (`gh label create ... || true`).
3. Comments, with the first line exactly `<!-- fabro:triage-questions -->`: the criteria, the
   non-goals, the reason, the question "change the criterion or the non-goal?", and the line
   "Answer by replying in a new comment. Editing this comment is not detected."
4. `gh issue edit N --remove-label agent --remove-label agent-in-progress --add-label needs-info`.
5. Prints one routing object: `{context_updates:{return_reason: <one line, at most 300 bytes>}}`.

Edges: `return_to_triage -> exit [outcome=succeeded]`, `return_to_triage -> mark_stuck`.
Hook in `backlog/workflow.toml`: `discord-triage-question`, `stage_start`, matcher
`^return_to_triage$`, script `/storage/scripts/discord-notify.sh triage-question`.

### C5. The Unmet criteria report section

In `render.jq` (`_shared/review-merge/review-merge.fabro`), directly after the axis table and
before `### Findings`:

```
### Unmet criteria

- **not_met** - <criterion> - <evidence>
- **unverifiable** - <criterion> - <evidence>
```

`_None: the Refuter found every criterion met._` when the list is empty, and
`_The Refuter did not return a result, so no criterion was checked._` when `criteria` is not an
array. The `### Refuter` section stays as it is.

## Tasks

| # | File | Commit subject |
|---|---|---|
| 01 | `01-triage-contradictions.md` | `feat(triage): a contradiction between a criterion and a non-goal is a question (#17)` |
| 02 | `02-return-to-triage.md` | `feat(backlog): return a contradictory issue to triage instead of stopping stuck (#17)` |
| 03 | `03-coder-block-contract.md` | `feat(backlog): the coder blocks through a contract, never by substitution (#17)` |
| 04 | `04-review-prompts.md` | `feat(review): an unmet criterion is always a finding, never relabelled (#17)` |
| 05 | `05-unmet-criteria-report.md` | `feat(review-merge): the report lists unmet criteria (#17)` |
| 06 | `06-docs-validate-deploy.md` | `docs: unmet-criteria invariants and baselines (#17)` |

Run `make check` after every task that changes a command node's shell, and `make check-fast`
after the rest.
