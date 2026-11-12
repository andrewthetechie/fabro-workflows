# Invariants: ralph

Read before changing `.fabro/workflows/ralph/`, its prompts, its `workflow.toml`, or its
copies of `backlog`'s and the shared merge phase's nodes (ADR 0029).

Every rule here passes `fabro validate` and fails at runtime, usually silently. The graph,
stylesheet and hook rules in `invariants-graph.md` apply as well; this file holds what is
particular to `ralph`.

## The Task list

| Rule | What happens otherwise |
|---|---|
| `/tmp/fabro/tasks.json` is written ONLY by command nodes: `plan_gate`, `pick` (the blocked-marking write), `record`, `refute_gate`, `review_gate`, `local_review_gate`, `final_check` and `ci_task`. No agent may write it, and the Iteration prompt says so | An agent that adds, reorders or passes its own Task decides what the loop does next, and `record`'s pass/miss accounting is computed from a file the agent graded itself. The `io-guard` hook seals it from the Refuter and both reviewers too (C10). |
| A Task keeps its `id` for the whole run, and every appended Task gets a new one (`r<round>c<N>`, `r<round>d<N>`, `rv<round>{s,p}<N>`, `rvx{s,p}<N>` for the one extra review pass, `lr{s,p}<N>` for the local review, `ci<N>`, `final_fix`) | `record` finds the current Task by id. A duplicate id makes it update the first match, so a Refuter Task could be marked passed by an Iteration that worked a different one. |
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
| When the loop has run out, `refute_gate` routes `hold` whatever the verdict (`pass` and `invalid` included) and writes `fix_result.json` (outcome `needs_human`, `not_fixed` = every Task that did not pass); `review_gate` also makes an exhausted run `needs_human` | A PR that ran out of budget would otherwise be mergeable: an exhausted run must never reach check 7 as `fixed`, and the kill-switch file is the wrong lever (its bucket is a policy hold the Retro leaves out). |

## The pre-PR review

| Rule | What happens otherwise |
|---|---|
| `review_gate` blocks only on severity `error` (R4); `warning` and `info` are reported | A loop that fixed every style warning spends its three rounds on style. |
| `review_gate` writes `/tmp/fabro/review/fix_result.json` in `review_fix`'s shape: `outcome` mergeable only when nothing blocks and neither decision is `needs_human_review`, `risk` from `spec.json`, `fixes_applied` = every `review`-sourced Task that passed, `not_fixed` = EVERY blocking finding of this round, never filtered by id, `own_findings` empty | `merge_gate` checks 7-10 read that file and there is no `review_fix` in this graph to write it. The reviewers restart their ids every round (`STD-001`), so a filter against earlier rounds' fixes dropped a new error that reused an old id and made the PR mergeable. `own_findings` must stay empty because check 10 requires every error there to be in `fixes_applied`, and this gate fixes nothing itself. |
| The reviewers' receipts are checked by `_io.stage` + a non-empty `_io.visit`, not by comparing one visit to `.io/stage.json` | Two stages wrote the two files, so their visits differ. `review_prep` deleting both first is what stops a stale file; the receipt is what stops a hand-written one. |
| `review_gate` reads the Refuter's route from `/tmp/fabro/refute_route` | A command node cannot read another's context updates, and the route decides whether a blocking finding may become a Task at all: `tasks` and `pass` may, `hold` may not. |
| The reviews run in EVERY Refute round, after the Refuter: `refute_gate -> review_prep` on `tasks` as well as `pass` and a non-exhausted `hold`, and only `review_gate` returns to `pick`. On `tasks`, `review_gate` routes `tasks` even when the reviews found nothing | `refute_gate -> pick` would skip the reviews for that round, and a review error would then cost a whole extra round (a Final validation, a Refute visit and both reviews). A `review_gate` that routed `pass` on `tasks` would open the PR with the Refuter's Tasks unworked. They run after the Refuter, never beside it: two agent stages at once would share `.io/stage.json` (invariants-graph). |
| The one extra review pass is a state file, `/tmp/fabro/review_extra`: `review_gate` writes `pending` (and `review_rerun`, the axes that blocked) only on a `pass` route with a blocking finding, `refute_visits >= 3` and no earlier pass; the next `review_gate` visit routes `pass` whatever it finds and writes `done`. `refute_prep` publishes `refute_skip` on every visit and skips (no visit counted, no Refuter file touched) only while it is `pending`; `review_prep` publishes `review_run_standards` and `review_run_spec` on every visit, and deletes only the result it re-runs. `prep` deletes both files | A pass that could repeat is an unbounded loop. A skip that counted a visit or deleted `refute_verdict` would leave `merge_gate` check 14 with no Refuter verdict. A stale key from an earlier visit routes the reviewers wrongly, and a result kept from the wrong reviewer is read as this round's. |
| `criterion_checks` runs only the commands in `/tmp/fabro/criterion_checks.json`, which only `plan_gate` writes, after its allowlist (no shell metacharacters, a known runner, no deploy, publish, push or install, at most four); it always exits 0 and has one unconditional edge to `hygiene`, runs nothing during the extra pass, on a dirty checkout or without `timeout`, and reverts any check that changes the checkout or HEAD | The plan is an LLM: a command it names runs in the sandbox with the run's token, so the list is the boundary. A failure of this node must not end a run that would have shipped without it, and a check's edits would be committed into the PR by the next checkpoint. |
| `refute_prep` appends `review/criterion_checks.md` to `refute_validation.md` only when `criterion_checks_tree` is this HEAD's tree and the checkout is clean | Output from another tree is not evidence for this one, just as an old validation is not. |

