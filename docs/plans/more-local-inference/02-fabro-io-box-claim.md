# Task 02: `fabro-io` reads the new fields; `box-claim` and `box-guard`

Read `00-overview-and-contracts.md` first (D5, D6, D17, D18, D23; C1-C3, C10). **Invoke
`/ms-rust`.** Read `ops/fabro-io/src/stage.rs` and `ops/fabro-io/src/manifest.rs`: `stage` is
the template for a hook subcommand that must never fail its stage.

**This is the first task of Phase 1, before task 01** (D18). Its commit is pushed and deployed
on its own, so it must not change anything under `.fabro/`.

## Goal

The deployed binary accepts the manifest fields task 01 adds, and two hook subcommands exist:
- `box-claim`, run by a coder loop before a box stage, frees the box of background work. It is
  fast when there is nothing to do, and it always exits 0.
- `box-guard`, run by a background run before each of its agent stages, stops that run when
  the box's coder loop is in a box stage.

## Steps

1. **`src/manifest.rs`.** Keep `deny_unknown_fields`.
   - `Stage` gains `box`, optional, deserialized as an enum `BoxMode { Never, Always, PlanArm }`
     from `false`, `true` or `"plan-arm"`; absent reads `Never`. Any other value is a load
     error, as unknown fields are today.
   - `Manifest` gains `coder_box_stages`, optional: workflow name → stage id → `BoxMode`.
   - Tests in `tests/` (extend the existing manifest or stage tests): a manifest without the
     fields loads as before; one with each value loads; `"maybe"` fails.
   - Bump the crate version in `Cargo.toml` to `0.9.0`. Task 01 sets `min_binary` to it.
2. **`Cargo.toml`:** add `ureq` with `default-features = false` and its `json` feature (D23).
   No TLS: every URL is `http://`. Check that the static release build still links.
3. **`src/boxclaim.rs`**, dispatched from `src/cli.rs` as `"box-claim"` and `"box-guard"`, and
   listed in `help`. Implement C3 and C10 exactly:
   - manifest lookup by `FABRO_NODE_ID` under `FABRO_WORKFLOW`;
   - the `"plan-arm"` rule: from `/tmp/fabro/issue.json` `.number` for the claim, from the
     other run's `labels.issue` for the guard;
   - `/tmp/fabro/box`;
   - the run's own id: `FABRO_RUN_ID`, else the `run_id` field of the JSON file named by
     `FABRO_HOOK_CONTEXT`, else unknown. Log which source was used, so task 12 can see it;
   - the run list (one page of `page[limit]=100`; there are only a handful of active runs),
     filtered on `labels.lane` and `labels.box`, never the run's own id;
   - cancels, where 200, 202 and 409 all count as done;
   - for the guard, `GET /runs/{id}/stages` and the running stage;
   - the `/health` reads: two consecutive idle reads for the claim, one for the guard;
   - the deadlines (claim 45 s, guard 20 s) and 5 s per-call timeouts;
   - the single stderr summary line of each.
4. **Exit codes and output.** Never print to stdout: fabro reads a hook's stdout as its
   decision. `box-claim` exits 0 on every path. `box-guard` exits 0 on every path except C10
   step 5's fallback, where it exits 2. Use no `unwrap`/`expect` outside tests. Do not try to
   catch panics: the release profile is `panic = "abort"`, and the hook scripts' trailing
   `exit 0` (C4, C10) is the backstop. Do not change the profile.
5. **Pure core, thin shell.** Put the decisions in pure functions:
   - whether to act (stage, arm rule);
   - which runs to cancel;
   - whether a coder run's current stage is a box stage (guard);
   - when the box is idle;
   - parsing `FABRO_BOX_HEALTH` and the hook context.

   `run_claim()` and `run_guard()` only do I/O around them.
6. **Tests** in `ops/fabro-io/tests/box_claim_tests.rs`, with fixtures:
   - a non-box stage, and a stage absent from the manifest, return at once with no I/O;
   - `"plan-arm"` with an even `(n // 2)` does nothing, and with an odd one acts;
   - a missing `/tmp/fabro/box` or an unknown pool does nothing;
   - the claim's filter keeps only `lane=background` and `box=<pool>` runs, and never the
     run's own id;
   - the guard's decision for: no coder run; a coder run in a hosted stage; in a box stage;
     in a `"plan-arm"` stage on each arm; between stages (no running stage) with an idle box;
   - a `FABRO_BOX_HEALTH` parse with spaces, a trailing comma and an unknown pool;
   - the claim's idle rule needs two consecutive reads.

   For the HTTP shell, add a small stub server in `tests/common/` on `std::net::TcpListener`
   that answers canned responses, rather than a new dev-dependency. Cover: a 202 and a 409
   cancel, a timeout (the stub never answers), and the guard's self-cancel.

## Done when

- `CHECK_CARGO=always make check` passes, and `fabro-io help` lists both subcommands.
- Run with no environment at all, each exits 0 and prints one stderr line.
- Nothing under `.fabro/` changed.
- Commit: `feat(fabro-io): read box fields; box-claim and box-guard (more-local-inference 02)`.
  Its body says that it must be pushed and its images deployed before task 01 is pushed.
