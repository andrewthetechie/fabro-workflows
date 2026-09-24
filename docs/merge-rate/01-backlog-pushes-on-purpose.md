# `backlog` stops pushing a checkpoint after every stage

## Tracer-Bullet Outcome
A `backlog` run pushes its branch only where a node pushes it on purpose: at `open_pr`,
`deliver`, `ci_fix_gate` and `remerge_base`. It no longer pushes after every stage. The
PR gets about three CI runs instead of about 80, and the head that CI passed on is the
head that `merge` merges. An offline test, run against a real git remote, proves that
`deliver`'s `--force-with-lease` push still lands on a single-branch clone.

## User Story
As the operator, I want the run branch pushed only when a node means to push it, so that
CI is not started for every empty checkpoint commit, and a PR whose CI passed is merged
instead of timing out while CI starts over.

## Description
Four edits and one new test section:

1. `.fabro/workflows/backlog/workflow.toml`: add a `[run.run_branch]` table with
   `enabled = true` and `push = false`, and rewrite the stale comment.
2. `.fabro/workflows/backlog/workflow.fabro`, node `open_pr`: after
   `git push -u origin HEAD`, add a fetch refspec and a remote-tracking ref for the run
   branch, once only.
3. `.fabro/workflows/_shared/review-merge/review-merge.fabro`, node `merge`: add
   `timeout="50m"`, and correct one comment paragraph above it.
4. `AGENTS.md`: add one row to the "Deployment invariants" table.
5. `ops/test-task-gates.sh`: add a section that runs `open_pr` and then `deliver`
   against a real bare git repository.

### Why step 2 is required (do not skip it)
The sandbox clone is **single-branch**: `remote.origin.fetch` maps only `main`, so
`refs/remotes/origin/fabro/run/<id>` never exists. `deliver` pushes with
`git push --force-with-lease origin HEAD:"$H"`. A bare `--force-with-lease` compares the
remote against the local remote-tracking ref. When that ref is missing, git refuses
with `! [rejected] HEAD -> <branch> (stale info)`. This was reproduced on 2026-09-24.

Today it works only by accident: fabro's per-stage checkpoint push has already moved the
remote to `HEAD`, so `deliver`'s push is an `Everything up-to-date` no-op, and a no-op
succeeds. Once step 1 turns the checkpoint push off, `deliver` would fail on every run
unless `open_pr` creates the tracking ref. Once the refspec maps the branch, every later
push updates the tracking ref automatically, so the lease stays correct.

## Context Pack
- Source decisions: overview decisions 1, 2 and 3. ADR 0011 D1.
- Repo facts:
  - `pr-review/workflow.toml` already uses `enabled = true, push = false`, and its
    comment reads:
    ```toml
    #   enabled = true  -> checkpoints still commit between stages, onto the PR branch
    #   push    = false -> fabro never pushes the stale fabro/run/<id>; `deliver` pushes
    # Legal only because there is no pull-request block in this workflow. Do not add one.
    [run.run_branch]
    enabled = true
    push = false
    ```
    `backlog/workflow.toml` has no `[run.pull_request]` block either, so the same setting
    is legal there.
  - Fabro makes every checkpoint commit with `--allow-empty`, and nothing can turn that
    off (`context/fabro/lib/components/fabro-workflow/src/sandbox_git.rs:105`).
  - The shared merge phase already pushes explicitly. `deliver`, verbatim from
    `review-merge.fabro`:
    ```
    deliver [label="Push and label", shape=parallelogram, output_schema="routing",
        script="PR=$(cat /tmp/fabro/pr_number)
    H=$(cat /tmp/fabro/head_ref)
    B=$(cat /tmp/fabro/base_ref)
    if git push --force-with-lease origin HEAD:\"$H\"; then
      gh pr edit $PR --add-label ai-review-complete || echo 'WARNING: could not add the ai-review-complete label. Check that the run token has issues:write.' >&2
      echo 'The review was delivered and the merge decision did not complete; a human should finish this PR.' > /tmp/fabro/needs_human_reason
      echo '{\"context_updates\":{\"delivered\":true}}'
    else
      echo 'git push --force-with-lease was rejected: the PR branch moved while this run was working. Not overwriting.' > /tmp/fabro/needs_human_reason
      echo '{\"context_updates\":{\"delivered\":false}}'
    fi"]
    ```
    `ci_fix_gate` and `remerge_base` use the same `git push --force-with-lease origin HEAD:\"$H\"`.
  - `merge` currently has **no** `timeout` attribute, so it gets fabro's command default
    of 600000 ms (10 min). On womens-fantasy-sports#1247 it timed out at "3 pending of
    14" while it waited for CI on a head that only an empty checkpoint commit had moved.
