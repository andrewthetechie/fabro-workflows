# Ralph: overview and canonical contracts

**Status:** designed 2026-10-21 (grilling session of that date, ADR 0029 proposed). Not
built. The trial cannot start until the preconditions below hold.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. **Before you edit a `.fabro` file, a prompt or a `workflow.toml`, invoke the
`/fabro-workflow` skill** and read `docs/agents/invariants-graph.md`,
`docs/agents/invariants-backlog.md` and `docs/agents/invariants-merge.md`: almost every rule
in them applies to `ralph` too, because `ralph` copies the nodes they were written for.
Before you change anything under `ops/`, read `docs/agents/invariants-host.md` and
`docs/agents/deploying.md`. `CONTEXT.md` defines **Task**, **Task list**, **Iteration**,
**Refute round** and **Learning**, and ADR 0029 records why this exists.

## Hard rule for this series: do not push

A push to `main` deploys (`AGENTS.md`). Commit each task locally on `main`, one commit per
task, Conventional Commit subjects (`feat(ralph): ...`, `feat(scheduler): ...`,
`docs(ralph): ...`). After each task run `make check` at the repository root, and
`uv run pytest` in `ops/scheduler/` when the task touched it. Both must pass. **Stop after
task 11** and report. Do not run `git push`, `make deploy*`, `make compose-up`,
`make check-host`, `ssh`, `docker`, or any `gh` command that writes. Task 12 is the
operator's.

`ralph` is unreachable until the scheduler's `ralph_share` is 50 or someone fires it by
hand, so an unfinished graph on `main` cannot start a run. That is why tasks 01-08 may leave
interim edges (named in each task) that a later task replaces.

## What this is

An eighth package, `.fabro/workflows/ralph/`, works the same Queue items as `backlog` with a
different shape. It is trialled against `backlog` on half the issues.

```
issue [agent]  (even number, ralph_share=50, not fast-path)
 └─ ralph: claim → prep → plan → plan_gate ──────────────────────────────┐ contradiction → return_to_triage
                                                                         │ redundant → close_noop, needs_human → mark_stuck
     ┌──────────────────────────────── pick ◄───────────────────────────┘
     │  work: excerpts → iterate_t{1..4} → task_block_gate → autofix → check → record → pick
     │  done (pre-PR):  final_merge → final_check → hygiene → refute_prep → refute → refute_gate
     │       refute_gate: pass → standards → spec → review_gate ──pass──► open_pr → file_remainder → pr_handoff
     │                    tasks ─────────────► pick        review_gate: tasks ─► pick
     │  exhausted: final_merge → final_check → hygiene → refute (report only) → open_pr (merge blocked)
     │
     │  merge stage (phase=post): merge_gate → watch_checks → merge → report_merged
     │       red CI → ci_task → pick → (iterations) → hygiene → deliver → merge_gate
     │       BEHIND / conflict → remerge_base → resolve_base → final_check → hygiene → deliver
     │       deadline, every gate passed → report_queued (merge-on-green: merge-queue is the fallback)
     └─ every ending → learn_prep → learn → learn_post → exit
```

## Preconditions (the operator checks each before task 12's switch)

