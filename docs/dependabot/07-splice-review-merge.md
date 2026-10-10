# 07. Open the PR and splice the merge phase

## Outcome

A green patch becomes an `agent-authored` PR titled
`fix(deps): patch urllib3 security alerts in uv.lock (#412)`, whose commit-body block says
`Resolves #412`. The run satisfies the shared phase's entry contract, splices
`_shared/review-merge/` unchanged as `review_merge`, and routes on
`review_merge_outcome`:

| Outcome | What the run does |
|---|---|
| `merged` | Closes every Dependabot PR in `group.replaces` with a comment. GitHub closes the tracking issue. |
| `blocked` or `needs_human` | Puts `needs-human` on the tracking issue, so the next run skips the group. |

The phase's own Discord hooks (`merged`, `blocked`, `needs-human`) report the PR.

## Why

This is D1, D5, D14, D15 and D19: the patch gets exactly the review, validation, hygiene,
Refuter, merge gate and CI watch that every agent change gets, with no special case. A
third importer brings three rules with it. Every merge class needs a stylesheet rule
(R3), every report node needs a hook (R7), and `io-guard` must cover `refute`.

## Change

**Invoke `/fabro-workflow` first.** Read 00 (D5, D14, D15, D19, C6, C8, C9),
`docs/agents/invariants-merge.md` in full, and `backlog`'s `pr_title`/`commit_body`
logic, `open_pr` and `pr_handoff`.

### 1. `open_pr` (routing)

1. Subject:
   `fix(deps): patch <package> security alerts in <manifest>`. Cut it with `backlog`'s
   awk to `72 - len(" (#N)")` characters, then append ` (#N)`. Write
   `/tmp/fabro/commit_subject.txt`, and copy it to `pr_title.txt`.

   The `fix:` type is deliberate: on jelly-swipe and lawncare-saas, release-please cuts
   a patch release for it, which is right for a security fix. Say so in a `//` comment.
2. `/tmp/fabro/commit_body.md`: the `summary` from `patch.json` (cut to 500 characters),
   a blank line, then `Resolves #N`.
3. `/tmp/fabro/pr_body.md`:
   - `## Summary`, then the `<!-- fabro:commit-body:start -->` /
     `<!-- fabro:commit-body:end -->` block around `commit_body.md`;
   - `## Alerts` (the tracking issue's table);
   - `## Changes` (one line per `changes[]` entry: `pkg from → to (manifest; direct |
     via parent)`, plus `major: true` in bold when set);
   - `## Validation` (one line per `verification[]` entry, plus `./.fabro/setup.sh &&
     ./.fabro/ci.sh green before the PR opened`);
   - `## Replaces` (links to `group.replaces`, when non-empty).
4. Push and open, as `backlog`'s `open_pr` does: `git push -u origin HEAD`, the
   fetch-refspec widening, `gh pr view` before `gh pr create`. Labels:
   `--label agent-authored --label security --label dependencies`.
5. `echo <number> > /tmp/fabro/pr_number`. On the tracking issue, `gh issue edit N
   --add-label Review || true`.
6. Publish `pr_url`.
7. End with the three `grep` checks from `backlog`'s commit-body node: the subject is
   conventional, the body has `^Resolves #`, and the PR body has the start marker.

### 2. `pr_handoff` (no routing schema)

Copy `backlog`'s `pr_handoff`, including its comment on why it has no routing schema.
Change one thing: delete the automation-row read. `prep` already wrote
`/tmp/fabro/auto_merge_repo` from the `dependabot-<repo>` row (task 02), and `rm_setup`
reads that file. Reading the row a second time could give a different answer within
one run.

### 3. The import and the outcome routing

```dot
review_merge [import="../_shared/review-merge/review-merge.fabro"]
```

`after_merge` (no routing schema) branches on the file `/tmp/fabro/review_merge_outcome`.
The phase's three report nodes write it (`report_merged`, `report_blocked` and
`mark_needs_human`, in `review-merge.fabro`). A missing file means the phase itself
failed (`backlog` routes `review_merge` `outcome=failed` to its rescue), so treat it as
`needs_human`, with the reason "the review phase failed before it reported". Never
interpolate `{{ context.* }}` in a script: it does not exist.

- **`merged`:** for each PR in `group.replaces`, run `gh pr close P --comment
  "Superseded by #<ours>, which patched <package> to <to> and merged. Closing on behalf
  of the daily dependabot run (ADR 0022 D4)."`. Every call ends in `|| true`.
- **Otherwise:** `gh issue edit <issue> --add-label needs-human || true`, then a comment
  that links the PR and quotes `merge_block_reason` (or `needs_human_reason`) cut to
  300 bytes.
