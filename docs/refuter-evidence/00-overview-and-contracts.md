# Refuter evidence: overview and canonical contracts

**Status:** Deployed 2026-10-15 (pushed `2a2be46` ~20:55Z, `make deploy-scheduler` 20:56Z). Task 06 step 1, the live check, passed on 2026-10-16, and it added the test route to D7 (see `06-live-check-and-measure.md`). **Measured and closed 2026-10-21** (`result-2026-10-21.txt`): the operator ended the window early, at 2026-10-21 19:25Z. No command criterion that `ci.sh` runs was held. The *Done when* counts were still exceeded on 10-18 and 10-19, by process criteria, the class D1 put out of scope. That class moves to #52. D9 did not fire. Issue #40 (closed).

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. **Before you edit a `.fabro` file, a prompt or a `workflow.toml`, invoke the
`/fabro-workflow` skill** and read `docs/agents/invariants-graph.md` and
`docs/agents/invariants-merge.md`. Read ADR 0013 (D4-D6 and its amendments) and ADR 0025
before you change the gates they govern.

## Hard rule for this series: do not push

The operator reviews before anything reaches `main` (pushing to `main` deploys; see
`AGENTS.md`). Commit each task locally on `main`, one commit per task, Conventional Commit
subjects. Run `make check` after a task that changes a command node's shell. **Stop after
task 05** and report. Do not run `git push`, `make deploy*`, `make compose-up`,
`make check-host`, `ssh`, or any `gh` command that writes. `make check` is the gate.

## What this is

The **Refuter** is the last reviewer before a PR merges. For each acceptance criterion of
the linked issue it writes `met`, `not_met` or `unverifiable`; `refute_gate` computes the
verdict, `pass` only when every criterion is `met` and there are no defects. Since the
Refuter moved to the smaller model `gpt-6-luna` on 2026-10-14, it marks **command
criteria** `unverifiable` — a criterion whose whole content is that a named command
succeeds, such as "`bunx tsc --noEmit` passes". The prompt tells it not to run commands and
that "a green CI is not evidence", so it blocks a PR whose command criteria already ran
green on that exact tree in `./.fabro/ci.sh`. 17 PRs were held this way, and 16 were merged
by hand. Issue #40.

The fix has three parts:

1. **`validation` input.** `refute_prep` writes a new input, `validation`, which says which
   validation passed on this exact tree (Final validation, or the merge-phase `validate`)
   and quotes `.fabro/ci.sh`.
2. **Prompt rule.** A command criterion is `met` when `ci.sh` runs the same tool over every
   file the PR changes, citing the line. It stays `unverifiable` when no validation is
   recorded for the tree.
3. **Two new `refute_gate` fields.** `refute_unverifiable_only` and `refute_ci_credited`,
   which the scheduler's scorecard counts so the result can be measured.

## Decisions (settled; do not re-open)

| # | Decision |
|---|---|
| D1 | Fix **command criteria only**. The three non-command `unverifiable` holds are out of scope: wfs#2080 (a diagnosis-comment requirement), wfs#2097 ("tests pass unchanged", which names no command) and lawncare-saas#2841 (a lint-probe requirement). The measure task checks whether that class recurs. |
| D2 | Issue options 1 and 2 together: a prompt rule plus a `validation` input. Luna stays. |
| D3 | No deterministic gate (issue option 3). Running a command parsed from criterion text would execute untrusted issue text. Matching free text against commands is fragile. |
| D4 | Triage does not change (issue option 4 is not adopted). A command criterion still tells the coder what to run, and changing triage would not fix issues already queued. |
| D5 | Going back to `gpt-6.1-sol` (issue option 5) is not adopted, because of the $100 hosted budget (ADR 0013 luna amendment). |
| D6 | Missing evidence blocks the merge. If no validation is recorded for HEAD's tree, command criteria stay `unverifiable` and the merge is still blocked (consistent with ADR 0013 D5). |
| D7 | "Runs that command" means that `ci.sh` runs the same tool over every file this PR changes. The run can be direct, or through a script, hook or test that `ci.sh` calls (a test that runs the command as a subprocess, added 2026-10-16 after the live check). A different config file or a narrower scope is acceptable when it still covers every changed file. |
| D8 | Only `ci.sh` is evidence. `.fabro/test.toml` checks are not (they ran on earlier per-task trees, not on this tree). |
| D9 | Add an over-blocking revert trigger. If `unverifiable`-only first verdicts stay above 5 a day across repositories for 3 consecutive days after this fix, reopen the choice of Refuter model. |

## Contracts

Task 01 writes the `validation` input; task 02 adds the prompt rule; task 03 adds the two
`refute_gate` fields; task 04 splits the scorecard. Every contract here is a file-backed
fact the gate suite (Step 2.4 of `make check`, `ops/test-task-gates.sh`) covers.

### C1. `/tmp/fabro/validated_tree`

