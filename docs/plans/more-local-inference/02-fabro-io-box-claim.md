# Task 02: `fabro-io box-claim`

Read `00-overview-and-contracts.md` first (D5, D6; C1-C3). **Invoke `/ms-rust`.** Read
`ops/fabro-io/src/stage.rs` and `ops/fabro-io/src/manifest.rs`: `stage` is the template for a
hook subcommand that must never fail its stage.

## Goal

A subcommand that, run as a `stage_start` hook, frees the run's box of background work before
a box stage starts. It is fast when there is nothing to do, and it always exits 0.

## Steps

1. **`ops/fabro-io/src/boxclaim.rs`**, dispatched from `src/cli.rs` as `"box-claim"`.
   Implement C3 exactly:
   - manifest lookup by `FABRO_NODE_ID` under `FABRO_WORKFLOW`;
   - the `"plan-arm"` rule from `/tmp/fabro/issue.json` `.number`;
   - `/tmp/fabro/box`;
   - the run list, filtered on `labels.lane` and `labels.box`;
   - cancels;
   - the `/health` poll, until two consecutive idle reads;
   - the 80 s overall deadline and 5 s per-call timeouts;
   - the single stderr summary line.
2. **Exit 0 on every path.** Never print to stdout: fabro reads a hook's stdout as its
   decision. Panics must not escape. Wrap the body so that a panic logs and exits 0.
3. Use the HTTP client already in the crate's dependencies (see `flakeradar.rs`). Do not add
   one. Keep the run-list paging to one page of 100: there are at most a handful of active runs.
4. **Pure core, thin shell.** Put the decisions in pure functions:
   - whether to act,
   - which runs to cancel,
   - when the box is idle,
   - parsing `FABRO_BOX_HEALTH`.

   The `run()` function only does I/O around them.
5. **Tests** in `ops/fabro-io/tests/box_claim_tests.rs`, with fixtures:
   - a non-box stage returns at once, with no I/O;
   - `"plan-arm"` with an even `(n // 2)` does nothing, and with an odd one acts;
   - a missing `/tmp/fabro/box` or an unknown pool does nothing;
   - the filter keeps only `lane=background` and `box=<pool>` runs;
   - a `FABRO_BOX_HEALTH` parse with spaces, a trailing comma and an unknown pool;
   - the idle rule needs two consecutive reads.

   For the HTTP shell, use a local test server if the crate already has one in its dev
   dependencies; otherwise test the pure parts only and say so in the commit.

## Done when

- `CHECK_CARGO=always make check` passes, and `fabro-io box-claim` appears in `fabro-io help`.
- Run with no environment at all, it exits 0 and prints one stderr line.
- Commit: `feat(fabro-io): box-claim cancels background runs on the run's box (more-local-inference 02)`.
