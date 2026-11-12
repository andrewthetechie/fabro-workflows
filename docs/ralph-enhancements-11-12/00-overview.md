# Ralph and factory enhancements, from the 2026-11-12 triage

The order in which to work the open bugs and enhancements that the 2026-11-12 triage kept. The
design for each task is in its GitHub issue. This file says only **what** to work, **in what
order**, and **what each task depends on**. Tasks are dispatched **one at a time**, each to a
fresh agent. This is not a loop: a task is finished, pushed and deployed before the next one
starts.

## Goal

1. **Merge rate first.** Baseline for runs dispatched 2026-11-02..11-11
   (`ops/fabro-scorecard.sh --from 2026-11-02 --to 2026-11-11`): **44%**, 45 of 103 PRs merged by
   the workflow. Issues the precheck rated `unverifiable` merge **14%** (9 of 66); `ready` issues
   merge **41%** (146 of 354) (`GET :32280/api/background/calibration`).
2. **Local inference as the tie-breaker.** Use the boxes' idle time (the background lane), and
   stop wasting box time on stages that hang or time out.

## Rules for every task

- **Read the issue and all of its comments.** Where a comment titled *Decided in the 2026-11-12
  triage* (or *Scope decided…*) differs from the issue's original body, the comment wins.
- **For any graph, prompt or `workflow.toml` change:** invoke `/fabro-workflow`, and read the
  `docs/agents/invariants-*.md` file for the area first (`AGENTS.md` lists them).
- **For a node or edge change:** update the baseline in `AGENTS.md` and verify it with
  `make check-host`.
- **Before the push:** run `make check`. Then deploy what the task's *Deploy* column says.
  `ops/` changes do not go live without a deploy.
- **`fabro-io` changes ship image-first (D18).** Push and `make deploy-images` *before* any
  commit that depends on the new behaviour.
- **When done:** comment on the issue with what shipped (commit and deploy time), and update this
  file's status column. Close the issue only when its own *Done when* is met. Many issues end with
  a measurement that comes later.
- **A task marked *host* needs the server** (`ssh andrew@10.10.0.32`): run events or a live check.
  Stream events (`fabro events <run> -p`). Never export raw event logs to this Mac.

## Tasks

