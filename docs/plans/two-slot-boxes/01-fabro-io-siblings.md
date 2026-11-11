# Task 01: `box-claim` and `box-guard` judge sibling pools

Read `00-overview-and-contracts.md` first (F2–F4, D1, D2, D4, D5, C1, C3). Invoke `/ms-rust`.
Read `ops/fabro-io/src/boxclaim.rs` whole and `ops/fabro-io/tests/box_claim_tests.rs`, and
`ops/fabro-box-claims.py` with `ops/tests/test_fabro_box_claims.py`.

## Goal

On a box that two pools share, a box stage never waits on the sibling's work, a claim cancels
the background run whichever sibling it was dispatched under, and the guard yields to either
sibling's coder loop. A one-slot box behaves as before (D4 has the one intended difference).

## Steps

1. **`siblings` (C1).** Add `pub fn siblings(map: &BTreeMap<String, String>, pool: &str) ->
   Vec<String>` next to `parse_box_health`. Test the three C1 cases.
2. **`lane_runs_on`.** Change the box filter to "the `box` label is in a given sibling set".
   Take `&[String]` in place of `box_name: &str` and update every caller. Test: a background
   run labelled `coders-b` is found by a claim from `coders-a` when both share a URL.
3. **`claim`.**
   - Read the map once, compute the siblings of `read_box()`'s pool, and cancel background
     runs on all of them.
   - Find the **other** siblings' coder runs (lane `coder`) in the same active-runs response,
     and reuse the stage logic the guard uses (`running_stage`, `is_box_stage`) to decide
     whether one is in a box stage. If reading stages costs extra calls, count them against
     the existing deadline.
   - Call `wait_for_idle` only when `cancelled > 0` and no other sibling is in a box stage
     (D4). Otherwise log `idle=skip`.
   - Append `siblings=<n>` to the log line (C3).
   - Keep the decision in a pure function, e.g. `should_wait(cancelled, sibling_in_box_stage)
     -> bool`, and unit-test its four cases.
4. **`guard`.** It now collects coder runs labelled with **any** sibling pool, not only its own
   box, and passes them to `guard_clear`. Test: a guard on `coders-a` with a `coders-b` loop in
   a box stage is not clear.
5. **`ops/fabro-box-claims.py`.** Accept `idle=skip` and the optional `siblings=` field. Lines
   from before this change must still parse. Report `skip` as its own count, not as a failure.
   Add tests with one old line and one new line.
6. Do not change any manifest struct (`manifest.rs`). D18's ordering does not apply, but the
   images still deploy before the host var changes (task 04).

## Done when

- `cargo test` in `ops/fabro-io` and `make check` pass.
- One local commit, not pushed.
