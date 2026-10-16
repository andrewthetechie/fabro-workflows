# Code counts test tampering, and an isolated refuter tries to disprove "done", before any auto-merge

**Status:** accepted (2026-09-26). Implementation plan: `docs/merge-gate/`. Roadmap items
`docs/factory-roadmap/A4-diff-hygiene-gate.md` and `A5-cross-family-refuter.md`.

The shared merge phase (`_shared/review-merge/`) squash-merges a PR into `main` with no
human. It is imported by both `backlog` and `pr-review`. Today the gate between the
coder and `main` is two reviewers (`standards`, `spec`), a fixer (`review_fix`) and the
thirteen checks in `merge_gate`. The reviewers and the fixer all run on GLM. None of the
checks looks at what the diff did to the tests. `fix_gate` confirms that each finding is *cited*, not that it was
fixed. A model that weakens a test to get a green run, or that says it fixed a finding
when it did not, meets no obstacle that cannot be talked out of its position.

We add two gates to the phase, between `validate` (CI green in the sandbox) and
`deliver`. The first is **diff hygiene**, a set of counters in shell. The second is a
**Refuter**, an agent that sees only the requirement and the code. Both write a file that
`merge_gate` reads, and either one can block the merge. Neither can loop the run back
to a fixer.

## D1. Counters, not a reviewer instruction, catch test tampering

A command node, `hygiene`, counts patterns over the lines that the PR **added**
(`git diff -U0 origin/<base>...HEAD`). Two kinds of counters exist:

- **Tamper counters.** These count a deleted test file, an added skip or `.only`, an
  assertion removed from a hunk with no replacement, an assertion that cannot fail, and a
  change to `.fabro/hygiene.json` itself. **Any non-zero tamper counter blocks the
  auto-merge, from day one.**
- **Erosion counters.** These count broad exception handlers, type-checker escapes,
  disabled lint rules, `.unwrap()`/`.expect(` in non-test Rust, and TODOs with no issue
  link. They are **report-only** by default. A repository can switch them to block above
  a threshold.

