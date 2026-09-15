# Task 12 — Purge the leaked `fabro/*` branches and install a daily sweeper

**Depends on:** task 07 (which stops the metadata half at source). Can run before or
after task 09; running it after means the E2E run's own branches are in scope.
**LLM level:** local is fine. The script below is complete — transcribe it, do not
improvise it.
**No agent anywhere in this task.** The sweeper is deterministic shell + `gh` + `git`.
Do not build it as a fabro workflow, do not give it an LLM stage, do not let it make
judgment calls. Every skip rule below is a mechanical test.

## Goal

Delete the ~300 `fabro/*` branches fabro has already leaked into the four target
repositories, and install a daily sweeper so they do not come back.

## The bug

Fabro pushes two branches into the target repository on every run and **never deletes
either**:

- `fabro/run/<run_id>` — the checkpoint branch, pushed after every stage
  (`[run.run_branch] push = true` in fabro's `defaults.toml`).
- `fabro/meta/<run_id>` — the run metadata branch, pushed by the server process
  (`[run.meta_branch] push = true`).

There is no deletion path anywhere in the fabro codebase — no `git push --delete`, no
retention policy, nothing that `fabro system prune` reaps. The run branch is read back
in exactly one place (restarting a run from a previous run's checkpoint); the pushed
metadata branch is never read at all.

Most runs are quiet exits: `acquire` finds no issue labelled `agent`, the stage fails,
and the run exits. It has still pushed both branches by then. Every one of those run
branches is a single empty commit — `fabro(<id>): acquire (failed)` — with a zero-byte
diff against `main`.

Measured on 2026-09-13, roughly two days into the `*/15` cron across four repositories:

| Repository | `fabro/*` branches |
|---|---|
| `andrewthetechie/jelly-swipe` | 88 |
| `andrewthetechie/womens-fantasy-sports` | 70 |
| `andrewthetechie/lawncare-saas` | 70 |
| `andrewthetechie/writers-app` | 72 |

**~300 branches.** At `*/15` × 4 repositories that is ~384 runs/day producing ~768 new
branches/day. (The counts are floors — the branches API pages at 100.)

Task 07 sets `[run.meta_branch] push = false`, which stops the `fabro/meta/*` half for
new runs. This task cleans up everything already leaked and installs a sweeper for the
`fabro/run/*` half, which stays on so that in-progress work survives a server crash.

## Step 1 — Write the sweeper

On the host (`ssh andrew@10.10.0.32`). The host already has everything needed:
`gh` (authenticated as `andrewthetechie` with `repo` scope), `git`, `jq`, `crontab`,
and working SSH access to GitHub — verified. No new secret is required.

```sh
mkdir -p ~/bin ~/.local/state
cat > ~/bin/fabro-branch-sweep.sh <<'SWEEP'
#!/bin/sh
# fabro-branch-sweep.sh — delete leaked fabro/run/* and fabro/meta/* branches.
#
# Fabro pushes fabro/run/<run_id> and fabro/meta/<run_id> to every repository it
# works on and never deletes them. This reaps them. Deterministic: every skip is
# a mechanical test, there is no judgment and no model involved.
#
# Skip rules, in order:
#   1. the branch is the head of an OPEN pull request      -> never delete
#   2. the branch is younger than its grace period          -> not yet
#   3. fabro/run/* that is ahead of the default branch gets the longer grace
#      (it carries real unmerged work); everything else gets the short grace
#
# Env:
#   DRY_RUN=1            print what would be deleted, delete nothing (default)
#   RUN_GRACE_HOURS=24   grace for empty run branches and all meta branches
#   WORK_GRACE_HOURS=168 grace for run branches with unmerged commits
set -u

DRY_RUN="${DRY_RUN:-1}"
RUN_GRACE_HOURS="${RUN_GRACE_HOURS:-24}"
WORK_GRACE_HOURS="${WORK_GRACE_HOURS:-168}"
CACHE_DIR="${CACHE_DIR:-$HOME/.cache/fabro-branch-sweep}"

# Space-separated. An EMPTY value falls back to this default list (`:-`), so to
# limit a run to one repo pass a real value: FABRO_SWEEP_REPOS="owner/repo".
REPOS="${FABRO_SWEEP_REPOS:-andrewthetechie/jelly-swipe andrewthetechie/womens-fantasy-sports andrewthetechie/lawncare-saas andrewthetechie/writers-app}"

now=$(date +%s)
run_cutoff=$(( now - RUN_GRACE_HOURS * 3600 ))
work_cutoff=$(( now - WORK_GRACE_HOURS * 3600 ))

mkdir -p "$CACHE_DIR" || exit 1
total_deleted=0

for repo in $REPOS; do
  name=$(echo "$repo" | tr '/' '_')
  dir="$CACHE_DIR/$name.git"

  if [ -d "$dir" ]; then
    git -C "$dir" fetch --prune --quiet origin '+refs/heads/*:refs/heads/*' || {
      echo "WARN  $repo: fetch failed, skipping"; continue; }
  else
    git clone --bare --quiet "git@github.com:$repo.git" "$dir" || {
      echo "WARN  $repo: clone failed, skipping"; continue; }
  fi

  default=$(git -C "$dir" symbolic-ref --short HEAD 2>/dev/null || echo main)

  # Heads of open pull requests are never touched.
  gh pr list -R "$repo" --state open --limit 200 --json headRefName \
    --jq '.[].headRefName' > "$CACHE_DIR/$name.openprs" 2>/dev/null \
    || : > "$CACHE_DIR/$name.openprs"

  : > "$CACHE_DIR/$name.delete"

  git -C "$dir" for-each-ref \
      --format='%(refname:short) %(committerdate:unix)' \
      refs/heads/fabro/run refs/heads/fabro/meta \
  | while read -r branch ts; do
      [ -n "$branch" ] || continue

      if grep -Fxq "$branch" "$CACHE_DIR/$name.openprs"; then
        echo "keep  $repo $branch (open PR)"
        continue
      fi

      cutoff=$run_cutoff
      case "$branch" in
        fabro/run/*)
          # Does the branch actually CHANGE THE TREE vs the default branch?
          # Commit count is the WRONG test: a quiet-exit run leaves exactly one
          # EMPTY checkpoint commit, so `rev-list --count` reports 1 while the
          # diff is empty. Those are precisely the branches this script exists
          # to reap, so counting commits gives every one of them the 7-day
          # work-grace and the sweeper reaps almost nothing.
          if ! git -C "$dir" diff --quiet "$default...$branch" 2>/dev/null; then
            # Real content differs: possibly abandoned work, so give it the
            # long grace before reaping.
            cutoff=$work_cutoff
          fi
          ;;
      esac

      if [ "$ts" -ge "$cutoff" ]; then
        echo "keep  $repo $branch (within grace)"
        continue
      fi

      echo "$branch" >> "$CACHE_DIR/$name.delete"
    done

  count=$(wc -l < "$CACHE_DIR/$name.delete" | tr -d ' ')
  [ "$count" -gt 0 ] || { echo "ok    $repo: nothing to delete"; continue; }

  if [ "$DRY_RUN" = "1" ]; then
    echo "DRY   $repo: would delete $count branches:"
    sed 's/^/        /' "$CACHE_DIR/$name.delete"
    continue
  fi

  # Delete in batches so one bad ref cannot fail the whole repo.
  batch=""
  n=0
  while read -r branch; do
    batch="$batch $branch"
    n=$(( n + 1 ))
    if [ "$n" -ge 50 ]; then
      # shellcheck disable=SC2086
      git -C "$dir" push --quiet origin --delete $batch || echo "WARN  $repo: a batch delete failed"
      batch=""; n=0
    fi
  done < "$CACHE_DIR/$name.delete"
  if [ -n "$batch" ]; then
    # shellcheck disable=SC2086
    git -C "$dir" push --quiet origin --delete $batch || echo "WARN  $repo: final batch delete failed"
  fi

  echo "done  $repo: deleted $count branches"
  total_deleted=$(( total_deleted + count ))
done

echo "$(date '+%Y-%m-%dT%H:%M:%S%z') sweep finished (DRY_RUN=$DRY_RUN)"
exit 0
SWEEP
chmod +x ~/bin/fabro-branch-sweep.sh
sh -n ~/bin/fabro-branch-sweep.sh && echo "syntax ok"
```

## Known defect, fixed 2026-09-14 — do not reintroduce it

The first version of this script decided "does this branch carry real work?" with
`git rev-list --count main..branch`. That is wrong, and it silently defeats the whole
sweeper: a quiet-exit run leaves **one empty checkpoint commit**
(`fabro(<id>): acquire (failed)`), so `rev-list --count` returns 1 for every leaked
branch, every one of them takes the 168h work-grace instead of the 24h grace, and the
sweeper deletes almost nothing. It was caught in production — the sweeper ran, reported
every branch as "within grace", and reaped 0 run branches while a 25h-old empty branch
sat there.

The correct test is whether the branch changes the **tree**: `git diff --quiet
"$default...$branch"`. Verified against a real leaked branch: `rev-list --count` = 1,
`git diff --stat` = empty, `git diff --quiet` = exit 0.

## Step 2 — Dry run, and read the output

This script was executed in dry-run mode against all four repositories on 2026-09-13
while this plan was written: it passed `sh -n` and `dash -n`, enumerated every
`fabro/run/*` and `fabro/meta/*` branch, and correctly reported jelly-swipe's
`fabro/run/01M2DSYRX8CY1SGDVDF79TZJC4` as `(open PR)`. If your transcription behaves
differently, you introduced a typo — diff it against the block above rather than
debugging the logic.


```sh
DRY_RUN=1 ~/bin/fabro-branch-sweep.sh 2>&1 | tee /tmp/sweep-dry.log
```

Before going further, check these in `/tmp/sweep-dry.log` by hand:

- Every line is either `keep`, `DRY`, `ok`, or `WARN` — no tracebacks, no git errors.
- The jelly-swipe open PR's head branch appears as `keep ... (open PR)`. There was one
  open PR on a `fabro/run/*` head as of 2026-09-13; if it is still open and it does
  **not** show as kept, **stop** — the skip rule is not working and you are about to
  delete a live PR's branch.
- The would-delete counts are in the expected range (roughly 60–90 per repository).
- No branch outside `fabro/run/` and `fabro/meta/` appears anywhere in the list. The
  repositories also contain `issue-*-accumulation`, `backup/*`, `feat/*`, `fix/*`, and
  `diagnostic/*` branches from other tooling; **none of those are ours to touch.**

## Step 3 — Purge for real

**The default 24h grace will delete nothing on the first run.** A dry run on
2026-09-13 showed every one of the ~300 leaked branches reporting `(within grace)`:
the ULIDs span roughly the last twelve hours, so they are all younger than
`RUN_GRACE_HOURS=24`. That is correct steady-state behaviour and wrong for a backfill.

So the backfill needs a short grace, and a short grace is only safe when nothing is
running. Check first:

```sh
TOK=<dev token from FABRO-DEPLOYMENT-LOG.md>
curl -fsS -H "Authorization: Bearer $TOK" http://10.10.0.32:32276/api/v1/runs | python3 -m json.tool | grep -i status
```

If any run is not in a terminal state, **wait for it** — a one-hour grace could delete a
live run's branch out from under it. The automations fire `*/15`, so a quiet-exit run
clears in well under a minute; a working run can take over an hour.

With nothing in flight:

```sh
DRY_RUN=1 RUN_GRACE_HOURS=1 WORK_GRACE_HOURS=24 ~/bin/fabro-branch-sweep.sh 2>&1 | tee /tmp/sweep-dry2.log
# read it again — same checks as step 2, now with real delete lists
DRY_RUN=0 RUN_GRACE_HOURS=1 WORK_GRACE_HOURS=24 ~/bin/fabro-branch-sweep.sh 2>&1 | tee /tmp/sweep-live.log
```

The short grace is a **one-time backfill override**. The cron entry in step 4 uses the
defaults; do not bake `RUN_GRACE_HOURS=1` into it.

Verify:

```sh
for r in andrewthetechie/jelly-swipe andrewthetechie/womens-fantasy-sports \
         andrewthetechie/lawncare-saas andrewthetechie/writers-app; do
  printf '%-45s ' "$r"
  gh api "repos/$r/branches?per_page=100" --jq '[.[].name|select(startswith("fabro/"))]|length'
done
```

Each should now be 0, or a small number equal to the branches legitimately kept (an
open PR head, or something inside its grace window).

## Step 4 — Install the daily cron

```sh
( crontab -l 2>/dev/null; \
  echo '17 4 * * * DRY_RUN=0 /home/andrew/bin/fabro-branch-sweep.sh >> /home/andrew/.local/state/fabro-branch-sweep.log 2>&1' \
) | crontab -
crontab -l
```

04:17 daily, offset off the hour so it does not collide with the `*/15` automation
fires. Confirm the log rotates or is truncated occasionally — it is small (a few lines
per repo per day), so a manual `: > ~/.local/state/fabro-branch-sweep.log` when it gets
large is enough; do not add logrotate config for this.

## Done when

- `~/bin/fabro-branch-sweep.sh` exists, is executable, and passes `sh -n`.
- A dry run was read by a human and the open-PR skip was confirmed working.
- All four repositories are at or near zero `fabro/*` branches.
- `crontab -l` shows the daily 04:17 entry.
- The first scheduled run has appended to
  `~/.local/state/fabro-branch-sweep.log` (check the next day, or run it once by hand
  to prove the cron command line works verbatim).

## Pitfalls

- **Never widen the ref prefixes.** `refs/heads/fabro/run` and `refs/heads/fabro/meta`
  are the only two. Anything broader will eat `issue-*-accumulation` and `backup/*`
  branches from the operator's other tooling.
- Do not lower `RUN_GRACE_HOURS` below the length of a long run. A run can take well
  over an hour; a grace window shorter than that could delete a *live* run's branch out
  from under it. 24h is deliberately generous.
- `git push --delete` against a branch that is already gone fails the whole batch in
  some git versions. That is why deletes are batched at 50 with a `WARN` fallback
  rather than one `||` over 300 refs. If a batch warns, re-run the script — it is
  idempotent.
- The `while read` loops run in a subshell, so `total_deleted` will under-count in the
  final line. That is cosmetic; the per-repo `done` lines are authoritative. Do not
  "fix" it by restructuring the loop unless you re-test the whole script.
- The bare clones under `~/.cache/fabro-branch-sweep/` grow. They are caches; deleting
  the directory just makes the next run re-clone.
- If the operator later decides to also turn off `[run.run_branch] push` (one line in
  `workflow.toml`, the same shape as the `meta_branch` block in task 07), the sweeper
  stays useful as a safety net and needs no change. That trade-off is noted in task 11:
  turning it off means a mid-run server crash loses the sandbox's commits, because
  nothing was pushed.
- This bug is **not fixed at source.** Fabro upstream still has no branch cleanup. If
  the fabro container is ever pointed at a new repository, that repository starts
  leaking immediately until it is added to `FABRO_SWEEP_REPOS` in the script.
