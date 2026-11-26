# Task 13: review, push, shakedown, switch (reviewer and operator only)

Not for a building agent.

## Review, before any push

1. `/code-review` on the series' commits against this folder and #87, then read each graph node in the order
   it really runs. Attend to:
   - every claimed copy, diffed against `ralph` by hand (the first `ralph` build claimed copies that were not);
   - `ralph-v2/workflow.toml` section by section against `ralph`'s: `[run.run_branch]`, every hook, both
     `stage-end` lines, the `io-guard` matcher;
   - the empty paths: no plan, no patch, no finding, no `FABRO_BOX_HEALTH`, a crashed gate, both reviewers missing;
   - that no node prints issue, diff or finding text to stdout or stderr;
   - the exhausted path end to end, and the cap rule for a reviewed Task (C1 step 4);
   - a v2 dispatch traced through the real code: `choose_workflow` -> `ENTRYPOINTS` -> `build_version_payload` ->
     the `workflow` label -> `coder_box_stages` -> the lane (FakeFabro skips `ENTRYPOINTS`);
   - each new `SCHEDULER_*` variable has a compose `environment:` line.
2. `make check` with gawk; the gate tests in the bookworm container; `make check-host` (needs group C's tree,
   not a push: it rsyncs the checkout). Record the verified baseline in `AGENTS.md`.
3. `fabro preflight` of `ralph-v2` with a bound and an unbound `coder_pool` renders every class; of `ralph` and
   `backlog` for issues 4-7 names the box for `plan`.
4. On the host: `fabro model test` for `glm-5.3`, `glm-5.3-flash`, `kimi-for-coding`, `gpt-6-luna`,
   `hy4-preview`, `gpt-6.1-terra`, `qwen3.8-27b`. `gpt-6.1-terra` must report provider `openai`. One that
   fails is taken out of its chain in both packages before the push, and named here.

## Push and deploy, in three groups

The commits are in task order and the groups are contiguous, so each is
`git push origin <last commit of the group>:main`.

| Group | Tasks | When | Then |
|---|---|---|---|
| A | 01, 02 | once reviewed | `make deploy-scheduler deploy-scripts deploy-notify` from the deploy checkout; `make verify-host`. Check: the scheduler log's next `workflow-version:` line shows about 750,000 bytes or less; one dispatch of `ralph` and one `issue-precheck` starts; `fabro-fire-ralph.sh` in `DRY_RUN=1` lists only `ralph` and `_shared`; C11 prints `ok` |
| B | 03, 04 | after #94's read is posted there (about 2026-12-01) | live on push. Post the push time on #94 and #92. Check the next `ralph` run: `plan` on the box, `standards`/`spec` on `glm-5.3` |
| C | 05-12 | **only after group A is deployed and checked** | `make deploy-scheduler deploy-compose` (share stays 0). Check: `GET /api/pools` unchanged, the next `ralph` dispatch starts, the v1 column of `fabro-scorecard.sh --by-workflow` for the last 7 days equals its value before the deploy |

Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` for each.

## Shakedown (E1)

1. One hand-fired run on a flakeradar issue: `WORKFLOW=ralph-v2 DRY_RUN=0 ~/bin/fabro-fire-ralph.sh
   andrewthetechie/flakeradar <issue>`. `coders-a` must be free: a hand fire takes no lease, so drain it first
   and undrain it after. The run opens a real PR and can merge. It is excluded from the results.
2. Read `fabro events <run> -p`, every edge, against 00:
   - each Task: plan, Iteration, `check`, both reviewers, gate, in that order; a rejection returns to the same Task;
   - every `task_*_check` at `retry` or `fallback` has a reason; `.io/stage-end/` marks exist while it runs;
   - `standards` -> `spec` -> `refute` -> `refute_gate` -> `review_gate`;
   - `box-claim` waits before every box stage and before no `_fb` stage (`ops/fabro-box-claims.py`);
   - no `is sealed for this stage` on a legitimate read, in any stage;
   - `submit` rejected at most once per stage visit;
   - the payload size of the version, and the manifest loads (`fabro-io` prints no warning).
3. Re-time `task_plan`, `task_refute`, `task_review` and `resolve_merge` from it. A stage over 60% of its
   timeout gets a longer one, in the same commit as its comment.
4. If a fault needs a fix, fix it, push, and fire a second issue. Do not switch on with an open fault.

## Switch

1. **Not before 2026-12-08** (A7), and after #92's read is posted.
2. `GET /api/pools`: `coders-b` and `coders-d` drained (E20).
3. `SCHEDULER_RALPH_V2_SHARE=50` in `~/fabro/.env`, then `docker compose up -d --no-deps scheduler`. Write the
   UTC time here: it is the window's start. **Window start:** (not yet)
4. First 24 h: read the first two runs of each arm, C11, the queue depth, the lane's fills, z.ai credits.
5. Kill switch: the share to 0 and the same command. Running runs finish.
6. After 20 v2 runs: re-time every new stage from its visits, and re-measure the `ralph-v2` gap rows (the scripts
   in `.scratch/more-local-inference/`). A gap-row change is a scheduler deploy and touches only the lane.
7. Set `docs/README.md`'s row and this file's table to their dates.
