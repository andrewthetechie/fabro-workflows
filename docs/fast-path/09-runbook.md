# Task 09 host runbook (operator)

Task 09's repo changes are committed; these steps change the host and are yours. Spike S7 shows room (26 GiB available, 16 cores, two runs live). Run from a clean checkout at `origin/main`.

**Done 2026-10-11:** steps 2-5 (2: the first hand-fired sweep reached its first node in 4 s, so no `light` environment; 4: `make check-host` matched every count). Step 1 is open: two `backlog` runs were live, and the restart fails them. Step 6 stands.

1. Cap 4 to 6: drain both coder pools on the scheduler page; wait until `GET /runs` lists no active run (avoid 05:00-06:00, when `dependabot` runs); edit `max_concurrent_runs = 6` in `/storage/.home/settings.toml` in the container; `cd ~/fabro && docker compose restart fabro`; verify `docker inspect -f '{{.State.StartedAt}}' fabro-fabro-1` is later than the overlay's mtime (`ops/README.md`); undrain.
2. Environments: use each repository's own. S6 was not measured; if the first hand-fired run spends over 60 s before its first node, create a `light` environment (cpu=1, mem=1GB) and record the numbers in 00 and ADR 0024 D8.
3. `make provision` creates `merge-queue-<repo>` x4 with schedules disabled; confirm with `GET /automations | jq '.data[] | select(.workflow == "merge-queue")'`.
4. `make deploy-notify`, the retention step (`ops/fabro-run-prune.sh` plus its cron entry, `ops/README.md`), then `make verify-host`; `make check-host` for the node and edge baselines (`MergeQueue (8 nodes, 10 edges)`, Backlog 72/175, PrReview 35/76, Dependabot 47/104 verified 2026-10-11).
5. Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`, no secrets.
6. Leave ADR 0023 and 0024 proposed until task 10's first live merge.