| # | Task | Issue | Touches | Deploy | Host | Depends on | Status |
|---|---|---|---|---|---|---|---|
| **Phase 1: quick fixes that stop wasted box time** | | | | | | | |
| 01 | Give a merge-queue handoff its own held bucket (it now reads "no reason recorded") | #71 | `ops/scheduler/.../scorecard.py` `held_bucket`, retro class | `make deploy-scheduler` | — | — | todo |
| 02 | `compose-up` refuses while a coder lease is held; `FORCE=1`, `DRAIN=1` | #21 | `ops/deploy-host.sh`, `ops/tests/`, `docs/agents/deploying.md` | `make deploy` (only `deploy-host.sh` changes; **do not** run `compose-up`) | — | — | todo |
| 03 | An agent stage waited 9 h on `human_input_required`: find the cause, then bound or refuse it | #64 | depends on the cause: fabro settings, the monitor or the scheduler | depends on the cause | host | — | todo |
| 04 | A checkpoint after a double timeout got 413 and lost the run: find the cause, keep checkpoints small, fail as `transient_infra` | #65 | depends on the cause: ignore rules, size check | depends on the cause | host | — | todo |
| 05 | `learn` timeouts: derive its timeout, or run it less often | #79 | `ralph/workflow.fabro` (`learn`), maybe `learn_prep` | workflow only, live on push | host | — | todo |
| **Phase 2: the two biggest levers** | | | | | | | |
| 06 | Background lane on two-slot boxes, **option (b) only**: measure `pr-review` gaps; a sibling between stages is gap 0, not a block | #78 | `ops/scheduler/.../config.py` (`BACKGROUND_GAP_*`), `background.py` (`_gap_minutes`) | `make deploy-scheduler` | host (to measure gaps) | — | todo |
| 07a | Sharpening: the `issue-sharpen-draft` package (Elephant) with `draft_gate`, its Stage manifest entries and gate tests | #68 | `.fabro/workflows/issue-sharpen-draft/`, `_io/`, `ops/gate_tests/` | none (no kind turns itself on) | — | — | todo |
| 07b | Sharpening: the `issue-sharpen-audit` package (Goldfish): three lenses plus `audit_gate`, with the finding lifecycle and gate tests | #68 | `.fabro/workflows/issue-sharpen-audit/`, `_io/`, `ops/gate_tests/` | none | — | 07a (shares the proposal contract) | todo |
| 07c | Sharpening in the scheduler: `SharpenProducer`; the `unverifiable` hold, gated on the kinds; approve, reject and stale apply; a fresh precheck on a new body hash; onboarding labels | #68 | `ops/scheduler/`, `skills/setup-repo-for-factory/` | `make deploy-scheduler` (kinds stay off) | — | 07a, 07b | todo |
| 07d | Sharpening calibration: the `sharpen-calibration` kind, `GET /api/background/sharpen-calibration`, the measure section | #68 | `ops/scheduler/` | `make deploy-scheduler` | — | 07c | todo |
| 07e | **Operator.** Turn on `sharpen-calibration`; read the report and a hand sample. Go live only if untouched-criterion preservation is 100% and no Substitutions turn up | #68 | `SCHEDULER_BG_KINDS` | env change | host | 06, 07d | todo |
| **Phase 3: clear held PRs without the operator** | | | | | | | |
| 08a | `pr-shepherd` phase 1, **dry-run**: select mechanical holds (CI-unsettled handoffs, merge-phase timeouts, rejected force-push, CI-fix failures FlakeRadar rates flaky); at most 2 retries per PR, counted in a marker | #31 | new `.fabro/workflows/pr-shepherd/` (or a scheduler job; decide in the task), fixture tests, automation rows | per the chosen home | host (dry-run output) | 01 | todo |
| 08b | **Operator.** Review each repository's dry-run list, then switch to live | #31 | — | — | host | 08a | todo |
| 08c | `pr-shepherd` phase 2: superseded-gate blocks (a tracked file of pattern and fixing commit, compared with `workflow_sha`) | #31 (from #39) | as 08a | as 08a | — | 08b | todo |
| 09a | `gitguard` refuses `pre-commit run --all-files` and `-a` | #45 | `ops/fabro-io/src/gitguard.rs` and its tests | `make deploy-images` (image-first) | — | — | todo |
| 09b | Remove `.github/workflows/` commits after each code stage, in **both** `ralph` and `backlog` | #45 | `ralph/workflow.fabro`, `backlog/workflow.fabro`, `ops/gate_tests/` | workflow only, live on push | host (`check-host`) | — | todo |
| 09c | Reviewer rule: a diff that changes a workflow file is blocking. Scoped-command rule in `plan.md.j2` and `iterate.md.j2` | #45 | `ralph` and `backlog` prompts, `_shared/review-merge` (R10) | workflow only | — | 09a | todo |
| **Phase 4: resilience and measurement** | | | | | | | |
| 10 | Andon cord, **layers 2 and 3 only**: pause a repository after 2 `base-broken` runs and file one "main is red" issue; a fleet-wide stop on repeated instant failures | #24 | `ops/scheduler/`, `ops/scheduler/tests/` | `make deploy-scheduler` | — | — | todo |
| 11 | `plan_gate` criterion coverage, shadow only (`plan_uncovered`) | #74 | `ralph/workflow.fabro` (`plan_gate`), `ops/gate_tests/test_ralph.py`, summary keys | workflow (and scheduler, if summary keys change) | — | — | todo |
| 12 | Compute #76's baseline. Build Phase 3a if at least 5% of Tasks reach rung 3; otherwise close #76 | #76 | `ops/iterate-attempts.py` over two weeks of events | — | host | — | todo |
| 13 | Compute #77's baseline. Build shadow overlap logging if overlap repairs reach at least one a week; otherwise close #77 | #77 | scorecard collector; then `ops/scheduler/.../dispatch.py` | `make deploy-scheduler` if built | host | — | todo |

**Deferred, not in this series:** #70 (review finding fingerprints) and #75 (red probe). Pick
them up after task 11's numbers.

### Why this order

- **Phase 1** first: each task is small and gives back box hours or a correct number at once.
  01 also names the class that 08a selects.
- **06 before 07e:** sharpening jobs run in the background lane, and on two-slot boxes the lane
  rarely gets a gap without 06.
- **07a–07d** can ship ahead of 06, because nothing runs until the operator adds the kinds.
- **The scheduler tasks (01, 06, 07c, 07d, 10, 13) all edit `ops/scheduler/`.** Run them one at a
  time and pull before each, so that no two in-flight branches touch it.
- **09a ships before 09c**, because the prompt rule names the guard's refusal.

## Scheduled reads (operator, no task)

| When | What | Issue |
|---|---|---|
| ~2026-11-17 | The two-week re-count of `ci.sh`-gap and PR-description holds after the five `ci.sh` PRs (merged 11-03) | #57 |
| Now overdue | The 11-09 retro read: close `held:refuter`, `held:unfixed-findings` and `held:review-needs-human`, or name what is left | #62 |
| ~2026-11-26 | `iterate_t1` on writers-app, from runs dispatched since 11-12, comparing durations (window restarted for the 120m timeout) | #63 |
| ~2 weeks after 07e | Merge rate of sharpened issues against 14% (`unverifiable`) and 41% (`ready`) | #68 |
| ~2 weeks after 08b | Held PRs with mechanical reasons, resolved without the operator | #31 |

## Dispatching a task

Give the agent this, with the task number filled in:

> Do task NN of `docs/ralph-enhancements-11-12/00-overview.md` in `~/Documents/code/fabro-workflows`.
> Pull first. Read the overview's *Rules for every task*, then the task's issue and all of its
> comments. Where they differ, the triage decision comment overrides the issue body. Do only
> task NN. When it is pushed and deployed, comment on the issue and update the task's status in the
> overview.