## The merge stage and the copies

| Rule | What happens otherwise |
|---|---|
| Every `ralph` copy of a `backlog` or `review_merge` node is a row in `R10_COPIES` (script) or `R10_PROMPTS` (prompt) in `ops/check-graph-invariants.py`, and a copy that must differ in a way a replacement cannot express is NOT a row: its `//` comment says why | A fix to a shared merge node silently stops applying to `ralph`, or a `ralph` fix silently diverges. The copies are why the two arms' merge stages can be compared at all. |
| `merge_prep` is `review_merge.rm_setup` under another id, with NO script replacement: an `R10_COPIES` row with no replacements | `deliver` and all four reports run `render_comment.sh`, and `mark_needs_human` also runs `render.jq`; nothing else in `ralph` writes them, because there is no import. Its first block resolves the two auto-merge switches into `/tmp/fabro/auto_merge`, and `merge_gate` blocks every PR when that file is missing: the first copy left the block out. Checked by R10; the gate tests (`test_ralph.py`) run the switch resolution. |
| `watch_checks`' two deadline stops publish `checks_deadline=true` and a reason, and do NOT call `blocked` | `checks_blocked=true` outranks everything and routes to `report_blocked`. A PR that passed every gate but ran out of time must reach `report_queued` instead (O12), which is the only node that adds `merge-on-green`. |
| `ci_task` appends a Task and publishes `phase=post`; at two post-PR Iterations it writes BOTH reason files and routes `stop` | The bound is O13's budget. `pick`'s edges read `phase`, and `report_blocked` renders `merge_block_reason` while `mark_needs_human` renders `needs_human_reason`. |
| `final_check` writes `/tmp/fabro/base_ref` = `main` | `hygiene` reads `origin/<base>`. The shared phase gets that file from the PR in `rm_setup`, and `ralph` has not opened one when it first runs. |
| `stop_reason` is the only node with an edge into `mark_needs_human` that is not a report, and it derives the reason from `failure_signature` and writes both reason files, except that a deterministic failure of a command node keeps the `stuck_reason.md` that node wrote | R9's rule. A sink that writes one file leaves the other renderer reading a previous stage's text. A command node that writes its reason and exits 1 (`open_pr`'s refusals, `review_prep`, `review_gate`) has a signature that says only "exit 1". Every writer of `stuck_reason.md` ends the run or exits into `stop_reason`, so the file it keeps is never stale. |
| `stop_reason` treats a recorded `pr_number` as `post`, whatever `phase` says | `phase` stays `pre` until `pr_handoff` finishes, but the PR exists from the moment `open_pr` writes `pr_number`. A `pr_handoff` that failed on a GitHub error would otherwise reach `mark_stuck`, and the PR would get no comment. `mark_needs_human` works before `merge_prep`: with no `render_comment.sh` it posts its fallback comment, which has the reason but not the review table or the Refuter's findings. |

## The plan review (issue #69)

One enforcing box stage on EVERY run, between `plan_gate` and the first `pick`: `plan_gate ->
plan_review_prep -> plan_review -> plan_review_gate`. There is no arm and no shadow period. It is
issue #69's three lenses (comprehension, architecture, readiness) collapsed into one stage
(`prompts/plan_review.md.j2`), on `.plan-review`, the run's leased box. Its timeout (40m) is
unmeasured: re-time it from the first runs.

| Rule | What happens otherwise |
|---|---|
| It is bounded by a counter file, `/tmp/fabro/plan_review_visits`, written by `plan_review_prep`, not by the breaker: visit 1 reviews; any `error` finding routes `repair` (one planner repair turn, then `plan_gate` re-validates); visit 2 re-reviews and routes `pass` whatever it finds; visit 3 or later routes `go=false` straight to `pick` | The cycle `plan -> plan_gate -> plan_review_prep -> plan_review -> plan_review_gate -> plan` passes through command nodes, and the breaker (limit 100) would not stop it for a long time. A second repair turn would also double the box time the issue priced at one review |
| `plan_review_gate` fails CLOSED: a missing result, one whose `_io.stage` is not `plan_review`, one whose `_io.visit` differs from `.io/stage.json`'s, or a `decision` outside `pass`/`repair` writes `stuck_reason.md` and exits 1 to `stop_reason` | The opposite of the local review below: a Task list nobody could review is not waved through. The gate counts `error` findings itself and ignores a `decision` that disagrees with them |
| The reviewer reads `plan_review/plan.json`, a copy `plan_review_prep` makes of `tasks.json` with only `id`, `title`, `body`, `acceptance`, `files`, `covers` and `context` | It sees the Task list as a coder will. The raw `task_list.json` (the planner's `summary` and `checks`), `tasks.json`, the review and refute files, `progress.md` and the other review dir are Sealed in the manifest, and `io-guard` is matched on `plan_review` |
| On `repair`, the gate writes `plan_review/findings.md` (the error findings only), copies the planner's `task_list.json` to `plan_review/previous_task_list.json` and DELETES `task_list.json`; both files are optional inputs of the `plan` stage | A planner that exits without writing would hand `plan_gate` its first result as the repair. The `plan` prompt's *Repair turn* section says to fix a finding, refute it with evidence, or return `contradiction` |
| `plan_gate` treats `invalid` after two attempts as `ready` when `previous_task_list.json` exists, and leaves `tasks.json` as the first pass wrote it | `invalid` goes to `close_noop`, which would close the issue as having no actionable work because a repair turn failed. The first plan stands and the second review still runs |
| Every visit's result and a markdown record are kept in `/tmp/fabro/plan_review/` (`review_<N>.json`, `record_<N>.md`) and the counts are on stderr | The run summary is built by `merge_prep`, which is an R10 byte-for-byte copy of the shared node and cannot carry a new section. The record is for `fabro dump` and for measuring |

## The local review (replaces the pre-review, 2026-11-12)

Ralph's whole-branch `pre_review` (`pre_review_prep`, `pre_review`, `pre_review_check` and its
prompt) is REMOVED from ralph. backlog keeps its own (`invariants-backlog.md`). In its place is one
local standards and spec round on the leased box, run once before the hosted Refuter, Standards and
Spec rounds, which are unchanged: `hygiene -> local_review_prep -> local_standards -> local_spec ->
local_review_gate`, then `pick` (the findings became Tasks) or `refute_prep`.

| Rule | What happens otherwise |
|---|---|
| It sits after `hygiene` on the `phase=pre` edge; `pick -> final_merge [loop=done && phase=pre]` is direct again | Every Task it adds returns through `pick`, `final_merge`, `final_check` and `hygiene`, so `validated_tree` (the Refuter's "validation passed on this exact tree") and `hygiene.json` always describe the tree the hosted stages see. The review also sees the branch exactly as `final_check` passed it |
| `local_review_prep` writes the latch `/tmp/fabro/local_reviewed` on its first visit, and a later visit publishes `local_review_go=false`; it also routes past the review (without latching) when `/tmp/fabro/loop_exhausted` exists | The loop comes back to this edge after the Tasks it adds. Without the latch it would review its own fixes forever; an exhausted run is about to be held (O18), so box time spent on it is wasted |
| It may only help. Every fallback goes to `refute_prep`, never to `stop_reason` or `mark_needs_human`: the prep (exit 0 on every path, including a diff it cannot take or one over 2,000,000 bytes), both agent edges (each has an unconditional twin), and the gate | A box fault must not end a run that would have shipped. A `needs_human_review` decision from a local reviewer is ignored for the same reason; only `error` findings count |
| `local_review_gate` requires both results to carry `_io.stage` equal to `local_standards` / `local_spec` and a non-empty `_io.visit`; otherwise it routes `pass` with a stderr note and adds nothing | A hand-written file, or a result left by the hosted stage of the same schema, is not a local review. The hosted `review_gate` fails the node here; this one cannot |
| Each `error` becomes a Task with id `lrs<N>` (standards) or `lrp<N>` (spec), `axis` `standards`/`spec`, `finding_id` the reviewer's id and `source: local_review` | `source` is deliberately not `review`: `review_gate` counts `source == review` and `passed` into `fixes_applied`, which `merge_gate` checks 7-10 and the PR body report as the hosted reviews' fixes, and the local reviewers restart their ids at `STD-001`, so a local fix would be listed under a hosted id. Nothing else reads `source` except `refute_history` (`refute` only) and the scorecard's tasks-by-source counts, which gets a `local_review` line |
| `local_standards` and `local_spec` run the hosted `standards.md.j2` and `spec.md.j2` prompts unchanged (their closing sentence no longer names an output path) on `.review-local`, with `/tmp/fabro/local_review/` inputs and the `standards` and `ralph_spec` schemas | One rubric, so the local round finds what the hosted round would. The prompts name no `/tmp/fabro` path, because the manifest generator rejects a live prompt that names a Sealed path |
| `/tmp/fabro/local_review/` is Sealed for `refute`, `standards` and `spec`, and the local stages seal the hosted results, the plan review and the Refuter's files | The hosted reviews' independence is the product (ADR 0013 D6). Only the code changes reach them |
| `local_standards` (60m) and `local_spec` (40m) are the hosted 30m and 20m doubled for the two-slot boxes, and unmeasured | Re-time from the first runs |

The scheduler still reads `pre_review_arm` and `pre_review_verdict` from the node ids `pre_review_prep`
and `pre_review_check` (`summary.py`, `measure.py`); backlog still publishes them, ralph no longer does,
so a ralph run's columns read NULL, which `measure.py` already treats as "the stage never ran".

## The end of a run

| Rule | What happens otherwise |
|---|---|
| Every terminal node reaches `exit` only through `learn_prep` (and `learn_post`), and each writes its own id to `/tmp/fabro/ending` | A run with Iterations that never proposes a Learning is a run whose record nobody reads. `learn_prep` decides skip from `iterations`, so the guard is one place. |
| `learn_post` drops an entry whose `evidence` names no `path:N` and no backticked command, never fails the run, and never edits a file | A Learning without evidence is a guess on a public-to-the-team issue. A `learn_post` that failed would take an unconditional edge and turn a successful merge into a failed run. |
| The `ralph learnings` issue is found by its `<!-- fabro:ralph-learnings -->` marker, is labelled `learning`, and is never labelled `agent` | A title match breaks the moment a human retitles it, and `agent` would put the issue in the queue the scheduler reads. |

## The package

| Rule | What happens otherwise |
|---|---|
| `ralph`'s `kimi-k3` fallback order is the reverse of `backlog`'s (`zai:glm-5.3-flash`, then `zai:glm-5.3`, then `orfallback:qwen3.8-27b`), and `"glm-5.3"` has NO `kimi` rung | OpenRouter must only ever be a fallback (O5), and it is on a fixed $100 budget. Flash leads since 2026-10-28 because z.ai bills it at a third of glm-5.3's credits, and this row carries plan, standards and spec for days whenever Kimi's weekly quota is spent. And `.coder-t4` requests `glm-5.3`: a Kimi rung there spends the reviewers' weekly quota on coding. |
| `ralph` has no human gate. Every stop that `backlog` sends to `human_rescue` ends at `mark_stuck` (no PR) or at a merge-stage report (PR exists), with the reason | A 4-hour gate holds a coder box idle. R1, and O18 already says what happens when the loop cannot finish. |
| `ralph` imports no shared phase, so every hook matcher is `^node$` | The `(^|[.])node$` form exists for imported ids. A matcher that cannot match is silent: the run just never fires the hook. |
| `plan` (80m since the two-slot boxes), `standards` (30m) and `spec` (20m) are sized for the slowest model their visit can land on: the `kimi-k3` row's flash rung, and for `plan` the box arm of the box-plan trial (docs/plan-box-trial/) | A measurement-derived timeout whose subject changed is not a measurement. Re-derived 2026-10-28 from 61-62 visits each; re-time `plan` from the trial's QFN visits. |
