# Task 07 — The observation window (human task)

**Depends on:** 06. **Blocks:** 08. **LLM level:** none — this task is executed by the
operator, not an agent. It exists as a task because "watch that PRs get generated for a
few days" is not executable until it has a checklist, exit criteria, and stop
conditions. Decision 10.

**Five days** from the first canary run. Short enough to hold attention, long enough
for the slow paths (a 30-minute triage gate expiring unanswered, a multi-hour merge
budget, a weekend's worth of queue) to occur naturally.

## The daily checklist — once a day, ~5 minutes, in any order

1. **Discord scan.** Read everything since the last check. Every `🔴` and `🟡` gets an
   explanation you believe before you move on. `❓` triage questions are the only ones
   with a clock — 30 minutes before they post to the issue.
2. **Monitor state.** `ssh andrew@10.10.0.32 'cat ~/.local/state/fabro-monitor.state
   2>/dev/null; tail -5 ~/.local/state/fabro-monitor.log'` — a state file with content
   means a condition is firing; an empty log means cron stopped, which the heartbeat
   should have caught. Did it?
3. **The pipeline moved.** `gh pr list -R andrewthetechie/jelly-swipe --label
   agent-authored` and the merged-PR list. Over five days the canary should open and
   merge several PRs; zero movement with a non-empty `agent` queue is a finding.
4. **Labels tell the truth.** `gh issue list -R andrewthetechie/jelly-swipe --label
   agent-in-progress` is empty or holds exactly the issue a live run is on; no
   `agent-stuck` without a story; closed issues lost their `Review` label (the merge
   path's job).
5. **Spot-check one run.** Pick any run from the day, `fabro events <run> -p`, and read
   the routing. You are looking for edges taken for the reason you expect — a quiet-exit
   with work present, or a merge that skipped `watch_checks`, is a bug whether or not
   the outcome looked right.
6. **Queues.** `gh issue list` per repo for `agent` and `needs-triage`. Filing new
   `needs-triage` issues on the empty repos during the window is part of the window —
   it feeds triage and prepares fleet expansion (task 08). This is also your answer if
   C5 fires: the alert worked, now go write issues.
7. **Merged quality.** Read one merged squash commit's subject and body against the
   contract (Conventional Commits title, marker-block body, `Resolves #N`). On
   jelly-swipe, confirm release-please reacted sanely to the title type.
8. **Log the day.** One dated line in the deployment log: runs, merges, alerts, and
   anything unexplained. Uneventful is a line too — it is what makes the exit criteria
   evidence rather than vibes.

## Exit criteria — all must hold to proceed to task 08

- Five consecutive days with a daily log line each.
- **≥ 3 PRs squash-merged** on jelly-swipe by the full chain (backlog → bridge →
  pr-review → merge), auto-merge armed throughout.
- **Zero unexplained notifications** — every `🔴`/`🟡`/monitor alert in the window has
  a cause recorded in the log, and that cause is either fixed or a filed issue.
- **Zero stop conditions hit**, or each one hit produced a fix and the window extended
  by two clean days from the fix (not restarted — the factory is being tuned, not
  graded).
- Triage promoted at least one `needs-triage` issue to `agent` somewhere in the fleet.

## Stop conditions — any one disables the canary schedule immediately

The PUT recipe from task 06, flag to `false`; then treat as a workflow bug. Mirrored
deliberately from the auto-merge shakedown, plus the operational ones:

- a merge lands on a PR a human touched (any commit not authored `noreply@fabro.sh`,
  any human review or comment before merge);
- `ci_fix` edits outside the PR's changed set and the gate does not catch it;
- a merge with any check not `pass`/`skipping`;
- a blocked merge labeled `ai-review-needs-human` for an *expected* block;
- the monitor stayed silent while something it owns happened (a failed container, a
  dead scheduler with schedules on, a stuck run past 3h);
- a quiet-exit on jelly-swipe while `agent` issues were open — the acquire path is
  broken, and that is the whole factory;
- a double-fire: two pr-review runs on the same PR, or two backlog runs on the same
  issue;
- Discord goes quiet for a full day while the log shows runs completed — the hooks
  broke, and the notification surface is itself now suspect.

## What this task produces

Evidence. The daily log lines plus the exit criteria are what task 08 cites when it
enables the rest of the fleet, and what task 10 renders into the deployment log's
turn-on entry. If the window fails, the log says why and what changed — that is the
deliverable either way.
