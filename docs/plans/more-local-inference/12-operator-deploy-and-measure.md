# Task 12: operator — deploy and measure, phase by phase

**Operator only.** Read `00-overview-and-contracts.md` and ADR 0031. Every step that pushes,
deploys or edits the host is here. Append each step's outcome to
`~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`, as a dated section. When a phase's exit check
passes, record it in the overview's **Phase gates passed** line and commit that; the next
phase's agent reads it there.

## Phase 0: the gap measurement (running since 2026-10-28 03:50 UTC)

1. After about 2026-10-30 03:50 UTC, `~/coder-util/sample.sh` stops on its own. Copy
   `~/coder-util/box-{a,b}.tsv` (columns: epoch, busy, queued, idle_seconds, live_tokens) and
   compute per box and per hour:
   - the busy share;
   - the distribution of idle stretches (consecutive `busy=0, queued=0` samples, 10 s apart);
   - the hours in idle stretches of 10 min or more.
2. **The gap table.** For every released `backlog` and `ralph` run since 2026-10-21, read its
   stage visits with `GET /runs/{id}/stages` (start and end of each node visit, the same call
   the probe makes). Order the visits by start. For each visit of a node that is not a box
   stage (C1), the remaining gap is the start of the next box-stage visit minus the end of this
   visit, or the run's end if no box stage follows. Take the median per `(workflow, node)`. Put
   the table into task 05's gap-prediction config before Phase 1 deploys.
3. **Exit:** fillable gap-hours per box per day, recorded in the log. If they are under 4 h a
   day per box, stop and rethink before building further.

## Phase 1: claim plumbing and the lane, with `box-noop` (tasks 02, 01, 03, 04, 05, 11)

1. **Verify the three unverified facts** (overview *Facts*) with a throwaway probe package,
   before anything is pushed, and record each answer in the log and in
   `docs/agents/invariants-graph.md`:
   - what fabro does to a stage when a blocking sandbox `stage_start` hook outlives its
     `timeout` (a hook that sleeps 20 s under a 5 s timeout);
   - whether a sandbox `stage_start` hook sees `FABRO_RUN_ID`, and whether the
     `FABRO_HOOK_CONTEXT` file has `run_id`;
   - whether `FABRO_API_TOKEN` from a sandbox may `POST /runs/{id}/cancel` another run.

   If the first is "the stage fails", keep C3's 45 s deadline and 60 s timeout. If the token
   cannot cancel, stop: the claim cannot work.
2. **Server variable.** Run `make provision` (task 03 added `FABRO_BOX_HEALTH` to
   `provision-server-state.sh`), or set it by hand to
   `coders-a=http://10.10.0.29:5300/health,coders-b=http://10.10.0.56:5300/health`. Confirm it
   with `GET /variables`. **It must exist before step 5**, or no `backlog`, `ralph` or
   `pr-review` run is created.
3. **Overlay rows.**
   - Add `coders-a-bg` and `coders-b-bg` to the live settings overlay. Build each from the
     **live** `coders-a`/`coders-b` row, renamed, with a `display_name` that says background,
     so the two names stay the same model (task 04).
   - Then run `docker logs --since 1m fabro-fabro-1 | grep -cE "Rejected reloaded|Failed to reload"`.
     It must print `0` (invariants-host).
   - Then `fabro model test -m coders-a-bg` and `-m coders-b-bg`.
