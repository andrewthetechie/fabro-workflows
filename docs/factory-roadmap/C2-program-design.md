# C2 · Program-design step for large tasks

**Status:** proposed (rev 2 P2-9). **Axis:** quality. **Effort:** M. **Feasibility:** high.
**Depends on:** nothing. **Better with:** A5 (the refuter checks the diff against the
declared interface).

## Problem

wsff.md: *"30 minutes of planning saves hours of review."* Between "task spec" and "code"
sits the program design: types, method signatures, the file-tree diff, and the call stack of
any control-flow change. Each is *"a decision you'd otherwise be making implicitly during code
review, at the most expensive possible time to change your mind."* `decompose` and `improve`
stop at user story, acceptance criteria and files. The coder then invents the interface, the
reviewers argue about it, and the rework ladder pays for it.

## Design

For tasks above a size threshold (the existing `covers` count: more than 2 covered parent
criteria, or `size ≥ m` once B2 exists), produce `/tmp/fabro/program_design.json` before the
coder:

- **Signatures:** new or changed functions, classes and types, with the interface and no body.
- **File-tree diff:** `+ src/foo/client.ts`, `~ src/foo/route.ts`.
- **Call stack:** for any control-flow change, the path from entry point to the new code.
- **Interface justification:** one line in the deep-module vocabulary (what the module hides,
  what callers get).

A gate validates the shape (names files, types and at least one signature). The coder prompt
gains "implement this design; if it is wrong, say so in your summary rather than silently
diverging." A5 checks that the diff matches the declared signatures.

## The design question to settle first

Which node writes it? `improve` now runs on the run's **local box** (`9b7f5ff`), so putting
design there inverts Aider's architect split (strong model designs, cheaper model edits).

- **Option 1:** `decompose` (hosted `glm-5.3`) emits the design per large task. It is hosted
  and runs once per issue, but it has not seen the checkout as it will be when task *k* starts.
- **Option 2:** a new hosted node, `design`, between `improve` and `coder` for large tasks only.
  It sees the current checkout and uses a strong model, at the cost of one more hosted stage per
  large task.

Recommendation: **option 2**, scoped to large tasks, because it matches both "design against
the real tree" and the architect split. It is not a hosted `improve` (which the operator
declined), because it is a separate stage that runs only for large tasks and leaves `improve`
on the box.

## Verification

B1: compare the rework tier reached, and review findings per task, on large tasks before and
after.
