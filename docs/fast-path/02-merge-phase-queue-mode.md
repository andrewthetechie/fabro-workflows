# 02. The merge phase's queue mode and `report_queued`

## Outcome

`_shared/review-merge/` has a second ending. When the importing graph wrote
`/tmp/fabro/merge_mode = queue` and `merge_gate` passes, the phase posts its review comment
with the approval marker (C4), labels the PR `merge-on-green`, and exits with
`review_merge_outcome = queued`. It does not run `watch_checks`, `ci_fix_*` or `merge`.
Without the file, every importing graph behaves exactly as today. Every entry into the phase
removes `merge-on-green` from the PR (rule 2). Discord has a `queued` message.

## Why

ADR 0023 D5 and ADR 0024 D2. This task is the seam between the two halves: the fast lane
writes the mode (task 03), and the merge queue reads the marker (task 06). It goes first
because both halves depend on its contract.

## Change

Read 00: C1, C3, C4, rules 1 and 2. Read `review-merge.fabro` in full: the header contracts,
`rm_setup`, `render.jq`, `merge_gate`, `report_merged`, `report_blocked`, `mark_needs_human`,
`done` and the edges.

### `rm_setup`

- Read `/tmp/fabro/merge_mode`. Anything but exactly `queue` is `watch`. Write the
  normalised value back to the file.
- Remove the label: `gh pr edit "$PR" --remove-label merge-on-green >/dev/null 2>&1 || true`.
  The node is `set -e`, so the `|| true` is required. A failure here is safe: the queue
  refuses a head that it has no approval for (rule 3).
- Add `merge_mode` to the routing object that it prints:
  `{"context_updates":{"review_merge_outcome":"needs_human","merge_mode":"<mode>"}}`. Keep
  it the last JSON object on stdout.

### `render.jq`

Add kind `queued`:

- The heading is `## AI PR review complete - queued to merge on green`, with the same
  ` - risk N/5` suffix that the other success kinds have.
- `shipped` is true for `queued`, so the fixes appear under "Fixes applied".
- Add a section after "Diff hygiene":

  ```
  ### Merge queue

  Every merge gate passed. This run did not wait for CI. The merge queue (ADR 0024) squash-merges
  this PR when the checks on head `<short sha>` are green and GitHub reports it mergeable. If CI
  fails, the queue reruns the failed jobs once, then labels the PR `ai-review-needs-human`. Any push
  that the queue did not make cancels this approval.
  ```

  `render_comment.sh` passes the head as `--arg qhead` (empty for every other kind).

### `report_queued` (new, routing, `timeout="2m"`)

```
exec 3>&1 >>/tmp/fabro/report.log 2>&1
```

Then, in order:

1. Write `queued` to `/tmp/fabro/review_merge_outcome`. If any later step fails, write both
   reason files and exit 1 (the invariant of `invariants-merge.md`, *The merge decision*,
   rows 2 and 3). Every refusal below uses this one `fail` helper.
2. `H=$(gh pr view "$PR" --json headRefOid --jq .headRefOid)`. Fail if it is empty.
3. `git fetch origin "$H"` if `git cat-file -e "$H^{commit}"` fails. Fail unless
   `git rev-parse "$H^{tree}"` equals `git rev-parse 'HEAD^{tree}'`. The reason is
   `The PR head on GitHub is not the tree this review approved, so the PR was not queued.`
4. Render the comment: `sh /tmp/fabro/render_comment.sh queued "$H"`. Prepend the C4 marker
   as line 1. The run id is `basename "$(git rev-parse --abbrev-ref HEAD)"`. Unlike
   `report_merged`, a render failure here fails the node, because the marker must be posted
   with the review.
5. `gh pr comment "$PR" --body-file /tmp/fabro/pr_comment.md`. Fail on error.
6. `gh label create merge-on-green …` (C1, `|| true`), then
   `gh pr edit "$PR" --add-label merge-on-green`. Fail on error.
7. Print `{"context_updates":{"merge_mode":"queue","queued_head":"<H>"}}` to fd 3, then
   exit 0. Print only that object (invariants-merge, *Reporting*, last row).

### `done`

Accept `queued`: `case "$O" in merged|blocked|needs_human|queued) ;; …`.

### Edges

```
merge_gate    -> watch_checks      [condition="context.merge_eligible=true && context.merge_mode!=queue", weight=10]
merge_gate    -> report_queued     [condition="context.merge_eligible=true && context.merge_mode=queue", weight=10]
merge_gate    -> report_blocked    [condition="context.merge_eligible=false"]
merge_gate    -> mark_needs_human
report_queued -> done              [condition="outcome=succeeded"]
report_queued -> mark_needs_human
```

Update the header comment: the entry contract gains `/tmp/fabro/merge_mode` (optional), and
the exit contract gains `queued`.

### Hooks, the notify script and the checker

- Add `report_queued` to `REPORT_NODES` in `ops/check-graph-invariants.py`.
- Add a `discord-queued` hook to **each** importing `workflow.toml` (`backlog`, `pr-review`,
  `dependabot`), shaped exactly like `discord-merged`:
  `event = "checkpoint_saved"`, `matcher = "(^|[.])report_queued$"`,
  `script = "/storage/scripts/discord-notify.sh queued"`. `pr-review` and `dependabot`
  never write `queue`, but the hook keeps R7 uniform and stays correct if one of them ever
  does.
- `discord-notify.sh`: add
  `queued) msg="🕒 fabro approved a PR and queued it to merge on green${subject}" ;;`.
  `report_queued` must publish `pr_url` and `issue_number` the way `report_merged` does, so
  the enrichment fills in the subject and the PR link. Add them to step 7's object.

## Acceptance

- `make check` passes, R7 included.
- With no `merge_mode` file, the phase's routing is unchanged: the existing
  `ops/test-task-gates.sh` merge-phase sections pass unchanged.
- `make check-host`: `PrReview` and `Dependabot` each gain 1 node and 3 edges
  (`merge_gate -> report_queued` and the two edges out of `report_queued`), and `Backlog`
  gains the same from this task. Record the new counts in `AGENTS.md`'s baseline
  paragraph in this commit.

## Tests

Add `review-merge — queue mode` to `ops/test-task-gates.sh`, with a real scratch git
repository and a `gh` stub that logs its calls:

1. `rm_setup` with no file: prints `merge_mode":"watch"`, and the `gh` log has the
   `--remove-label merge-on-green` call.
2. `rm_setup` with `queue`: prints `queue`. With `QUEUE`: prints `watch`.
3. `report_queued` with the stub's `headRefOid` equal to HEAD: the comment's first line
   matches `^<!-- fabro:merge-queue approved head=[0-9a-f]{40} run=`, the comment call comes
   **before** the label call in the log, the outcome file says `queued`, and stdout holds
   one JSON object.
4. `report_queued` with a `headRefOid` whose tree differs from HEAD: exit 1, both reason
   files written, and no label call.
5. `report_queued` with a failing `gh pr comment`: exit 1, and no label call.
6. `done` with `queued` in the file: publishes `queued`.
7. The `merge_gate` edges: with the DOT reader in `ops/check-graph-invariants.py`, assert that the two
   `merge_eligible=true` edges cannot both match (one has `merge_mode!=queue`, the other
   `merge_mode=queue`).

## Depends on

01 (only S3, for the comment-author field name the queue will read).
