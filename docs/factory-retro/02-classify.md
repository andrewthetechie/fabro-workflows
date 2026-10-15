# Task 02: failure classes

Read `00-overview-and-contracts.md` first (contracts C1, C1a, C2).

## Goal

`retro.classify(rows)` turns one window's `ScorecardRows` into its failure classes, and
`over_threshold` says which ones count. Pure code: no I/O, no clock.

## Steps

1. **Extract `merged_by_workflow` (C1a).** In `scorecard.py`, `compute` computes
   `by_workflow` inline from `released_merged` and `fact`. Move that expression into a
   public function `merged_by_workflow(runs, fact)` above `held_bucket`, and call it from
   `compute`. No behaviour change: every existing test in `tests/test_scorecard.py` must
   pass unchanged.
2. **Create `ops/scheduler/src/fabro_scheduler/retro.py`.** Write a module docstring in the
   style of `scorecard.py`'s: what the retro is (issue #33, ADR 0026), that this module is
   pure, and the four kinds. Then add:
   - the constants `KINDS`, `EXCLUDED_ENDING_NODES = frozenset({"close_noop", "demote_lane"})`,
     `POLICY_HELD_BUCKETS` (the seven buckets in C1), `DEFAULT_MIN_RUNS`,
     `DEFAULT_MIN_BOX_HOURS`, `DEFAULT_CAP`;
   - `slug(text)` and the key regex `KEY_PATTERN` (C1);
   - the dataclasses `RunRef`, `ClassStat`, `Classified` (C1);
   - `classify(rows) -> Classified`;
   - `over_threshold(stat, *, min_runs, min_box_hours)` and
     `ranked(classes: Iterable[ClassStat]) -> list[ClassStat]` (C2).
3. **`classify`, step by step.**
   - Index summaries by `run_id` (all statuses). A run is summarized when its status is
     `ok` or `missing`. Count the others (no row, or `error`) in `unsummarized`, and skip
     them in every kind.
   - `ending`: follow C1's table row exactly. The node is `terminal_node` only when the
     status is `ok`. Otherwise it is `unknown`; that covers a `lost` run, whose summary is
     `missing`.
   - `held`: build `pr_runs` as `compute` does (`pr_lookup == "found"` and `pr_number` set,
     keyed by `(repo, pr_number)`). For each PR where `merged_by_workflow` is false, take
     `held_bucket(fact)`. Skip `POLICY_HELD_BUCKETS`. Skip the PR when none of its runs is
     summarized. Its runs are the summarized ones that opened it, and `events` counts PRs.
   - `timeout` and `failover`: from `rows.stages` and `rows.failovers`, summarized runs
     only. `events` is the summed `timeouts`, or the summed `count`.
   - `RunRef.ending` is `scorecard.ending(row)` and `RunRef.lease_ms` is
     `scorecard._lease_ms(row)`, from that run's `run_history` row.
   - Keep the samples for `held:other` only, as raw reasons for now. Task 03 makes them
     safe; leave a `# made safe by task 03` comment where they are collected.
   - Every key must match `KEY_PATTERN`. Assert it in the code, because a key that does not
     match would never be found again by its marker.
4. Do not touch `app.py` or the store.

## Tests (`ops/scheduler/tests/test_retro.py`, new)

Copy the `store`, `run`, `facts` and `summary` fixtures from `tests/test_scorecard.py`.

- `slug`: `"diff hygiene"` → `diff-hygiene`, `"kimi/kimi-k3"` stays, `"  "` → `unknown`,
  `"CI"` → `ci`.
- `ending`: one row each for a deterministic failure at `improve`, `mark_stuck` with no PR,
  `close_noop` (absent), `demote_lane` (absent), a cancel (absent), `terminated`, a `lost`
  run with a `missing` summary (`ending:unknown:lost`), and `pr_lookup = failed` (absent).
- `held`: a refuter hold is `held:refuter`; an architecture hold and a `queued` PR are
  absent; a PR merged by the workflow is absent; a PR opened by two runs puts both runs in
  the class with `events == 1`; `held:other` keeps its raw reason in `samples`.
- `timeout` and `failover`: two runs with `improve` timeouts make one `timeout:improve` with
  `events` equal to the summed timeouts; a `kimi/kimi-k3 → zai/glm-5.3 access_denied`
  failover keys as `failover:spec:kimi/kimi-k3:access_denied`.
- An unsummarized run is in no class and counts in `unsummarized`.
- One run in a timeout class and a held class contributes its full lease to both (D12).
- `over_threshold`: 3 runs and 0.5 box-hours is over; 2 runs and 4.0 box-hours is over; 2
  runs and 3.9 box-hours is not. `ranked` breaks a box-hours tie by key.
- Every key produced in these tests matches `KEY_PATTERN`.

## Done when

`uv run pytest` in `ops/scheduler/` and `make check` pass. One commit:
`feat(scheduler): group a window's runs into failure classes (factory-retro 02, #33)`.
