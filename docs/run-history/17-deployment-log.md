# Append the deployment-log section

## Tracer-Bullet Outcome
The private deployment log carries a dated section for this deploy: what shipped, what the
first real rows proved, what they did **not** prove, and the scheduler's new `StartedAt`
that draft 14's shakedown window is now measured from.

## User Story
As the operator six months from now, I want to know what this deploy actually
demonstrated, so that I do not assume a green test suite proved something only a live run
could — or re-derive the shakedown window's start time from container metadata that has
since been overwritten.

## Description
One appended section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`, which is on the
Mac at mode 600 and **deliberately outside this public tree**. Append a dated section;
never edit an earlier one. They are a chronological record.

The half of this that matters is the honest account of what the deploy did **not** prove.
A run-history page can be fully deployed, fully green, and still have shown nothing about
the paths that are rare by design: the lost-run row needs fabro to 404, and the
`pr_lookup = "failed"` row needs GitHub to be down. Neither will have happened on deploy
day, and a log entry that quietly omits that invites a later reader to trust two branches
no live run has ever taken.

## Context Pack
- Source decisions: AGENTS.md — "The operational history — every deployment, each E2E
  result and what it did and did not prove, and the bugs found along the way — is
  `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac, mode 600, deliberately outside
  this public tree. Append a dated section rather than editing earlier ones."
- Repo facts: this is the same pattern as `docs/auto-merge/12-update-deployment-log.md` and
  `docs/pr-review-bridge/10-update-deployment-log.md`. The log is private, so it may carry
  host addresses, run ids and issue numbers freely — unlike anything in this tree.
- Non-goals: no edit to any tracked file (task 16 did those), no edit to an earlier log
  section, no 24-hour observation window.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft (no tracked file changes at all)

## Implementation Contract
- Expected files:
  ```
  ~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md     (append one dated section; mode 600)
  ```

  Nothing in this repository is modified. Confirm with `git status` afterwards: it must be
  clean.

