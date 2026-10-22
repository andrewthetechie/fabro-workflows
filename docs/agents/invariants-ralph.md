# Invariants: ralph

Read before changing `.fabro/workflows/ralph/`, its prompts, its `workflow.toml`, or its
copies of `backlog`'s and the shared merge phase's nodes (ADR 0029).

Every rule here passes `fabro validate` and fails at runtime, usually silently. The graph,
stylesheet and hook rules in `invariants-graph.md` apply as well; this file holds what is
particular to `ralph`.

## The Task list

| Rule | What happens otherwise |
|---|---|
| `/tmp/fabro/tasks.json` is written ONLY by command nodes: `plan_gate`, `pick` (the blocked-marking write), `record`, `refute_gate`, `review_gate`, `final_check` and `ci_task`. No agent may write it, and the Iteration prompt says so | An agent that adds, reorders or passes its own Task decides what the loop does next, and `record`'s pass/miss accounting is computed from a file the agent graded itself. The `io-guard` hook seals it from the Refuter and both reviewers too (C10). |
| A Task keeps its `id` for the whole run, and every appended Task gets a new one (`r<round>c<N>`, `r<round>d<N>`, `rv<round>{s,p}<N>`, `ci<N>`, `final_fix`) | `record` finds the current Task by id. A duplicate id makes it update the first match, so a Refuter Task could be marked passed by an Iteration that worked a different one. |
| `plan_gate` writes the first EIGHT Tasks to `tasks.json` and the rest to `remainder.json`, in the shape `file_remainder` already files | The Task budget of 8 (O7) is what bounds a run's PR. More in `tasks.json` means a run that implements everything and files no Remainder. |
| `pick` deletes the per-Iteration files at the top (`iteration.json`, `task_block_scope.md`, `task-context.md`, `task-code.md`, `check_ok`, `rung`) | A stale file from the previous Iteration is read as this one's: a `check_ok` of 1 makes `record` pass a Task whose visit never ran a check. |

## The loop

| Rule | What happens otherwise |
|---|---|
| `pick` publishes EVERY key on every branch: `loop`, `rung`, `phase`, `rebase_failed`, `loop_exhausted`. `rung` is 0 whenever the loop is not working | A stale key from an earlier visit satisfies an edge meant for this one. `excerpts -> iterate_tN` reads `rung`, and `rung=0` with `loop=work` reaches `stop_reason`. |
| `excerpts` publishes nothing, so `rung` on its four edges is still the value `pick` printed | A second publisher of `rung` would have to agree with `pick` forever; one that disagreed would pick the wrong model for the Iteration. |
| `pick` writes `/tmp/fabro/rung` as well as the context key | `record` is a separate command node and cannot read another node's context updates; the progress line names the rung. |
| `check` deletes `/tmp/fabro/check_ok` at the top and writes `1` to it on the green path, immediately before its routing object | `record` decides pass from `check_ok`. A file left by the previous Iteration passes a Task whose `check` was red, timed out or failed. |
| `record` always succeeds and prints only its routing object; a bad Iteration is a MISS, never a stop | An Iteration that failed is what the rung ladder is for (O17). A `record` that failed would take its unconditional edge to `stop_reason` and end the run at rung 1. |
| `task_block_gate` reads the block from `iteration.json`'s optional `blocked` object, and never deletes that file | A stage has exactly ONE output contract (`ops/fabro-io/src/manifest.rs`), so `iteration` and `task_blocked` cannot both be a stage's output. The gate reads the receipt to tell this visit's block from an earlier one's, and `record` needs the same file after it. |

## The Refuter

| Rule | What happens otherwise |
|---|---|
| `refute_gate` applies the evidence rule before it computes the verdict: a `not_met` criterion survives only when its `evidence` names a `path:N` that exists or a backticked command, a defect only when its `file` exists, and everything dropped is listed in `refute_dropped.json` | A Refuter that says "I could not find it" without a path becomes a Task nobody can work, and the loop spends Iterations on it. A dropped finding is silently a *pass*, which is why the prompt states the rule too. |
| The round cap counts REFUTER VISITS (`refute_visits`, written by `refute_prep`), not Tasks and not findings | A run that raises five findings in one round would otherwise spend five rounds. O8's budget is three visits. |
| `refute_gate` writes `/tmp/fabro/review/refute_verdict`, which `merge_gate` check 14 reads, and publishes the four keys `summary.py` reads (`refute_verdict`, `refute_not_met`, `refute_unverifiable_only`, `refute_ci_credited`) | The scorecard's Refuter lines and the merge gate read the same four names from two places. A rename makes one of them silently absent. |
| `refute_gate` publishes `loop_exhausted` itself, read from `/tmp/fabro/loop_exhausted` | `pick` was not the last node to run; its context is not what the gate's edges see. |
| `refute_prep` records the visit's tree in `/tmp/fabro/refute_tree` AFTER it computes the delta, and deletes every Refuter contract file at the top | The delta would be empty every round, or a stale verdict would be read as this round's. |
| `refute_gate` writes `fix_result.json` (outcome `needs_human`, `not_fixed` = every Task that did not pass) when the loop has run out and the route is `hold` | A PR that ran out of budget would otherwise be mergeable: an exhausted run must never reach check 7 as `fixed`, and the kill-switch file is the wrong lever (its bucket is a policy hold the Retro leaves out). |

