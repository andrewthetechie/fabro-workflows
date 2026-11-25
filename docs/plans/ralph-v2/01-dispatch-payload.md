# Task 01: the dispatch payload is one package plus `_shared/`

Read `00-overview-and-contracts.md` first (A1, C9). Read
`ops/scheduler/src/fabro_scheduler/workflow_version.py` whole, `ops/scheduler/tests/test_workflow_version.py`,
and the "fetch the tree" step of the three fire scripts.

## Why

A workflow version may be at most 2 MiB of compact JSON. Every dispatch uploads the whole `.fabro/`
tree: 114 files and 1,993,895 bytes on 2026-11-24. `build_version_payload` raises above the limit, for
whatever workflow is being dispatched. Adding `ralph-v2` to that tree would stop the scheduler.

## Steps

1. **`workflow_version.py`**
   - Add `package_paths(paths: Sequence[str], entrypoint: str) -> list[str]`: the entrypoint must match
     `workflows/<package>/<file>` (raise `WorkflowVersionError` naming it otherwise). Keep a path that
     starts with `workflows/<package>/` or `workflows/_shared/`. Keep the order.
   - `build_version_payload` filters with it before reading any file, so an unreadable file in another
     package can no longer fail this dispatch. The limits, the errors and the log line stay. The
     "entrypoint is not in the tree" error stays and is checked before the filter.
   - Rewrite the docstring paragraphs that say the version is the whole tree (the module's, the
     `ENTRYPOINTS` comment's, `FabroTree`'s, `VersionCache`'s): the version is the entrypoint's package
     and `_shared/`, at the commit's content. The cache key does not change.
   - `read_box_stages` is unchanged: it reads `workflow.toml` from the clone, not from the payload.
2. **The three fire scripts**: `ops/fabro-fire-backlog.sh`, `ops/fabro-fire-ralph.sh`,
   `.fabro/workflows/backlog/scripts/fire-pr-review.sh`. After the `find` line that writes
   `$tmp/paths.all` (rename the output of that `find`), write `$tmp/paths` with
   `grep -E "^workflows/($PKG|_shared)/" "$tmp/paths.all"`, where `PKG` is set next to `ENTRYPOINT` as
   `PKG="${ENTRYPOINT#workflows/}"; PKG="${PKG%%/*}"`. Every later check stays as it is. Update the
   comment above the `find` in each: it still enumerates and never lists files by hand.
3. **`ops/fabro-fire-ralph.sh` serves both packages.** Add `WORKFLOW="${WORKFLOW:-ralph}"`, accept only
   `ralph` or `ralph-v2` (`die "WORKFLOW must be ralph or ralph-v2, got '$WORKFLOW'"`), set
   `ENTRYPOINT="workflows/$WORKFLOW/workflow.fabro"`, and use `$WORKFLOW` for the run's `workflow` label
   in the RunIntent (a `--arg`). The automation lookup keeps `ralph` and `backlog` rows. Document the
   variable in the header's Env list and keep the usage line.
4. **The offline gate.** In `ops/check.sh`, after the `tomllib parse` step, add a step
   `say "workflow version payload, per package"` that runs a new `ops/check-payload.py`
   (standard library only). For every `.fabro/workflows/*/workflow.fabro` it builds the same payload
   (the package and `_shared/`, `ensure_ascii` on, compact separators) and fails when it is over
   **1,500,000 bytes** or any file is over 512 KiB. It prints one line per package with its byte count.
   Test it in `ops/tests/test_check_payload.py`: a package under and over the limit, and `_shared` counted.

## Tests

`test_workflow_version.py`: the filter keeps the package and `_shared`, drops `_io` and other packages;
a non-UTF-8 file in another package no longer raises, and one in this package still does; an entrypoint
outside `workflows/` raises; every entry of `ENTRYPOINTS` builds from this repository's own tree and is
under 1,500,000 bytes; and **every `import="..."` target and `prompt="@..."` of each of those graphs is
in its payload** (parse with a regex over the graph text, resolve against the graph's directory).
`ops/gate_tests/test_ops_scripts.py`: follow how it runs the fire scripts with stubs today. In
`DRY_RUN=1`, `version.json` holds no path of another package and holds `_shared`; `WORKFLOW=ralph-v2`
changes the entrypoint and the label; `WORKFLOW=backlog` dies with the sentence above.

## Done when

- `make check` passes and prints every package's byte count.
- The report gives the byte counts (expected: the largest is `backlog`, about 730,000).
- Commit: `fix(dispatch): a run uploads its own package and _shared, not the whole tree (ralph-v2 01)`.
