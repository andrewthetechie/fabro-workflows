# Gate relief: overview and canonical contracts

**Status:** deployed 2026-10-12 (pushed to `main`). Issues #28, #29, #30. Commits: 01 `7771b52`, 02 `485cc94`, 03 `0c3256e`, 04 `6d55e1e`, 05 `b6552d0`, 06 `6b84733`, and a review fix `3ba8912` (a failed `refute_fix` turn is reset before `deliver` pushes; see `docs/agents/invariants-merge.md`). Task 07 (measurement) is due 2026-10-19.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. **Before you edit a `.fabro` file, a prompt or a `workflow.toml`, invoke the
`/fabro-workflow` skill** and read `docs/agents/invariants-graph.md` and
`docs/agents/invariants-merge.md`. For task 03 also read the `_shared/triage` header
comments (entry and exit contracts). Read ADR 0012 D8, ADR 0013 D4-D5 and ADR 0016 before
you change the gates they govern.

## Hard rule for this series: do not push

The operator reviews before anything reaches `main` (pushing to `main` deploys; see
`AGENTS.md`). Commit each task locally on `main`, one commit per task, Conventional Commit
subjects. Run `make check` after every task that changes a command node's shell, and
`make check-fast` after the rest. **Stop after task 06** and report. Do not run
`git push`, `make deploy`, or anything that writes to GitHub or the host. `make check-host`
is read-only and is allowed (task 06).

## What this is

Three changes to the merge phase, each from the 2026-09-25..10-11 merge-rate data:

1. **#30, architecture PRs auto-merge (ADR 0012 D8 amendment).** Operator chose
   **option B**: an `architecture` PR merges like any other PR when every `merge_gate`
   check passes (refute `pass`, hygiene clean, risk <= 3, ...) and no hold label is set.
   The human gate moves to the issue (promotion to the queue).
2. **#28, post-deploy checks leave the refuter's verdict (ADR 0013 D4 amendment).**
   Criteria only a deployed environment can check live under a `## Post-deploy checks`
   heading in the issue body. The refuter never sees them; the human sees them as a
   checklist in the merge-phase report and the PR body.
3. **#29, one local fix turn after a refuter `not_met` (ADR 0013 D5 amendment).** Operator
   **agreed as proposed**. On a verdict with at least one `not_met` criterion, route once to
   a fix stage on the run's coder pool, then `validate` -> `hygiene` -> `refute_prep` ->
   `refute` again. A second `not_met` goes to a human as today.

## Decisions (settled; do not re-open)