- Non-goals: do not change `deliver`, `ci_fix_gate`, `remerge_base`, `watch_checks` or
  `pr-review`. Do not add `--delete-branch`. Do not touch `[run.meta_branch]`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  .fabro/workflows/backlog/workflow.toml
  .fabro/workflows/backlog/workflow.fabro                  (node open_pr only)
  .fabro/workflows/_shared/review-merge/review-merge.fabro (node merge: attribute + one comment)
  AGENTS.md                                                (one new table row)
  ops/test-task-gates.sh                                   (one new section)
  ```

- **Edit 1: `backlog/workflow.toml`.** Replace this exact block:
  ```toml
  # Do not push the run metadata branch. Fabro pushes fabro/meta/<run_id> to the
  # target repository on every run and never deletes it; nothing reads the pushed
  # copy (run metadata is served from the fabro API and the run store). Leaving
  # this on accumulates one dead branch per run forever. See task 12.
  # run_branch.push stays at its default (true) so in-progress work survives a
  # server crash; task 12's sweeper reaps the run branches instead.
  [run.meta_branch]
  push = false
  ```
  with:
  ```toml
  # Do not push the run metadata branch. Fabro pushes fabro/meta/<run_id> to the
  # target repository on every run and never deletes it; nothing reads the pushed
  # copy (run metadata is served from the fabro API and the run store). Leaving
  # this on accumulates one dead branch per run forever. See task 12.
  [run.meta_branch]
  push = false

  # Checkpoints still COMMIT after every stage, but fabro no longer PUSHES them
  # (ADR 0011 D1). Every checkpoint is an allow-empty commit, and once the PR exists
  # each push started a full CI run: womens-fantasy-sports#1247 carried 80 commits
  # and 78 Actions runs. The push after `watch_checks` also moved the head CI had
  # just passed, so `merge` timed out waiting for CI to start over.
  #   enabled = true  -> checkpoints still commit between stages
  #   push    = false -> only open_pr, deliver, ci_fix_gate and remerge_base push
  # open_pr widens the fetch refspec for the run branch; without that, every later
  # `--force-with-lease` push is rejected as `stale info` on this single-branch clone.
  # Nothing resumes from a pushed run branch (a server restart fails the run and any
  # retry starts from a fresh clone), so the old "survives a crash" reason is void.
  # Legal only because there is no [run.pull_request] block in this workflow.
  [run.run_branch]
  enabled = true
  push = false
  ```

- **Edit 2: `open_pr` in `backlog/workflow.fabro`.** The current node, verbatim:
  ```
      open_pr [label="Push and open PR", shape=parallelogram, output_schema="routing",
          script="set -e
  B=$(git rev-parse --abbrev-ref HEAD)
  git push -u origin HEAD
  N=$(jq -r .number /tmp/fabro/issue.json)
  ...
  echo '{\"context_updates\":{\"pr_url\":\"'$PR_URL'\"}}'"]
  ```
  Insert exactly these three lines **directly after** the line `git push -u origin HEAD`
  and before `N=$(jq -r .number /tmp/fabro/issue.json)`:
  ```
  R=\"+refs/heads/$B:refs/remotes/origin/$B\"
  git config --get-all remote.origin.fetch | grep -qxF \"$R\" || git config --add remote.origin.fetch \"$R\"
  git fetch origin \"$R\"
  ```
  - `\"` is the DOT escape for `"`. After extraction the shell sees `R="+refs/heads/$B:…"`.
  - The `grep -qxF … ||` guard matters. `open_pr` runs again when an operator answers
    `[P] Accept partial` on `human_rescue`, and `git config --add` would otherwise add a
    duplicate line.
  - `set -e` is already on. A failed `git fetch` fails the stage, and that is intended:
    a missing tracking ref breaks every later push.
  - Add this `//` comment directly above the `open_pr [label=…` line. Do not put it
    inside the script:
    ```
    // The three lines after `git push -u origin HEAD` give this single-branch clone a
    // remote-tracking ref for the run branch. Checkpoints no longer push (ADR 0011 D1),
    // so `deliver`, `ci_fix_gate` and `remerge_base` push for real, and a bare
    // `--force-with-lease` with no tracking ref is rejected as `stale info`. The guard
    // keeps a second visit (human_rescue -> [P]) from adding the refspec twice.
    ```

- **Edit 3: `merge` in `review-merge.fabro`.**
  - The first line of the node is:
    ```
        merge [label="Squash merge", shape=parallelogram, output_schema="routing",
    ```
    Change it to:
    ```
        merge [label="Squash merge", shape=parallelogram, output_schema="routing", timeout="50m",
    ```
  - In the `//` comment block above it, replace these exact lines:
    ```
        // The fix is structural rather than a longer retry: the wait for checks has to be
        // INSIDE this node. A node boundary is a checkpoint is a new head, so `watch_checks`
        // -- being a separate node -- can never be the last word before a merge, no matter
        // what it observes.
    ```
    with:
    ```
        // The fix is structural rather than a longer retry: the wait for checks has to be
        // INSIDE this node. A node boundary is a checkpoint commit, and it used to be a
        // PUSH too, so `watch_checks` -- being a separate node -- could never be the last
        // word before a merge. Since ADR 0011 D1 neither backlog nor pr-review pushes
        // checkpoints, so the remote head normally stays where `watch_checks` saw it;
        // `settle` stays as the guard for a push by ci_fix_gate or remerge_base.
        // timeout="50m": `settle` is bounded by merge_deadline, and fabro's 10m command
        // default fired first on womens-fantasy-sports#1247 and writers-app#963. 50m stays
        // under the root graphs' stall_timeout="60m".
    ```
    Leave the rest of the comment block and the whole `script` unchanged.

- **Edit 4: `AGENTS.md`.** In the "Deployment invariants" table, insert this row
  directly **after** the row that starts `` | `gh pr merge` never passes `--delete-branch` ``:
  ```
  | `backlog` does not push checkpoints (`[run.run_branch] push = false`); every push after `open_pr` is an explicit node push, and `open_pr` widens the fetch refspec for the run branch | Fabro's checkpoint commit is always `--allow-empty`, so a per-stage push started a full CI run for every stage once the PR existed (womens-fantasy-sports#1247: 80 commits, 78 Actions runs), and the push after `watch_checks` moved the head CI had passed, so `merge` timed out waiting. Without the refspec, the single-branch clone has no `refs/remotes/origin/<run branch>` and `deliver`'s bare `--force-with-lease` is rejected as `stale info` — it only ever worked because the checkpoint push had already made it a no-op. Covered by `ops/test-task-gates.sh` (real git). ADR 0011 D1. |
  ```

- **Edit 5: new section in `ops/test-task-gates.sh`.** Insert it directly before the
  final block that begins
  `# ---------------------------------------------------------------------------` followed
  by `echo ""` and `if [ "$FAIL" -eq 0 ]; then`. The harness helpers it uses exist
  already. Verbatim from the top of the file:
  ```bash
  extract() { extract_from "$GRAPH" "$1"; }       # a node from backlog/workflow.fabro
  extract_from() { ... }                          # extract_from "$SHARED" <node> for review-merge.fabro
  stage() { stage_into "$1" "$T"; }               # writes "$T/<node>.sh" with /tmp/fabro -> $T, runs sh -n
  lastjson() { grep -o '{.*}' <<<"$1" | tail -1; }
  check() { # check "<name>" "<expected>" "<actual>" -> ok/FAIL line, counts PASS/FAIL
  ```
  `$ORIG_PATH` is the PATH before any section added a stub `git`. Sections that need
  the **real** git must start from it. Write the section exactly like this:
  ```bash
  # ---------------------------------------------------------------------------
  # open_pr + deliver — the lease push once checkpoints stop pushing (ADR 0011 D1)
  #
  # REAL git, against a real bare remote, because what is under test is git's own
  # --force-with-lease rule: with no remote-tracking ref for the branch it refuses
  # as `stale info`. The sandbox clone is single-branch, so that ref only exists
  # if open_pr creates it. Case 3 is the control: it proves this section can see
  # the failure, so a green case 1 means something.
  # ---------------------------------------------------------------------------
  echo ""
  echo "open_pr + deliver (real git)"
  SAVED_PATH="$PATH"
  T="$WORK/lease"; mkdir -p "$T/bin"; stage open_pr
  extract_from "$SHARED" deliver | sed "s#/tmp/fabro#$T#g" > "$T/deliver.sh"
  if ! sh -n "$T/deliver.sh" 2>"$T/deliver.syntax"; then
      FAIL=$((FAIL + 1)); printf '  FAIL deliver is not valid POSIX sh\n'; sed 's/^/       /' "$T/deliver.syntax"
  fi
  cat > "$T/bin/gh" <<'STUB'
  #!/bin/sh
  echo "$*" >> "$GH_LOG"
  case "$1 $2" in
    "pr view") echo "https://github.com/o/r/pull/7"; exit 0 ;;
  esac
  exit 0
  STUB
  chmod +x "$T/bin/gh"
  PATH="$T/bin:$ORIG_PATH"
  export GH_LOG="$T/gh.log" GH_STATE="$T"

  BR="fabro/run/01TEST"
  lease_repo() {
      rm -rf "$T/up.git" "$T/seed" "$T/wt"
      git init -q --bare -b main "$T/up.git"
      git clone -q "$T/up.git" "$T/seed" 2>/dev/null
      git -C "$T/seed" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
      git -C "$T/seed" push -q origin HEAD:main
      git clone -q --single-branch --branch main "$T/up.git" "$T/wt"
      git -C "$T/wt" config user.email t@t
      git -C "$T/wt" config user.name t
      git -C "$T/wt" checkout -qb "$BR"
      echo work > "$T/wt/a.txt"
      git -C "$T/wt" add -A
      git -C "$T/wt" commit -qm 'fabro(01TEST): coder (succeeded)'
      printf '%s' '{"number":350,"title":"t"}' > "$T/issue.json"
      printf '%s' 'fix: a title' > "$T/pr_title.txt"
      : > "$T/pr_body.md"
      echo "$BR" > "$T/head_ref"
      echo main > "$T/base_ref"
      : > "$T/gh.log"
  }
  REFSPEC="+refs/heads/$BR:refs/remotes/origin/$BR"

  # 1. open_pr pushes and creates the tracking ref; deliver then pushes a new commit.
  lease_repo
  OUT=$( cd "$T/wt" && sh "$T/open_pr.sh" 2>&1 ); RC=$?
  check "open_pr exits 0 on a single-branch clone" "0" "$RC"
  check "open_pr creates the tracking ref" \
      "$(git -C "$T/wt" rev-parse HEAD)" \
      "$(git -C "$T/wt" rev-parse -q --verify "refs/remotes/origin/$BR")"
  echo more >> "$T/wt/a.txt"
  git -C "$T/wt" commit -qam 'fabro(01TEST): review_merge.review_fix (succeeded)'
  OUT=$( cd "$T/wt" && sh "$T/deliver.sh" 2>&1 )
  check "deliver reports delivered=true" "true" \
      "$(jq -r '.context_updates.delivered' <<<"$(lastjson "$OUT")")"
  check "deliver moved the remote branch" \
      "$(git -C "$T/wt" rev-parse HEAD)" "$(git -C "$T/up.git" rev-parse "$BR")"

  # 2. A second open_pr (human_rescue -> [P]) adds no duplicate refspec.
  ( cd "$T/wt" && sh "$T/open_pr.sh" >/dev/null 2>&1 )
  check "second open_pr keeps one refspec line" "1" \
      "$(git -C "$T/wt" config --get-all remote.origin.fetch | grep -cxF "$REFSPEC")"

  # 3. Control: push WITHOUT the widening, as the old open_pr did; deliver is refused.
  lease_repo
  ( cd "$T/wt" && git push -q -u origin HEAD 2>/dev/null )
  echo more >> "$T/wt/a.txt"
  git -C "$T/wt" commit -qam 'more'
  OUT=$( cd "$T/wt" && sh "$T/deliver.sh" 2>&1 )
  check "control: no tracking ref, deliver refused" "false" \
      "$(jq -r '.context_updates.delivered' <<<"$(lastjson "$OUT")")"
  check "control: git says stale info" "1" "$(grep -c 'stale info' <<<"$OUT")"

  PATH="$SAVED_PATH"
  unset GH_LOG GH_STATE
  ```

- Verified external contracts: git's `--force-with-lease` with no remote-tracking ref
  gives `! [rejected] HEAD -> <branch> (stale info)`. With the refspec
  `+refs/heads/<b>:refs/remotes/origin/<b>` configured and fetched, the lease push
  succeeds, and the next push succeeds too because the push updates the tracking ref.
  Verified 2026-09-24 with git on macOS against a local bare repository.
- Behavior rules: `open_pr`'s routing output (`pr_url`) is unchanged. `merge`'s script is
  unchanged.
- Error and security rules: none new. No token appears in any file.

## Acceptance Criteria
- [ ] `backlog/workflow.toml` contains a `[run.run_branch]` table with exactly
      `enabled = true` and `push = false`, and the sentence about surviving a server
      crash is gone.
- [ ] `python3.11 -c 'import tomllib; print(tomllib.load(open(".fabro/workflows/backlog/workflow.toml","rb"))["run"]["run_branch"])'`
      prints `{'enabled': True, 'push': False}`.
- [ ] `open_pr`'s script has the three new lines directly after `git push -u origin HEAD`.
      The only backslashes they contain are `\"`.
- [ ] `merge`'s first line carries `timeout="50m"`.
- [ ] `./ops/test-task-gates.sh` ends with `PASS: 191 checks` (184 before, plus 7 new),
      and the "open_pr" section's existing checks still pass.
- [ ] `AGENTS.md` has the new invariant row.

## Test Expectations
- Framework: the repository's bash harness `ops/test-task-gates.sh` (its own `check`
  function, no external framework). Command: `./ops/test-task-gates.sh`.
