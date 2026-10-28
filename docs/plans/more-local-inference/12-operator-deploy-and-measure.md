# Task 12: operator — deploy and measure, phase by phase

**Operator only.** Read `00-overview-and-contracts.md` and ADR 0031. Every step that pushes,
deploys or edits the host is here. Append each step's outcome to
`~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`, as a dated section.

## Phase 0: the gap measurement (running since 2026-10-28 03:50 UTC)

1. After about 2026-10-30 03:50 UTC, `~/coder-util/sample.sh` stops on its own. Copy
   `~/coder-util/box-{a,b}.tsv` (columns: epoch, busy, queued, idle_seconds, live_tokens) and
   compute per box and per hour:
   - the busy share;
   - the distribution of idle stretches (consecutive `busy=0, queued=0` samples, 10 s apart);
   - the hours in idle stretches of 10 min or more.
2. From fabro's per-visit stages (`GET /runs/{id}/stages`, the method in the overview's
   *Facts*), compute the median remaining gap after each hosted or command node of
   `backlog` and `ralph`. Put the table into task 05's gap-prediction config before Phase 1
   deploys.
3. **Exit:** fillable gap-hours per box per day, recorded in the log. If they are under 4 h a
   day per box, stop and rethink before building further.

## Phase 1: claim plumbing and the lane, with `box-noop` (tasks 01-05, 11)

1. **Server variable.**
   `fabro variable set FABRO_BOX_HEALTH 'coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.56:5300/health'`.
   Set it before the graphs reach `main` if task 03 found an unset variable fails a run.
2. **Overlay rows.**
   - Add `coders-a-bg` and `coders-b-bg` to the live settings overlay, copied from the
     example (task 04).
   - Then run `docker logs --since 1m fabro-fabro-1 | grep -cE "Rejected reloaded|Failed to reload"`.
     It must print `0` (invariants-host).
   - Then `fabro model test -m coders-a-bg` and `-m coders-b-bg`.
3. **Scripts first:** `make deploy-scripts` for `fire-pr-review.sh`'s `coder_pool` (task 03).
4. **Push** tasks 01-05 and 11, then `make deploy-images` (the new `fabro-io` with
   `box-claim`), then `make deploy-scheduler`.
   - Then `make check-host`: `box-noop` validates, and the node and edge baselines are
     unchanged.
   - The lane is still `off`.
5. **Claim with nothing to cancel:** watch one live `backlog` run. Each box stage's
   `box-claim` stderr line shows `cancelled=0 waited=0s idle=true`, and the stage times match
   the week before.
6. **Cancel latency:**
   - Set `SCHEDULER_BACKGROUND=on`, then
     `cd ~/fabro && docker compose up -d --no-deps scheduler`.
   - Enqueue `box-noop` for one repository (`POST /api/background/box-noop`).
   - When the coder loop on that box reaches its next box stage, read the claim line's
     `waited=`, strix-infer's `strix_requests_total{outcome="cancelled"}`, and the background
     job's state (`queued`, `cancels=1`).
   - Repeat 10 times across both boxes.
7. **Exit:**
   - Cancel latency p95 is under 30 s.
   - No coder-loop box stage ever ran while a background run was mid-request on its box (the
     claim line's `idle=true` every time).
   - Coder-loop stage times are unchanged.
   - Then set the lane back to `off` until Phase 2.

## Phase 2: the PR doctor (task 06)

1. Push task 06, `make deploy-scheduler`, and set the lane `on`.
2. Watch the first five doctor runs end to end:
   - doctor push → `merge-on-green` removed → one `pr-review` → merged, or handed off with
     `ai-review-needs-human`;
   - no PR ever gets a second doctor run.
3. **Exit after 7 days:**
   - blocked PRs turned into merges per week;
   - doctor runs that pushed nothing;
   - hand-offs;
   - hosted credits spent on the follow-up reviews against the credits the merged PRs had
     already spent;
   - claim wait p95 under 30 s.

   Then set ADR 0031 to *accepted* (task 11 step 6).

## Phase 3: precheck and context packs (tasks 07-08)

1. Push, `make deploy-images` if `fabro-io` changed, `make deploy-scheduler`.
2. **Exit after 14 days:**
   - the box busy share, against Phase 0's;
   - runs that ended `close_noop`, `return_to_triage` or `mark_stuck`, per 100 runs;
   - z.ai credits per `decompose` and `plan` visit, with a pack against without (task 08's
     record), priced at the overview's z.ai credit rates (*Facts*), with off-peak at half.

   A pack that does not lower credits per visit is turned off (producer disabled) and recorded.

## Phase 4: pre-review and long-horizon jobs (tasks 09-10)

1. Push, deploy, and `make check-host` (new `Backlog` baseline).
2. **Exit after 14 days:**
   - hosted `review` and `rework` visits per task, before and after;
   - the orientation file's effect on credits per `decompose` visit;
   - flaky issues closed with the investigation's cause.

## Kill switches

- **The lane:** `SCHEDULER_BACKGROUND=off` in `~/fabro/.env`, then
  `docker compose up -d --no-deps scheduler`. Running background runs finish; cancel them from
  fabro if needed.
- **The claim:** remove the `box-claim` hook from a workflow's `workflow.toml` and push. A
  workflow change needs no deploy.
