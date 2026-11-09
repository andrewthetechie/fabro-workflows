# Adoption plan

**Status:** a proposed plan. It is not an approved task series. Every work item starts in
shadow mode, and none of them changes routing until its own exit evidence exists.

## 1. Invariants for every work item

These add to the EGM plan's invariants (`../egm-research/03-adoption-plan.md` section 2), which
also apply.

1. A shadow node always exits 0, and has one unconditional edge forward. A failure of the
   measurement never ends a run that would have shipped without it.
2. A node that changes the checkout to measure something restores it, and asserts that the
   tree is byte-identical before it exits. `criterion_checks` sets the precedent.
3. Predicted files never limit what a run may edit. They only order dispatch (the factory
   readiness standard, *Decisions, not file boundaries*).
4. A new agent stage gets a Stage manifest entry, a schema and a sealing policy. It reads none
   of the Refuter's or reviewers' outputs.
5. Each graph change updates the `Ralph` node and edge baseline in `AGENTS.md`, verified by
   `make check-host`.
6. Each new shell path gets a test in `ops/gate_tests/test_ralph.py`.

## 2. Slice 0: baselines

Collect these before any slice changes a graph. All of them come from data that already
exists.

| Number | Source | Used by |
| --- | --- | --- |
| Tasks per run that reach rung 3, and the pass rate at rungs 3 and 4 | `ops/iterate-attempts.py` over the last two weeks of event files | Slice 3 |
| Box-hours spent at rungs 3 and 4 on Tasks that never passed | Same | Slice 3 |
| Runs that visited `final_merge -> resolve_merge`, `resolve_base` or a merge-queue rebase, by repository | `fabro events -p` through the scorecard collector | Slice 4 |
| Share of queued issues with a context-pack marker at dispatch | The scheduler store (`had_pack`, D21) | Slice 4 |
| Refuter `not_met` findings whose criterion no Task's `covers` quotes | Archived `task_list.json` and `refute.json` | Slice 1 |

Stop condition: if overlap repairs total fewer than one a week across all repositories, drop
Slice 4. If fewer than 5% of Tasks reach rung 3, drop Slice 3.

## 3. Slice 1: criterion coverage in `plan_gate`

### 3.1 Change

`plan_gate` already validates `task_list.json`. Add a coverage pass after it:

1. Extract the bullets under the issue body's `## Acceptance criteria` heading. Leave out
   `## Post-deploy checks` and `## Human actions`.
2. Normalise each bullet and each Task's `covers` entries: lower case, collapsed whitespace,
   and no list or checkbox markup.
3. A criterion is covered when a `covers` entry contains it or it contains a `covers` entry,
   after normalisation. A `checks` entry for the criterion also counts.
4. Write `/tmp/fabro/plan_coverage.json`, with `{criteria, covered, uncovered: [text]}`, and
   publish `plan_uncovered` as a count.

Only acceptance criteria are checked, not requirements. Under the planner's own rules, a
criterion that a non-goal excludes is a `contradiction`, so for a `ready` plan every
criterion must have a Task. Free-form requirements are too loose to match by string.

### 3.2 Measurement

Over two weeks, for each run with `plan_uncovered > 0`:

- whether the Refuter later returned `not_met` on an uncovered criterion;
- whether the run merged;
- a hand-classified sample of 20: a true gap, a paraphrase that the match missed, or a heading
  that the extraction misread.

### 3.3 Enforcement candidate

If true gaps are at least 70% of the sample and they predict `not_met`, give the planner one
repair turn with the uncovered list. Then re-run `plan_gate`. Share that turn with #69's plan
repair, if #69 reaches enforcement, so that a plan has one repair turn in total. A second
failure ends at `mark_stuck` with the uncovered criteria as the reason.

This is cc-sdd's order inside one gate: mechanical checks first, then judgement (#69's
lenses), with a bounded repair loop.

## 4. Slice 2: the red probe

### 4.1 Change

Add a command node `red_probe` between `check` and `record`:

```text
check -> red_probe [outcome=succeeded]    red_probe -> record
check -> record    [outcome=failed]       (unchanged)
```

`pick` records the Task's starting tree in `/tmp/fabro/task_base_tree`, next to the files it
already resets. `red_probe` runs only when `iteration.json` says `done`:

1. Split the diff from `task_base_tree` to the working tree into test files and other files,
   with the same classifier `fabro-io task-check` uses to select tests.