- Always exit 0.

Edges:

```
patch_validate -> open_pr        [condition="outcome=succeeded && context.patch_ci=green"]   (retarget from task 06)
open_pr -> pr_handoff            [condition="outcome=succeeded"]
open_pr -> summarize
pr_handoff -> review_merge       [condition="outcome=succeeded"]
pr_handoff -> summarize
review_merge -> after_merge
after_merge -> summarize
```

If the PR opened but `pr_handoff` failed, the PR stays open with no review, and the
summary must say so (task 08, `stopped_at = pr_handoff`).

### 4. Stylesheet: every merge class, named (R3, D14)

```
*                 { model: glm-5.3-flash; }
.dep-patch        { model: glm-5.3-flash; }
.merge-standards  { model: glm-5.3-flash; }
.merge-spec       { model: glm-5.3; }
.merge-fix        { model: glm-5.3-flash; }
.ci-fix           { model: glm-5.3-flash; }
.refute           { model: gpt-6.1-sol; }
```

Add every other class that a phase node uses (`check-graph-invariants.py` R3 lists any
you miss: `ci_fix_t2`'s class, for example). Give each one the model `pr-review` gives
it. Exception: `.merge-fix` goes to `glm-5.3-flash` here, by D14, where `pr-review` has
`glm-5.3`. Write a `//` comment that this is the one deliberate difference, and that
`review_fix`'s 15m timeout was measured on `glm-5.3`. That timeout is now unmeasured for
this graph (invariants-graph, *Timeouts*, last row). Task 10 records the first
durations.

### 5. Hooks in `workflow.toml`

Copy these from `backlog/workflow.toml`, with their comments:

- `discord-merged`, `discord-blocked` and `discord-needs-human`: `checkpoint_saved`, on
  `(^|[.])report_merged$`, `(^|[.])report_blocked$` and `(^|[.])mark_needs_human$`.
- `discord-fallback`: `stage_start` on `(^|[.])ci_fix_t2$`. The merge phase has no
  rework tiers here.

`io-guard` is already there (task 02).

### 6. The manifest

Run `ops/fabro-io-manifest.py generate`. `find_imports` sees the new import and adds the
phase's live stages as `review_merge.*` to the `dependabot` block. Do not edit
`manifest.json` for this.

### 7. Hygiene and lockfiles: a check, not a change

The hygiene counters' default `exclude_paths` already drop `package-lock.json`,
`uv.lock`, `Cargo.lock` and the other lockfiles (`review-merge.fabro`, `hygiene`). But
a repository's `.fabro/hygiene.json` is merged over the default with `.[0] + .[1]`, so
a repository that sets its own `exclude_paths` **replaces** the default list. Read each
target repository's `.fabro/hygiene.json` on `main`. If one sets `exclude_paths`
without the lockfile pattern, a lockfile diff will count as erosion, and every patch in
that repository will be blocked. Record what you find in 00's facts. If any repository
needs a fix, it is a PR **in that repository**, not a change to the shared phase
(rule 2).

## Acceptance

- `check-graph-invariants.py` is clean: R3 (every phase class has a rule), R7 (three
  report hooks), and R1/R2.
- `fabro-io-manifest.py check` passes, and the `dependabot` block lists `review_merge.*`
  stages beside `patch`.
- `./ops/test-task-gates.sh` passes: both the existing review-merge fixtures, which are
  unchanged, and the new section below.
- `git diff --stat .fabro/workflows/_shared/` is empty.

## Tests

Add `dependabot — open_pr, after_merge`, using a scratch repository with a bare origin
and a logging `gh` stub.

1. **`open_pr`, a long package name.** The subject is ≤ 72 characters with ` (#N)`, and
   it is conventional. `commit_body.md` ends in `Resolves #N`. The PR body has both
   markers.
2. **`open_pr`, the labels.** The `gh` log shows `pr create … --label agent-authored
   --label security --label dependencies` and `issue edit N --add-label Review`.
3. **`after_merge`, merged.** It closes each PR in `replaces` once, with the comment.
4. **`after_merge`, `needs_human`.** It adds `needs-human` to the issue, and the
   comment quotes the reason.
5. **`after_merge` with `gh` failing.** The node exits 0.
6. **`after_merge`, no outcome file.** It is treated as `needs_human`, and the issue is
   labelled.

`pick_group` must delete `/tmp/fabro/review_merge_outcome` (and `prep` too), so that a
stale file from an earlier phase visit cannot be read. Add the `rm -f` in this task.

## Depends on

06.
