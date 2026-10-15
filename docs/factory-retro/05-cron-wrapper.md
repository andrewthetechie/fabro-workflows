# Task 05: the cron wrapper

Read `00-overview-and-contracts.md` first (contract C8). Read `ops/fabro-monitor.sh`'s
header and its `load_webhook` and `send` functions: copy their style.

## Goal

`ops/fabro-retro.sh` runs the subcommand in the scheduler container once a week and posts
its text to Discord. `make deploy-scripts` installs it in `~/bin`. Task 07 installs the
crontab line.

## Steps

1. Create `ops/fabro-retro.sh`, `#!/bin/sh`, mode 755. The header comment says what it is
   (issue #33, ADR 0026), the C8 env table, that a dry run still posts (that is the
   review), the crontab line, and that it never alerts on a single run (ADR 0004). Then
   implement C8's flow. Put `$RETRO_ARGS` unquoted on purpose, with a comment that it is
   word-split by design.
2. `ops/deploy-host.sh`: add `fabro-retro.sh` to `BIN_SCRIPTS`. Check whether anything else
   in the file lists the bin scripts (the verify step's host-matches-repo diff) and needs
   the name too.
3. `ops/README.md`:
   - a row in the contents table for `fabro-retro.sh`, after `fabro-monitor.sh`: the weekly
     retro (Monday 12:00 UTC cron), what it writes, the dry-run default, and links to
     `../docs/factory-retro/00-overview-and-contracts.md` and ADR 0026;
   - a section `## Cron — the weekly retro` after `## Cron — the health monitor`, in the
     same form as the others: the dry crontab line from C8, then the live line, one
     sentence on when to switch (after the operator has reviewed a dry-run message), and
     the by-hand command `NO_POST=1 ~/bin/fabro-retro.sh`.

## Tests (`ops/tests/test_fabro_retro.py`, `unittest`, new)

Model it on `ops/tests/test_fabro_auto_merge_switch.py`: put stub `docker` and `curl`
scripts first on `PATH` in a temp directory. The `docker` stub records its arguments and
answers by its first argument and the container:

- `docker exec fabro-scheduler ...`: prints `$STUB_OUT` and exits `$STUB_RC`;
- `docker exec fabro-fabro-1 cat /storage/secrets/discord_webhook_url`: prints a fake URL,
  or nothing when `$STUB_NO_HOOK` is set.

The `curl` stub saves the request body to a file and exits `$CURL_RC` (default 0).

Cases:

- Default env: the subcommand gets `retro --last-week --dry-run`; the posted JSON's
  `content` is the stub output; exit 0.
- `DRY_RUN=0`: no `--dry-run`.
- `RETRO_ARGS="--week-of 2026-10-08 --cap 1"`: both words reach the subcommand.
- `STUB_RC=3` with output: the post starts `retro failed: exit 3; see` and contains the
  output; the wrapper exits 3.
- `STUB_RC=1` with no output: the post is the failure line alone.
- A 3000-character output: the posted content is 2000 characters.
- `NO_POST=1`: no `curl` call; the message is on stdout.
- `STUB_NO_HOOK=1`: a warning on stderr, exit 1.

`make check` already runs `python3.11 -m unittest discover -s ops/tests`.

## Done when

`make check` passes. One commit:
`feat(ops): fabro-retro.sh posts the weekly retro to Discord (factory-retro 05, #33)`.