| # | Precondition | Why |
|---|---|---|
| P1 | The plan trial (#25) has closed with its option 1: `improve` deleted, `plan` kept, `trial.py`, `/api/scorecard/arms` and `--by-arm` deleted (`docs/plan-trial/README.md`, *Ending the trial*). | The control arm must be one stable `backlog`, and issue parity must be free. |
| P2 | The Task validation result (`docs/task-validation/`, due 2026-10-22) and the Refuter-evidence measurement (`docs/refuter-evidence/`, due 2026-10-23) are written. | Their windows must not overlap a trial that changes which workflow runs half the issues. |
| P3 | `make check-host` passes with the `Ralph` node and edge counts recorded in `AGENTS.md`. | `fabro validate` exists only in the container. |

Tasks 01-11 can be built before P1 and P2 hold. Task 10 is written against the scheduler
after P1 (no `trial.py`); if P1 has not landed when task 10 starts, stop and report.

## Operator decisions (settled 2026-10-21; do not re-open)

| # | Decision |
|---|---|
| O1 | A whole-package trial: `ralph` against `backlog` after P1. It cannot say which part of `ralph` made a difference, and does not try to. |
| O2 | Arm by issue parity: even → `ralph`, odd → `backlog`, when `ralph_share` is 50. `fast-path` issues always run `backlog`. A Remainder issue or Child issue takes the parity of its own number. A requeued issue keeps its arm. |
| O3 | Primary metric: PRs merged with no operator commit, per run. Guardrails: box-hours per merged PR no worse than +20%, and total hosted spend under $100 a month. Secondary: the Refuter's first verdict, held reasons, time to PR. Judged at ≥50 runs per arm, split by repo × coder pool. |
| O4 | `plan` runs once per run, on the hosted frontier chain, never on the coder. Every Iteration runs on the coder. |
| O5 | Every hosted frontier stage (`plan`, `standards`, `spec`) uses `kimi-k3 → zai:glm-5.3 → orfallback:qwen3.8-27b`. OpenRouter is only ever a fallback. `glm-5.3-flash` appears only on the coder escalation rungs, which are `backlog`'s. |
| O6 | An Iteration is a fresh coder session (no `thread_id`) that takes the first unpassed Task, implements it, runs `task_check`, and submits a result. A mechanical gate (Task validation) decides whether it passed. There is no LLM review per Task. |
| O7 | Iteration cap: 3 × the Task list's length, at most 24. The Task budget of 8 holds, and the rest goes to a Remainder issue. |
| O8 | When every Task has passed, the Refuter (gpt-6-luna, fallback `orfallback:qwen3.8-27b`) is told the work is not done. Each finding it can prove becomes a Task. It may return `pass`. A finding with no evidence is dropped. It sees its earlier rounds' findings and how each ended. At most 3 Refute rounds; findings still held after the last are Unmet criteria and block auto-merge. |
| O9 | After the Refuter passes, `ralph`'s own pre-PR standards and spec review runs. Its blocking findings become Tasks, inside the same 3 rounds. `_shared/review-merge/` is not imported and not changed. |
| O10 | The run merges its own PR and holds its lease until the PR is merged or handed off. "Merge before moving on" is about the box's next Queue item. Ordering dependent issues in the scheduler is out of scope. |
| O11 | `ralph` owns its merge stage, built from copies of the shared phase's command nodes. Red CI and a base conflict become work on the coder, never a hosted fixer. A `make check` gate fails when a copy drifts from its original. |
| O12 | Merge deadline 60 minutes, as in the control. When it runs out, or the run hands off a PR that passed every gate and waits only on CI or a rebase, the PR gets `merge-on-green` and the approval marker, so `merge-queue` catches it. |
| O13 | After the PR is open, an Iteration's work passes Task validation and `hygiene` (any tamper counter blocks), with no new Refute round and no re-review. At most 2 Iterations after the PR opens; then the run hands off. |
| O14 | ADR 0028 applies: a red check that fails only on Known flaky tests gets a second rerun, then a hold, and never becomes a Task. |
| O15 | Auto-merge rules are the control's, read from the same switches: `FABRO_AUTO_MERGE`, the `pr-review-<repo>` row's `auto_merge=` token, `do-not-merge`, no tamper counter, the Refuter's last verdict `pass`, standards and spec passed, no Unmet criteria, and the rest of `merge_gate`'s checks. |
| O16 | `plan` writes the Task list and a disposition (`ready`, `redundant`, `invalid`, `contradiction`, `needs_human`). A Contradiction goes to the same `return_to_triage` exit as `backlog`. It reads the Task map as a hint, uses the Repo map and the Code index, and cites line ranges so `excerpts` can copy them. |
| O17 | A Task that stays unpassed for 2 Iterations on one rung moves up `backlog`'s ladder: box → `glm-5.3-flash` → `kimi-for-coding` → `glm-5.3`. Each Iteration has a 60-minute timeout with `allow_partial`. |
| O18 | When the Iteration cap or the top rung is spent with Tasks unpassed: Final validation green → open the PR with the unpassed Tasks as Unmet criteria and auto-merge blocked; red → `agent-stuck` with a report, no PR. |
| O19 | The progress log lives only in the run. No Iteration edits `AGENTS.md`. |
| O20 | The Refuter and both reviewers cannot read the progress log, the Iterations' results or the Task list's notes. The reviewers cannot read the Refuter's findings. |
| O21 | Scheduler: a second entrypoint, the arm from `ralph_share` (0 or 50, default 0, 0 is the kill switch), and `workflow` on the run labels, the lease and `run_history`. The scorecard splits by workflow. |
| O22 | The Retro reads both workflows, with the workflow in the class key. |
| O23 | Rollout: `fabro-fire-ralph.sh`, a four-run shakedown checked by hand (one per repository), then `ralph_share=50`. The trial window starts at the switch. |
| O24 | Ending: `ralph` wins → it becomes the standard lane and `backlog` keeps the fast lane (its own series); `backlog` wins → `ralph` is deleted; unclear → extend. A result file and an ADR decision either way. |
| O25 | Every run with at least one Iteration ends with a Learnings stage on the coder (`reasoning_effort: medium`, 10 minutes, never blocks). 0-5 Learnings, each with a rule, evidence and a proposed home; no evidence, no entry. |
| O26 | Learnings go to one rolling `ralph learnings` issue per target repository, labelled `learning` (never `agent`), found by a hidden marker, created if missing, one comment per run. Nothing in this public repository. |

## Drafted decisions (the operator may veto each one until the task named lands)

These follow from the settled decisions but were not asked as questions.

| # | Decision | Why | Lands in |
|---|---|---|---|
| R1 | `ralph` has no human gate. Every stop that `backlog` sends to `human_rescue` ends at `mark_stuck` (no PR) or at a merge-stage report (PR exists), with the reason. | A 4-hour gate holds a box idle. O18 already says what happens when the loop cannot finish. | 02 |
| R2 | A `scope` block from `task_block_gate` marks the Task `blocked`. A blocked Task ends the loop as exhausted (O18). A `criterion` block goes to `return_to_triage`. | A Task that cannot be done in its files will not pass on a stronger rung either. | 03 |
| R3 | The Refuter sees the whole diff against `main` and, from round 2, the diff since its previous round. | A criterion checklist cannot be judged from a delta. The delta and the round history stop it from re-arguing settled points. | 04 |
| R4 | A review finding blocks (becomes a Task) when its severity is `error`. `warning` and `info` go into the PR report only. | `warning` findings on the standards axis are the commonest kind, and a loop that fixed every one would spend the round budget on style. | 05 |
| R5 | `ralph`'s spec reviewer also returns `risk` (1-5) with `review_fix`'s rubric. `review_gate` writes `fix_result.json` in the shared shape so the copied `merge_gate` keeps its checks 7-10 unchanged. | `merge_gate` check 8 needs a risk, and in the shared phase only `review_fix` writes one. | 05 |
| R6 | A base conflict is resolved by a copy of the shared `resolve_base` stage on the coder box (it is the git exception), then full Final validation and `hygiene`, then `deliver`. It counts against O13's 2. | An ordinary Iteration may not run git. | 07 |
| R7 | A Retro class key for a `ralph` run puts `ralph.` before its node part (`ending:ralph.mark_stuck:no-pr`). `backlog` keys do not change. | The `retro` issues' markers hold `backlog` keys, and a changed key would re-file every known class. | 10 |
| R8 | `/tmp/fabro/tasks.json` keeps `backlog`'s array shape, and `/tmp/fabro/task_index` holds the count of passed Tasks. | The probe reads both files to show "task i of n" (`probe.py`). | 03 |

## Verified facts (2026-10-21, HEAD `82550fc`)

- **The scheduler fires one entrypoint.** `BACKLOG_ENTRYPOINT = "workflows/backlog/workflow.fabro"`
  (`ops/scheduler/src/fabro_scheduler/workflow_version.py:59`). A version is the whole
  `.fabro/` tree plus an entrypoint; `VersionCache` is keyed by the commit sha alone, so a
  second entrypoint needs a cache keyed by `(sha, entrypoint)`.
- **`build_run_intent`** sends `inputs: {issue_number, coder_pool}` and labels
  `{source: scheduler, issue: N, workflow_sha}` (`fabro.py`). `Dispatcher._dispatch` swaps
  `agent` for `agent-in-progress` before it creates the run and rolls the labels back on any
  failure (`dispatch.py`).
- **A Manual fire appears nowhere in `run_history`** (`CONTEXT.md`, *Run history*), so
  shakedown runs fired by `fabro-fire-ralph.sh` are outside the scorecard by construction.
- **`run_history` and `leases`** have no workflow column. `Store` adds missing columns with
  `ALTER TABLE ... ADD COLUMN` at open (`store.py` around line 445).
- **The summary reads node ids.** `summary.py`: `REFUTE_GATE = "review_merge.refute_gate"`,
  `CODER_NODE = "coder"`. The probe reads `/tmp/fabro/tasks.json` and `/tmp/fabro/task_index`
  through `GET /runs/{id}/sandbox/file`.
- **`backlog`'s stylesheet** puts the coder on `{{ inputs.coder_pool | default('coders-a') }}`
  with `reasoning_effort: medium`, and the ladder on `.coder-t2` `glm-5.3-flash`, `.coder-t3`
  `kimi-for-coding`, `.coder-t4` `glm-5.3`. Its fallback rows for `coders-a`..`coders-d` are
  `["zai:glm-5.3-flash", "openai:gpt-6-luna"]`, `kimi-for-coding = ["zai:glm-5.3"]`,
  `"gpt-6-luna" = ["orfallback:qwen3.8-27b"]` (`backlog/workflow.toml`).
- **`backlog`'s `kimi-k3` row is `["orfallback:qwen3.8-27b", "zai:glm-5.3"]`**, non-GLM
  first. O5 orders `ralph`'s the other way. Fallback rows live per `workflow.toml`, so the two
  never meet.
- **Schema names are taken:** `plan` is triage's task map, `improve_result` is `backlog`'s
  dossier. `ralph`'s new contracts are `task_list`, `iteration` and `learnings` (C2, C4, C9).
- **The Refuter's schema already demands evidence:** each criterion has `evidence`, each
  defect `file` and `scenario` (`_io/schemas/refute.schema.json`).
- **`merge_gate`'s checks** (`_shared/review-merge/review-merge.fabro`): 1 kill switch file,
  2 `agent-authored`, 2b no `do-not-merge`, 3 open and not draft, 4 no requested changes,
  5 no unresolved threads, 6 no human commits, 7 `fix_outcome` mergeable, 8 `risk` present
  and ≤ 3, 9 `not_fixed` empty, 10 every own `error` finding accounted for in
  `fixes_applied`, 11 Conventional Commit title, 12 commit-body block with `Resolves #`,
  13 hygiene, 13b tree unchanged since hygiene, 14 Refuter pass. Checks 7-10 read
  `review_fix`'s `fix_result.json`.
- **`watch_checks`** already holds ADR 0028's Known-flaky rule (`fabro-io flakeradar
  classify`), and the merge phase's deadline is written as `$(date +%s) + 3600` to
  `/tmp/fabro/merge_deadline`.
- **`backlog`'s `validate`** publishes `task_validation_ok` and writes
  `/tmp/fabro/feedback/rework.md` on red; a timeout applies no context update
  (`invariants-backlog.md`, *Task validation*).
- **Hooks for a node of the graph that owns the run** are anchored `^node$`; the
  `(^|[.])node$` form is for imported phases. `ralph` imports nothing.

## The graph

`digraph Ralph`. Graph attributes: `default_fidelity="truncate"`, `stall_timeout="60m"`
(`watch_checks` is 50m), `loop_restart_signature_limit=100` (R1 of
`ops/check-graph-invariants.py`: the graph has cycles through command nodes). The goal:
`Ralph: plan the agent-labelled issue, implement it one task per fresh coder session until a refuter cannot break it, review it, and merge the PR`.

| Node | Kind | Class / timeout | Source | Task |
|---|---|---|---|---|
| `claim` | command | | copy of `backlog.claim` | 02 |
| `prep` | command | 20m, `max_retries=1` | copy of `backlog.prep`, its counters replaced by C3's | 02 |
| `plan` | agent | `.plan`, 20m, `max_retries=1` | new prompt `plan.md.j2` | 02 |
| `plan_gate` | command, routing | | new (C2) | 02 |
| `pick` | command, routing | | new (C3) | 03 |
| `excerpts` | command | 5m | copy of `backlog.excerpts` | 03 |
| `iterate_t1`..`iterate_t4` | agent | `.coder`, `.coder-t2`, `.coder-t3`, `.coder-t4`; 60m, `max_retries=1`, `allow_partial=true` | new prompt `iterate.md.j2` | 03 |
| `task_block_gate` | command, routing | 1m | copy of `backlog.task_block_gate` | 03 |
| `autofix` | command, routing | 15m | copy of `backlog.autofix` | 03 |
| `check` | command, routing | 20m | copy of `backlog.validate` (C3) | 03 |
| `record` | command, routing | 1m | new (C4) | 03 |
| `resolve_merge`, `resolve_merge_gate` | agent, command | `.merge-resolve` 30m | copies of `backlog`'s | 03 |
| `final_merge` | command, routing | 5m | copy of `backlog.final_merge` | 06 |
| `final_check` | command, routing | 20m | new (C7) | 06 |
| `hygiene` | command | 5m | copy of `review_merge.hygiene` | 06 |
| `refute_prep`, `refute`, `refute_gate` | command, agent, command | `.refute` 15m | new (C5); prompt from `refute.md.j2` | 04 |
| `review_prep`, `standards`, `spec`, `review_gate` | command, agent, agent, command | `.review-standards` 25m, `.review-spec` 15m | new (C6); `review_prep` writes the reviewers' inputs as the shared `prep_review` does; prompts from the shared ones | 05 |
| `open_pr`, `file_remainder` | command | | copies of `backlog`'s | 06 |
| `pr_handoff` | command | | copy of `backlog.pr_handoff`, writes `phase=post` | 07 |
| `merge_gate`, `watch_checks`, `merge`, `deliver`, `remerge_base`, `resolve_base`, `resolve_base_gate`, `report_merged`, `report_blocked`, `report_queued`, `mark_needs_human` | as in the shared phase | | copies of `review_merge.*` (C8) | 07 |
| `ci_task` | command, routing | 2m | new (C8) | 07 |
| `close_noop`, `return_to_triage`, `mark_stuck` | command | | copies of `backlog`'s | 02 |
| `stop_reason` | command, routing | 1m | new: every failure (unconditional) edge lands here. It writes the reason from `failure_signature` and routes on `/tmp/fabro/phase`: `pre` → `mark_stuck` (`stuck_reason.md`), `post` → `mark_needs_human` (both reason files) (R1) | 02 |
| `learn_prep`, `learn`, `learn_post` | command, agent, command | `.learn` 10m | new (C9) | 08 |

## Contracts

### C1. The package

`.fabro/workflows/ralph/workflow.toml`:

- `[workflow] graph = "workflow.fabro"`. No `[run.inputs]` binding of `issue_number` or
  `coder_pool` (invariants-graph, *Syntax and stylesheets*); `claim` reads
  `{{ inputs.issue_number }}` as `backlog`'s does, so `make check-host` will warn that
  `issue_number` is unbound in `claim`, the same deliberate warning as `Backlog`.
- `[run.run_branch] enabled = true, push = false` (ADR 0011 D1).
- `[run.integrations.github.permissions]`, `[run.agent.mcps.io]`, `FABRO_AGENT_GUIDE`,
  `FABRO_API_TOKEN`, `FABRO_API_URL`, `FABRO_AUTO_MERGE`, `FLAKERADAR_URL`: as in `backlog`.
  `FABRO_IO_MANIFEST` is generated (task 01).
- Hooks: `io-stage`, `git-guard` as in `backlog`; `io-guard` matched by
  `^(refute|standards|spec)$` (C10); Discord hooks `discord-complete` on `^open_pr$`,
  `discord-failed` on `run_failed`, `discord-merged` on `^report_merged$`, `discord-queued`
  on `^report_queued$`, `discord-blocked` on `^report_blocked$`, `discord-triage-question`
  on `^return_to_triage$`, and a `checkpoint_saved` hook for `mark_needs_human` exactly as
  `backlog` wires the merge phase's three reports (R7). No `discord-rescue`: there is no
  human gate (R1).

Model stylesheet (the root `model_stylesheet`):

```
*                 { model: coders-a; }
.plan             { model: kimi-k3; }
.coder            { model: {{ inputs.coder_pool | default('coders-a') }}; reasoning_effort: medium; }
.coder-t2         { model: glm-5.3-flash; }
.coder-t3         { model: kimi-for-coding; }
.coder-t4         { model: glm-5.3; }
.merge-resolve    { model: {{ inputs.coder_pool | default('coders-a') }}; reasoning_effort: medium; }
.review-standards { model: kimi-k3; }
.review-spec      { model: kimi-k3; }
.refute           { model: gpt-6-luna; }
.learn            { model: {{ inputs.coder_pool | default('coders-a') }}; reasoning_effort: medium; }
```

Fallback rows:

```
"coders-a" = ["zai:glm-5.3-flash", "openai:gpt-6-luna"]   (and coders-b, -c, -d the same)
kimi-k3 = ["zai:glm-5.3", "orfallback:qwen3.8-27b"]
kimi-for-coding = ["zai:glm-5.3"]
"glm-5.3" = ["orfallback:qwen3.8-27b"]
"glm-5.3-flash" = ["zai:glm-5.3"]
"gpt-6-luna" = ["orfallback:qwen3.8-27b"]
```

Fallbacks are non-recursive: the `kimi-k3` row is the whole chain for `.plan` and the two
reviewers. `"glm-5.3"` must not fall back to `kimi:kimi-k3` here: `.coder-t4` would then
spend Kimi on the top rung, and Kimi's quota is the reviewers'.

### C2. The plan and the Task list

`plan` submits `/tmp/fabro/task_list.json` (schema `task_list`):

```json
{
  "disposition": "ready | redundant | invalid | contradiction | needs_human",
  "summary": "two or three sentences",
  "contradictions": [{"criterion": "", "non_goal": "", "needed_surface": ""}],
  "tasks": [{
    "id": "t1", "title": "", "body": "what to change and why",
    "acceptance": ["one checkable statement each"],
    "files": ["path"], "covers": ["the issue requirement it serves"],
    "context": "markdown: ## Files and symbols, ## Plan (one step per edit, each with path:N-M), ## Done when"
  }]
}
```

`tasks` is non-empty only for `ready`; `contradictions` non-empty only for `contradiction`.
`context` is capped at 12,000 bytes per task (the plan trial's `## Plan` cap). The schema sets
`additionalProperties: false` everywhere.

`plan_gate` (receipt check as in `decompose_gate`; one retry on `invalid`, counter
`/tmp/fabro/plan_attempts`):

- `ready`: writes `/tmp/fabro/tasks.json` as the first 8 tasks, each with
  `source: "plan"`, `round: 0`, `status: "open"`, `iterations: 0`, `misses: 0`. Tasks 9 and
  later go to `/tmp/fabro/remainder.json` in `backlog`'s remainder shape (`file_remainder`
  files them after `open_pr`). Writes `task_index` 0.
