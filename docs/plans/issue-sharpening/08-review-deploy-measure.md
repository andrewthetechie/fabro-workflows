# Task 08: review, push, deploy, and the first runs (reviewer and operator only)

Not for the implementing agent.

## Review

1. Run the `/code-review` skill on the series' commits against this folder and #68, then read
   each graph node in the order it really runs. Pay attention to these:
   - `draft_gate`'s preservation rules against a real issue body from each of the five
     repositories, including one with a `fabro:` marker and one with a triage section;
   - that no lens manifest input lies outside `audit/`;
   - the empty paths: no `ci.sh`, no criteria, a recheck failure, a post failure after apply;
   - the shell under `mawk`;
   - the hash pin;
   - the lease's release on every terminal state;
   - that no issue label is written by the lease.
2. Trace a sharpen run through real dispatch: `ENTRYPOINTS`, `JOB_WORKFLOWS` (none: it is not a
   lane kind), `CODER_LOOP_WORKFLOWS` in both the scheduler and the manifest generator.
3. `make check` (with `gawk` first on `PATH`), then `make check-host`. Record
   `IssueSharpen (N nodes, E edges)` and its warnings in `AGENTS.md` as verified.

## Deploy

1. Push. Workflow packages are live on the push, but nothing dispatches `issue-sharpen` until the
   scheduler deploy.
2. Create `precheck-unverifiable` in all five target repositories, with the description from
   task 04: `/setup-repo-for-factory` re-check, or `gh label create` per repository.
3. `make deploy-scheduler` from `~/.fabro-deploy/fabro-workflows` at `origin/main`. Then
   `make verify-host`.
4. Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`.

## First runs

1. Read the back-fill log line (how many issues were held) and the queue page's *Sharpening*
   strip.
2. Follow the first run with `fabro events <run> -p`:
   - `claim`'s routing;
   - the draft's inputs;
   - `draft_gate`;
   - each lens's inputs (only `audit/`);
   - `audit_gate`'s result;
   - if applied: both comments, the body edit, the recheck marker, and the label removed.
3. Read the first five applied issues against their `fabro:sharpen-original` comments. One
   Substitution, or one untouched criterion changed, stops the loop: revert the scheduler commit
   that dispatches (`make deploy-scheduler`), restore each affected body from its original
   comment, and file the defect.
4. Check that no sharpen stage fails over to a hosted model. There should be no
   `agent.route.failover` to a non-box provider in its events, because the `-bg` rows have no
   fallback.

## Two-week read (two weeks after the deploy)

From `GET :32280/api/background/measure`:
- the merge rate of applied issues against the `unverifiable` and `ready` rates;
- the outcome mix;
- box-minutes;
- each stage's p95 duration, to set D21's timeouts from measurements (write the numbers beside
  them in the graph comments).

Post the result on #68 and close it if the applied issues merge measurably above the
`unverifiable` rate. Otherwise name what is left.
