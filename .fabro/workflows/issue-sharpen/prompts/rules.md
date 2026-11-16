### Writing criteria the factory can verify

A PR merges on its own only when the Refuter can mark every acceptance criterion
`met` from the code, the diff and `.fabro/ci.sh`'s run. A criterion it cannot check
holds the PR for you however good the code is: in the two weeks to 2026-11-01, 70
of the 85 agent PRs with an unmet criterion were held by one of the first two
cases below (#57). Skipping triage means nothing rewrites these for you, so apply
the rules yourself (the precheck's `unverifiable` comment flags a miss before the
run):

1. **No criterion needs text in the PR description.** No stage after `open_pr`
   writes the PR body, and the Refuter often runs before the PR exists.
   - "The PR explains why X is unreachable" → "a code comment at the line says
     why X is unreachable".
   - "The uncovered remainder is listed in the PR" → the same code-comment form,
     or drop it.
   - "The PR lists before/after coverage" → drop it. Keep the before numbers in
     the issue as context; the after numbers are in GitHub CI's coverage summary.
2. **A command criterion names only a command `.fabro/ci.sh` runs.** Read the
   repository's `ci.sh` first. Otherwise name a state the Refuter can read from
   the diff.
3. **"Coverage must not drop" becomes a raised threshold:** "`coverage.thresholds`
   in `<config>` (or `fail_under`) is raised to the new totals, rounded down".
   `ci.sh` enforces the threshold, and the Refuter verifies the raise in the diff.
   It also ratchets coverage, which is what the criterion was for.
4. **A check only a human can make after merge goes under `## Post-deploy
   checks`.** The pipeline cuts that heading from the Refuter's input and lists
   it in the PR body for you.

A change to a file under `.github/workflows/` cannot be the agent's work at all:
its token cannot push one, so the run stops before opening a PR. Make that edit
yourself.

### The closed list of transformations

A change row names exactly one of these. Nothing else is allowed, and nothing is ever deleted:
a criterion that none of the first four can make verifiable takes `to_post_deploy`.

| Transformation | From | To | Required evidence |
|---|---|---|---|
| `pr_text_to_code` | a criterion that needs PR-description text | the same content as a code comment, a docs file, or a repository file the diff adds | none beyond the original quote |
| `command_to_ci` | a command `.fabro/ci.sh` does not run | the `ci.sh` command that covers the same files | the `.fabro/ci.sh:N` line (or the script line it calls) |
| `command_to_behaviour` | a command `ci.sh` does not run, with no `ci.sh` equivalent | a behaviour the Refuter can read from the diff (a test that asserts X exists) | `path:line` where the behaviour lives or will live |
| `no_drop_to_threshold` | "coverage must not drop" and the like | "`<config key>` in `<file>` is raised to the new totals, rounded down" | the `path:line` of the threshold key |
| `to_post_deploy` | a human-only check, a screenshot, a manual or staging check, a `.github/workflows/` change, or anything else the agent cannot do or the Refuter cannot verify | moved to `post_deploy_checks`, verbatim or lightly reworded | none |