- `contradiction`: writes `return.json` and publishes `triage_questions` exactly as
  `decompose_gate` does (its `QT=` line, word for word).
- `redundant` and `invalid` → `close_noop`; `needs_human` → `mark_stuck` with the summary in
  `stuck_reason.md`.
- Publishes `plan_status` and `triage_questions` on every routing object.

A Task's fields after `plan_gate`: `id`, `title`, `body`, `acceptance`, `files`, `covers`,
`context`, `source` (`plan`, `refute`, `review`, `final_fix`, `ci`), `round`, `status`
(`open`, `passed`, `blocked`), `iterations`, `misses`. Only command nodes write
`tasks.json`; no agent may (C10 seals it from the reviewers and the Refuter; the Iteration
prompt says it is read-only, and `record` rewrites it whole every time).

### C3. `pick`, the loop's head

Files: `/tmp/fabro/iterations` (total Iterations this run), `/tmp/fabro/post_iterations`,
`/tmp/fabro/refute_visits`, `/tmp/fabro/phase` (`pre` until `pr_handoff` writes `post`),
all initialised by `prep`.

`pick`, in order:

1. Deletes the per-Iteration files: `iteration.json`, `task_blocked.json`,
   `task_block_scope.md`, `task-context.md`, `task-code.md`, `check_ok`, and the
   `base-tree` worktree (as `next_task` does).
