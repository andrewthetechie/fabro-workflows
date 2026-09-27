# 04 · Build `fabro-io` once and put it in all four images

Read `00-overview-and-contracts.md` first. Read `ops/profile-images/README.md` and the part
of `build-images.sh` that copies `fabro-code` into each context (docs/code-context task 02).

## Change

1. **One builder, before the per-profile builds.** In `build-images.sh`, before the loop
   over profiles, build `ops/fabro-io/` in a container:
   `rust:1.98.1-trixie`, `rustup target add x86_64-unknown-linux-musl`, `musl-tools`,
   `cargo test` (the whole suite; a failure stops the build and leaves yesterday's images
   in use), then `cargo build --release --locked --target x86_64-unknown-linux-musl`. Use a
   named volume for `~/.cargo/registry` and `target/`, so the nightly build is incremental.
   Copy the binary to each context root beside `fabro-code`.
2. **Each Dockerfile** gets `COPY fabro-io /usr/local/bin/fabro-io` next to the `fabro-code`
   line. No other change: the binary has no runtime dependencies.
3. **The image gate.** In `verify_image`, add an offline check (`--network=none`):
   `fabro-io version` prints the expected version, and `fabro-io stage` with
   `FABRO_NODE_ID=probe` and `FABRO_IO_MANIFEST='{"version":1,"min_binary":"0.0.0","phases":{},"workflows":{}}'`
   exits 0. A broken binary would block every agent stage in every run (ADR 0016,
   "Consequences"), so this check must fail the build and leave the old tag.
4. **README.** Add `fabro-io` to the list of tools that every image has, with one sentence
   that points to `docs/stage-io/`.

## Also

`ops/test-task-gates.sh` runs inside every profile image. Add a section that runs
`fabro-io version` and exits with `SKIP` when the binary is not on `PATH` (on the Mac).

## Acceptance

- `DRY_RUN=1 ./build-images.sh` assembles four contexts, each with `fabro-io`.
- On the host, `./build-images.sh` builds all four images, and each one passes the new
  check. `docker run --rm fabro-ts:local fabro-io version` prints the version.
- The build time added to a nightly build with a warm cache is under 60 s. Record it.