2. If the Task changed no test files, write `no_tests` and exit 0.
3. Reverse-apply the non-test part of the diff in place. Run only the changed test files with
   the runner `task-check` would choose, under `timeout 10m`.
4. Re-apply the diff. Assert that `git write-tree` equals the tree before step 3. On a
   mismatch, restore from the saved tree and record `restore_failed`.
5. Write `/tmp/fabro/red_probe/<task_id>.json` and append one line to `progress.md`.

`red_probe` must not touch `check_ok`, and it must run before the checkpoint. It writes
nothing else.

### 4.2 Contract

```json
{
  "task_id": "r1c2",
  "class": "red_assert",
  "tests": ["tests/test_billing.py"],
  "command": "pytest tests/test_billing.py",
  "exit": 1,
  "seconds": 41,
  "tree": "git tree id"
}
```

| `class` | Meaning |
| --- | --- |
| `red_assert` | The tests ran and failed on an assertion. This is the evidence cc-sdd asks for |
| `red_build` | Collection, import or compile failed. Inconclusive: a new symbol is missing |
| `green` | The tests pass without the implementation. They do not prove the Task |
| `no_tests` | The Task changed no test files |
| `skipped` | Timeout, an unknown runner, or a reverse-apply that did not apply cleanly |
| `restore_failed` | The tree did not match after re-applying. Count it as a defect of the probe |

To tell `red_assert` from `red_build`, parse the runner's summary line: pytest's
`errors during collection`, a TypeScript `error TS`, `cargo`'s `error[E`. Treat any other
non-zero exit as `red_assert`. The classifier is a gate test fixture for each runner the
onboarded repositories use.

### 4.3 Measurement

- The class distribution for each repository and each rung.
- For `green` Tasks: the rate of Refuter `not_met` and defect findings on their criteria,
  compared with `red_assert` Tasks.
- The probe's cost: its seconds as a share of the Iteration's duration.
- `restore_failed` must be zero. One occurrence stops the slice until the cause is fixed.

### 4.4 Enforcement candidates

Each candidate needs its own decision after the measurement:

1. Add the `green` list to `refute_validation.md`: "these tests pass with this Task's
   implementation reverted". This gives the Refuter evidence and does not change routing.
2. Make `green` a MISS on its first visit, with the probe's output under `## Last attempt`.
   Do this only if `green` predicts Refuter findings, and only at rungs 1 and 2, so that it can
   never cost a rung-4 Task its last attempt.

Add a hollow-test fixture to the EGM reviewer evaluation set (EGM work item B), so that the
Refuter's own recall on such tests is measured too.

## 5. Slice 3: diagnosis after two misses

### 5.1 Change

Add a read-only agent stage `diagnose`, and a command node `diagnose_gate`. They run on the
`pick -> excerpts` path when the current Task has missed twice and the next rung is 3. This
happens at most once per Task. Run the stage on the leased box's local model, as `pre_review`
does, so that it uses no hosted quota.

Inputs: `current_task.json`, `task-context.md`, the two failed Iterations' summaries and `check`
output from `progress.md`, the diff since `task_base_tree`, and `orientation.md`. It gets no
Refuter or reviewer file.

Output schema `diagnosis.schema.json`:

```json
{
  "category": "task_decomposition",
  "root_cause": "one or two sentences",
  "evidence": ["src/billing.py:88", "`pytest tests/test_billing.py::test_proration`"],
  "next_action": "retry",
  "fix_plan": ["the smallest repo-fixable step"]
}
```

`category` takes one of these values: `logic_error`, `missing_dependency`, `runtime_mismatch`,
`config_gap`, `test_environment`, `base_broken`, `task_ordering`, `task_decomposition`,
`spec_conflict` or `external_dependency`. `next_action` is `retry` or `stop`.

`diagnose_gate` applies the Refuter's evidence rule: every `evidence` entry must name a
`path:N` that exists or a backticked command. It drops a diagnosis with no surviving evidence.

### 5.2 Phases

| Phase | Behaviour | Exit evidence |
| --- | --- | --- |
| 3a | Record the diagnosis only. The rung-3 Iteration gets nothing new | Categories for at least 30 diagnosed Tasks, compared with whether rungs 3 and 4 then passed |
| 3b | On an A/B arm, put `root_cause` and `fix_plan` under `## Last attempt` for rung 3 | The arm's pass rate at rungs 3 and 4 against the control |
| 3c | `stop` on `task_ordering`, `task_decomposition`, `spec_conflict` or `external_dependency` ends the loop at `mark_stuck`, with the category and root cause as the reason | In 3a, Tasks with those categories almost never passed at rungs 3 or 4 |