2. In phase `pre` only, refreshes main: `git fetch origin main && git merge --no-edit
   origin/main`; a conflict aborts the merge and publishes `rebase_failed=true` (→
   `resolve_merge`, `merge_return=task`), exactly like `next_task`. Phase `post` never
   merges main here: the merge stage owns the base (C8).
3. Takes the first Task with `status: open`. None open: `loop=exhausted` if any Task is
   `blocked`, else `loop=done`.
4. The cap. Phase `pre`: `cap = min(24, 3 × length of tasks.json)`; `iterations >= cap` →
   `loop=exhausted`. Phase `post`: `post_iterations >= 2` → `loop=exhausted`.
5. The rung, from the Task's `misses`: 0-1 → 1, 2-3 → 2, 4-5 → 3, 6-7 → 4. At 8 the Task
   becomes `blocked` and `pick` goes back to step 3.
6. Writes `current_task.json` (the Task), and `task-context.md` from the Task's `context`,
   followed by `## Last attempt` with `/tmp/fabro/feedback/rework.md` when `misses > 0`.
   Records `git rev-parse HEAD` in `task_base_sha`.

It publishes, on every branch: `loop` (`work`, `done`, `exhausted`), `rung` (1-4, 0 when not
`work`), `phase`, `rebase_failed`, `loop_exhausted` (`true` once any branch printed
`exhausted` in this run, kept in `/tmp/fabro/loop_exhausted`). The edges:

