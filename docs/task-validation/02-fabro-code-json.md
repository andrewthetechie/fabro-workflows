# 02. `fabro-code tests --json` and `fabro-code list-tests`

## Outcome
`fabro-code` answers C2: for each changed file, the tests and conftests in its
reverse-import closure, and the tests under each of those conftests. It also lists every
tracked test file.

## Why
D4 and D8. `task-plan` needs a per-seed attribution (for `untested` and `why`), the
conftests the walk reaches (for the conftest rule), and an uncapped answer. The existing
`tests` verb flattens all three into one capped list. `TEST_RE` is defined only in this
wrapper, so the classification stays here.

## Change
1. `ops/profile-images/fabro-code`:
   - `tests_()` accepts a leading `--json`. The plain form is unchanged byte for byte: `code_tests` and its tests depend on it.
   - For `--json`, run the recursive query once per seed, with the same depth limit and the same `$TRUST` filter. Classify each closure path with `TEST_RE`, and a conftest as basename `conftest.py`.
   - For each conftest, list `git ls-files -- <dir>` filtered by `TEST_RE`.
   - Look up `indexed` in the `files` table.
   - Emit the C2 object with `jq -n`. No `cap_out`.
   - Exit codes per C2. `require_index` and `q` already exit 2.
2. Add a `list-tests` verb: `git ls-files | grep -E "$TEST_RE"`. It needs no index, and exits 0 even when it finds nothing. Add it to the `usage` line.
3. Keep the script POSIX `sh`: the images run it under dash.

## Acceptance
- In a `fabro-python-node` image container (`ssh andrew@10.10.0.32`, `docker run` with a clone
  of womens-fantasy-sports mounted, then `fabro-code index`):
  - `fabro-code tests --json backend/app/api/routes/<a route file>` lists at least one test under `backend/tests/` in `tests` or through `conftest_tests`.
  - `fabro-code tests --json frontend/src/<a component with a .test.tsx>` lists that test.
  - `fabro-code tests --json README.md` returns `indexed: false` and empty arrays, exit 0.
  - With `.codegraph/` deleted, it exits 2.
- `fabro-code tests <same file>` prints exactly what it printed before the change.
- `fabro-code list-tests` in the same clone prints 357 lines (the count on 2026-10-14; any
  difference must match `git ls-files | grep -E` with the same regex).
- Record the commands and outputs in `docs/task-validation/02-evidence.md`.

## Depends on
Nothing. It reaches the images in task 06.
