# 06. Ship `fabro-code` and `fabro-io` 0.5.0 in the images; raise `min_binary`

## Outcome
Every profile image carries `fabro-io` 0.5.0, the new `fabro-code` and the new `fabro-test`.
`_io/manifest.json` requires 0.5.0.

## Why
D13. The workflow (07) and the agent guide (08) name things that exist only in the new
images. A repository's `test.toml` may use the new keys only after this lands, because 0.4.x
rejects them.

## Change
1. Push tasks 02–05 to `main`. They touch only `ops/`, which no automation reads.
2. `make deploy-images` from a clean checkout at `origin/main`. The build gate runs
   `fabro-io test-env --check` against each repository and must still pass on their unchanged
   files. The new warnings are allowed and expected: no repository declares `tests` yet, so
   every test file is unowned. Copy the warning count per repository into the evidence file.
3. In each image: `fabro-io --version` is 0.5.0, `fabro-code list-tests` runs, and
   `fabro-test --task` with no task keys prints `mode: full`, `reason: no_config`.
4. `_io/manifest.json`: `min_binary` "0.5.0". Regenerate the manifest blocks
   (`ops/fabro-io-manifest.py generate`), run `make check`, push.
5. Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`.

## Acceptance
- `make verify-host` and `make check-host` pass, with the baselines unchanged.
- One `backlog` run started after the deploy shows `task_check` in an agent stage's tool
  list (`fabro events <run> -p`).

## Depends on
02, 05.
