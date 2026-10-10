# 04. Ship it in the images

## Outcome
Every profile image carries `fabro-io` 0.4.0 and a `fabro-test` shim. The image build
checks each target repository's `test.toml`. `min_binary` is raised to 0.4.0.

## Why
D8. A target repository's `ci.sh` that calls `fabro-io test-env` fails every `validate`
on an image without it. A broken `test.toml` should fail the image build, not a run.

## Change
1. `ops/profile-images/`: install `fabro-test` (`#!/bin/sh` / `exec fabro-io run-tests "$@"`)
   beside `fabro-code` in each Dockerfile.
2. `build-images.sh`: in the per-repository contract check, run
   `fabro-io test-env --check --root "$src"` when `$src/.fabro/test.toml` exists. Fail the
   image the way a failed `setup.sh` does.
3. `make deploy-images`. Confirm with `docker run --rm <image> fabro-io --version` for each
   image.
4. Then raise `min_binary` to 0.4.0 in `_io/manifest.json`, run
   `python3.11 ops/fabro-io-manifest.py generate` and `check`, and push.

## Acceptance
- Every image answers `fabro-io 0.4.0` and `fabro-test --help`.
- A deliberately broken `test.toml` in a scratch clone fails `build-images.sh`'s check.
- The first `backlog` run after the push passes `io-stage` on every agent stage.

## Depends on
03.
