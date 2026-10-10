# 01. Spike: the facts the graph needs

## Outcome

`docs/dependabot/spike-<date>.txt` answers seven questions with commands and output. It
also replaces every "task 01's finding" placeholder in `00-overview-and-contracts.md` (C3's
slug map, C6's "open PR links this issue" test) with the verified answer. Nothing
under `.fabro/` changes.

## Why

The graph depends on behavior that nobody has checked here yet: the token a sandbox gets,
how Dependabot names branches and lays out grouped PRs, and whether a validation in the
run's own checkout can be fully undone. Each one fails at run time, not in `fabro
validate`, so it is checked once now, cheaply.

## Change

Run each check, then paste the command and its trimmed output into the spike file under
a heading per question. Redact any token: no `gh[pousr]_…` string goes into the file.

### S1. The sandbox token can read alerts, comment and merge

Write a throwaway package **outside this repository** (in the scratchpad) with one
command node:

```sh
gh api "repos/$R/dependabot/alerts?state=open&per_page=1" --jq length
gh api repos/$R --jq .permissions
```

Give it these permissions:

```toml
[run.integrations.github.permissions]
vulnerability_alerts = "read"
contents = "write"
pull_requests = "write"
issues = "write"
checks = "read"
```

Copy it into the container and run it once against `andrewthetechie/jelly-swipe` with
`fabro run` on the `python` environment. Record whether the alert read returns a number.
If it returns 403, the remedy is a token with the right scope, and the series stops here
until the operator fixes it.

`permissions.push: true` shows the token can merge. Do **not** merge anything in the
spike.

### S2. Dependabot's branch names and the slug-to-ecosystem map

Run this for each repository:

```sh
gh pr list -R andrewthetechie/$r --author app/dependabot --state all --limit 200 \
  --json number,title,headRefName,state,mergedAt,body
```

Then record:

- Every distinct `dependabot/<slug>/` prefix, and the alert `ecosystem` it maps to
  (`npm`, `pip`, `rust`, `github_actions`, …). Cross-check with the ecosystems in
  `GET .../dependabot/alerts?state=fixed`.
- How the directory shows up in the branch name for a manifest outside `/` (jelly-swipe
  `frontend/`).
- The title forms. Expected: `bump X from A to B`, `update X requirement from A to B`,
  and grouped `bump the <group> group … with N updates`.
- For a grouped PR, how the body lists each package and its versions (the table or list
  Dependabot writes). Give one real example, trimmed.

### S3. The match rule against history

Take every alert with `state=fixed` in the four repositories (63 + 68 + 13 + 45 on
2026-10-10). For each one, find the merged Dependabot PRs whose `mergedAt` falls within
48 hours before the alert's `fixed_at`, and apply C3 as drafted. Count:

- the alerts C3 matches to exactly one such PR,
- the alerts it matches to none (fixed by hand or by a version update the rule missed),
- the false matches (C3 says yes, but the PR did not touch that package's version).

Record the counts and every miss and false match. Adjust C3 until it has **no false
matches**: a false match only reorders the queue and wrongly fills `replaces`, but
`replaces` closes a PR. Misses are acceptable.

### S4. "An open PR links this issue"

Create a scratch issue and a draft PR whose body says `Resolves #<issue>` in a **sandbox
repository**, not one of the four. Then record which of these show the link:

```sh
gh issue view N --json closedByPullRequestsReferences
gh pr view P --json closingIssuesReferences
```

Delete both afterwards. Write the working query into C6.

### S5. `@dependabot rebase` from the token's user

Find a Dependabot PR that is behind base in jelly-swipe. If none is, record that this
part waits for the first hand-fired run in task 10. Comment `@dependabot rebase` as the
host's `gh` user, and record whether Dependabot answers within 10 minutes. Also record
what Dependabot says when the PR was edited by someone else, if that is visible.

### S6. In-place validation leaves no trace

In a scratch clone of jelly-swipe inside the `fabro-python:local` image (`docker run
--rm -it fabro-python:local sh` on the host, then `git clone`), run exactly what D20
describes:

```sh
B=$(git rev-parse --abbrev-ref HEAD)
git fetch origin pull/<N>/head:dep-pr
git checkout -q --detach origin/main
git merge --no-edit --no-ff dep-pr
timeout 1020 sh -c './.fabro/setup.sh && ./.fabro/ci.sh'; echo rc=$?
git merge --abort 2>/dev/null; git checkout -q -f "$B" && git clean -fdq
git status --porcelain | wc -l
git rev-parse --abbrev-ref HEAD
```

Record the `rc`, the timings, and that the last two lines print `0` and `$B`. If
`ci.sh` leaves untracked files that are not ignored, list them: `git clean -fd` must
remove them, and they must not be under an ignored path that the next `setup.sh`
trusts.

Also record whether the merge commit needs an identity (`user.email`) in the image. If
it does, the node sets `-c user.email=… -c user.name=…` on that one command.

### S7. Alert closure lag

For the fixed alerts in S3 that matched a merged PR, compute `fixed_at − mergedAt`.
Record the median and the maximum. If the maximum is over 3 days, D22's 3-day window
is too short: raise it and record the new value in 00.

## Acceptance

- `spike-<date>.txt` exists with S1-S7, every command shown, no token in it.
- C3 in 00 carries the final slug map and has no false match on history (S3).
- C6 in 00 names the query from S4.
- If S1 fails, the operator is told, and the series is marked blocked in 00's status
  line.

## Tests

None. This task writes no code.

## Depends on

Nothing. Needs ssh to the host for S1 and S6, and a scratch GitHub repository for S4.
