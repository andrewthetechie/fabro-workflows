# Task 07 — Replace the host `fire-pr-review.sh` copy with a wrapper

**Depends on:** 06. **Blocks:** 09. **LLM level:** local is fine.

Delete a class of drift rather than adding a check for it.

## The problem

`fire-pr-review.sh` lives in two places:

| Copy | How it stays current |
|---|---|
| in-sandbox | `trigger_review` shallow-clones `fabro-workflows@main` at fire time and runs the tracked file. **Always correct by construction.** |
| `~/bin/fabro-fabro-fire-pr-review.sh` on the host | an `scp` in the deploy runbook. **Goes stale silently.** |

Task 06 changes that script. AGENTS.md's verification block already has a `diff` for
it, but a `diff` only catches drift when somebody runs it, and the runbook it lives in
is the one you run *after* remembering to deploy.

## The fix

The host copy stops being a copy. Deploy a wrapper that reads the tracked script from
`origin/main` and execs it:

```sh
#!/bin/sh
# ~/bin/fabro-fire-pr-review.sh
#
# Do not edit, and do not replace this with a copy of the real script. This wrapper
# exists so there is exactly one copy of fire-pr-review.sh: the tracked one. It runs
# whatever is on origin/main, which is the same thing backlog's trigger_review node
# runs, so the operator's manual fire and the automated one can never diverge.
set -eu
R="${FABRO_WF_CHECKOUT:-$HOME/.fabro-deploy/fabro-workflows}"
P=.fabro/workflows/backlog/scripts/fire-pr-review.sh
[ -d "$R/.git" ] || { echo "no fabro-workflows checkout at $R" >&2; exit 1; }
git -C "$R" fetch --quiet origin main \
  || echo "warning: could not fetch; using last-known origin/main" >&2
T="$(mktemp)"; trap 'rm -f "$T"' EXIT HUP INT TERM
git -C "$R" show "origin/main:$P" > "$T"
exec sh "$T" "$@"
```

Four properties worth stating:

- **`git show origin/main:<path>` never touches the working tree.** AGENTS.md names the
  two-checkouts divergence as a live hazard — "pull before you start and push when you
  finish, or the two will diverge". A wrapper that ran `git pull` would make that worse
  on a checkout with uncommitted work. Reading the blob sidesteps it.
- **A failed fetch warns and continues.** Firing a review is not something to block on
  a network blip, and the last-known `origin/main` is still closer to correct than a
  months-old `scp`.
- **The wrapper never changes**, so the `diff` in the verification block now compares a
  static file, and `scp`-ing `fire-pr-review.sh` leaves the deploy runbook entirely.
- **`exec sh "$T" "$@"`**, not a pipe into `sh`. The real script reads `$1` and `$2`
  and is POSIX `sh` by design; piping would take its stdin.

## What this does not fix

`discord-notify.sh` has the identical two-copy shape and **cannot use this**. It runs
as a hook with `sandbox = false`, i.e. inside the fabro server container: Alpine,
`/bin/sh` and `wget`, no `git`, no checkout, no repository to read from.

It keeps its `scp` + `docker cp` deploy step. The mitigation is that its `case`
statement ends `*) msg="ℹ️ fabro run ${run_id} notification ($kind)"`, so a host copy
that predates task 08 sends a plain message instead of failing. Verified in the current
script. Note it in task 08's acceptance rather than pretending the problem is gone.

## Deploying it

Once, and then never again:

```sh
scp docs/auto-merge/fabro-fire-pr-review-wrapper.sh \
  andrew@10.10.0.32:~/bin/fabro-fire-pr-review.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-fire-pr-review.sh'
```

The wrapper is tracked in this folder, not in `.fabro/workflows/`. Nothing in a
workflow reads it, and `.fabro/workflows/**` is defined as the only tree the
automations read — putting an operator-only file there would blur the line that makes
editing `ops/` safe.

## Acceptance

- `~/bin/fabro-fire-pr-review.sh andrewthetechie/jelly-swipe 378` with `DRY_RUN=1`
  prints the same report as running the tracked script directly.
- With the checkout's working tree dirty, the wrapper still runs the `origin/main`
  version and leaves the working tree untouched (`git -C "$R" status --porcelain`
  is unchanged before and after).
- With the network down, it warns on stderr and still fires from the last fetch.
- With `FABRO_WF_CHECKOUT` pointing somewhere with no `.git`, it exits 1 with the
  message and fires nothing.
- `sh -n` passes.
- The AGENTS.md verification block's `fire-pr-review.sh` `diff` is replaced by one
  against the wrapper, and prints nothing.
