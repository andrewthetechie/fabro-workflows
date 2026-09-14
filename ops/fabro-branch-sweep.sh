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
          # Commit count is the wrong test: a quiet-exit run leaves exactly
          # one EMPTY checkpoint commit, so rev-list --count reports 1 while
          # the diff is empty. Those are precisely the branches to reap.
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
