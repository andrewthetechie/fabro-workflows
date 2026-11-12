# Task 02: the background lane iterates boxes, not pools

Read `00-overview-and-contracts.md` first (F1, F2, F4, D1–D3, C1, C2). Read `ops/README.md`
*The coder scheduler*, then `ops/scheduler/src/fabro_scheduler/config.py` (the box-health
parsing and `Config.box_health`), and `background.py` whole: `tick`, `_refresh_health`,
`evict`, `fill`, `_gap_minutes`, `_loop_on`, the dispatch at `coder_pool=f"{box}-bg"`, and the
page and status rows. Then read `measure.py` and `calibration.py` wherever they read a job's
`box`, and `ops/scheduler/tests/test_background*.py` (or whichever tests cover the lane).

## Goal

With `coders-a` and `coders-b` on one health URL, the lane treats them as one box (D3). With
four distinct URLs, every existing test passes unchanged.

## Steps

1. **`config.py`.** Add `box_siblings(box_health, pool)` (C1) and `boxes(box_health)` (C2) as
   module functions, with tests in `test_config.py`. Do not change `box_health`'s type or the
   env format.
2. **Health reads.** `_refresh_health` and `_reading` read each **URL** once per tick, not once
   per pool. Key the cache by the box's first pool. Readers that look up a pool resolve it to its
   box first.
3. **`_loop_on` → `_loops_on(box)`.** It returns the `Loop` for every leased sibling. Callers
   ask "is any loop on a box stage" (`any(l.on_box …)`). `_gap_minutes` takes the list and
   returns the minimum predicted gap. A sibling with no lease adds no bound.
   A sibling whose stage read failed blocks the fill, as a single failed read does today.
4. **`evict`.** For each box, cancel the background runs whose job `box` is any sibling, when
   any sibling loop is in a box stage.
5. **`fill`.** It runs once per box from `boxes()`. It skips the box when any sibling is
   drained, or when any background job runs on any sibling. It dispatches with
   `coder_pool=f"{first_pool}-bg"` and records `box=first_pool` on the job row. The C6 labels
   on the background run are `box=<first_pool>`.
6. **The page and `/api/background`.** Render one row per box, named by its pools joined with
   `+` (`coders-a+coders-b`). Keep the JSON field `box` as the first pool, and add `pools:
   [..]`, so existing readers keep working.
7. **`measure.py` and `calibration.py`.** Wherever they group by `box`, confirm that grouping
   by the first pool is still correct. Job rows from before the change carry `coders-b`, so a
   per-box report shows two box-1 rows across the cutover. Fix only what is wrong, and say
   in the commit what you checked.
8. **Tests.** Add lane tests with a two-pool box:
   - a sibling in a box stage evicts and blocks the fill;
   - two leased siblings use the minimum gap;
   - one background run per box at most;
   - a drained sibling blocks the fill;
     *(amended 2026-11-12: it does not — see D3's amendment. A drained sibling leaves the box
     in the lane; the test is `test_a_drained_sibling_leaves_the_box_in_the_lane`, and
     `test_a_box_whose_every_slot_is_drained_stays_out_of_the_lane` holds the old rule where
     it still applies. That step's rule silenced both boxes for two hours after the cutover.)*
   - one `/health` read per URL per tick.

## Done when

- `cd ops/scheduler && uv run pytest` and `make check` pass.
- One local commit, not pushed.