We chose a counter over a reviewer instruction because a reviewer can be persuaded ("the
test was obsolete") and a counter cannot. A false positive costs one blocked auto-merge,
and a human merges by hand. That is the cheap direction in which to be wrong.

The counters use heuristics: `asserts_removed` compares removed and added assertion
lines per hunk, after excusing a removed line whose exact text the PR adds elsewhere (a
moved test). Per-hunk netting alone blocked every test split across files, and with no
override that is a false block on an ordinary refactor (review fix, 2026-09-26). The fixtures in `ops/test-task-gates.sh` are the contract, and a pattern
change starts as a fixture. A second false-positive class was refined the same way: two
assertions merged into one stronger equality assertion on the same subject counted as a
removal, so an added equality assertion (`assert x == y`, `expect(x).toEqual(...)`,
`assert_eq!`/`assertEqual`/`assertEquals`) now excuses removals of the subjects it
covers in its hunk (#38, 2026-10-13).

## D2. A repository tunes the counters from its base branch, and nothing overrides a block

`.fabro/hygiene.json` in a target repository sets the test-path and exclude-path
patterns, the erosion mode (`report` or `block`) and the thresholds. When the file is
absent, the defaults apply. The counters read the file **from the base branch**, never
from the PR head, and a PR that changes the file trips the tamper counter
`config_changed`. So a PR cannot loosen the rules that judge it. An invalid file blocks
(`config_invalid`) and never falls back silently. The format is JSON because the gate is
POSIX `sh` and `jq`, and the profile images do not all have a TOML parser.

The roadmap proposed an override: a `fabro:allow-hygiene <counter>` line in the issue
body, "written by a human". **We rejected it.** Triage agents edit issue bodies, and the
run's GitHub token cannot tell a human edit from an agent edit. So the rule "only a human
can write it" cannot be enforced, and an override that looks enforced but is not is
worse than having none. The override is the human merge click.

## D3. The counters run again after every CI fix

`ci_fix_t1` and `ci_fix_t2` change code **after** `merge_gate` has approved the diff, and a
CI fixer is the stage most likely to skip a failing test. `hygiene` writes its program to
`/tmp/fabro/hygiene.sh` once, and `ci_fix_gate` runs the same file after the fix is
committed and before the push. A blocking result refuses the push and reports
`blocked`. The Refuter does **not** run again after a CI fix, because of cost. Its
verdict describes the diff as it was at `deliver`, and later CI-fix changes are covered
only by the counters and the existing scope ceiling (`merge_base_files.txt`).

## D4. The Refuter sees the requirement and the code, and nothing the other agents wrote

`refute` is an agent node after `hygiene` and before `deliver`. It never runs inside
the `validate → review_fix` CI loop, where it would pay to review diffs that CI is about
to reject. A command node, `refute_prep`, builds its inputs:

- the linked issues' titles and **bodies**, fetched with `gh issue view`. The phase's
  `linked_issues.json` comes from `closingIssuesReferences`, which carries only
  `id`, `number`, `repository` and `url`, so no agent in the phase has seen an
  issue's text until now;
- the PR title and body;
- the diff, smallest files first, cut off at about 300 KB, with the omitted files listed
  by name and changed-line count;
- `hygiene.json`.

It gets **no** reviewer verdicts, no `fix_result.json` and no coder transcript. Fabro
has no way to hide a file from an agent, so the prompt names the files it must not open.
That instruction is a convention, not something the sandbox enforces.

The instruction is to assume the work is not done and try to prove it. The output is a
checklist: each acceptance criterion `met`, `not_met` or `unverifiable` with its
evidence, plus the blocking defects, each with a one-sentence failure scenario. No score.

## D5. Code decides the verdict, and any verdict other than `pass` blocks

The agent writes no verdict field. `refute_gate` validates the file, gives the agent one
repair turn if it is invalid, and **computes** the verdict itself. The verdict is `pass`
only when every criterion is `met` and `defects` is empty. `merge_gate` check 14 blocks on
anything else: `fail`, `invalid` (twice-invalid output), or no verdict file at all (the
agent timed out or the provider was down). A Refuter that cannot answer blocks the merge,
and it never weakens the gate.

When the verdict is `fail`, the run does **not** loop back to `review_fix`. The model whose
work was refuted does not get to argue with the refutation. `deliver` still runs, so
the PR has its branch and label, and the checklist is posted as a section of the
review comment. It is posted as a plain comment and not as a "request changes" review,
so GitHub branch protection is not coupled to it.

The Refuter runs on **every** PR that reaches a green `validate`. That includes PRs
that cannot auto-merge anyway: human PRs in `pr-review`, `architecture` PRs, and PRs
with auto-merge switched off. On those PRs the checklist helps the human who merges.

## D6. The Refuter starts on `glm-5.3`, blocks from day one, and moves off GLM later

The design argues for a model family unlike the ones that wrote and reviewed the code.
This host has keys only for z.ai (GLM) and Kimi (Moonshot), and Kimi is already the
fallback of `glm-5.3`, so it is not independent of the merge phase either. The operator
chose to get the gate working first. **`.refute` starts on `glm-5.3`**, in both importing
stylesheets. It therefore inherits the existing `"glm-5.3" = ["kimi:kimi-k3"]` fallback,
because fallbacks are keyed on the requested model and a class cannot opt out.

It **blocks from day one**. The operator accepts that some correct PRs will be blocked,
and merges them by hand, while the operator learns how the Refuter behaves. On
2026-09-25, 4 of 5 dispatched PRs merged automatically. If that rate drops, look at the
Refuter's checklists first.

When the gate is shown to work, the operator will add a provider from another family
and change the `.refute` rule in both stylesheets. Any fallback for that model must also
be outside GLM. A fallback back to GLM is the single-vendor problem again.

## Not adopted

- **A seeded-defect suite** (a scratch repository of deliberately broken PRs, used to
  measure the Refuter's catch rate and false-block rate). There is no time for it now.
  It is the right tool to judge the later model change, and it belongs with the
  scorecard (`docs/factory-roadmap/B1-factory-scorecard.md`).
- **Waiting for B1.** No baseline is taken. `hygiene.json` and `refute.json` have fixed
  shapes, so a later scorecard can read them.
- **A second, stronger Refuter class for `architecture` PRs.** Those PRs never
  auto-merge, so one class serves every PR.
- **Running the Refuter on task-level reviews.** Its value is at the gate in front of
  `main`.
- **An issue-body override** (D2).

## Consequences

- Both `backlog` and `pr-review` gain four nodes: `hygiene`, `refute_prep`, `refute`
  and `refute_gate`. The validation baselines in `AGENTS.md` change.
- Every merge attempt costs one more `glm-5.3` review: up to 300 KB of diff plus the
  issue text, 15 minutes at most, with one retry.
- `merge_gate` has sixteen checks (1 to 14, 2b and 13b). Check 13b compares HEAD's tree
  with the tree the counters read, so an edit by the Refuter (an agent with write
  access between `hygiene` and `deliver`) cannot reach `main` uncounted.
- The spec reviewer in the merge phase (`spec.md.j2`) still reads `linked_issues.json`,
  which has no issue text (D4). That is a separate defect and it is not fixed here.
  `refute_issues.json` is the file it should read.

## Amendment (2026-10-11): Post-deploy checks are outside the Refuter's input (#28)

**Status:** accepted (operator, session 2026-10-11). This amends D4. The original text above
is kept as written. Plan: `docs/gate-relief/`.

**Why.** Of 18 Refuter blocks, 8 were `unverifiable` on criteria that only a deployed
environment can check, such as a sign-in on staging or a check in a real browser build.
The operator merged all 8 by hand. The Refuter cannot run those
checks, so each block was a false stop.

**Decision.** Criteria that only a deployed environment can check go under a
`## Post-deploy checks` heading in the issue body. Each item is one `- [ ]` line. The
section ends at the next `## ` heading or the end of the body (gate-relief G3).
- The Refuter never sees them. `refute_prep` strips the section from each issue before
  it writes `refute_issues.json`, and `refute.md.j2` tells the Refuter to skip such a
  heading if one survives (G4). A criterion from that section is therefore never counted
  as `unverifiable`.
- The human sees them as a checklist in the merge-phase report and in the PR body, under
  the heading "Not checked by the agents. Verify after deploy."
- An issue with no such section behaves as before. `unverifiable` still blocks (G5).
- Triage writes the section. It keeps a criterion as an acceptance criterion whenever it
  is unsure whether a sandbox can check it, because a refuter block is the safe failure.

## Amendment (2026-10-11): One fix turn after a `not_met` (#29)

**Status:** accepted (operator, session 2026-10-11, "agreed as proposed"). This amends D5.
The original text above is kept as written. Plan: `docs/gate-relief/`.

**Why.** Ten PRs got a `not_met` from the Refuter, and the operator fixed several of them
by hand. Across the same period, 50 of 183 PRs had an operator commit.

**Decision.** When `refute.json` has at least one criterion with status `not_met` and no
fix turn has run yet, the run routes once to `refute_fix`. That stage runs on the run's
coder pool and receives only the `not_met` criteria (with their evidence) and the
defects, in `refute_findings.json`. Then the run goes through `validate`, `hygiene`,
`refute_prep` and `refute` again (G6, G7, G9, G11).
- *Why this is not the refuted agent arguing.* The fixer changes code and cannot read or
  write the verdict (`refute.json`, `refute_verdict` and `refute_reason.txt` are sealed
  paths). The Refuter judges again from scratch, and only its second verdict counts.
- The counter `refute_fix_attempts` is set to 1 before the fixer runs and is never reset
  in the run (G8). A second `not_met` goes to a human, as before.
- `invalid` verdicts, `unverifiable`-only verdicts and defect-only verdicts still block
  with no fix turn (G6).
- A failed or invalid fix routes to `deliver`, where the old verdict stands and the merge
  stays blocked (G10).

## Amendment (2026-10-13): A defect-only verdict takes the fix turn too (#36)

**Status:** accepted (operator, session 2026-10-13). This amends the #29 amendment above,
whose last-but-one bullet it replaces. The text above is kept as written.

**Why.** From 2026-10-12 to 2026-10-13, 3 of the 7 Refuter blocks across the four
repositories were defect-only (womens-fantasy-sports #1983 and #1976, lawncare-saas #2816).
Each went to a human with no fix turn, although a defect is the most concrete finding the
Refuter writes: a file, a line and a failure scenario. The #29 amendment gave no reason for
excluding defects.

**Decision.** `refute_gate` takes the fix route when the verdict is `fail`, the run has
not had a fix turn, and `refute.json` has at least one `not_met` criterion **or** at least
one defect. The gate emits that as `refute_fixable`, and the edge reads it. `refute_not_met`
keeps its meaning (at least one `not_met` criterion), because the scorecard reads it.
- Everything else in the #29 amendment stands: one turn per run, the sealed verdict files,
  a fresh second verdict, and any second `fail` goes to a human.
- `invalid` verdicts and `unverifiable`-only verdicts still block with no fix turn. A code
  change cannot make a criterion the Refuter could not check verifiable.
- The gate-relief task 07 measurement (`docs/gate-relief/07-measure.md`) still reads the
  `not_met` fix turns on 2026-10-19. Defect-only fix turns get their own row, measured from
  this change's deploy for a week.

## Amendment (2026-10-14): The Refuter runs on `gpt-6-luna` for cost

**Status:** accepted (operator, 2026-10-14).

The Refuter moved from `glm-5.3` to `gpt-6.1-sol` on 2026-09-26 (`9c76544`), per D6. That held while it ran
about ten times a day. Since 2026-10-11, four coder pools and the fast lane put about 65 PRs a day through the
merge phase, and the Refuter is effectively the whole OpenAI bill: $60 spent by 2026-10-14 against a $100
budget, running at about $10 a day (about $300 a month). One Refuter visit reads about 230k cached input
tokens and writes about 2.3k output tokens, with cache writes and reasoning on top; at OpenAI's prices that
is about $0.16 a visit on Sol.

We decided that **the Refuter runs on `gpt-6-luna`**, in all three importing graphs. OpenAI prices luna at a
twentieth of Sol on every line (input $0.10 vs $2.00, cached input $0.01 vs $0.10, cache writes $0.125 vs
$2.50, output $0.50 vs $10.00 per million tokens, short context), so a visit costs about $0.008 and the
Refuter about $16 a month at today's volume. D6's rule is unchanged: luna is still a family unlike the
coder's (DeepSeek) and the reviewers' (GLM, Kimi).

Its fallback becomes `orfallback:hy4-preview` (Tencent, through OpenRouter) instead of `gpt-6-luna`. A Sol
fallback would cost twenty times as much and fail in the same OpenAI outage that sent the run to it. The
cost of hy4: in a week when Kimi's quota is spent, the spec reviewer also runs on hy4, so a luna outage in
that week makes the Refuter and the spec reviewer one family until it ends.

Consequences:
- Luna is the smaller model. Watch the Refuter's first verdicts (`./ops/fabro-scorecard.sh`: pass /
  `not_met` / other fail / invalid, fix turns, held PRs by reason) against the week before the switch
  (2026-10-12..14: 142 / 11 / 10 / 0 of 163). A pass rate that rises while operator commits or reverts on
  merged PRs also rise means luna is missing what Sol caught; revert this amendment then.
- `gpt-6.1-sol` stays in the host's catalog, unused.

## Amendment (2026-10-15): The Refuter credits CI for command criteria (#40)

**Status:** accepted (operator, 2026-10-15).

Since moving to `gpt-6-luna`, the Refuter marks **command criteria** `unverifiable`: a criterion whose
whole content is that a named command succeeds, such as "`bunx tsc --noEmit` passes". The prompt told it
not to run commands and that a green CI is not evidence, so it blocked PRs whose command criteria had
already run green on that exact tree in `./.fabro/ci.sh`. 17 PRs were held this way, and 16 were merged
by hand. Issue #40.

This amends D4: the Refuter's inputs gain a `validation` input, which says whether `./.fabro/setup.sh`
and `./.fabro/ci.sh` passed on this exact tree, and quotes `.fabro/ci.sh`. The operating-rule bullet about
green CI now applies to **behavioural** criteria only, and a new Method rule covers command criteria.

**Decision.** A command criterion is `met` when the `validation` input says a validation passed on this
exact tree, and `.fabro/ci.sh` runs the same tool (directly, or through a script or hook it calls — a
`package.json` script, a pre-commit hook) over every file this PR changes. A different config file or a
narrower scope is acceptable when it still covers every changed file. The Refuter cites the `.fabro/ci.sh`
line in `evidence`. Missing evidence stays `unverifiable` and still blocks (consistent with D5).
`.fabro/test.toml` checks are not evidence: they ran on earlier per-task trees, not on this tree.

Options 3, 4 and 5 from the issue were not adopted: option 3 (a deterministic gate that runs a command
parsed from criterion text) would execute untrusted issue text; option 4 (triage tells writers what to
file) would not fix issues already queued; option 5 (back to `gpt-6.1-sol`) is blocked by the $100 hosted
budget.

Consequences:
- `refute_gate` now emits `refute_unverifiable_only` (a fail with no `not_met` criterion and no defect)
  and `refute_ci_credited` (how many met criteria cite `ci.sh`) on every verdict, and the scheduler's
  scorecard reads them: it splits the "other" fail bucket into defects / unverifiable-only / other, and
  counts CI-credited criteria over first verdicts. This **adds to** the 2026-10-14 luna amendment's
  consequences: if unverifiable-only first verdicts stay above 5 a day across repositories for 3
  consecutive days after this change, the choice of Refuter model is reopened.

**Addendum (2026-10-16): a test that runs the command counts.** The first five Refuter visits after
the deploy passed the live check: every visit was served and found its `validation`, and no behavioural
criterion was credited to CI. One hold was false. Its criterion was "`uv run alembic check` reports no
drift" (lawncare-saas#2847, hand-merged). `ci.sh` runs `pytest tests/`, and
`tests/test_migration_drift.py` runs `uv run alembic check` in a subprocess, but the rule named only
scripts and hooks as indirect routes. The rule now also accepts a test that `ci.sh` runs when that test
runs the command itself. Before it marks a command criterion `unverifiable`, the Refuter must search the
test suites for the command. The rule about behavioural criteria does not change.