- Location: a new section in `ops/test-task-gates.sh`, as given above.
- Behaviour under test: real git and a stub `gh`.
  - Case 1: `open_pr` exits `0`; `refs/remotes/origin/fabro/run/01TEST` equals `HEAD`;
    `deliver` prints `{"context_updates":{"delivered":true}}` last; the bare remote's
    branch equals the clone's `HEAD`.
  - Case 2: the refspec line count is `1`.
  - Case 3: `deliver` prints `delivered:false`, and its output contains `stale info`.
- The existing "open_pr" section uses a stub `git` that returns nothing for
  `config --get-all` and exits 0 for everything else. The new lines must keep its
  existing checks green, and they do, because a failed `grep` just runs `git config --add`, which
  the stub accepts.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: 11 (validate)

## Labels
`bug`, `backlog`, `review-merge`, `priority:high`

## Estimate
Small

## Risk
3 - This changes when every `backlog` run publishes its branch. The failure it guards
against (`deliver` refused) is covered by the real-git test. If this regresses in
production, the symptom is every run ending in `mark_needs_human` with "git push
--force-with-lease was rejected".

## Validator Stopping Point
`./ops/test-task-gates.sh` prints `PASS: 191 checks`, and the `tomllib` check above
prints the `run_branch` table.

The section above was run against a scratch copy of the repository with Edit 2 applied
(2026-09-24): all 7 new checks passed, for `PASS: 191 checks` in total. If this series is
implemented in a different order, the total differs. Each task names only the checks it
adds.
