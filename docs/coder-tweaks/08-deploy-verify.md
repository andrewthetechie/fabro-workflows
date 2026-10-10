# 08. Deploy, verify, and measure against C7

## Outcome
All of 02-07 is live, the images carry the new fabro-io and `fabro-code`, and
`docs/coder-tweaks/result-<date>.txt` reports M1-M8 for at least 15 `backlog` runs
created after the last deploy, compared with `baseline-2026-10-02.txt`.

## Rollout order
1. 06 and 07 (graph-only; live on push).
2. `make deploy-images`, after 03 and 04 are merged. Check
   `docker run --rm --entrypoint fabro-io <image> version` on all four images, and
   `fabro-code search` in one.
3. 02 (the guide). Then confirm M6 on the first run of each workflow.
4. 05 (the guard). Then run the forced-violation check from task 05 on one live run.
5. Append a dated entry to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` for each step.

A run created before a step does not count toward that step's metric. Check the creation
time against the deploy log, or look for `excerpts`, `.codex/instructions.md` in
`agent.memory.loaded`, or `git-guard` in the run's hook events.

## Measure
The recheck is two commands, run on the Mac (docs/recheck-follow-ups, tasks 02 and 03):
`ops/fabro-export-runs.sh --since <end of the last window>` exports the window's runs to an
`OUTDIR` outside the repo, then `python3.11 ops/fabro-agent-tools.py --local-only --outcomes
<OUTDIR>/*.jsonl` prints the metrics and the outcome table. Add `--prs` to read block reasons
from the PRs of runs without `merge_block_reason`.
`python3.11 ops/fabro-agent-tools.py` over the new runs. Report each metric with its
baseline and target, and flag a miss. For a miss, read 3 sessions and say why. Was the
guide loaded? Did the model see `code_search` and pick `grep` anyway? Was the guard
bypassed with `sh -c`?

## Decide (write the decision into the result file)
- **M1 missed and M6 met:** the guide reached the model and did not change its choice.
  Next try D5's profile override (one repo, one week), or the PATH enrichment.
- **M4 is not 0:** list the bypass forms and extend the parser. Do not try to close
  `python -c`.
- **M8 is worse:** find which change caused it before going further. Revert that one
  change, not the series.
- **Revisit D3 (hashline):** only if M7 is met and edit output is now the largest single
  output-token cost.

## Depends on
01-07.
