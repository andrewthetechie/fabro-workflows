# 04. The Dependabot PR merge loop

## Outcome

The run works through `prs.json` one PR at a time:

1. Check that the PR's GitHub checks are green.
2. Validate the PR head merged into the current base tip with
   `setup.sh && ci.sh`, in the run's checkout.
3. Squash-merge it when both pass and the switches allow.
4. Record the outcome in `results.json` (C5).

A conflicting PR gets one `@dependabot rebase` comment. The loop stops after 5 PRs or
90 minutes (D12). Whatever happens, the checkout is back on the run branch and clean
when each node ends (D20).

## Why

This is D3 and D4. Every open Dependabot PR is merged if it validates, without an LLM
and without the shared `merge_gate`, which would refuse it. The risk is that the loop
validates in the same checkout the patch path later commits from. So the restore
step and its assertion matter as much as the merge.

## Change

Read 00: D3, D4, D5, D12, D13, D17, D20, D23, C2, C5, C9, rules 1 and 4. Read the
shared phase's `merge` and `watch_checks` nodes, which are the models for the
mergeability wait, the retry and the check polling.

Add four command nodes. Every script starts with `echo <node> > /tmp/fabro/dep/stopped_at`
and clears it on success.

### `dep_next` (routing, no timeout beyond the default)

- **On the first visit**, write `date +%s > /tmp/fabro/dep/loop_started` and
  `echo 0 > /tmp/fabro/dep/cursor`, and record the run branch's name in
  `/tmp/fabro/dep/run_branch`. The first visit is the one where `loop_started` is
  missing; `prep` deletes it.
- **Assert the checkout** (rule 4): `git symbolic-ref --short HEAD` equals `run_branch`, and
  `git status --porcelain` is empty. If either fails, write a reason, publish
  `dep_pr_next = no`, and exit 1. The unconditional edge goes to `summarize`. The
  patch path never starts from a tree it cannot trust.
- **Stop at the cap.** If `cursor >= 5`, or `now - loop_started >= 5400`, append a
  `skipped_cap` entry to `results.json` for every remaining PR and publish `dep_pr_next = no`.
- **Otherwise**, if `cursor` is below `length(prs.json)`, write the entry at `cursor` to
  `/tmp/fabro/dep/cur.json`, increment `cursor`, delete `cur_result.json`, and publish
  `dep_pr_next = yes`. If the queue is used up, publish `no`.

### `dep_checks` (routing, `timeout="20m"`)

Refresh the PR first: `gh pr view N --json state,headRefOid,mergeable`.

- **Stale:** the PR is not `OPEN`, or `headRefOid` differs from `cur.json`'s
  `head_sha`. Write `cur_result.json = {outcome: "stale"}` and publish
  `dep_checks = stale`.
- **Conflict:** `mergeable` is `CONFLICTING`. This is checked here because it is cheap.
  Write `cur_result.json = {outcome: "conflict"}` and publish `dep_checks = red`.
  `dep_merge` posts the comment.
- **Otherwise poll** `gh pr checks N --json name,bucket` every 15 seconds, using
  `watch_checks`' pattern. The node stops polling when nothing is pending, or after
  900 seconds. The inner bound is below the node's 20 minutes.

  | Poll result | `outcome` | `dep_checks` |
  |---|---|---|
  | Any `fail` or `cancel` bucket | `red` (`reason` names the first failing check) | `red` |
  | No checks after a 5-minute grace | `no_checks` (D23) | `red` |
  | Still pending at the bound | `pending` | `red` |
  | All `pass` or `skipping` | none written | `green` |

`dep_checks = red` skips `dep_validate`. A red PR is not worth 20 minutes of CI.

### `dep_validate` (routing, `timeout="20m"`)

The D20 sequence, exactly as S6 in task 01 proved it, inside **one** script:

```sh
N=$(jq -r .number /tmp/fabro/dep/cur.json)
SHA=$(jq -r .head_sha /tmp/fabro/dep/cur.json)
RB=$(cat /tmp/fabro/dep/run_branch)
B=$(cat /tmp/fabro/base_ref)
restore() { git merge --abort 2>/dev/null; git checkout -q -f \"$RB\" && git clean -fdq; }
git fetch -q origin \"$B\" \"pull/$N/head\" || { restore; …fail…; }
git checkout -q --detach \"origin/$B\"
if ! git -c user.email=… -c user.name=… merge -q --no-edit --no-ff \"$SHA\"; then
  restore; outcome conflict; exit 0
fi
timeout 1020 sh -c './.fabro/setup.sh && ./.fabro/ci.sh' > /tmp/fabro/dep/validate_$N.log 2>&1
RC=$?
restore
```

- The identity flags are needed only if S6 found that the image has no identity.
- After `restore`, run rule 4's assertion again. If it fails, exit 1 **without**
  publishing `dep_valid = pass`.
- `RC` 0 publishes `dep_valid = pass`. Any other value writes `cur_result.json = {outcome:
  "validate_failed", reason: <the last non-empty log line, cut to 300 bytes>}` and
  publishes `fail`. `RC` 124 is the inner timeout, and its reason says so.