| # | Decision |
|---|---|
| G1 | #30 is option B. Not C (no size limit). |
| G2 | The hold label is **`do-not-merge`**. Both `merge_gate` and the `merge-queue` guard refuse a PR carrying it, for every PR, not only architecture PRs. Create it with `gh label create ... 2>/dev/null \|\| true` where a node first needs it (the existing pattern); do not create it from this Mac. *(Planner's choice; the operator may rename it at review.)* |
| G3 | The post-deploy heading is exactly `## Post-deploy checks`. Each item is one `- [ ] <check>` line. The section ends at the next `## ` heading or end of body, same as the `## Decisions made during triage` awk pattern in `_shared/triage/triage.fabro:327`. |
| G4 | The refuter is kept from post-deploy checks **structurally**: `refute_prep` strips the section from each issue body before writing `refute_issues.json`. `refute.md.j2` also says to skip such a heading if one survives. `refute_gate` does not need text-matching; "does not count criteria taken from it" holds because the refuter never receives them. |
| G5 | Issues with no `## Post-deploy checks` section behave exactly as today: `unverifiable` still blocks. No migration of queued issues. |
| G6 | The fix turn triggers only when `refute.json` has >= 1 criterion with status `not_met` **and** `refute_fix_attempts` is 0. `unverifiable`-only failures, defect-only failures, and `invalid` go to `deliver` as today. If a `not_met` verdict also has defects, the fixer gets both (they are in the same file), but defects alone never trigger it. **Amended 2026-10-13 (#36, ADR 0013):** a defect-only `fail` also takes the turn; the gate emits `refute_fixable` for the edge, and `refute_not_met` keeps its meaning. `unverifiable`-only and `invalid` still go to `deliver`. |
| G7 | The fixer's input is a new file `/tmp/fabro/review/refute_findings.json` written by `refute_gate`: the `not_met` criteria (criterion + evidence) and the defects. The fixer never reads or writes `refute.json`, `refute_verdict` or `refute_reason.txt`: add those to its **Sealed paths** in the Stage manifest. |
| G8 | The fix-turn counter is `/tmp/fabro/refute_fix_attempts`, written once to 1 when the fix route is taken. `refute_prep` already deletes `refute_attempts`; it must **not** delete `refute_fix_attempts`. The counter is also emitted as `context_updates.refute_fix_attempts` for the edge condition. |
| G9 | New node `refute_fix`, class **`refute-fix`**, agent, `timeout="45m"` (rework parity), `max_retries=1`, prompt `@prompts/refute_fix.md.j2` under `_shared/review-merge/prompts/`. Stylesheet rows: `backlog` and `pr-review` -> `{{ inputs.coder_pool \| default('coders-a') }}; reasoning_effort: medium;` (same as `.coder`/`.rebase`); `dependabot` has no `coder_pool`, so -> `glm-5.3-flash` (its `.merge-fix` model). Check `docs/agents/invariants-host.md` for fallback rows the new class needs. |
| G10 | The fixer writes `/tmp/fabro/review/refute_fix.json` via `submit` (new schema `_io/schemas/refute_fix.schema.json`: `summary`, `addressed[]` of `{criterion, change}`, `not_addressed[]` of `{criterion, why}`). It does **not** touch `fix_result.json`; `merge_gate` checks 7-10 keep reading the `review_fix` result. A failed or invalid `refute_fix` routes to `deliver` (the old verdict stands, the merge blocks). |
| G11 | Edges after the fix: `refute_fix` -> `refute_fix_gate` (command: receipt check + commit, mirroring how `review_fix`'s changes are committed; read `fix_gate` first) -> `validate`. From `validate` the existing path runs again (`hygiene` -> `refute_prep` -> `refute` -> `refute_gate`). On the second pass `refute_fix_attempts=1`, so `refute_gate` goes to `deliver`. |
| G12 | All ADR amendments are written as **accepted** (operator agreed in session 2026-10-11), as dated amendment sections appended to the ADR, not edits of the original decision text. |

## Facts the tasks rely on (verified 2026-10-11 at `72b2ff6`)

- Architecture check, merge phase: `_shared/review-merge/review-merge.fabro:706` (comment
  2b) and `:731-732` (the `gh pr view ... index("architecture")` check).
- Architecture check, merge queue: `merge-queue/workflow.fabro:232` (`hand("This PR
  implements an Architecture issue ...")`).
- Architecture wording in `backlog`'s PR body: `backlog/workflow.fabro:1809-1810`
  (`MERGE='A human merges it, because the issue is labelled architecture.'`).
- Risk check: `review-merge.fabro:771-772` (`risk <= 3`).
- Refute chain: `review-merge.fabro:575` `refute_prep`, `:614` `refute`, `:621-642`
  `refute_gate`; edges `:1403-1412`. `refute_gate` computes `pass` iff no criterion is
  not `met` and no defects.
- The report renderer that shows refuter results: `review-merge.fabro:~118-210`
  (`render.jq`, `--slurpfile ref $T/refute.json`).
- Triage body writers: `_shared/triage/triage.fabro:112` (`improved_body.md`),
  `:327-333` and `:363-374` (`decided_body.md`, the replace-a-section awk pattern).
- Schemas live in `.fabro/workflows/_io/schemas/`; the manifest is
  `.fabro/workflows/_io/manifest.json`; the `FABRO_IO_MANIFEST` blocks in each
  `workflow.toml` are **generated** (see `_io/README.md` for the generator; `check` fails on
  drift). Never hand-edit them.
- `_shared/review-merge` is imported by `backlog`, `pr-review` and `dependabot`, so task
  05 changes all three graphs' node/edge counts. `_shared/triage` is imported by
  `issue-triage` and `arch-review`.
- `ops/test-task-gates.sh` is the gate test suite (~5400 lines); find the existing
  `refute_gate` and `merge_gate` cases and extend them in the same style.
- Scorecard is issue #22 (open, not built). #29's "scorecard shows before/after" is
  task 07, after deploy.

## Task order

| Task | Issue | What | Shell change? |
|---|---|---|---|
| 01 | all | ADR amendments 0012 D8, 0013 D4, 0013 D5 | no |
| 02 | #30 | `architecture` no longer blocks; `do-not-merge` does | yes |
| 03 | #28 | triage writes `## Post-deploy checks` | yes |
| 04 | #28 | refuter strips it; report + PR body list it | yes |
| 05 | #29 | `refute_fix` loop | yes |
| 06 | all | full validation, baselines, docs index; **stop** | — |
| 07 | #29 | post-deploy measurement (operator, after push) | — |

02, 03-04 and 05 are independent; do them in order anyway so each commit is
reviewable. 04 depends on 03's heading contract (G3) only, not its code.
