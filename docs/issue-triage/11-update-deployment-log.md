# Task 11 — Update the deployment log

**Depends on:** 09, 10. **Blocks:** nothing. **LLM level:** ordinary.

The operational history is `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac,
mode 600, deliberately outside this public tree. **Append a dated section; never edit an
earlier one.** They are a chronological record, and a corrected past entry is a record
of nothing.

Cover, for the `issue-triage` deployment:

- The date, the commit on `main`, and the order things went live — LiteLLM, then the
  settings restart, then the merge, then the automations, then the first fire.
- The twelve checks from task 09: which passed, which failed, and **which were not
  run**. A check recorded as passed because it seemed likely is the one that costs a
  live incident later.
- What the first run proved and what it did not. One `needs_info` run with one answered
  gate proves the gate; it does not prove the timeout path, the return path, or a
  `not_actionable` verdict. Name each thing still unproven.
- The gate probe's result from task 03 Part 0 — which context key actually carries the
  freeform answer — because that is a fact about the fabro version, not about this
  workflow, and the next workflow to use a gate will need it.
- Whether the four schedules ended enabled, and at what crons.
- Any defect found and fixed along the way, with the symptom as it first appeared. The
  symptom is the part that helps next time; the fix is already in git.
- The concurrency picture as it now stands: three workflows, cap 3, `backlog` schedules
  still disabled, `issue-triage` hourly on four repos, and at most one blocked gate at
  a time. If that last property ever stops holding, ADR 0002 is the thing to re-read.

## Acceptance

- The file is still mode 600 and still outside this repository.
- The new section is appended, dated, and names the commit.
- Nothing in it is a secret: run ids, issue numbers, repository names and crons, never
  a token or a webhook URL.
