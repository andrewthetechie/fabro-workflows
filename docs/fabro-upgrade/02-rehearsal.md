# 02 · Rehearse the upgrade on a copy (the go/no-go gate for task 04)

## Outcome
A throwaway 0.362 server, started on a copy of today's database and overlay, proves the exact
cutover sequence of task 04 on current data: the run-history drop (C3), the catalog rewrite
(C1), the identity block (C2), then a clean start, `fabro validate` of all four packages
matching finding R4, and the model catalog resolving as finding R5. Then the throwaway server
is removed. If anything differs from the findings, task 04 does not start until the difference
is understood.

## Why
The rehearsal on 2026-09-25 found both blockers (R1, R2) on a copy. The database keeps
changing until cutover, and C2 (`[run.git.author]`) was **not** part of that rehearsal. Running
the sequence again costs about 15 minutes, and it turns task 04 into a repeat of something
that already worked.

## Read first
`00-overview-and-contracts.md`: findings R1–R5, contracts C1–C3, rule 1.

## Safety properties (keep all of them)
- A **separate** volume (`fabro-rehearsal-0362`) and a separate container
  (`fabro-rehearsal-0362`), bound to `127.0.0.1:32299` only.
- **No Docker socket mount.** The copy cannot start sandboxes, so nothing it does can reach a
  repository.
- **Do not copy `/storage/scripts` or `/storage/secrets`.** Without them no Discord hook can
  fire from the copy. The copy's automations include the enabled `arch-review` schedules, and
  it may try to fire them. Without a socket they fail harmlessly, so remove the container
  promptly.
- Any `fabro` CLI call inside the copy must use a scratch `FABRO_HOME` whose `[cli.target]` is
  `http://127.0.0.1:32276` (step 6). The copied overlay's own `[cli.target]` points at
  **production**.

## Steps (host)

1. **Volume with an online database copy** (the same method as task 01, step 3):
   ```sh
   docker volume create fabro-rehearsal-0362
   docker run --rm -v fabro_fabro-storage:/src -v fabro-rehearsal-0362:/dst alpine:3 sh -c '
     apk add -q sqlite >/dev/null
     mkdir -p /dst/db
     sqlite3 /src/db/fabro.sqlite3 ".backup /dst/db/fabro.sqlite3"
     cp -a /src/.home /src/objects /src/server.env /src/server.dev-token /dst/
     chown -R 1000:1000 /dst'
   ```

2. **Apply C3** (the run-history drop) to the copy. `run_session_records` does not exist on a
   0.354 database, so do not reference it. Use `VACUUM` too, and time it, because task 04 pays
   the same cost with production stopped.

3. **Apply C1 and C2** to `/dst/.home/settings.toml` in a helper container: the `sed` from C1,
   then append the C2 block. Keep a `settings.toml.0354` copy next to it and `diff` the two. The
   diff must show exactly the C1 hunks plus the appended C2 block.

4. **Start 0.362 on the copy.**
   ```sh
   docker pull ghcr.io/fabro-sh/fabro:0.362.0-nightly.0
   docker run -d --name fabro-rehearsal-0362 -v fabro-rehearsal-0362:/storage \
     -p 127.0.0.1:32299:32276 --env-file ~/fabro/.env --tmpfs /workspace \
     ghcr.io/fabro-sh/fabro:0.362.0-nightly.0
   sleep 45; docker ps -a --filter name=fabro-rehearsal-0362 --format '{{.Status}}'
   docker logs fabro-rehearsal-0362 2>&1 | grep -v -i 'slatedb\|slow statement' | tail -20
   ```
   Expect `Activated SQLite run history source_runs=0 … target_runs=0`, then
   `API server started`. If `[run.git.author]` is rejected, the log says so here. That is the
   one new thing this rehearsal tests.

5. **Graphs.** `rsync` this repo's `.fabro/` to the host and `docker cp` it into the copy at
   `/tmp/check`.

6. **Validate with the copy's CLI, targeted at the copy.**
   ```sh
   docker exec fabro-rehearsal-0362 sh -c 'mkdir -p /tmp/cli && printf "_version = 1\n[cli.target]\ntype = \"http\"\nurl = \"http://127.0.0.1:32276\"\n" > /tmp/cli/settings.toml'
   for w in backlog pr-review issue-triage arch-review; do
     docker exec fabro-rehearsal-0362 sh -c "cd /tmp && FABRO_HOME=/tmp/cli fabro validate /tmp/check/workflows/$w/workflow.toml" 2>&1 | tail -6
   done
   docker exec fabro-rehearsal-0362 sh -c 'FABRO_HOME=/tmp/cli fabro parse /tmp/check/workflows/backlog/workflow.fabro' | head -c 200
   ```
   The output must match finding R4 exactly, and `parse` must print JSON.

7. **Catalog resolution through the API** (the CLI `model list` needs a login on the copy, so
   use the API with the copied dev token):
   ```sh
   T=$(docker exec fabro-rehearsal-0362 cat /storage/server.dev-token)
   for q in coders long-context glm-5.3 kimi; do
     curl -s -m 10 -H "Authorization: Bearer $T" "http://127.0.0.1:32299/api/v1/models?query=$q&page%5Blimit%5D=100"; echo
   done
   ```
   Parse each line's `.data[] | [.id, .provider, .configured]`. The **configured** rows must be
   exactly finding R5's mapping. Every other provider listing those ids must be
   `configured: false`.

8. **Settings readback.** Do **not** use `GET /api/v1/settings`: on 0.362 it returns only the
   `server.*` layer (its top-level keys are `["server"]`), so it never shows `run.git.author`.
   The copy has no runs and no Docker socket, so `run.*` cannot be read back through a run
   here either. On the copy, the check is that step 4's startup log shows no overlay
   rejection with the C2 block present:
   ```sh
   docker exec fabro-rehearsal-0362 grep -A2 '^\[run.git.author\]' /storage/.home/settings.toml
   docker logs fabro-rehearsal-0362 2>&1 | grep -ci 'rejected\|invalid' # expect 0
   ```
   The real readback happens on the first production run (task 04, step 11).

9. **Tear down.**
   ```sh
   docker rm -f fabro-rehearsal-0362 && docker volume rm fabro-rehearsal-0362
   ```

## Acceptance criteria (all required before task 04)
- The copy started, with activation reporting zero runs and the API listening.
- The four validation results match R4. `parse` works.
- Model resolution matches R5.
- The overlay carries `run.git.author` and the copy started with no overlay rejection.
- The container and volume are removed, and production's `fabro-fabro-1` `StartedAt` is
  unchanged.

## If something differs
Stop. Record the log excerpt and what differed. A new overlay rejection is fixed in C1/C2 and
this task is re-run. A new migration failure means the overview's R1 is incomplete: find the
table and row the way the 2026-09-25 rehearsal did (`sqlite3` on the copy, `pragma_table_info`,
`json_each(summary_json)`), extend C3, and re-run. Decision 8 applies only if no data or
config change gets the copy to start.

## Report
The `VACUUM` duration and the resulting database size, the startup log excerpt, the four
validation outputs, the resolution table, and the step 8 output.
