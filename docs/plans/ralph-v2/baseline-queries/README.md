# Baseline queries

Read-only. `box-failover.py`, `holds.py`, `outcomes.py`, `refute-visits.py`, `stage-stats.py` and
`tail-predictors.py` read the scheduler store:

    ssh andrew@10.10.0.32 'docker exec -i fabro-scheduler python -I -' < file.py

`extract-events.py` runs on the host, reads run ids on stdin and streams each run's events into
one compact JSON line. Its output holds **issue and finding text, so never commit it**. The other
scripts read that file:

    python3 finding-attribution.py runs.jsonl ex.json
    python3 late-findings.py runs.jsonl
    python3 findings-per-visit.py runs.jsonl
    python3 task-predictors.py runs.jsonl
    python3 box-lens-vs-refuter.py runs.jsonl

Runs are the 133 `ralph` issue runs dispatched since 2026-11-12T12 that have a `refute` row in
`run_stage_stats`. `result-2026-11-23.txt` is the output.

Caveats:

- `box-failover.py` averages 2026-11-13..11-16, a four-day box incident, into its window. Read
  the failover rate by day. It is zero outside those days.
- Attribution is by edited file, not by line, and counts `edit_file`/`write_file` calls only.
  An edit made through the shell is not seen.
- `prompt.completed.provider` still names the box after a failover. Do not read a served model
  from it.
- `merged` counts a run's own merge; use `state` in `pr_facts` for the PR's final state.
