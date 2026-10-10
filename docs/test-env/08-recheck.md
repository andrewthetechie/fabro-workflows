# 08. Recheck

## Outcome
`docs/test-env/result-<date>.txt` holds M-env and M-run over at least 16 `backlog` runs
started after task 07's last merge. The overview's status line and ADR 0018's status say
whether the targets were met.

## Change
1. Collect the runs and run `ops/fabro-agent-tools.py` exactly as in task 01.
2. For each remaining M-env hit, record the stage and the cause: a target missing from
   `test.toml`, an agent using shell anyway, or preparation failing.
3. Compare preparation seconds (from the `prepared in Ns` lines) against stage durations.
   If preparation exceeds 10% of a coder stage, record it as the next lever.

## Acceptance
- M-env is about 0 on womens-fantasy-sports and lawncare-saas, and M-run is at least 60%.
  If so, mark ADR 0018 accepted. If not, record the causes and the follow-up.
- The `docs/test-env/` row in `docs/README.md` carries the result.

## Depends on
05, 06, 07.
