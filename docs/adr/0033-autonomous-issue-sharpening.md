# The factory rewrites unverifiable acceptance criteria itself, on the local boxes, with no human approval

**Status:** accepted (2026-11-16, issue #68)

Issues the precheck rates `unverifiable` merge 11–14% of the time, against 41% for `ready`
issues (`GET :32280/api/background/calibration`). The code is often right. The PR is held
because a criterion names a command `.fabro/ci.sh` does not run, asks for PR-description text,
says "must not drop", or asks for a check only a human can make. The Refuter cannot credit any
of these. #68 specified an Elephant-Goldfish loop that proposes a rewrite for a human to
approve. The operator runs the factory dark, so there is no human to approve it.

## Decisions

**1. The factory may edit an issue's body without a human.** This is the first stage that does.
It edits only `## Acceptance criteria` and `## Post-deploy checks`, only through a closed list of
five transformations, and only after a fresh three-lens audit round comes back clean. Nothing is
ever deleted: a criterion that cannot be made verifiable moves to `## Post-deploy checks`, which
the Refuter never reads and the PR body lists. A command gate, not a model, checks that every
other byte of the body is unchanged. The original body is kept in a marked comment.

**2. A proposal that does not converge is not applied.** On a capped round count, churn, a gate
refusal or repeated faults, the issue runs as written. That is the factory's behaviour before
this ADR, so a failure costs nothing beyond the status quo.

**3. Sharpening is a scheduler lease, not a background-lane kind.** ADR 0031's lane fills only
the gaps between a coder loop's box stages, and on two-slot boxes those are rare (#78). A
sharpening needs several model sessions in a row and would rarely finish there. It takes a coder
pool like an issue run, ahead of issue runs, with at most one active across the fleet. It runs
on the box's `-bg` model row, so it has no hosted fallback, and it ignores the peak-hours block
because it spends no hosted credits.

**4. It is always on.** This is a deliberate exception to "no new kind turns itself on"
(`AGENTS.md`, ADR 0031). The deploy turns it on. The off path is a revert and
`make deploy-scheduler`. The operator chose this because the factory is meant to run unattended,
and a switch nobody turns on is a feature nobody gets.

**5. One sharpening per original body.** The re-precheck runs inside the same run. If it still
says `unverifiable`, the issue runs as written, and the scheduler does not sharpen it again.

## Consequences

- The Refuter, `ralph`, `backlog` and the merge gates do not change.
- Every sharpened issue carries two comments: the original body, and the change table with the
  audit's findings. They are the audit trail. A bad rewrite is reverted by hand from the first.
- The weekly retro files `sharpen:<state>` classes, and the measure section compares the merge
  rate of applied issues with the `unverifiable` and `ready` rates. If applied issues do not
  merge measurably above the `unverifiable` rate, this ADR is revisited.

The design and its contracts are in `docs/plans/issue-sharpening/`.
