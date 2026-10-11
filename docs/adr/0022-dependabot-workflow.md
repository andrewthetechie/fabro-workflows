# A daily `dependabot` run per repository merges Dependabot PRs and patches open alerts

**Status:** accepted (2026-10-11; proposed 2026-10-10). The operator settled the decisions in a
grilling session on 2026-10-10, and added D26 (`--admin` for a behind-base Dependabot PR) during
go-live. The task series is `docs/dependabot/`.

Dependabot security updates are on in all four target repositories, yet on 2026-10-10
eleven alerts were open and **none** had a Dependabot PR: jelly-swipe 5 (urllib3 runtime,
brace-expansion dev), lawncare-saas 5 (brace-expansion dev), writers-app 1 (rustls
runtime). Meanwhile Dependabot version-update PRs wait for a human: jelly-swipe had 6
open. Nothing in the factory looks at either. We add a fifth workflow package,
`dependabot`. Once a day per repository, it merges every open Dependabot PR that
validates, and it patches one group of open alerts through the same review and merge gates as
every other agent change.

## D1. A new package that splices the shared merge phase

The package is `.fabro/workflows/dependabot/`. It has two paths in one graph:

1. The **merge path** is deterministic: command nodes with no agent.
2. The **patch path** has one agent stage, `patch`, which edits manifests and lockfiles.
   It then splices `_shared/review-merge/` unchanged, so a security patch gets the
   standards and spec reviews, `review_fix`, `validate`, the diff-hygiene counters, the
   Refuter, `merge_gate` and the CI watch that a `backlog` PR gets.

**Rejected:** a dispatcher that sends each Dependabot PR to `pr-review` and files `agent`
issues for `backlog`. `backlog` runs on the coder boxes and waits in the scheduler's
queue, and the operator wants this work on its own model. **Also rejected:** the
upstream `patch-cves` example as it stands: one agent with `gh` and no gates.

## D2. One run per repository per day, bounded

A run first works the Dependabot PRs, then patches **at most one alert group**. An
alert group is one package in one manifest, and its patch closes every alert in it. The next
group waits for the next day. The Dependabot PR loop stops after 5 PRs or 90 minutes,
whichever comes first. Each PR therefore has its own rollback, and a run's duration is
bounded.

## D3. Every open Dependabot PR is in scope, and the shared gate stays strict

The merge path works every open PR that Dependabot authored, version updates and
major bumps included, not only the ones that fix an alert. PRs that fix an open alert go
first, then the oldest. The shared `merge_gate` would refuse every one of them, because
it requires `agent-authored`, fabro-authored commits and `Resolves #N`. So the merge path
has its own gate and leaves the shared one unchanged:

- The PR's GitHub checks are green.
- `./.fabro/setup.sh && ./.fabro/ci.sh` pass on the PR head **merged into the current base
  tip**, inside the run's checkout. This validates a PR that is behind base without a
  rebase.
- The PR is still open, its head is the commit that was validated, and the auto-merge
  switches allow a merge (D7).

A PR that conflicts with base gets an `@dependabot rebase` comment and waits for the
next run. No LLM reads a Dependabot PR: for a dependency bump, the test suite, the lint
and the CI are the review.

## D4. A failed Dependabot PR becomes a patch

When a Dependabot PR that fixes an alert fails validation, its alerts are treated as
having no PR. The patch path fixes them on its own branch, where the agent can also fix
the breakage. When that patch merges, the run closes the replaced Dependabot PR with a
comment. We never push to Dependabot's branch: Dependabot stops managing a branch that
someone else has pushed to, and those commits would fail the authorship rule.

## D5. A patch resolves a tracking issue

The merge phase is built around an issue. `merge_gate` requires `Resolves #N`, and
`spec` and `refute` judge the diff against the linked issue. So the patch path files one
GitHub issue per alert group, or reuses the open one. The issue is labelled `security`,
`dependencies` and `ai-generated`, and it lists the alerts. It never gets `agent`, so
the coder scheduler ignores it. The issue is also the human-readable record of a group
the factory could not fix.

## D6. Its own model, by stylesheet

The graph's stylesheet puts every stage that writes code on `glm-5.3-flash`: `*`,
`.dep-patch`, `.ci-fix`, `.merge-fix` and `.merge-standards`. `.merge-spec` stays on
`glm-5.3` and `.refute` stays on `gpt-6.1-sol`, as in `backlog` and `pr-review`, so the
spec check and the Refuter are as strong here as anywhere (ADR 0013 D6). The fallbacks
are the same as `pr-review`'s. A model change is a commit, live on the next fire. A
settings-overlay role alias (the ADR 0003 pattern) was rejected, because the live
overlay is the riskiest file on the host to edit.

## D7. Merges obey the existing host switch and a new per-repository switch

Both paths honor `FABRO_AUTO_MERGE` and an `auto_merge=` token in the description of the
repository's `dependabot-<repo>` automation row. They read both exactly as `pr_handoff`
reads the `pr-review-<repo>` row: an absent token means on. With either switch off, the
run still validates and opens PRs, but it merges nothing. The rows are created with the
switch on, and there is no observation period: hand-fired test runs come first, then the
schedules go live.

## D8. Daily schedules, outside the coder scheduler

Four `dependabot-<repo>` automations have schedule triggers that are **enabled**, like
`arch-review`'s, at 05:00, 05:20, 05:40 and 06:00 host time. No stage uses a coder box,
so the coder scheduler does not admit these runs. It counts only its own leases
(`dispatch.py`, `busy_repos`).

## Consequences

- A fifth package splices `_shared/review-merge/`. Every rule that says "both importing
  stylesheets" or "every graph that imports the phase" now means three graphs:
  stylesheet classes (R3), report hooks (R7) and `io-guard`.
- A run holds one of the server's 4 `max_concurrent_runs` slots for up to about three
  hours. Fabro queues at the cap and does not reject (ADR 0001), so the cost is delay
  for `backlog` and `pr-review` in the 05:00-08:00 window. Task 10 measures it.
- The merge path validates inside the run's own checkout, then restores the run branch
  before the node ends. A node killed mid-validation would leave a foreign tree for the
  checkpoint to commit, so the validation command runs under an inner `timeout` that is
  shorter than the node's.
- Dependabot closes an alert some time after the fix lands on the default branch. A
  patched group whose alerts are still open shortly after its issue closes is
  therefore not patched again at once (contract C6 in the series).
