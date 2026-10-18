# Change compare: did a change to the factory help?

**Status:** baseline taken 2026-10-18 (`result-2026-10-18.txt`). Two changes are under
test. Neither has enough runs for an outcome verdict. Rerun when the targets in *What would
settle it* are met, and write the result as `result-<date>.txt` next to this file.

This is a handoff for the agent that does the rerun. It records the question, the exact
change moments, the tool, the rules the 2026-10-18 analysis followed, and what it found.

## The question

On the night of 2026-10-17/18 the operator made two changes and asked whether either one
helped:

| Label | Moment (UTC) | What changed | Where it is recorded |
|---|---|---|---|
| `coders-a` | 2026-10-18T04:53:23Z | coders-a's model, in the host settings overlay: `api_model` `deepseek-v4-flash-0731-iq3-xxs` → `qwen3.8-flash-next`, and `box-a`'s `base_url` port 8000 → 5300 | `/storage/.home/settings.toml.bak-codersa-20261018-045323` in the fabro container is the file before. `docker logs fabro-fabro-1` shows `Server config reloaded` at 04:53:27Z |
| `prompts` | 2026-10-18T06:59:40Z | `9b2b7cb` "improve backlog prompts": 11 `backlog` prompts rewritten (`coder`, `decompose`, `extra_decompose`, `improve`, `plan`, `quality`, `resolve_merge`, `reviewer`, `rework`, `spec`, `standards`) | git. The last run on the old prompts (`145bfeb`) was dispatched at 06:56Z, and the first on `9b2b7cb` at 07:54Z (`fabro inspect <run>` → `run_spec.settings.run.metadata.workflow_sha`) |

The same night also changed `pr-review`'s rebase prompt (`0771f6d`, 05:43Z), the dependabot
patch prompt (`0c6c05d`) and the scout scan prompt (`ff43eaf`). This tool cannot measure
them: the scheduler's database holds only `backlog` runs.

## Rerunning it

`ops/fabro-change-compare.py` reads the coder scheduler's database (`run_history`,
`run_summary`, `run_stage_stats`) read-only. It runs inside the scheduler container, and the
script travels over ssh on stdin, so nothing is copied to the host:

```sh
ssh andrew@10.10.0.32 'docker exec -i fabro-scheduler python - \
    --since 2026-10-15 \
    --change prompts=2026-10-18T06:59:40Z \
    --change coders-a=2026-10-18T04:53:23Z@coders-a \
    --exclude 01M5710QTY' < ops/fabro-change-compare.py
```

- `--change LABEL=ISO[@POOL]` is repeatable. Without `@POOL` the runs are split at the
  moment and compared within each repo × pool stratum. With `@POOL` only that pool's runs
  are compared, pooled and then per repo. Give every change that happened in the window, so
  the confounding check (below) can see them all.
- `--since` sets the start of the baseline. 2026-10-15 gives about 3 days of "before". Going
  further back mixes in older changes, such as Task validation on womens-fantasy-sports
  (2026-10-15T01:11Z) and the refuter fixes. Name those as `--change` too if you widen it.
- `--exclude 01M5710QTY` drops writers-app#1066's first run. It died of the disk-full
  incident as `failed/workflow_error`, which the tool cannot tell from a real failure.
  Exclude any later run that died the same way, after confirming it in
  `fabro events <run> -p` (`No space left on device`, `server.json is missing`).
- `--runs` adds one line per run. `--json` gives the data.

To test a new change, add a `--change` line and a row to the table above. The tool needs no
edit.

## What the columns count

Every figure is per run, from the summaries the scheduler's collector already builds (see
`docs/scorecard/README.md`). Nothing streams events.

| Column | Meaning |
|---|---|
| `runs` | Every run dispatched in the window that fabro started. `launch_failed` runs never ran and are dropped at load (148 of them on 2026-10-18) |
| `fair` | The runs that count for outcomes: summarized, and not ended by infrastructure (`terminated`, `cancelled`, `lost`) |
| `merged` | `run_history.merged = 1` when the run was released. A PR merged later by `merge-queue` still shows 0, so this undercounts fast-lane merges |
| `reached_pr` | The terminal node is in the review-merge phase |
| `gave_up` | Ended at `demote_lane`, `return_to_triage`, `mark_stuck` or `human_rescue` |
| `refute_pass_first` | The first Refuter verdict was `pass`, out of runs that reached the Refuter |
| `no_rework`, `median_rework` | Visits to `rework_t1`..`rework_t4` |
| `median_minutes` | Dispatch to finish, over succeeded fair runs. It depends heavily on the CI queue |
| `median_first_edit_s` | The coder's time to its first edit |
| `median_tps`, `max_tps` | Output tokens of `coder` + `rework_t*`, divided by those stages' coder-box time (`box_ms`, or `wall_ms` when it is 0). It includes tool time and provider latency, so it is **effective** speed, and a small edit scores low (lawncare-saas#2919: 1,847 tokens in 9.5 min on coders-d's free `auto` route, 3.2 tok/s). It is a fair measure for a model swap on one pool, and not an outcome measure for a prompt change |
| `rank-test p` | One-sided exact rank-sum test with midranks for ties: the chance the "after" runs rank this well on that metric if the change did nothing. It is `n/a` when a side is empty or there are more than 300,000 arrangements. With 1–3 "after" runs, the smallest p it can print is 1/C(n+k,k) |