- `tail -c 6000` of the log goes to stdout, so `fabro events` shows it.
- `fetch` here makes a shallow clone fetch history. That is acceptable, and S6 measured
  its cost.

### `dep_merge` (routing, `timeout="10m"`)

The node always ends by appending exactly one C5 entry for the current PR, then
`echo '{"context_updates":{}}'`. It acts as follows:

- **`cur_result.json` exists** (stale, conflict, red, no_checks, pending,
  validate_failed): record it and do not merge.
  - If the outcome is `conflict`, first post `@dependabot rebase` with `gh pr comment`,
    unless the PR already has a comment from the token's user that contains
    `@dependabot rebase` and is less than 24 hours old. A failed comment is a warning,
    not a failure.
- **`merge_enabled` is `0`:** record `switch_off`, with `merge_off_reason` as the
  reason.
- **Otherwise merge:**
  1. Re-read `state` and `headRefOid` from GitHub (invariants-merge, *Merging*, first
     row). If either has moved, record `stale`.
  2. Wait for `mergeable` to leave `UNKNOWN` (6 × 5 s).
  3. Run `gh pr merge N --squash --delete-branch=false --match-head-commit "$SHA"`, up
     to 3 times, 10 s apart, on a "Base branch was modified" rejection.
  4. Record `merged` or `merge_failed` with the last `gh` line.

  Keep Dependabot's own title and body: do not pass `--subject`. The repositories have
  `delete_branch_on_merge: true`, and `--delete-branch=false` only stops `gh` from
  touching the local checkout.

### Edges

```
inventory -> dep_next                 [condition="outcome=succeeded && context.dep_pr_count!=0"]   (retarget from task 03)
dep_next -> dep_checks                [condition="outcome=succeeded && context.dep_pr_next=yes"]
dep_next -> pick_group                [condition="outcome=succeeded && context.dep_pr_next=no"]
dep_next -> summarize
dep_checks -> dep_validate            [condition="outcome=succeeded && context.dep_checks=green"]
dep_checks -> dep_merge               [condition="outcome=succeeded && context.dep_checks!=green"]
dep_checks -> summarize
dep_validate -> dep_merge             [condition="outcome=succeeded"]
dep_validate -> summarize
dep_merge -> dep_next                 [condition="outcome=succeeded"]
dep_merge -> summarize
```

`pick_group` arrives in task 05. Until then, route `dep_next`'s `no` edge to `summarize`.

Every pair of conditional edges out of one node must be mutually exclusive. Do not rely
on edge order to choose between two conditions that are both true. (`!=` is in the
condition grammar: the skill's REFERENCE.md, *Condition grammar*.)

**Check the failure edges of `dep_checks` and `dep_validate` against the transition
cascade** in the skill's REFERENCE.md. A *failed* `dep_validate` must not reach
`dep_merge`, because its `outcome=succeeded` condition is false. It must fall to
`summarize`. The loop then ends early, and the summary says where.

## Acceptance

- `check-graph-invariants.py` is clean. Every node timeout is below 60m, and the
  breaker is 20.
- `./ops/test-task-gates.sh` passes the new section.
- With the merge switch off in a fixture, no `gh pr merge` call is made (the `gh` stub
  logs its calls, and the test greps the log).

## Tests

Add `dependabot — merge loop` to `ops/test-task-gates.sh`. Use a real scratch git
repository: a bare `origin` with `main`, a `pull/7/head` ref, and a clone on a run
branch. Use a `gh` stub that returns fixture JSON and logs every call.

1. **The cap.** A queue of 7 PRs. Visit `dep_next` 6 times. The 6th visit publishes `no`
   and appends 2 `skipped_cap` entries.
2. **The time cap.** `loop_started` 5,401 seconds ago: `no`, and every remaining PR is
   `skipped_cap`.
3. **A dirty checkout.** An untracked file in the clone: `dep_next` exits 1.
4. **A clean merge, green CI.** `.fabro/setup.sh` and `.fabro/ci.sh` are `exit 0`
   stubs: `dep_valid = pass`. Afterwards HEAD is the run branch, the porcelain is
   empty, and the files from the PR head are **not** in the working tree.
5. **Red CI.** `ci.sh` exits 3 and writes an untracked file: `fail`, with the reason
   from the log. Afterwards HEAD is the run branch and the untracked file is gone.
6. **A conflict.** `pull/7/head` edits the same line as a later `main` commit:
   `outcome conflict`. No `.git/MERGE_HEAD` is left, and HEAD is the run branch.
7. **The inner timeout.** `ci.sh` sleeps past a lowered bound (make the bound a
   variable the fixture can lower): `RC` 124, the reason says timeout, and the tree is
   restored.
8. **`dep_merge`, switch off.** `merge_enabled = 0`: a `switch_off` entry, and no merge
   in the `gh` log.
9. **`dep_merge`, stale.** The `gh` stub returns a different `headRefOid`: `stale`, and
   no merge.
10. **`dep_merge`, retry.** The first `gh pr merge` fails with "Base branch was
    modified", and the second succeeds: `merged`, with 2 merge calls in the log.
11. **The rebase comment is not repeated.** `conflict`, with a 2-hour-old
    `@dependabot rebase` comment from the same user: no new comment.

## Depends on

03, and 01's S5 and S6.
