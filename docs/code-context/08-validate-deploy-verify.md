# 08 · Validate, deploy the images, push, and measure

Read `00-overview-and-contracts.md` first. This task needs the host.

## Order

1. Run the offline gates on the Mac: `./ops/test-task-gates.sh`, the TOML check, and `sh -n`
   on the edited scripts.
2. Deploy the images **before** the graph change reaches `main`:
   `rsync … ops/profile-images/ …` and then `./build-images.sh` (AGENTS.md, "Deploying").
   Every image must pass the task 02 index check. The `command -v` guard from task 04 keeps
   an old image working, but a run on an old image gets no index. So the order still
   matters for the measurement.
3. Inside each image, run `./ops/test-task-gates.sh`. The `fabro-code` section must run
   there, not skip.
4. Run the container validation and the routing checker, and get the same baselines.
5. Commit to `main`, and push. The next scheduler dispatch uses the change.

## Watch the first run

- `prep`'s log shows `code_index` = `ok` and a map of 16,000 bytes or less.
- `fabro events <run> --json`: in `improve` and `coder`, `shell` calls whose command begins
  with `fabro-code`. If there are none, the prompts are not working. Stop and look before
  you measure.
- `git log origin/main..HEAD --stat` on the PR branch contains no `.codegraph` path.
- The hygiene counters and `open_pr_prep` behave as before.

## Measure

Measure when the runs since deploy hold at least **30 `improve` visits and 30 `coder`
visits**. (Operator decision 5: stage visits, not a run count. Runs take hours, and
each stage runs about 3.3–3.7 times per run, so 30 visits is about 8 runs.) Then run
`ops/fabro-exploration-share.py --since <deploy date>`. It also prints the dossier presence
rate: of the `improve` visits that ended `ready`, the share that wrote `task-context.md`. Compare with the ADR 0014 baseline. The targets (operator decision 5):

| Metric | Baseline | Target |
|---|---|---|
| Exploration share of input tokens (all stages) | ~39% | ≤ 25% |
| `coder` repeated `read_file` | 53% | ≤ 30% |
| `agent.loop.detected` per run | 3.3 | ≤ 1.5 |
| `improve` median minutes | 8.0 | ≤ 6.0 |
| Auto-merge rate (B1) | current | not lower |

Record the result as a dated line in the ADR's status. If the exploration share falls less
than 5 points, record that and set the ADR to `deprecated`. Do not tune the prompts without
end.
