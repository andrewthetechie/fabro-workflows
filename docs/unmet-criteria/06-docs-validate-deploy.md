# 06: docs, validation, deploy and the live trial (U15)

## Docs

- `docs/agents/invariants-backlog.md`: a section *Unmet criteria (#17)* with the rules for
  `task_block_gate` (a stale or invalid file is never `none`, and it is deleted after reading),
  `return_to_triage` (marker first line, removes `agent-in-progress`, ends succeeded,
  `mark_stuck` fallback), and the `triage_questions` key that must be published before the
  hook's `stage_start`.
- `docs/agents/invariants-graph.md` (Stage I/O): an `output` in the manifest does not oblige a
  stage to submit. `task_blocked` is the one contract that is used only to block.
- `docs/agents/invariants-merge.md` (Reporting): the Unmet criteria section reads only
  `refute.json`.
- `AGENTS.md`: the baselines. `backlog` gains `task_block_gate` and `return_to_triage`. Take
  the counts from `make check-host`.
- `docs/README.md`: a row for this series.

## Validate

1. `make check`.
2. `make check-host` (read-only): `fabro validate` on every graph, with the new counts.

## Deploy

Push to `main`. Workflow changes need no `make deploy`: the next fire reads them. No `ops/` or
`backlog/scripts/` file changes in this series.

## Live trial (operator picks the repository)

1. File a scratch issue with the #2723 shape: a criterion "the screen offers a Resend email
   action" and a non-goal "No backend changes", labelled `needs-triage`.
2. Fire `issue-triage` for that repository.
3. Expect `needs-info`, a triage comment whose question quotes both sides, and a `triage.json`
   whose `contradictions` holds one entry (`fabro events <run> -p`).
4. Close the scratch issue as not planned.

The coder block is covered offline only: triage now catches the Contradiction first.
