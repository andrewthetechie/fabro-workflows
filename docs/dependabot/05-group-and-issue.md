# 05. `pick_group`, `track_issue` and `sync_base`

## Outcome

After the merge loop, the run:

1. Groups the open alerts (C4).
2. Drops the alerts that a PR merged in this run fixed.
3. Applies D16's and D22's skip rules.
4. Picks the highest-ranked group.

For that group it finds or files the tracking issue (C6), writes
`/tmp/fabro/issue.json` and `group.json`, and merges the current base into the run
branch. On jelly-swipe on 2026-10-10 it would pick `pip:uv.lock:urllib3` (high,
runtime, 3 alerts) over `npm:frontend/package-lock.json:brace-expansion` (medium,
development), and file `fix(deps): patch urllib3 security alerts in uv.lock`.

## Why

This is D2, D5, D6, D8, D16 and D22. The tracking issue is what lets the shared merge
phase review a patch: `Resolves #N`, `linked_issues.json`, and the spec and refute
criteria. It is also the factory's record of a group it gave up on.

## Change

Read 00: D2, D5, D6, D8, D16, D21, D22, C4, C5, C6, C8, C9, and 01's S4 answer.

### `pick_group` (routing)

Inputs: `alerts.json`, `prs.json`, `results.json`.

1. `merged_fixes` = the union of `fixes` over the C5 entries with `outcome == "merged"`.
   Drop those alerts.
2. Write the alerts with `first_patched == null` to `no_patch.json`, and drop them.
3. Group the rest by `(ecosystem, manifest, package)` into C4 records. `replaces` = the
   numbers of the PRs whose `fixes` hit the group and whose C5 outcome is neither
   `merged` nor `switch_off`, plus PRs in `prs.json` that the loop never reached
   (`skipped_cap`) **only if** their `fixes` hit the group.

   A `switch_off` PR passed validation, so it is not replaced.

   A PR still in the queue, or `skipped_cap`, keeps its group in the patch path. This
   is a deliberate choice. The alternative leaves an alert open behind a queue that may
   never reach it. The cost is that a later run may merge that Dependabot PR after our
   patch: Dependabot then closes its own PR as superseded, or the PR merges as a no-op.
4. Drop every group with a `switch_off` PR in its `fixes`: that PR will merge once the
   switch is on.
5. Rank by D16: severity rank (`critical` 0, `high` 1, `medium` 2, `low` 3), then scope
   (`runtime` 0, anything else 1), then `-(alerts | length)`, then the oldest
   `created_at`.
6. Walk the ranked list. For each group, look up its tracking issue by marker:
   `gh issue list --state all --label security --search "fabro:dep-group key=<key>"
   --json number,state,stateReason,closedAt,labels,body`, then filter on the exact
   first-line marker in jq. Search is fuzzy, so the marker is the check. Apply C6's
   skip rules in order:
   - **Rule 2** needs the S4 query. A `Review` issue with no open linked PR gets
     `gh issue edit --remove-label Review`, and the group stays eligible.
   - **Rule 4** acts as well: it reopens the issue, adds `needs-human`, and comments
     `The patch for this group merged on <closedAt>, but alerts <list> are still open
     after 3 days. A human should check whether the fix reached the lockfile.` Then it
     skips the group.

   The first group that is not skipped wins.
7. Write `group.json` with `issue = <number or 0>`, and publish `patch_group = <key>`.
   If every group was skipped, or none exists, publish `patch_group = none` and write
   `skipped_groups.json` (key and reason) for the summary.

### `track_issue` (no routing schema)

1. Create the C8 labels if they are missing (`gh label create … 2>/dev/null || true`):
   `security`, `dependencies`, `ai-generated`, `needs-human`, `Review`. Copy the colors
   and descriptions from the repositories that already have them. Look them up with
   `gh label list` during implementation, and keep them the same everywhere.