- Interfaces and names: the section to append, matching the file's existing register.
  Every angle-bracketed value is filled from what task 15 captured — nothing is estimated.

  ```md
  ## <YYYY-MM-DD> — Scheduler run history (`docs/run-history/`, ADR 0008)

  **What shipped.** Seventeen tasks. The scheduler now writes one `run_history` row per
  released coder lease, inside the same transaction that deletes the lease, and serves it
  at `GET /history` and `GET /api/history`.

  - New table `run_history`, fourteen columns, created by `CREATE TABLE IF NOT EXISTS` on
    the existing `/data/scheduler.db`. Additive: no migration, and the previous image
    ignores it, so rollback is a rebuild rather than a database restore.
  - `Store.archive_and_release_lease` replaces the plain release on the three terminal
    paths and on the fabro-404 path. The GitHub orphan pass still uses the plain release
    and writes no row.
  - `github.fetch_pull_for_branch` resolves each run's PR by its `fabro/run/<run_id>` head
    branch, `state=all`, 5s timeout, best-effort.
  - `/history` is server-rendered, sortable by header link, with no meta-refresh and no
    JavaScript.

  **Deployed at `<StartedAt from task 15>`.** That is the scheduler container's new
  `StartedAt`, which is what draft 14's shakedown window is measured from — this deploy
  re-dated it. `fabro-fabro-1` was **not** restarted.

  **In-flight leases at deploy time:** `<coders-a: repo#issue run-id>`,
  `<coders-b: repo#issue run-id>`. Both adopted across the restart with their original
  `dispatched_at`, not released.

  **What the first rows proved.**

  | Run | Repo/issue | Box | Ending | PR |
  |---|---|---|---|---|
  | `<run id>` | `<repo>#<issue>` | `<coders-a\|coders-b>` | `<label>` | `<#N merged \| none>` |

  - The table is created on a database that already existed — `PRAGMA table_info` on the
    live volume returned all fourteen columns.
  - A real release wrote a real row: a lease taken before the deploy, released after it,
    with its `dispatched_at` intact.
  - `<if a PR merged:>` the PR lookup resolved a **closed, merged** PR — which is the case
    `state=open` would have missed entirely, and the reason `state=all` is in the contract.

  **What it did NOT prove.** Both of these are rare by design and neither occurred:

  - **The lost-run row.** `kind="lost"` needs fabro to answer `404` for a lease the
    scheduler holds. Covered by `test_reconcile.py` offline; never exercised live.
  - **The failed PR lookup.** `pr_lookup="failed"` needs GitHub to be unreachable at the
    moment of a release. Covered offline; never exercised live. `docker logs --since 10m
    fabro-scheduler | grep -c "could not resolve the PR"` printed `0`.

  Also unproven: behaviour at volume. The page renders `<N>` rows today; the 200-row
  default and the `?limit=all` escape hatch have not been exercised against a table large
  enough to matter, and will not be for months.

  **Bugs found.** `<any, with what was changed — or "None." if the deploy was clean.>`
  ```

- Verified external contracts: none. This is a record of contracts verified in task 15.

- Behavior rules:
  - **Append. Never edit an earlier section.** The file is chronological and earlier
    entries were true when written.
  - Every placeholder is filled with a value captured during task 15. If a value was not
    captured, go and read it off the host now — do not estimate one and do not delete the
    line.
  - The "What it did NOT prove" section is not optional and is not a formality. If the
    deploy happened to exercise the lost-run path or a failed lookup, move that line into
    "What the first rows proved" with its evidence; otherwise leave it exactly where it is.
  - Preserve the file's mode: `600`. Check it afterwards.
  - This task modifies **no** tracked file.

- Error and security rules: the log is private and outside this repository, so it may carry
  the host address, run ids, issue numbers and container names freely. It must still carry
  **no token** — not `GITHUB_TOKEN`, not `FABRO_API_TOKEN`, not the fabro dev token — for
  the same reason those never appear in a tracked file: a private file is one `cp` from a
  public one.

## Acceptance Criteria
- [ ] A new dated section exists at the end of
      `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`.
- [ ] No earlier section in that file was modified.
- [ ] The section names the scheduler's new `StartedAt` and states that this deploy
      re-dated draft 14's shakedown window.
- [ ] The section records both in-flight leases at deploy time and that they were adopted.
- [ ] The first-rows table has at least one real row with a real run id, repo, issue and box.
- [ ] A "What it did NOT prove" section names the lost-run row and the failed PR lookup.
- [ ] No placeholder of the form `<...>` remains.
- [ ] No token string appears anywhere in the new section.
- [ ] The file's mode is still `600`.
- [ ] `git status` in this repository is clean — this task changed nothing tracked.

## Test Expectations
There is no test runner for a log entry. These commands are the check:

```sh
# the section is appended, not spliced in
tail -n 60 ~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md

# no earlier section was touched, and only one section was added
cd ~/.fabro-deploy && git diff --stat 2>/dev/null || echo "(not a git checkout)"

# no placeholder survived
grep -n '<[A-Za-z ]*>' ~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md | tail -n 20
# -> nothing from the new section

# no credential leaked in
grep -cE 'gh[pousr]_[A-Za-z0-9]{16,}|dev-token|FABRO_API_TOKEN=' \
  ~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md
# -> 0

# the mode is intact
stat -f '%Lp' ~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md      # -> 600

# nothing tracked changed
cd ~/Documents/code/fabro-workflows && git status --short        # -> no output
```

`stat -f '%Lp'` is the macOS form; this file lives on the Mac.

## Dependencies
- Blocked by: `Deploy and verify on the host`
- Why blocked: every value in the section — the `StartedAt`, the adopted leases, the first
  rows, what was and was not exercised — is captured during that task and cannot be
  reconstructed later. Container metadata is overwritten by the next restart.
- Blocks: None

## Labels
`docs`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
1 - An append to a private file. The only real risk is writing it late, once the values it
records have been overwritten.

## Validator Stopping Point
The commands above print what is stated, and `git status` in this repository is clean.