One line: `<tree-hash> <source>`, where `<source>` is `final` or `merge`. The merge phase's
`validate` node writes it:

- `rm -f /tmp/fabro/validated_tree` as the first command of its script;
- on the **skip** path, `$T final` (the tree hash already in `$T`);
- on the **green run** path, `HEAD^{tree} merge`;
- on the red path, nothing.

`refute_prep` reads it to know which validation passed on this exact tree.

### C2. `/tmp/fabro/review/refute_validation.md`

`refute_prep` always writes it, so it is never missing. It records whether a validation
passed on HEAD's exact tree — `final` (Final validation) or `merge` (the merge-phase
`validate`) — and quotes `.fabro/ci.sh`. When HEAD's tree is not in `validated_tree`, or the
worktree is not clean, it says "No validation is recorded for this tree. Every command
criterion is unverifiable." It is a new Refuter input.

### C3. Manifest input

`refute_prep`'s rewrite writes `refute_validation.md`; the `refute` stage's manifest gains a
`validation` input (path `/tmp/fabro/review/refute_validation.md`, required). The manifest
lives in `.fabro/workflows/_io/manifest.json`; the `FABRO_IO_MANIFEST` blocks in each
`workflow.toml` are **generated** by `ops/fabro-io-manifest.py`, never hand-edited.

### C4. `refute_gate` fields

`refute_gate` adds two keys to **every** `context_updates` object it prints:

- `refute_unverifiable_only` (boolean): true exactly when the verdict is `fail`, `NM` (the
  number of `not_met` criteria) is 0, and `DF` (the number of defects) is 0.
- `refute_ci_credited` (integer): the number of criteria with `status == "met"` whose
  `evidence` contains `ci.sh`.

The scorecard reads them (task 04).

## Facts the tasks rely on (verified)

- `backlog`'s `open_pr_prep` (`backlog/workflow.fabro`, ~1626-1681) runs **Final validation**
  (`./.fabro/setup.sh && ./.fabro/ci.sh`). On green it writes `HEAD^{tree}` to
  `/tmp/fabro/final_validated_tree`.
- The merge phase's `validate` node (`review-merge.fabro`, ~385-421) skips its suite when
  HEAD's tree equals `final_validated_tree` and the worktree is clean (ADR 0025 C8).
  Otherwise it runs `setup.sh && ci.sh`. Only `backlog` writes `final_validated_tree`, so in
  `pr-review` and `dependabot` the merge-phase `validate` always runs the suite.
- A PR that edits `.fabro/ci.sh`, `.fabro/test.toml` or `.fabro/hygiene.json` is blocked by
  the hygiene node (`config_changed`). So `.fabro/ci.sh` at HEAD equals the base branch's
  copy, and it is safe to quote.
- `.fabro/test.toml` checks run in Task validation on earlier per-task trees, not on this
  tree. They are not evidence (D8).
- The scheduler reads `refute_gate`'s `context_updates`
  (`ops/scheduler/src/fabro_scheduler/summary.py`, ~283). It stores them as a JSON array of
  `{verdict, not_met}` (`store.py`, ~1102). The scorecard counts the first verdict of each
  run (`scorecard.py`, `_refute_line` ~768, and the table line ~669).
- `_shared/review-merge` is imported by `backlog`, `pr-review` and `dependabot`.

## Out of scope

- The three non-command `unverifiable` holds (D1). The measure task checks whether that
  class recurs, but this series does not change the Refuter's handling of them.
- A deterministic gate that runs a command parsed from criterion text (D3).
- Triage telling writers what kinds of criteria to file (D4).
- The Refuter's model (D5, D9 only reopens the choice on the trigger).

## Done when (7 days after deploy)

- `unverifiable`-only first verdicts are ≤ 5 a day across repositories, measured by the new
  scorecard row (`first_fail_unverifiable_only`).
- The fast lane's held-by-Refuter count is ≤ 1 a day.
- `refute_ci_credited` has not risen while operator fixes or reverts on merged PRs rose.

## Task order

| Task | What | Shell change? |
|---|---|---|
| 01 | `validate` records the tree; `refute_prep` writes the `validation` input (C1-C3) — done (commit `0c4ee59`) | yes |
| 02 | the Refuter credits `ci.sh` for command criteria (the prompt rule) — done (commit `7080221`) | no |
| 03 | `refute_gate` reports two new fields (C4) — done (commit `d10e4fc`) | yes |
| 04 | the scorecard split — done (commit `7b90344`) | yes |
| 05 | ADR 0013 amendment, plan-trial note, docs; **stop** — done (commit `124690d`) | — |
| 06 | live check and measurement (operator; live check passed 2026-10-16; measure 2026-10-17 to 2026-10-23) | — |

01, 03 and 04 change command nodes; 02 is a prompt. Do them in order so each commit is
reviewable. 04 depends on 03's fields. 06 is write-only here; the operator carries it out.
