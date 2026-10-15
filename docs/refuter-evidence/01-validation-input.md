# 01: `validate` records the tree; `refute_prep` writes the `validation` input

## Steps

1. `validate` (`review-merge.fabro`, ~385-421): as the first command of its script, add
   `rm -f /tmp/fabro/validated_tree`. On the **skip** path (the `if` block that prints
   `validate: skipped, ...`), before `exit 0`, write `$T final` to
   `/tmp/fabro/validated_tree` (`$T` already holds the tree hash there). On the **green run**
   path (the final `else` branch, where `RC` is 0), write `<HEAD^{tree}> merge` to
   `/tmp/fabro/validated_tree` with `git rev-parse 'HEAD^{tree}'`. On the red path, write
   nothing. Update the comment block above the node: one or two sentences saying it records
   the tree it passed for `refute_prep` (#40).
2. `refute_prep` (`review-merge.fabro`, ~575): add `$R/refute_validation.md` to the existing
   `rm -f` list at the top. Before the final `echo 'refuter inputs: ...'` line, add the
   logic in C2: read `VT`/`VS` from `/tmp/fabro/validated_tree`, compare `VT` against
   `HEAD^{tree}` and the worktree against `git diff --quiet HEAD`, and write
   `refute_validation.md` (the `final`/`merge` sentence, or the no-validation sentence,
   then the quoted `.fabro/ci.sh`). Keep the `|| true` guards (the script runs `set -e`).
   Add a sentence to the comment block above `refute_prep` saying it writes
   `refute_validation.md` (#40).
3. Manifest (`.fabro/workflows/_io/manifest.json`): add a `validation` input as the **last**
   element of the `refute` stage's `inputs` array, after `hygiene` (C3). Then run
   `python3 ops/fabro-io-manifest.py generate` and `python3 ops/fabro-io-manifest.py check`.

## Tests

`ops/test-task-gates.sh`:

**Merge-skip fixture** (about line 7752, "the merge phase skips a tree Final validation
passed"):
- the skip path writes `validated_tree` as `<tree> final`;
- a run whose tree does not match writes no `final` line;
- a red run leaves no `validated_tree`.

If that fixture's harness cannot reach the green run path without a real `ci.sh`, add a stub
`ci.sh` that exits 0 in the fixture repo, following how the fixture already stubs things.

**`refute_prep` fixture** (about line 4211):
- `validated_tree` matches HEAD's tree with `final`: the file contains `Final validation
  passed on this exact tree`;
- it matches with `merge`: the file contains `The merge phase validate passed`;
- it is absent: the file contains `No validation is recorded`;
- it names a different tree: the file contains `No validation is recorded`;
- the file contains the text of the fixture's `.fabro/ci.sh` (create one in the fixture
  repo);
- a stale `refute_validation.md` from an earlier run is replaced, not appended to.

Read how the existing checks there use `check`, `extract_from`, `$T` and `sed
"s#/tmp/fabro#$T#g"` and copy that pattern exactly.

## Done when

`make check` passes. Commit:
`feat(review-merge): the Refuter's validation input names what passed on this tree (refuter-evidence 01, #40)`.
