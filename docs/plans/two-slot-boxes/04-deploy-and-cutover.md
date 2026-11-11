# Task 04: deploy and cut over

Read `00-overview-and-contracts.md` first (D1, D7). Read `docs/agents/deploying.md` and
`ops/README.md`'s runbooks for draining a pool, the settings overlay and the background lane.
The operator confirms before each **[confirm]** step. **Never run `make compose-up`**: no
step here restarts fabro.

## Preconditions

- Tasks 01–03 are committed locally and `make check` passes on the combined tree.
- `coders-a` and `coders-b` are drained, and no lease or background run is on them
  (`GET :32280/api/background`, the queue page).
- `curl http://10.10.0.29:5300/health` answers with `"context_length":524288` from the new
  build.

## Steps

1. **Ship the code (no behaviour change yet).**
   - Push 01–03 to `main`.
   - Run `make deploy-images`, then `make deploy-scheduler`, from a clean checkout at
     `origin/main`.
   - The host vars still give each pool its own URL, so nothing should change: run
     `make verify-host`, and confirm the lane's strip still shows coders-a and coders-b as
     separate boxes.
2. **Cut over box 1. [confirm]**
   - In the host settings overlay, move `coders-b` to provider `box-a`. Set both rows and
     `coders-a-bg` to 524288 (copy from `settings.toml.example`), and remove `coders-b-bg`.
   - Set the server var `FABRO_BOX_HEALTH` and `SCHEDULER_BOX_HEALTH` (in `~/fabro/.env` or
     `scheduler.env`) to `coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.29:5300/health`.
     Leave .56 out while it is being upgraded.
   - Apply the overlay the way the runbook says (no fabro restart). Recreate the scheduler only:
     `docker compose up -d --no-deps scheduler`.
   - Confirm a model call resolves: a `box-noop` run queued by hand on box 1
     (`POST /api/background/box-noop {"repo": …}`) succeeds.
3. **Undrain coders-a and coders-b. [confirm]** Watch the first two concurrent coder runs on
   box 1:
   - `ops/fabro-box-claims.py` shows claims of a few seconds, with `idle=skip` when the
     sibling is busy, and no 45 s waits;
   - `/health` `queued` stays 0 with two loops in box stages at once;
   - the background strip shows one `coders-a+coders-b` row, and a background run is evicted
     when either loop enters a box stage.

   If `queued` goes above 0 with only two coder sessions, the box is not serving two slots.
   Drain both pools and stop.
4. **Cut over box 2. [confirm]** When .56 reports the new build at 524288:
   - The operator drains `coders-c` and `coders-d` and waits for their leases to end.
   - Move both rows to provider `box-b` (.56), add `coders-c-bg`, and drop the spark `coders-c`
     row and the freellm `coders-d` row.
   - Set both box-health values to all four pairs, matching the compose default.
   - Recreate the scheduler only, run a `box-noop` on box 2, undrain, and watch as in step 3.
5. **Record.** Add a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` (mode 600,
   append only). Mark this series *done* in `docs/README.md` and in `00`'s status line, then
   push. Update the memories that describe coders-a/b and c/d as the old layout.

## Rollback

- Point the box-health values back at one pool per URL and recreate the scheduler. The
  01–02 code then behaves as before (D1).
- Repoint the overlay rows. On box 2 that means restoring the spark and freellm rows from
  `git show HEAD~:ops/settings.toml.example`.
