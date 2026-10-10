# 02. `ops/fabro-export-runs.sh` (#14, part 1)

## Outcome
One command lists a window of runs and exports each run's events, with no hand-written
`curl` and no `while read` loop.

## Why
Each of the three rechecks rebuilt this step. `GET /runs` returns 20 runs unless it is
paged, and the working parameters were found by probing. A `while read` loop around
`docker compose exec -T` exported one run and stopped, because the command read the rest of
the list from stdin. Both cost a round of work on 2026-10-09.

## Change
1. Write `ops/fabro-export-runs.sh` exactly to C2. Put the remote listing in one function, so
   that a test can replace `SSH` with a stub that serves fixture pages.
2. Read the token inside the remote command:
   `docker exec fabro-fabro-1 cat /storage/server.dev-token`. Never print it and never pass
   it as an argument on the Mac.
3. Add the script to `ops/README.md`'s contents table. It is a Mac tool, so `make deploy`
   does not copy it.

## Acceptance
- `--since 2026-10-08T05:34Z --until 2026-10-10T00:20Z` lists the 24 runs that
  `result-2026-10-09.txt` names as created in that window, with the right repositories and
  issue numbers.
- A second run of the same command exports nothing new and prints the same index.
- An `OUTDIR` inside this repository is refused before anything is written.
- One run that fails to export (for example a bad id from the stub) leaves a `.err` file,
  the others are exported, and the exit is 1.

## Tests
`ops/tests/test_fabro_export_runs.py` (unittest, run through `subprocess`): a stub `SSH`
script serves two pages of fixture JSON and fixed event lines. Cover paging until
`has_more` is false, the stop at `--since`, the `--until` and `--workflow` filters,
`--status terminal`, the resume, the `.err` path and the refusal inside a work tree.

## Depends on
Nothing.
