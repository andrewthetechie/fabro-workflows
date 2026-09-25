# A4 · Deterministic diff-hygiene gate: tamper and erosion counters

**Status:** proposed. **Axis:** quality. **Effort:** S–M. **Feasibility:** high.
**Depends on:** nothing. **Pairs with:** A3 layer 2 (same node position), A5 (the refuter
reads its output).

## Problem

wsff.md's central claim is that *"there is no penalty for eroding codebase maintainability"*.
Models wrap everything in try/except, cast away the type system, and weaken tests to get a
green run. no_human stopped 44 attempts on test tampering alone, and it **counted** them
mechanically before any reviewer ran.

Here, the only thing that looks for this is reviewer prose (`reviewer.md.j2`,
`standards.md.j2`), and the reviewers are the same model family as the coders.
`fix_gate` (`review-merge.fabro:257-306`) checks that each error finding is *cited*, not that
the tests still test anything.

## Design

One command node, `hygiene`, in `_shared/review-merge/`, after `validate` succeeds and before
`deliver` → `merge_gate`. It counts over **added lines only**, from
`git diff -U0 origin/main...HEAD`:

**Tamper counters (any non-zero blocks auto-merge):**

| Counter | Detects |
|---|---|
| `tests_deleted` | a deleted file matching the repo's test globs (`tests/`, `*_test.*`, `*.test.*`, `*.spec.*`, `test_*.py`) |
| `skips_added` | `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `it.skip`, `describe.skip`, `xit(`, `.only(`, `#[ignore]` |
| `asserts_removed` | a removed `assert`/`expect(`/`assert_eq!` line with no added assertion in the same hunk |
| `tautologies_added` | `expect(true)`, `assert True`, `assert 1 == 1`, `expect(x).toBe(x)` |

**Erosion counters (reported; block above a threshold):**

| Counter | Pattern |
|---|---|
| `broad_except` | `except Exception`, bare `except:`, `catch (e) {}` with an empty body |
| `type_escape` | `# type: ignore`, `as any`, `as unknown as`, `@ts-ignore`, `@ts-expect-error`, `cast(Any` |
| `lint_disable` | `eslint-disable`, `noqa`, `#[allow(`, `# pylint: disable` |
| `rust_unwrap` | `.unwrap()` / `.expect(` in non-test Rust |
| `todo_unlinked` | `TODO`/`FIXME` with no `#<n>` or issue URL on the line |

Output: `/tmp/fabro/hygiene.json` with the counters and up to 20 `file:line` samples. New
`merge_gate` check: block when any tamper counter > 0, or any erosion counter exceeds its
threshold (start at 3 per PR, per counter). The block reason lists the samples. As the
invariant requires, write both `merge_block_reason` and `needs_human_reason`.

**Override:** a line in the issue body, written by a human and outside the triage-decisions
section (`fabro:allow-hygiene tests_deleted`), whitelists one counter for that issue. Nothing
an agent writes can override.

**Per-repo config:** test globs and thresholds in the target repo's `.fabro/hygiene.toml`,
with the defaults above when the file is absent, so a repo with an unusual layout adjusts
without a graph change.

## Why a gate, not a reviewer instruction

A reviewer can be talked out of a finding: *"the test was obsolete."* A counter cannot. The
false-positive cost is a blocked auto-merge, and a human merges by hand, which is the cheap
direction to be wrong in.

## Verification

`ops/test-task-gates.sh` with real git: one fixture per tamper counter, one clean refactor
that moves a test without weakening it (which must pass), and one erosion case over the
threshold.

## Open questions

1. Block or only report erosion at first? Suggest **report-only for two weeks**, read B1, then
   switch on the block. Tamper blocks from day one.
2. `asserts_removed` hunk matching is heuristic. Its fixtures define the contract.
