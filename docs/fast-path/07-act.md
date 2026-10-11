# 07. `merge-queue`: `act`

## Outcome

`act` carries out `plan.json` in order and writes one C10 entry per plan entry to
`/tmp/fabro/mq/results.json`. It merges, rebases, reruns and hands off through the GitHub
API alone. It never checks out a PR, never pushes and never waits for CI. A failure on one
PR is recorded, and the loop goes on to the next PR.

## Why

ADR 0024 D4-D7 and rules 4 and 5. `decide` chose each action from a snapshot. `act` is
where that snapshot can be stale, because a human or a concurrent sweep may have changed the
PR in the meantime. So every action carries the head that it was decided for, and GitHub
refuses it when the head moved.

## Change

Read 00: C5, C6, C8, C10, rules 3-5. Read `dep_merge` in `dependabot/workflow.fabro` and
`merge` and `report_merged` in `review-merge.fabro`, which are the models. Read the spike
file, S2, for the rebase mutation's exact shape and error text, and for the update method
that works with the host token.

### `act` (no routing schema, `timeout="8m"`)

Loop over `plan.json` with `jq -c '.[]'`. Give each entry an internal budget: stop starting
new entries after 7 minutes, and record each entry that did not start as `skipped_cap` with
detail `the run's time budget ran out`. Each branch ends with exactly one `record` call.

**`merge`:**

1. Re-read the PR: `gh pr view N --json state,headRefOid,mergeStateStatus,title,body`.
   - `MERGED`: record `merged`, detail `already merged`. A concurrent sweep or a human
     merged it.
   - Not `OPEN`, or the head is not `head`: record `waiting`, detail `the PR changed since
     the plan; the next tick decides again`.
   - `mergeStateStatus` is not `CLEAN` or `HAS_HOOKS`: record `waiting` with the status. In
     jelly-swipe, an earlier merge in this tick makes the PR `BEHIND`, and the next tick
     rebases it.
2. Recheck the live title with check 11's regex. Extract the body block from the live body
   with the same `awk` as `merge_gate`. If either fails, hand off (below) with
   `merge_gate`'s sentence.
3. `gh pr merge N --squash --delete-branch=false --match-head-commit "$HEAD" --subject "$TITLE" --body-file <block>`,
   up to 3 attempts, 10 s apart, on `Base branch was modified` only. Never pass
   `--delete-branch` (invariants-merge, *Merging*), and never pass `--admin`. The queue has
   not validated the result itself, unlike `dep_merge`'s D26 case.
4. Success: record `merged`. Then, for each issue in the body block's `Resolves #N` lines,
   `gh issue edit N --remove-label Review || true`, as `report_merged` does. Then post
   `Merged by the merge queue (run <run>): checks green on <short head>.` on the PR, with
   `|| true`.
5. Failure: record `failed` with the last non-empty `gh` line, cut to 300 bytes. Do not hand
   off on a merge failure. The next tick decides again with fresh state.

**`rebase`:**

1. `gh api graphql` with `updatePullRequestBranch(input: {pullRequestId: $id,
   expectedHeadOid: $head, updateMethod: <S2's method>})`, returning
   `pullRequest { headRefOid }`.
2. Success: post the C5 marker with the returned head. Retry the comment 3 times, 5 s apart.
   Record `rebased`, with detail `<short old> -> <short new>`.
3. An `expectedHeadOid` mismatch (S2 step 5's text): record `waiting`, detail `the head moved
   before the rebase`.
4. Any other error: hand off with `retryable: the queue could not rebase this PR on <base>:
   <first line of the error>`. This covers a conflict that `mergeStateStatus` did not show,
   and a workflow-scope refusal (00, open questions).

**`rerun`:** for each id in `rerun_ids`, run `gh run rerun <id> --failed`. Record `reran` if
at least one succeeded. If none succeeded, record `failed` with the first error. The next
tick sees attempt 2, or the same attempt 1, and decides again.

**`handoff`:** post the C6 comment, then
`gh pr edit N --remove-label merge-on-green --add-label ai-review-needs-human`. Record
`handed_off` with the reason. If the label call fails, record `failed`. The PR still carries
`merge-on-green`, so the next tick plans the same hand-off again. That is the intended retry.

**`wait`:** record `waiting` with the plan's reason. Make no call.

`act` always exits 0 once the loop has run. Only a failure to read `plan.json` exits 1 and
writes `stop_reason`.

## Acceptance

- `make check` passes.
- The fixtures below pass.

## Tests

Add `merge-queue — act` to `ops/test-task-gates.sh`. Use a `gh` stub that answers from
per-call fixtures and logs every call:

1. `merge`, the PR unchanged: one `gh pr merge` with `--match-head-commit <head>`,
   `--squash`, `--delete-branch=false`, the live title as `--subject`, and no `--admin`.
   Then `--remove-label Review` on the resolved issue, then the comment.
2. `merge`, the re-read shows `MERGED`: `merged` with `already merged`, and no merge call.
3. `merge`, the re-read shows a new head: `waiting`, and no merge call.
4. `merge`, the re-read shows `BEHIND`: `waiting`, and no merge call.
5. `merge`, the first attempt says `Base branch was modified` and the second succeeds:
   `merged`, with 2 merge calls.
6. `merge`, `gh pr merge` fails once with another error: `failed`, 1 merge call, and no
   label change.
7. `rebase` success: a C5 comment with `from=<old> to=<new>`, and `rebased`.
8. `rebase`, the expected head is stale: `waiting`, and no comment.
9. `rebase`, a conflict error: a C6 comment starting with `retryable:`, and the label swap.
10. `rerun` with two ids, one fails: `reran`, and 2 rerun calls.
11. `handoff`: the C6 comment comes before the label call. Its first line matches
    `^<!-- fabro:merge-queue handoff head=`.
12. The budget: a plan of 3 entries, with the time budget set to 0 through a test hook such
    as `MQ_BUDGET_SECONDS`, as `dependabot`'s `DEP_` hooks work: three `skipped_cap`
    entries, and no `gh` call.
13. `results.json` has exactly one entry per plan entry in every case.

## Depends on

06, and 01 (S2).
