# Task 07: deploy, go live, and the one-month check (operator only)

Not for the implementing agent. Every step writes to `main`, the host or GitHub.

## Deploy

1. Review tasks 01-06's commits. Run `make check`, then `git push` (pre-push runs
   `make check-fast`).
2. `make deploy-scheduler`. This restarts only the scheduler container, not fabro.
3. `make deploy-scripts` (installs `~/bin/fabro-retro.sh`).
4. Create the label once:
   `gh label create retro -R andrewthetechie/fabro-workflows --color 5319e7 --description "Filed by the weekly factory retro (ADR 0026)"`.
5. A by-hand dry run on the host, with no post:
   `ssh andrew@10.10.0.32 'NO_POST=1 ~/bin/fabro-retro.sh'`. Check that the text is under
   2000 characters, names no target repository, and that its classes look right against
   `ops/fabro-scorecard.sh --last-week`.
6. Install the **dry** cron line from `ops/README.md` (`## Cron — the weekly retro`).

## Go live

7. After the first Monday's `DRY RUN` message in Discord: if the planned writes look right,
   replace the cron line with the `DRY_RUN=0` one. If not, fix it and wait for the next
   dry run. To make one real write early instead of waiting for Monday, run
   `RETRO_ARGS="--cap 1" DRY_RUN=0 ~/bin/fabro-retro.sh` by hand. That is not a preview:
   it files up to one issue, comments on every known open one, and posts to Discord.
8. Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`.

## The one-month check (four live Mondays after step 7)

9. List the retro issues: `gh issue list -R andrewthetechie/fabro-workflows -l retro --state all`.
   "Done when" is met when at least one issue is open or closed as completed (D10), and
   four weekly messages reached Discord.
10. Count each kind of action from the four messages. If no class was ever over the
    threshold, or the cap held something back every week, change the defaults by changing
    the cron line's `RETRO_ARGS`. Record that change in the deploy log.
11. Set ADR 0026 to accepted (or amend it), update the `docs/README.md` row and this
    folder's status, and close #33.