2. Render the C6 body with one jq program into `/tmp/fabro/dep/issue_body.md`.
   - Severity, vulnerable range, GHSA and CVE come from `alerts_raw.json` (the C1
     projection drops the range, so take `.security_vulnerability.vulnerable_version_range`
     from the raw file).
   - The table is one row per alert, sorted by number.
3. If `group.json`'s `issue` is 0: `gh issue create --title <C6 title> --body-file …
   --label security --label dependencies --label ai-generated`. Parse the number from
   the URL, as `arch-review`'s `file_issues` does.

   Otherwise: `gh issue edit N --body-file …`. If the issue is closed (the D22 window
   expired, but rule 4 did not fire because the alerts closed and reopened), reopen it.
4. Write the number back into `group.json`. Then write
   `gh issue view N --json number,title,body,labels,url,state > /tmp/fabro/issue.json`:
   `open_pr` and the patch prompt read this file.
5. If the create fails, exit 1. The edge goes to `summarize`, which reports that the
   group was chosen but its issue could not be filed. A patch with no issue cannot pass
   `merge_gate`, so there is no point continuing.

### `sync_base` (no routing schema, `timeout="5m"`)

```sh
B=$(cat /tmp/fabro/base_ref)
git fetch -q origin \"$B\"
git -c user.email=… -c user.name=… merge -q --no-edit \"origin/$B\"
```

Do this exactly as D21 describes. The identity flags are needed only if S6 found the
image has none. A conflict "cannot happen" because the run branch holds only empty
checkpoint commits. If it happens anyway, run `git merge --abort` and exit 1: that
invariant has broken, and a human should look. Afterwards, rule 4's assertion: on the
run branch, clean.

### Edges

```
dep_next -> pick_group               [condition="outcome=succeeded && context.dep_pr_next=no"]   (retarget from task 04)
inventory -> pick_group              [condition="outcome=succeeded && context.dep_pr_count=0"]   (retarget from task 03)
pick_group -> track_issue            [condition="outcome=succeeded && context.patch_group!=none"]
pick_group -> summarize              [condition="outcome=succeeded && context.patch_group=none"]
pick_group -> summarize
track_issue -> sync_base             [condition="outcome=succeeded"]
track_issue -> summarize
sync_base -> summarize               (task 06 adds the edge to `patch`)
```

## Acceptance

- `check-graph-invariants.py` is clean, and `./ops/test-task-gates.sh` passes.
- On the jelly-swipe fixture, `group.json` is `pip:uv.lock:urllib3`, with alerts
  `[72,73,74]` and `first_patched` `2.8.0`.

## Tests

Add `dependabot — pick_group, track_issue, sync_base`, with `gh` stubbed and logging.

1. **The ranking.** jelly-swipe's 5 alerts: urllib3 first.
2. **Merged this run.** `results.json` has a `merged` PR whose `fixes` are `[72,73,74]`:
   brace-expansion is picked.
3. **No patched version.** Every alert has `first_patched: null`: `patch_group = none`,
   and `no_patch.json` holds them all.
4. **`needs-human`.** The urllib3 issue is open with `needs-human`: brace-expansion is
   picked, and `skipped_groups.json` names urllib3 and the reason.
5. **A stale `Review`.** The urllib3 issue is open with `Review`, and the S4 query finds
   no open PR: the label is removed (the stub logs `issue edit … --remove-label
   Review`), and urllib3 is picked.
6. **Inside the D22 window.** The issue was closed `COMPLETED` 1 day ago: skipped.
7. **Past the D22 window.** The issue was closed 4 days ago: it is reopened with
   `needs-human` and a comment, and skipped.
8. **`replaces`.** A PR that fixes 72 has outcome `red`: `replaces: [<pr>]`. The same PR
   with `switch_off`: the group is dropped.
9. **The issue body.** The rendered body's first line is the exact marker, and the table
   has one row per alert.
10. **`sync_base`.** In a scratch repository whose run branch has two empty commits
    and whose `origin/main` moved by one commit: after the node, HEAD contains the new
    commit, and the porcelain is empty.

## Depends on

04 and 01's S4.