4. **The binary first (D18).** Push task 02's commit alone
   (`git push origin <task-02-sha>:main`), then `git pull --ff-only` in the deploy checkout
   (`deploy-host.sh` refuses a HEAD that is not `origin/main`), then `make deploy-images`, and
   wait for the build to finish. Check that a new sandbox reports `fabro-io 0.9.0`
   (`fabro-io version` from a probe, or the build log: `build-images.sh` fails an image whose
   `fabro-io version` is not the crate's).
5. **The rest.** First run `make check-host` from a checkout at the commits about to be
   pushed: it rsyncs that checkout's `.fabro/` and validates it, so it is the first
   `fabro validate` of `box-noop` before the package is on `main`. `box-noop` validates
   (`4 nodes, 3 edges`), and the `backlog`, `ralph` and `pr-review` node and edge baselines are
   unchanged. Then push tasks 01, 03, 04, 05 and 11 (and any review fixes). Task 03's change to
   `fire-pr-review.sh` is live with this push; no `deploy-scripts` is needed for it. Then:
   - `make deploy-compose` (the scheduler's new `environment:` lines; this copies the file and
     restarts nothing), then `make deploy-scheduler`.
   - The lane is still `off`.
6. **Claim with nothing to cancel:** watch one live `backlog` run. Each box stage's
   `box-claim` stderr line shows `cancelled=0 waited=2s idle=true` (two idle reads 2 s apart
   are the minimum, C3 step 4), and the stage times match the week before, plus about 2 s
   per box-stage visit. Hand-fire one `pr-review` and confirm it is created (its `coder_pool`
   input).
7. **Cancel latency and the guard:**
   - Set `SCHEDULER_BACKGROUND=on` in `~/fabro/.env`, then
     `cd ~/fabro && docker compose up -d --no-deps scheduler`, and check the *Background* strip
     shows `on`.
   - Enqueue `box-noop` for one repository (`POST /api/background/box-noop`).
   - When the coder loop on that box reaches its next box stage, read the claim's wait,
     strix-infer's `strix_requests_total{outcome="cancelled"}`, and the background job's state
     (`queued`, `cancels=1`). fabro 0.362 does not record the `box-claim:`/`box-guard:` lines
     (step 1's finding), so read the claim and the guard from the run events with
     `ops/fabro-box-claims.py --since <the lane's switch-on time>` (`ops/README.md`).
   - Repeat 10 times across both boxes.
   - Read the report's background runs cancelled before their first request (the guard, or an
     eviction before the box) and the strip's eviction count over the same period.
8. **Exit:**
   - Cancel latency p95 is under 30 s.
   - No coder-loop box stage ever ran while a background run was mid-request on its box:
     `fabro-box-claims.py` reports no `idle=false` wait and no overlap (it exits 0), and every
     eviction happened before the evicted run's first agent stage.
   - Coder-loop stage times are unchanged.
   - Then set the lane back to `off` until Phase 2, and record Phase 1 in the Status line.

## Phase 2: the PR doctor (task 06)

1. Push task 06 and its docs commit, `make deploy-scheduler`, and set the lane `on`.
2. Watch the first five doctor runs end to end:
   - marker posted → `ai-review-needs-human` and `merge-on-green` removed → push → one leased
     `pr-review` → merged or queued, or handed off with `ai-review-needs-human` back on;
   - no PR ever gets a second doctor run;
   - the `pr-review` lease released without touching any issue label.
3. **Exit after 7 days:**
   - blocked PRs turned into merges per week;
   - doctor runs that pushed nothing;
   - hand-offs, with and without a review;
   - hosted credits spent on the follow-up reviews against the credits the merged PRs had
     already spent;
   - claim wait p95 under 30 s.

   Then set ADR 0031 to *accepted* (task 11 step 6), and record Phase 2 in the Status line.

## Phase 3: precheck and context packs (tasks 07-08)

1. If task 07 bumped `min_binary`, push the `fabro-io` commit and `make deploy-images` first,
   as in Phase 1. Then push the rest and `make deploy-scheduler`.
2. Check that a new `backlog` run's `decompose` received `background.md` when the issue has a
   marker, and that runs without one are unchanged.
3. **Exit after 14 days:**
   - the box busy share, against Phase 0's;
   - runs that ended `close_noop`, `return_to_triage` or `mark_stuck`, per 100 runs;
   - issues held by precheck, and how many a human released (a false hold);
   - z.ai credits per `decompose` and `plan` visit, with a pack against without (task 08's
     `had_pack`), stratified by the box-plan trial's arm, priced at the overview's z.ai credit
     rates (*Facts*), with off-peak at half.

   A pack that does not lower credits per visit is turned off (producer disabled) and recorded.
   A precheck whose false-hold rate is over a third is turned off the same way.

## Phase 4: pre-review and long-horizon jobs (tasks 09-10)

1. **Before task 09 starts:** record in the Status line either the ralph trial's verdict or
   that you accept the confound (D21).
2. Push, deploy, and `make check-host` (new `Backlog` baseline).
3. **Exit after 14 days:**
   - hosted `review` and `rework` visits per task, before and after;
   - pre-review reverts per task;
   - the orientation issue's effect on credits per `decompose` visit;
   - flaky issues closed with the investigation's cause.

## Housekeeping

- File the latent `issue.json` comments bug as its own issue: the `decompose` and `plan`
  prompts tell the agent to read `.comments`, and `claim` never fetches them (*Facts*, D16).

## Kill switches

- **The lane:** `SCHEDULER_BACKGROUND=off` in `~/fabro/.env`, then
  `cd ~/fabro && docker compose up -d --no-deps scheduler`. This works only because task 05
  maps the variable in `ops/docker-compose.yaml`. Running background runs finish; cancel them
  from fabro if needed.
- **The claim:** remove the `box-claim` hook from a workflow's `workflow.toml` and push. A
  workflow change needs no deploy. Turn the lane off first, or background runs will share the
  box with that workflow's box stages until the guard or eviction catches them.
- **A job kind:** disable its producer (a config flag per kind, added with the producer).