```
pick -> resolve_merge   [condition="context.rebase_failed=true"]
pick -> excerpts        [condition="context.loop=work && context.rebase_failed=false"]
pick -> final_merge     [condition="context.loop=done && context.phase=pre"]
pick -> final_merge     [condition="context.loop=exhausted && context.phase=pre"]
pick -> hygiene         [condition="context.loop=done && context.phase=post"]
pick -> report_blocked  [condition="context.loop=exhausted && context.phase=post"]
pick -> stop_reason
excerpts -> iterate_t1  [condition="context.rung=1"]   (t2..t4 likewise; excerpts always succeeds)
```

`excerpts` publishes nothing, so `rung` from `pick` is still the latest value when its edges
are chosen. `check` is `backlog.validate` with two declared replacements (C8's drift table):
it deletes `/tmp/fabro/check_ok` first, and writes `1` to it on the green path just before
its routing object. A red, timed-out or failed `check` leaves no `1`.

### C4. An Iteration and `record`

Each `iterate_tN` reads (Stage manifest, task 01): `current_task.json` (required),
`issue.json` (required), `progress.md` (optional, absent text "No Iteration has run yet."),
`task-context.md` (optional), `task-code.md` (optional). It may submit `task_blocked`
(unchanged contract, #17) to block, and must submit `/tmp/fabro/iteration.json`
(schema `iteration`):

```json
{"status": "done | progress", "summary": "what changed, at most 600 characters",
 "learnings": ["at most 3, each one sentence, about the repository, not about this task"]}
```

Edges: `iterate_tN -> task_block_gate` on `outcome=succeeded` and on
`outcome=partially_succeeded`; unconditional → `record` (a dead provider counts as a miss,
never a stop). `task_block_gate -> autofix` on `task_block=none`, `-> return_to_triage` on
`criterion`, unconditional → `record` (a `scope` block, R2: `record` sees
`task_block_scope.md`). `autofix -> check` on both outcomes. `check -> record` on both
outcomes.

`record` (always succeeds, prints only its routing object):

- `passed` exactly when `/tmp/fabro/check_ok` is `1`, `iteration.json` carries this visit's
  receipt, its `status` is `done`, and no `task_block_scope.md` exists.
- Updates the current Task in `tasks.json`: `iterations + 1`; on `passed`, `status: passed`;
  otherwise `misses + 1`, and `status: blocked` when `task_block_scope.md` exists (its text
  goes to `feedback/rework.md`).
- Adds 1 to `iterations` (phase `pre`) or `post_iterations` (phase `post`).
- Appends to `/tmp/fabro/progress.md`: `## Iteration N: <task id> <passed|not passed>
  (rung R)`, the summary, and the learnings as a list. A missing or invalid
  `iteration.json` appends `(no result submitted)`.
- Writes `task_index` as the number of `passed` Tasks (R8).
- Publishes `iteration_passed` (`true`/`false`). `record -> pick` unconditionally.

### C5. Refute rounds

`refute_prep` adds 1 to `refute_visits`, then writes the Refuter's inputs under
`/tmp/fabro/review/`: the issue (`refute_issues.json`, cut of `## Post-deploy checks` as the
shared `refute_prep` does), the whole diff against `origin/main` (`refute_diff.patch`), the
counters (`hygiene.json`), the validation evidence (`refute_validation.md`, from
`final_check`'s `validated_tree`, the #40 rule), and from visit 2:
`refute_delta.patch` (`git diff <tree of the previous visit> HEAD`) and
`refute_history.md`, one line per earlier finding: the finding, the Task it became, and
that Task's `status`. It never copies an Iteration's text.

`refute` uses the shared `refute.md.j2` text with one added section, *Earlier rounds*: a
finding listed in `refute_history.md` whose Task `passed` may be raised again only with
evidence that is new in `refute_delta.patch`. It submits `refute.json` (schema `refute`,
unchanged).

`refute_gate`:

1. Receipt check; a missing or invalid result is `invalid`, which blocks the merge and
   routes `hold` (never a retry loop: the shared gate's rule).
2. **Evidence rule.** A `not_met` criterion survives when its `evidence` names a
   `path:N` whose path exists in the checkout, or a backticked command. A defect survives
   when its `file` exists in the checkout. Everything else is dropped and listed in
   `/tmp/fabro/refute_dropped.json`. `unverifiable` criteria are kept but never become
   Tasks.
3. The verdict over what survives, computed as in the shared gate: `pass` when every
   criterion is `met` and no defect is left. It writes `refute_verdict` (the file
   `merge_gate` check 14 reads) and publishes `refute_verdict`, `refute_not_met`,
   `refute_unverifiable_only` and `refute_ci_credited` (the scorecard's keys).
4. The route, published as `refute_route`:
   - `pass` → `standards`.
   - `tasks` when the verdict is `fail`, at least one `not_met` criterion or defect
     survived, `refute_visits < 3` and `loop_exhausted` is false. Each survivor becomes
     a Task (`source: refute`, `round: <visit>`, title from the criterion or
     `Fix: <file>`, body with the evidence or scenario, `acceptance` = the criterion or
     "the scenario no longer happens"), appended to `tasks.json`. → `pick`.
   - `hold` otherwise (round cap spent, only `unverifiable` left, exhausted, or invalid).
     → `standards` when `loop_exhausted` is false, else `open_pr`. The verdict stays
     `fail`, so `merge_gate` check 14 blocks the merge and the report lists the Unmet
     criteria from `refute.json`.

### C6. The pre-PR review

`standards` and `spec` run after `refute_gate` routes `pass` or `hold` (never `tasks`). Their
prompts are the shared `standards.md.j2` and `spec.md.j2` texts, copied into `ralph/prompts/`
and changed only where they name `review_fix` or the merge phase. They submit `standards.json`
(schema `standards`) and `spec.json` (schema `ralph_spec`: `spec` plus a required `risk`,
integer 1-5, with `review_fix.md.j2`'s risk rubric, R5).

`review_gate` (receipt checks on both):

- A finding **blocks** when its severity is `error` (R4).
- `tasks` when any finding blocks, `refute_visits < 3` and `refute_route` was `pass`: each
  blocking finding becomes a Task (`source: review`, `round: <refute_visits>`, title from its
  id and message, body with its files, line and suggestion), → `pick`. The next done loop goes
  through `final_merge`, `final_check`, `hygiene` and a new Refute round, then this review
  again.
- `pass` otherwise → `open_pr`. It writes `/tmp/fabro/fix_result.json` in the shape
  `review_fix` writes (task 05 reads `review_fix.md.j2`'s contract): `fix_outcome` mergeable
  only when no finding blocks and either decision is `approve` or `findings`; `risk` from
  `spec.json`; `not_fixed` the blocking findings left; `fixes_applied` the ids of every
  `review`-sourced Task that `passed`. A `needs_human_review` decision on either axis makes
  `fix_outcome` not mergeable and the reason goes to `merge_block_reason`.

### C7. Final validation, the PR, and running out

`final_merge` (copy) merges `origin/main` before every Refute round and before the PR. A
conflict goes to `resolve_merge` with `merge_return=pr`, which comes back to `final_merge`.

`final_check` first makes `open_pr_prep`'s two refusals (`ralph` has no `open_pr_prep`): a
branch tree equal to `origin/main`, and a branch whose non-merge commits touch
`.github/workflows/` (the `git rev-list --no-merges` walk, word for word). Either one →
`mark_stuck` with the reason, in both phases. Then it runs `./.fabro/setup.sh &&
./.fabro/ci.sh` (the full suite), records `<tree> final` in `/tmp/fabro/validated_tree` and
the tree in `final_validated_tree` on green, and publishes `final_ok`, `final_route` and
`phase` (read from `/tmp/fabro/phase`):

- green → `hygiene`.
- red, phase `pre`, `final_fix_attempts` 0 and the Iteration cap not spent: appends one
  Task `source: final_fix` with the failing test names (as `open_pr_prep` writes
  `final_failures.txt`), writes `final_fix_attempts` 1 (never reset). → `pick`.
- red otherwise, phase `pre` → `mark_stuck` with the failures in `stuck_reason.md`
  (O18 red).
- red, phase `post` → `mark_needs_human` with both reason files (C8).

`hygiene` (copy) publishes nothing, so its edges read the `phase` that `pick` or
`final_check` published last: phase `pre` → `refute_prep`; phase `post` → `deliver`.

`open_pr` and `file_remainder` are copies. When `loop_exhausted` is true (O18 green):
`refute_gate`'s `hold` route writes `fix_result.json` with `fix_outcome` not mergeable and
`not_fixed` = the title of every Task not `passed`, so `merge_gate` checks 7 and 9 block and
the held reason is `unfixed findings`. Never the kill-switch file: its bucket,
`auto-merge off`, is a policy hold the Retro leaves out, and a run that ran out would vanish
from it. The PR body lists the same Tasks under `### Unmet criteria`, beside the Refuter's.

### C8. The merge stage

**Copies.** These `ralph` nodes are copies of the named originals:

| `ralph` node | Original |
|---|---|
| `claim`, `excerpts`, `task_block_gate`, `autofix`, `resolve_merge`, `resolve_merge_gate`, `final_merge`, `open_pr`, `file_remainder`, `pr_handoff`, `close_noop`, `return_to_triage`, `mark_stuck` | `backlog.<same id>` |
| `check` | `backlog.validate` |
| `hygiene`, `deliver`, `merge_gate`, `watch_checks`, `merge`, `remerge_base`, `resolve_base`, `resolve_base_gate`, `report_merged`, `report_blocked`, `report_queued`, `mark_needs_human` | `_shared/review-merge/review-merge.fabro` `<same id>` |

**The drift gate (R10).** `ops/check-graph-invariants.py` gains `R10_COPIES`, a table of
`(ralph node, original file, original node, replacements)`, where `replacements` is a list
of `(old, new)` string pairs. For each row, R10 fails unless the original's `script` with
every replacement applied, in order, equals the copy's, and each `old` occurs in the
original exactly once. For an agent node (`resolve_merge`, `resolve_base`) the compared text
is the prompt file each `prompt="@..."` names. Attributes other than `script` and `prompt`
are not compared. A copy that must differ in a way a replacement cannot express
is not a copy: take it out of the table and say why in a `//` comment above the node.

**Routing.** `pr_handoff` (copy, plus a replacement that writes `post` to
`/tmp/fabro/phase` and the merge mode `watch`) → `merge_gate`. From there the copied nodes
keep their routing, with these targets changed:

- `watch_checks` red and not Known flaky → `ci_task` (where the shared phase goes to
  `ci_fix_t1`).
- `ci_task` reads `post_iterations`. At 2 or more it writes both reason files
  (`merge_block_reason`, `needs_human_reason`: "CI is red after 2 post-PR Iterations") and
  publishes `ci_route=stop` → `report_blocked`. Otherwise it appends one Task
  (`source: ci`) whose body is the failing checks and the log excerpt `watch_checks` saved,
  and publishes `ci_route=task` and `phase=post` → `pick`.
- `pick`'s `exhausted` route in phase `post` writes both reason files the same way before it
  routes to `report_blocked`.
- After the Iterations, `pick` (phase `post`, `loop=done`) → `hygiene` → `deliver` →
  `merge_gate`. The `hygiene` re-run is what makes check 13 and 13b judge the new tree.
- `remerge_base` conflict → `resolve_base` (once per run, as in the shared phase) →
  `resolve_base_gate` → `final_check` (phase `post`) → `hygiene` → `deliver`. Each
  `resolve_base` visit adds 1 to `post_iterations` (R6).
- `watch_checks` past the deadline, or pending at hand-off, when `merge_gate` passed on the
  current head → `report_queued` (approval marker, then `merge-on-green`, O12). Every other
  hand-off → `report_blocked` or `mark_needs_human`, as in the shared phase.
- `report_merged`, `report_blocked`, `report_queued`, `mark_needs_human` → `learn_prep`.

`merge_gate` writes the 60-minute deadline as in the shared phase. Every invariant in
`invariants-merge.md` holds for the copies: two reason files, `--delete-branch=false`,
`--match-head-commit`, the commit-body markers, the reporting rules.

### C9. Learnings

`learn_prep` reads `iterations`. Zero → `exit`. Otherwise it writes the inputs: the progress
log, `refute_history.md` (or a fresh one from `tasks.json`), a table of Tasks with
`iterations`, `misses`, the highest rung reached and `source`, and the run's ending (the node
that sent it here). → `learn`.

`learn` (`.learn`, 10m, `max_retries=0`) submits `/tmp/fabro/learnings.json` (schema
`learnings`):

```json
{"learnings": [{"rule": "one sentence", "evidence": "task id, iteration, refute round, and a path:N or the failing command",
                "home": "agents_md | test_toml | ci_sh | ralph_prompt"}]}
```

At most 5. `learn -> learn_post` on every outcome.

`learn_post` (always succeeds):

- Drops an entry whose `evidence` names no `path:N` and no backticked command.
- No surviving entry → `exit` without a write.
- Finds the open issue labelled `learning` whose body contains
  `<!-- fabro:ralph-learnings -->` (`gh issue list --label learning --state open --json
  number,body`). None: creates the label if missing and the issue, titled
  `ralph learnings`, body: the marker and one paragraph on how to accept a Learning.
- Comments once: the run id, the PR or issue number, and the entries as a list.
- Any `gh` failure prints one stderr line and → `exit`. Never fails the run.

The issue is never labelled `agent`, and the scheduler never reads `learning`.

### C10. Stage manifest and Sealed paths

`_io/manifest.json` gains a `ralph` workflow with stages `plan`, `iterate_t1`..`iterate_t4`
(identical inputs and outputs), `refute`, `standards`, `spec`, `resolve_merge`,
`resolve_base`, `learn`. `resolve_merge` and `resolve_base` carry `"git": "write"`.

Sealed for `refute`, `standards` and `spec`: `/tmp/fabro/progress.md`,
`/tmp/fabro/iteration.json`, `/tmp/fabro/tasks.json`, `/tmp/fabro/current_task.json`,
`/tmp/fabro/task-context.md`, `/tmp/fabro/task-code.md`, `/tmp/fabro/task_list.json`,
`/tmp/fabro/feedback/`. Also sealed for `standards` and `spec`: `/tmp/fabro/refute.json`,
`/tmp/fabro/refute_verdict`, `/tmp/fabro/review/refute_history.md`,
`/tmp/fabro/refute_dropped.json`. `io-guard` is wired with matcher `^(refute|standards|spec)$`.

### C11. The scheduler

- `SCHEDULER_RALPH_SHARE`, read by `load_config` into `SchedulerConfig.ralph_share`. Allowed
  values `0` and `50`; default `0`; anything else is a `ConfigError` naming the key.
- `choose_workflow(issue, share) -> "backlog" | "ralph"`, pure: `ralph` exactly when
  `share == 50`, the issue number is even, and `fast-path` is not among its labels.
- `RALPH_ENTRYPOINT = "workflows/ralph/workflow.fabro"`. `FabroClient.dispatch` takes the
  workflow and registers the version for its entrypoint; the version cache is keyed by
  `(sha, entrypoint)`.
- The run labels gain `workflow: backlog|ralph`. `leases` and `run_history` gain a
  `workflow TEXT NOT NULL DEFAULT 'backlog'` column, copied from the lease on release.
- `/` and `/history` show the workflow per row.
- `ops/fabro-fire-ralph.sh`: `fabro-fire-backlog.sh` with `ENTRYPOINT` and the usage text
  changed, run label `source: manual`, `workflow: ralph`.

### C12. Scorecard, Retro and probe

- `summary.py` reads node ids per workflow: for `ralph` the refute gate is `refute_gate`, the
  coder node for first-edit is `iterate_t1`, and the Task validation node is `check`.
- `scorecard.compute` is unchanged per window. A new `scorecard.by_workflow(rows)` splits a
  window's rows by `run_history.workflow` and computes a card for each, plus a strata table:
  per repo × coder pool, runs, merged with no operator commit, box-hours per merged PR.
  `GET /api/scorecard/workflows` and `ops/fabro-scorecard.sh --by-workflow` print it.
- The `ralph` line adds: Iterations per run (p50/p90), Refute visits per run, Tasks by
  `source`, runs that ran out (`loop_exhausted`), post-PR Iterations, and `report_queued`
  hand-offs.
- `retro.classify` reads both workflows. A `ralph` run's node part is `ralph.<node>` (R7).
- The probe needs no change (R8).

## Tasks

| # | Task | Touches |
|---|---|---|
| 01 | Stage manifest and schemas | `_io/manifest.json`, `_io/schemas/{task_list,iteration,ralph_spec,learnings}.schema.json` |
| 02 | Package skeleton: claim, prep, plan, the exits | `.fabro/workflows/ralph/` (new) |
| 03 | The Iteration loop | `ralph/workflow.fabro`, `prompts/iterate.md.j2` |
| 04 | Refute rounds | `ralph/workflow.fabro`, `prompts/refute.md.j2` |
| 05 | The pre-PR review | `ralph/workflow.fabro`, `prompts/{standards,spec}.md.j2` |
| 06 | Final validation and the PR | `ralph/workflow.fabro` |
| 07 | The merge stage and the drift gate | `ralph/workflow.fabro`, `ops/check-graph-invariants.py` |
| 08 | Learnings | `ralph/workflow.fabro`, `prompts/learn.md.j2` |
| 09 | Scheduler routing | `ops/scheduler/`, `ops/fabro-fire-ralph.sh` |
| 10 | Scorecard, Retro and summary | `ops/scheduler/`, `ops/fabro-scorecard.sh` |
| 11 | Docs | `AGENTS.md`, `docs/README.md`, `docs/agents/invariants-ralph.md`, `ops/README.md`, `docs/USER-GUIDE.md` |
| 12 | Operator: push, shakedown, switch, measure, end | the host |

Every task that changes a `.fabro` file adds its gate fixtures to `ops/test-task-gates.sh`
under a `ralph — <node>` heading, and its edge-exclusivity checks in the style of the fast
lane's (`docs/fast-path/03-backlog-fast-lane.md`, test 8).

## Risks to watch

- **Kimi's quota is shared.** `ralph` spends `kimi-k3` on three stages per run, so the
  weekly quota runs out sooner, and `backlog`'s `spec` and `extra_decompose` fall back to
  `qwen3.8-27b` earlier in the week. That moves the control. The scorecard's failover rows,
  split by workflow, show how much.
- **The Refuter never says pass.** O8's evidence rule and round cap bound it. Watch
  `refute_dropped.json` sizes and the share of runs that reach the cap.
- **Loops that do not converge.** A review finding fixed in round 1 and re-raised in round 2
  costs a round. `refute_history.md` covers the Refuter; nothing covers the reviewers. Watch
  review-sourced Tasks per run.
- **Box idle time.** The lease holds the box through `plan`, the Refute rounds, the reviews
  and CI. That is the same as `backlog`, but `ralph` has more hosted visits per run. Box-hours
  per merged PR (O3's guardrail) is the number that shows it.
- **Copy drift.** R10 catches it. The cost is that a fix to a shared merge node now needs a
  second commit or a recorded replacement.
