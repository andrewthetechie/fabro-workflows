# FlakeRadar feeds the scout's flaky source and the merge phase's red-check decision

**Status:** proposed (2026-10-16). The operator's decisions from the scout deploy session
(issue #32). The task series is `docs/flakeradar/`. It amends ADR 0027 D5 (Flaky jobs) and
ADR 0011 D2 (one rerun before `ci_fix`).

FlakeRadar (`flakes.herrington.services`, the operator's own service) already ingests the JUnit
reports of lawncare-saas, womens-fantasy-sports and writers-app. It scores every test, proves a
flake when one commit both fails and passes a test, sorts failures into categories (`timing`,
`assertion`, `network`, `environment`, `other`), and scores CI jobs only on the failures no
test explains. fabro used none of it: the scout's flaky source saw whole CI jobs through
GitHub's rerun attempts, and the merge phase sent every red check after one rerun to the
`ci_fix` agent, flaky or not.

## D1. The scout files flaky tests from FlakeRadar, and flaky jobs only when no test explains them

In a repository FlakeRadar covers, the scout's flaky source reads FlakeRadar's flaky-tier tests
and files one issue per test. The issue carries the test's identity, failure category, recent
failure messages and last failing commit. A GitHub-attempts job is filed only when FlakeRadar
does not know the job, or rates it flaky on unexplained failures. Otherwise its flakes are its
tests', and the action is `explained`. jelly-swipe, and any job without a JUnit report, keep the
ADR 0027 job source unchanged.

Rejected: replacing the job source. A job without a JUnit report would lose flaky detection.

## D2. A red check that fails only on known-flaky tests gets a second rerun, then a hold

After the merge phase's one rerun (ADR 0011 D2), if CI is still red and FlakeRadar says every
failing check's failures are known-flaky tests, the phase reruns the failed jobs once more,
under the same budget guards. Green continues the merge. Still red holds the PR for a human,
with a reason that names the tests and their scout issues. `ci_fix` is never sent to fix a flake
the PR did not cause: a CI fixer is the stage most likely to skip, loosen or retry a failing
test. A red check with any failure FlakeRadar does not explain goes to `ci_fix` as before, and
`ci_fix.md` names the known-flaky failures so the agent leaves them alone.

Rejected: hold at once (fewer merges for no extra safety), and `ci_fix` with context only (the
agent may still touch the flaky test).

## D3. FlakeRadar is advisory: unreachable means the existing path

Any FlakeRadar read that fails, times out, or finds no report leaves fabro on today's path: the
ADR 0027 job source with no `explained` filter, and `ci_fix` after one rerun. No decision rests
on FlakeRadar's silence.

## D4. The scout is the only filer, and FlakeRadar records its issues

FlakeRadar's own GitHub filing stays off. When FlakeRadar can link an externally filed issue
(andrewthetechie/flakeradar#6), the scout records each issue it files, so the dashboard shows
it and FlakeRadar's filing could be turned on without duplicates. D2 needs FlakeRadar to report
the failed tests of one CI run (andrewthetechie/flakeradar#5). Both are filed upstream, because
any CI gate or external filer can use them.

## D5. The coder agents do not get the FlakeRadar MCP in v1

The issue body carries the traceback and history. The MCP (`/mcp/`, bearer token) is added to
the agent stages once scout flaky PRs show that a coder needs fresher data than the issue has.
That defers putting a FlakeRadar token on the host.