## The pre-PR review

| Rule | What happens otherwise |
|---|---|
| `review_gate` blocks only on severity `error` (R4); `warning` and `info` are reported | A loop that fixed every style warning spends its three rounds on style. |
| `review_gate` writes `/tmp/fabro/review/fix_result.json` in `review_fix`'s shape: `outcome` mergeable only when nothing blocks and neither decision is `needs_human_review`, `risk` from `spec.json`, `fixes_applied` = every `review`-sourced Task that passed, `not_fixed` = the blocking findings left, `own_findings` empty | `merge_gate` checks 7-10 read that file and there is no `review_fix` in this graph to write it. `own_findings` must stay empty because check 10 requires every error there to be in `fixes_applied`, and this gate fixes nothing itself. |
| The reviewers' receipts are checked by `_io.stage` + a non-empty `_io.visit`, not by comparing one visit to `.io/stage.json` | Two stages wrote the two files, so their visits differ. `review_prep` deleting both first is what stops a stale file; the receipt is what stops a hand-written one. |
| `review_gate` reads the Refuter's route from `/tmp/fabro/refute_route` | A command node cannot read another's context updates, and "was the Refuter's route `pass`" decides whether a blocking finding may become a Task at all. |

## The merge stage and the copies

| Rule | What happens otherwise |
|---|---|
| Every `ralph` copy of a `backlog` or `review_merge` node is a row in `R10_COPIES` (script) or `R10_PROMPTS` (prompt) in `ops/check-graph-invariants.py`, and a copy that must differ in a way a replacement cannot express is NOT a row: its `//` comment says why | A fix to a shared merge node silently stops applying to `ralph`, or a `ralph` fix silently diverges. The copies are why the two arms' merge stages can be compared at all. |
| `merge_prep` is `review_merge.rm_setup` under another id, with NO script replacement, and its two heredocs (`render.jq`, `render_comment.sh`) are byte-identical to the shared node's | `deliver` and all four reports run `render_comment.sh`, and `mark_needs_human` also runs `render.jq`. Nothing else in `ralph` writes them: there is no import. The gate tests (`test_ralph.py`) compare the heredocs. |
| `watch_checks`' two deadline stops publish `checks_deadline=true` and a reason, and do NOT call `blocked` | `checks_blocked=true` outranks everything and routes to `report_blocked`. A PR that passed every gate but ran out of time must reach `report_queued` instead (O12), which is the only node that adds `merge-on-green`. |
| `ci_task` appends a Task and publishes `phase=post`; at two post-PR Iterations it writes BOTH reason files and routes `stop` | The bound is O13's budget. `pick`'s edges read `phase`, and `report_blocked` renders `merge_block_reason` while `mark_needs_human` renders `needs_human_reason`. |
| `final_check` writes `/tmp/fabro/base_ref` = `main` | `hygiene` reads `origin/<base>`. The shared phase gets that file from the PR in `rm_setup`, and `ralph` has not opened one when it first runs. |
| `stop_reason` is the only node with an edge into `mark_needs_human` that is not a report, and it derives the reason from `failure_signature` and writes both reason files | R9's rule. A sink that writes one file leaves the other renderer reading a previous stage's text. |

## The end of a run

| Rule | What happens otherwise |
|---|---|
| Every terminal node reaches `exit` only through `learn_prep` (and `learn_post`), and each writes its own id to `/tmp/fabro/ending` | A run with Iterations that never proposes a Learning is a run whose record nobody reads. `learn_prep` decides skip from `iterations`, so the guard is one place. |
| `learn_post` drops an entry whose `evidence` names no `path:N` and no backticked command, never fails the run, and never edits a file | A Learning without evidence is a guess on a public-to-the-team issue. A `learn_post` that failed would take an unconditional edge and turn a successful merge into a failed run. |
| The `ralph learnings` issue is found by its `<!-- fabro:ralph-learnings -->` marker, is labelled `learning`, and is never labelled `agent` | A title match breaks the moment a human retitles it, and `agent` would put the issue in the queue the scheduler reads. |

## The package

| Rule | What happens otherwise |
|---|---|
| `ralph`'s `kimi-k3` fallback order is the reverse of `backlog`'s (`zai:glm-5.3` first, then `orfallback:qwen3.8-27b`), and `"glm-5.3"` has NO `kimi` rung | OpenRouter must only ever be a fallback (O5). And `.coder-t4` requests `glm-5.3`: a Kimi rung there spends the reviewers' weekly quota on coding. |
| `ralph` has no human gate. Every stop that `backlog` sends to `human_rescue` ends at `mark_stuck` (no PR) or at a merge-stage report (PR exists), with the reason | A 4-hour gate holds a coder box idle. R1, and O18 already says what happens when the loop cannot finish. |
| `ralph` imports no shared phase, so every hook matcher is `^node$` | The `(^|[.])node$` form exists for imported ids. A matcher that cannot match is silent: the run just never fires the hook. |
| `plan`'s timeout is the plan trial's number, measured on glm-5.3, and O5 moves `.plan` to kimi-k3; the reviewers' timeouts were measured on glm-5.3-flash and glm-5.3 | A measurement-derived timeout whose subject changed is not a measurement. Task 12's shakedown re-derives all three. |
