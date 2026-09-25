# 01 · Backups, the run-history export, and the reversal runbook

## Outcome
Before anything changes, `~/fabro-archive/0354/` on the fabro host holds everything needed to
put the host back exactly as it is now: a consistent copy of the database, the settings
overlay, `.env`, the compose file, and a slim JSON export of fabro's run history. A written
reversal runbook sits next to them. Production is **not** interrupted by this task.

## Why
The upgrade drops fabro's run history (overview decision 2, finding R1) and applies migrations
that the 0.354 binary refuses to start against. Rollback is a restore, never a tag change
(`ops/README.md`, "Upgrading the server"). A restore needs a backup, and this task makes sure
it exists before task 04 needs it. The export keeps the 2026-09-19..25 baseline that
`docs/factory-roadmap/B1-factory-scorecard.md` backfills from.

## Read first
`00-overview-and-contracts.md`: decisions 2, 7, 8, contract C4, rules 1 and 3.

## Steps

All commands run on the host (`ssh andrew@10.10.0.32`). Nothing here stops a container.

1. **Record the starting point.**
   ```sh
   docker exec fabro-fabro-1 fabro --version        # expect 0.354.0-nightly.0 (d6fc85b ...)
   docker inspect -f '{{.Config.Image}} {{.State.StartedAt}}' fabro-fabro-1
   grep '^FABRO_VERSION=' ~/fabro/.env
   ```
   Put the three outputs in your task report.

2. **Create the archive directory** (mode 700):
   ```sh
   mkdir -p ~/fabro-archive/0354 && chmod 700 ~/fabro-archive ~/fabro-archive/0354
   ```

3. **Online, consistent database copy.** The database is in WAL mode, so copying the file
   while fabro runs is **not** consistent. Use SQLite's online backup from a helper container
   that mounts the live volume:
   ```sh
   docker run --rm -v fabro_fabro-storage:/src -v ~/fabro-archive/0354:/dst alpine:3 sh -c '
     apk add -q sqlite >/dev/null
     sqlite3 /src/db/fabro.sqlite3 ".backup /dst/fabro.sqlite3"
     sqlite3 /dst/fabro.sqlite3 "PRAGMA integrity_check;"'
   ```
   It must print `ok`. The file is about 1.6 GB. The host has hundreds of GB free.

4. **The slim run-history export.** Write a local SQL file and copy it over; inline quoting
   through `ssh` mangles the single quotes. The query:
   ```sql
   .mode json
   SELECT id, status, created_at_ms, started_at_ms, completed_at_ms, title,
          workflow_slug, workflow_name, repository_name, automation_id,
          diff_files_changed, diff_additions, diff_deletions,
          input_tokens, output_tokens, reasoning_tokens, cache_read_tokens,
          cache_write_tokens, total_usd_micros,
          json_extract(summary_json, '$.labels') AS labels
   FROM runs ORDER BY id;
   ```
   Run it with `sqlite3 /dst/fabro.sqlite3 < /q.sql > /dst/runs-export.json` in the same kind of
   helper container, against the **backup** from step 3, not the live file. Check it:
   `python3 -c 'import json;d=json.load(open("runs-export.json"));print(len(d))'` prints the
   run count (809 on 2026-09-25, more by the time you run it).

5. **Config copies.**
   ```sh
   docker exec fabro-fabro-1 cat /storage/.home/settings.toml > ~/fabro-archive/0354/settings.toml
   cp ~/fabro/.env ~/fabro-archive/0354/env && chmod 600 ~/fabro-archive/0354/env
   cp ~/fabro/docker-compose.yaml ~/fabro-archive/0354/docker-compose.yaml
   ```

6. **Write `~/fabro-archive/0354/REVERSAL.md`** on the host, from the template below, with
   the real paths filled in. It is the runbook task 04 follows if decision 8 applies.

7. **Checksums.** `cd ~/fabro-archive/0354 && sha256sum * > SHA256SUMS`.

## Reversal runbook template (write it to the host, not to this repo)

```
# Reverse the 0.362 upgrade (decision 8: only for a fabro bug that blocks the upgrade)

1. cd ~/fabro && docker compose stop scheduler fabro
2. Restore the volume from the stopped-state tarball taken in task 04:
   docker run --rm -v fabro_fabro-storage:/s -v ~/fabro-archive/0354:/a alpine:3 sh -c \
     'find /s -mindepth 1 -delete && tar -xzf /a/fabro-storage.tar.gz -C /s'
   (If task 04 never got as far as the tarball, the database alone is enough: restore
   ~/fabro-archive/0354/fabro.sqlite3 to /storage/db/fabro.sqlite3, delete
   fabro.sqlite3-shm and fabro.sqlite3-wal, chown 1000:1000, chmod 600, and restore
   ~/fabro-archive/0354/settings.toml to /storage/.home/settings.toml.)
3. cp ~/fabro-archive/0354/env ~/fabro/.env   (FABRO_VERSION=0.354.0-nightly.0)
   cp ~/fabro-archive/0354/docker-compose.yaml ~/fabro/docker-compose.yaml
4. docker compose up -d fabro; wait for "healthy"; docker exec fabro-fabro-1 fabro --version
5. Repo: revert the task 05 commit (it deletes [run.meta_branch]; on 0.354 that re-enables
   metadata-branch pushes). The task 03 commits are valid on 0.354 and stay.
6. docker compose up -d --build scheduler   (the task 03 scheduler works on 0.354; the
   Docker-socket mount it removed is not needed)
7. Runs dispatched on 0.362 during the attempt are lost from fabro's history. Their issues
   are requeued by the scheduler's receipt scan.
```

## Acceptance criteria
- `~/fabro-archive/0354/` contains `fabro.sqlite3` (integrity `ok`), `runs-export.json`
  (valid JSON, row count recorded), `settings.toml`, `env` (mode 600),
  `docker-compose.yaml`, `REVERSAL.md` and `SHA256SUMS`.
- Production is unchanged: the same `StartedAt` as step 1, and in-flight runs still running.
- Nothing from `env` or the vault was printed into your report or committed.

## Report
The step 1 outputs, the export's row count, the archive's `ls -la`, and the database file size.