Check the active trial arms before you choose a split bit. Bit 0 was the Ralph trial's split,
and bit 2 is the pre-review's. 3c ends a run with no PR, so its rule must be conservative: a
category that sometimes recovers stays `retry`.

### 5.3 Retro use

From Phase 3a, publish `diagnosis_category` in the run summary. A `mark_stuck` run then has a
category for the retro's failure classes (#62, #33), in addition to its `failure_signature`.

## 6. Slice 4: overlap-aware dispatch (shadow)

### 6.1 Change

In `dispatch.py`, compute a predicted file set for each candidate issue before it claims a box:

1. Parse the paths under `### Files` and `### Tests` in the issue's context-pack marker
   comment. They are already checked against the checkout.
2. Add any paths under the issue's `## Task map` or expected-files section (#67's format).
3. With neither source, the issue has no prediction, and it is dispatched as today.

For each running `ralph` or `backlog` run in the same repository, keep its issue's predicted
set. When a candidate's set intersects a running set, log a `would_defer` record with the
issue, the running issue and the shared paths. Dispatch exactly as today.

Add `GET :32280/api/dispatch/overlap`, which returns the `would_defer` records for a window,
each joined to the overlap repairs from Slice 0 that later ran for either issue.

### 6.2 Measurement

- Precision: the share of `would_defer` pairs where either run later visited `resolve_merge`,
  `resolve_base` or a conflicting `final_merge`, or failed a merge-queue rebase.
- Recall: the share of all overlap repairs that a `would_defer` record predicted.
- Cost: how long a deferral would have delayed the issue, which is the remaining time of the
  run it overlapped.

The pack's `files` mixes files that the issue will read with files it will change, so expect
low precision at first. If precision is under 50%, try changed-only paths (the Task map)
before you drop the slice.

### 6.3 Enforcement candidate

Defer, never block. A deferred issue stays queued, and the scheduler dispatches the next
non-overlapping issue. Defer one issue for at most `SCHEDULER_OVERLAP_DEFER_MAX` (start at
the median run duration), and then dispatch it anyway. A repository with one queued issue is
never deferred. The switch is `SCHEDULER_OVERLAP=off|shadow|defer`, with `shadow` as the
compose default, and it is added to `ops/docker-compose.yaml`'s `environment:` lines.

## 7. Slice 5: amendments to the #67 spec

These are text changes to an open spec, with no runtime effect. Post them as a comment on #67.

1. **A fourth fresh review over the whole draft set**, after the three per-issue reviews and
   before *Quiz Graph*. It reads every draft in the plan, and it checks:
   - that a data shape or type named in two drafts is defined the same way in both;
   - that the producer issue's contract matches the consumer issue's, for each pair;
   - that no capability appears in two drafts;
   - that two drafts in different chains do not share an expected file;
   - that a contract change in one draft has a dependent draft ordered after it.

   It returns findings per pair of drafts. The parent fixes them or lists them in *Quiz Graph*,
   with the same no-dismiss rule as the Refuter's-eye review.
2. **Lint rule L11:** two drafts that are not in the same Split chain, and that share a path in
   their expected files, give a warning: "chain them, merge them, or accept parallel overlap".
   This is the planning-time half of Slice 4.
3. **A style note in the reference file:** write a behavioural criterion as one trigger and one
   observable response. EARS's "When X, the Y shall Z" is the model. Do not make this a lint
   rule (see the README, *Considered and deferred*).

## 8. Sequence

| Order | Slice | Production behaviour | Depends on |
| --- | --- | --- | --- |
| 0 | Baselines | None | Nothing |
| 1 | Criterion coverage in `plan_gate`, shadow | None | Slice 0's coverage row |
| 2 | Red probe, shadow | None, plus one scoped test run per `done` Task | Nothing |
| 3 | #67 amendments | None (spec text) | Nothing |
| 4 | Overlap-aware dispatch, shadow | None | Slice 0's overlap rows |
| 5 | Diagnosis, Phase 3a | None, plus one local-model stage per twice-missed Task | Slice 0's rung rows |
| 6 | Enforcement reads | One decision for each slice, from its exit evidence | Two weeks of each shadow |

Slices 1, 2 and 3 are independent of each other and can ship in any order. Slice 1 is the
cheapest: one command node's shell, and gate tests. Slice 2 has the most direct value per
run. Slice 4 is the only one that touches the scheduler and needs `make deploy-scheduler`.
