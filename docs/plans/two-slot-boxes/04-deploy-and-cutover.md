# Task 04: deploy and cut over

Read `00-overview-and-contracts.md` first (D1, D7). Read `docs/agents/deploying.md` and
`ops/README.md`'s runbooks for draining a pool, the settings overlay and the background lane.
The operator confirms before each **[confirm]** step. **Never run `make compose-up`**: no
step here restarts fabro.

**Revised 2026-11-11: one cutover, not two (D7 amended).** Both boxes already run strix-infer
0.7.0 (two slots, 524288, F4) and all four pools are drained, so both boxes are cut over at
once. The original two-cutover plan is in git history.

## Preconditions (checked 2026-11-11)

- Tasks 01–03 and the review fixes are committed locally and `make check` passes.
- `GET :32280/api/pools`: `coders-a` to `coders-d` all `drained: true`, no lease.
- `curl http://10.10.0.29:5300/health` and `…10.10.0.56…` both answer with
  `"context_length":524288` and `"slots":2`.
- No background run is running (`GET :32280/api/background`). A run still on `coders-b-bg`
  fails once step 3 removes that row, so let it finish, or cancel it.
- The host does **not** pin `SCHEDULER_BOX_HEALTH` in `~/fabro/.env` or `scheduler.env`, so
  the compose default is what is live. That is why step 4 needs no `.env` edit, and also why
  any `make deploy` that carries the new compose file is the cutover. Do not run one before
  step 3.
- `FABRO_BOX_HEALTH` (server variable) is the old
  `coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.56:5300/health`.

## Steps

1. **Push. [confirm]** Push the local commits to `main`. The workflow changes go live on the
   next fire. Every pool is drained, so the only runs that fire are background runs and
   hand-fired ones. The coder loops' manifests now carry `coder_box_stages`. The deployed
   `fabro-io` already knows that field (`#[serde(default)]`), so D18 does not apply.
2. **Images. [confirm]** From a clean checkout at `origin/main`: `make deploy-images`. This
   ships the new `box-claim` and `box-guard`. With the old one-pool-per-URL server variable,
   each pool is still its own box (D1).
3. **Overlay. [confirm]** The candidate is already on the host at
   `~/fabro/settings.two-slot.toml` (mode 600). The live file is backed up beside it as
   `~/fabro/settings.pre-two-slot.toml`. The candidate passed `tomllib` and
   `check-agent-profiles.py`. Its diff against the live file:
   - adds `coders-b` on `box-a`, and `coders-c`, `coders-d` and `coders-c-bg` on `box-b`;
   - raises `coders-a` and `coders-a-bg` to 524288;
   - removes `box-b`'s `coders-b` and `coders-b-bg`, spark's `coders-c`, and the `freellm`
     provider with its `coders-d`.

   It was built by a scratch script from the overlay as of 2026-11-11. If the live file has
   changed since then, rebuild the candidate. Apply it in place, which keeps the file's owner,
   and let the 5 s poll rebuild the catalog (no restart):
   ```sh
   ssh andrew@10.10.0.32 'docker exec -i fabro-fabro-1 sh -c "cat > /storage/.home/settings.toml" < ~/fabro/settings.two-slot.toml'
   ssh andrew@10.10.0.32 'cd ~/fabro && for m in coders-a coders-b coders-c coders-d coders-a-bg coders-c-bg; do docker compose exec -T fabro fabro model test -m $m; done'
   ```
4. **Box health, both values. [confirm]**
   ```sh
   ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro variable set FABRO_BOX_HEALTH "coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.29:5300/health,coders-c=http://10.10.0.56:5300/health,coders-d=http://10.10.0.56:5300/health"'
   make deploy-compose deploy-scheduler   # copies the compose default and recreates the scheduler only
   make verify-host
   ```
   `deploy-compose` warns that compose is not applied to fabro. That is expected: only the
   scheduler reads `SCHEDULER_BOX_HEALTH`, and `deploy-scheduler` recreates it with
   `--no-deps`.
5. **Smoke. [confirm]** `GET :32280/api/background` shows two rows, `coders-a` with
   `pools: [coders-a, coders-b]` and `coders-c` with `[coders-c, coders-d]`. Queue a
   `box-noop` (`POST /api/background/box-noop {"repo": …}`) and watch it run on each box.
   The lane fills one box per job.
6. **Undrain. [confirm]** Undrain `coders-a` and `coders-b` first. Watch the first two
   concurrent coder runs on box 1:
   - `ops/fabro-box-claims.py --since <undrain time>` reports no overlap and no `idle=false`
     (its default `--box-health` is the two-slot layout);
   - `/health` `queued` stays 0 and `busy_slots` reaches 2 with two loops in box stages at
     once;
   - a background run is evicted when either loop enters a box stage.

   If `queued` goes above 0 with only two coder sessions, the box is not serving two slots:
   drain both pools and stop. Otherwise undrain `coders-c` and `coders-d` and watch the
   same things on box 2.
7. **Record.** Add a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` (mode
   600, append only). Mark this series *done* in `docs/README.md` and in `00`'s status line,
   then push. Update the memories that describe coders-a/b and c/d as the old layout. Decide
   what happens to `ops/provision-coder-groups.sh` (stale since strix-infer: it still
   provisions LiteLLM groups against llama.cpp on `:8000`).

## Rollback

- Point both box-health values back at one pool per URL: set the server variable, and pin
  `SCHEDULER_BOX_HEALTH` in `~/fabro/.env` (compose's default is now the two-slot layout),
  then `docker compose up -d --no-deps scheduler`. The 01–02 code then behaves as before (D1).
- Restore the overlay:
  `docker exec -i fabro-fabro-1 sh -c "cat > /storage/.home/settings.toml" < ~/fabro/settings.pre-two-slot.toml`.
