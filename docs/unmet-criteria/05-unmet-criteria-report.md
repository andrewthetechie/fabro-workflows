# 05: the Unmet criteria section (U13, C5)

## Changes

`_shared/review-merge/review-merge.fabro`, `render.jq`: the C5 section after the axis table.
It reads `$RF` (already bound from `refute.json`). It is not limited to one report kind:
`merged`, `queued`, `complete`, `blocked` and `needs-human` all render it.

## Tests (`ops/test-task-gates.sh`)

Find the render tests (`grep -n render_comment ops/test-task-gates.sh`). Add:

- a `refute.json` with one `not_met` and one `met`: the section lists only the `not_met`
  entry;
- all `met`: the section reads the None line;
- no `refute.json`: the section reads the did-not-run line.

Compile the jq program separately when you edit it: `sh -n` is blind inside it.

## Done when

`make check` passes.
