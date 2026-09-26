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
lines per hunk. The fixtures in `ops/test-task-gates.sh` are the contract, and a pattern
change starts as a fixture.

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
- `merge_gate` has fifteen checks (1 to 14, and 2b).
- The spec reviewer in the merge phase (`spec.md.j2`) still reads `linked_issues.json`,
  which has no issue text (D4). That is a separate defect and it is not fixed here.
  `refute_issues.json` is the file it should read.
