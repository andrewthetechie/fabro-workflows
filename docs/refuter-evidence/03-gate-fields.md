# 03: `refute_gate` reports two new fields

## Steps

`refute_gate` (`review-merge.fabro`, about line 724) adds two keys to **every**
`context_updates` object it prints (C4):

- `refute_unverifiable_only` (boolean): true exactly when the verdict is `fail`, `NM` (the
  number of `not_met` criteria) is 0, and `DF` (the number of defects) is 0. Otherwise
  `false`.
- `refute_ci_credited` (integer): the number of criteria with `status == "met"` whose
  `evidence` contains `ci.sh`. In jq:
  `[.criteria[] | select(.status == \"met\" and ((.evidence // \"\") | test(\"ci[.]sh\")))] | length`.
  (Already escaped for the `.fabro` file; note `[.]`, never `\.`.)

In the `invalid` branch (twice-invalid output), emit `false` and `0`. Do not change any
existing key, route or edge. Update the comment block above `refute_gate` with one sentence
naming the two fields and saying the scorecard reads them (#40).

## Tests

`ops/test-task-gates.sh`, `refute_gate` fixture (about line 4305). Add cases:
- all met with an evidence string containing `.fabro/ci.sh:13`: `refute_ci_credited` = 1,
  `refute_unverifiable_only` = false;
- one `unverifiable` criterion, no defects: verdict `fail`, `refute_unverifiable_only` = true;
- one `unverifiable` and one `not_met`: `refute_unverifiable_only` = false;
- one `unverifiable` and one defect: `refute_unverifiable_only` = false;
- the invalid path: `false` and `0`.

Use the fixture's existing helpers (`rg_verdict`, `lastjson`). Add a small helper the same
way if you need to read the other keys.

## Done when

`make check` passes. Commit:
`feat(review-merge): refute_gate reports unverifiable-only fails and CI-credited criteria (refuter-evidence 03, #40)`.