## Rules the analysis follows

1. **Stratify by repo × pool.** Repo mix dominates every outcome figure. coders-a ran
   womens-fantasy-sports almost exclusively before the swap, and writers-app after it.
   writers-app's current fast-path issues (lint and type cleanups) end in `demote_lane`
   after 5 rework rounds on every pool, coders-b included. A pooled comparison would have
   blamed the new model for the issues. The plan trial hit the same trap
   (`docs/plan-trial/`).
2. **Read the `CONFOUNDED` lines.** A stratum is confounded when another `--change`
   touches its pool and falls between its first and last run. On 2026-10-18 every coders-a
   run after the prompt change was also after the model change. So use the unchanged pools
   (coders-b, -c, -d) for prompts, and for coders-a look at the runs between 04:53Z and
   06:59Z (the `--runs` lines).
3. **Speed counts every summarized run; outcomes count only fair ones.** A run killed by a
   restart still ran its coder stages honestly.
4. **Check the boundary.** The split is by dispatch time, but the scheduler clones `main`
   at dispatch, so a prompt change takes effect at **push** time, which can be later than
   the commit time. Confirm `workflow_sha` on the runs on either side of the moment with
   `fabro inspect`. `run_history` does not store the sha, and fabro prunes old runs
   (`fabro-run-prune.sh`), so do this while the runs exist.
5. **Fast-lane runs exercise only part of a prompt change.** Every run after `9b2b7cb` so
   far did one task. `decompose`, `extra_decompose`, `plan`/`improve` and `resolve_merge`
   are untested until standard-lane runs arrive.

## Result on 2026-10-18 (`result-2026-10-18.txt`)

- **coders-a model: faster, quality unknown.** Effective speed went from median 9.1 tok/s
  (24 runs since 10-15, max 12.1) to 31.3 and 34.9 on the two runs before the prompt
  change, and 18.8 on womens-fantasy-sports#2104 after it, against that repo's median of
  8.9. All three are above every old run, rank-test p = 0.0004. The model is credited
  rather than the prompts because the two fastest runs predate `9b2b7cb`. Outcomes cannot
  be compared yet. The two fair runs were writers-app (one `demote_lane`, one refute
  `fail`), and the old baseline has no coders-a writers-app runs to compare with.
- **Prompts: no signal.** Two fair runs:
  - lawncare-saas on coders-d (#2919): merged, refute pass, no rework, 53 min. That is
    inside the old distribution of 45 runs (median 47 min, 39 of 45 with no rework,
    refute 35 of 44).
  - writers-app on coders-c (#1066): 2 rework rounds and refute `fail`, against 2 old runs
    with none. n=2 against n=2 means nothing.

## What would settle it

| Question | Stratum | Target | Baseline to beat (since 10-15) |
|---|---|---|---|
| coders-a speed holds | coders-a, all repos | 10 more runs | median 9.1 tok/s |
| coders-a quality | coders-a / womens-fantasy-sports | 10–15 runs after 2026-10-18T06:59Z | refute first-pass 11 of 21, no rework 20 of 23, merged 4 of 23 |
| prompts, fast lane | lawncare-saas / coders-d | ~20 after-runs | refute 35 of 44, no rework 39 of 45, median 47 min |
| prompts, fast lane | the busiest coders-b or coders-c stratum | ~20 after-runs | from the rerun's own "before" rows |
| prompts, standard lane | any | a handful of runs that visit `decompose` | read their run lines by hand; too few for a test |

At about 30 runs a day across four pools, that is several days. Check the queue mix first:
coders-a quality needs womens-fantasy-sports issues to reach coders-a, and that depends on
what the queue holds.

The coders-a quality row is confounded with the prompt change by construction. If the
operator wants the model judged alone, the runs have to come from a pool whose prompts did
not change, which is not possible now that `main` has `9b2b7cb`. Report it as "new model +
new prompts" against "old model + old prompts".

## Next agent checklist

1. `git pull`. Read this file and `result-2026-10-18.txt`.
2. List every change since 2026-10-15 that could move these numbers: `git log --since
   2026-10-15 -- .fabro/workflows/backlog .fabro/workflows/_shared`, the overlay backups in
   `/storage/.home/` (`ls -la --full-time`), and the deployment log. Add each as a
   `--change`.
3. Run the command above, with `--runs`. Check the boundary runs' `workflow_sha`, and add an
   `--exclude` for any new infra death you confirm.
4. Compare against the targets. Write `result-<date>.txt`, and update the status line here
   and the `docs/README.md` row.
