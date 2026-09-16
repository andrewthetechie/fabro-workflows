# Task 10 — Deploy, shake down with the switch off, then merge for real

**Depends on:** 09. **Blocks:** 11, 12. **LLM level:** yes.

The rollout *is* the kill switch. There is no dry-run mode, deliberately — an
unremoved dry-run flag is its own footgun, and the switch has to work anyway.

## 1 — Set the host switch off **before** pushing

Not after. A commit on `main` is live on the next fire.

```sh
ssh andrew@10.10.0.32 "grep -q '^FABRO_AUTO_MERGE=' ~/fabro/.env \
  || echo 'FABRO_AUTO_MERGE=0' >> ~/fabro/.env"
ssh andrew@10.10.0.32 'grep FABRO_AUTO_MERGE ~/fabro/.env'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d'
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 sh -c "echo \$FABRO_AUTO_MERGE"'
```

The last line must print `0`. A variable in `.env` that never reached the container is
a kill switch that does nothing, and everything after this point assumes it is armed.

## 2 — Deploy what does not come from `main`

Workflow changes need no deploy — every automation resolves `.fabro/workflows/**` from
`main` at fire time. These do not:

```sh
cd ~/Documents/code/fabro-workflows && git pull --ff-only

scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'

scp docs/auto-merge/fabro-fire-pr-review-wrapper.sh \
  andrew@10.10.0.32:~/bin/fabro-fire-pr-review.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-fire-pr-review.sh'

FABRO_API_URL=http://10.10.0.32:32276/api/v1 FABRO_DEV_TOKEN=<dev token> \
  ./ops/provision-server-state.sh
```

Then confirm the host matches the repo — each of these prints nothing:

```sh
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-fire-pr-review.sh' \
  | diff - docs/auto-merge/fabro-fire-pr-review-wrapper.sh
```

Note the second `diff` now targets the wrapper. `fire-pr-review.sh` itself is no longer
deployed at all — task 07.

## 3 — Shakedown: three runs with the switch armed

Fire `pr-review` against three PRs that reach `merge_gate` and differ in outcome. On
`jelly-swipe`, where there is branch protection and a merge does not deploy.

Each run must:

- reach `merge_gate`, block on `auto_merge=0`, and say so in the PR comment;
- render the `blocked` report with the `complete` heading and a `### Not auto-merged`
  section naming the host switch;
- keep `ai-review-complete`, not `ai-review-needs-human`;
- fire **no** Discord notification, because `report_merged` was never reached;
- leave the PR unmerged and its branch intact.

This is the evidence a dry-run mode would have produced, from the mechanism that has to
work anyway. Read the comments. If a reason line does not make sense to a human reading
the PR cold, fix it now — it is the only thing the blocked path outputs.

Also check `fabro events -p` for the routing decision itself, not just the outcome:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro events <run> -p'
```

The selected edge out of `merge_gate` must be the conditional
`merge_eligible=false` one, not an unconditional fallthrough. A node whose routing went
inert still *looks* like it worked from the PR comment.

## 4 — Prove the per-repo switch independently

Flip `jelly-swipe`'s automation label off, leave the host switch off, fire, confirm the
reason names the repo switch. Then flip the label back on. Both switches must be
demonstrably load-bearing before either is trusted.

## 5 — First live merge

Arm the repo, disarm the host:

```sh
ssh andrew@10.10.0.32 "sed -i 's/^FABRO_AUTO_MERGE=0/FABRO_AUTO_MERGE=1/' ~/fabro/.env"
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d'
```

**Subject: `jelly-swipe#378`.** It is the right first test for three reasons: it is
open and `agent-authored`; it is the PR whose `lint` check fails on exactly the title
bug this stage exists to fix; and `jelly-swipe` is the only repo with branch protection
and the only one where a merge does not immediately deploy.

Two things about it need a hand first, and both are the point rather than a workaround.

**Its title is wrong and no `backlog` run will re-open it.** Task 02 fixes the
derivation for *future* PRs; #378 was opened by the old one. Correct it by hand to
`chore: Remove the unreferenced 1 MB frontend/public/favicon.png (#377)`. Leaving it
wrong would test check 11 blocking correctly, which is worth doing once — do that
first, confirm the block, then fix the title and re-fire.

**Its description predates the commit-body contract**, so it has no marker block and
fails closed at check 12. Add one:

```
<!-- fabro:commit-body:start -->
Removes the unreferenced 1 MB frontend/public/favicon.png.

Resolves #377
<!-- fabro:commit-body:end -->
```

That exercises the live-read path and the human-edit path, which are the two new things
about the commit body. Editing inside the markers is the supported way to correct a
commit message, and this is the first proof it works.

Then fire and watch:

```sh
~/bin/fabro-fire-pr-review.sh andrewthetechie/jelly-swipe 378
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro events <run> -pf'
```

Expected sequence: `merge_gate` eligible → `watch_checks` green on the first pass →
`merge` → `report_merged`. This run deliberately does **not** exercise the `ci_fix`
ladder; section 6 does that against a PR with a real test failure.

## 6 — Then prove the `ci_fix` ladder separately

A merge that never exercised `ci_fix` has not tested half this stage. Find or make an
`agent-authored` PR on `jelly-swipe` with a genuinely failing `test` check, fire, and
confirm:

- `ci_fix_t1` runs and `ci_fix_gate` accepts its scope;
- the force-push lands and `watch_checks` re-runs against the new HEAD;
- the `### CI fixes` section appears in the final comment;
- a fix that touches a file outside `changed_files.txt` is **rejected** — construct
  this one deliberately, because it is the check that makes "no re-review" safe.

## 7 — Confirm the whole chain

One `backlog` run, fired manually, on `jelly-swipe`, end to end: issue → tasks → PR
with a Conventional Commits title and a marker block → `pr-review` triggered → review →
merge → issue closed, `Review` label gone, branch deleted, Discord rocket posted.

That is the first time all four stages have run as one chain. Record what it did and
did not prove in task 12.

## Stop conditions

Disarm both switches and stop if any of these happen:

- a merge lands on a PR that a human had commented on or reviewed;
- `ci_fix` edits a file outside the changed set and the gate does not catch it;
- a run merges with any check not in `pass`/`skipping`;
- the blocked path labels a PR `ai-review-needs-human` for an expected block;
- a merge fires on any repo other than the one under test while the others' labels are
  meant to be off.

## Acceptance

Sections 1-7 complete, on `jelly-swipe` only. The other three repos keep auto-merge
provisioned-on but stay untouched until the deployment log records a clean chain — they
have no branch protection, and on `lawncare-saas` a merge deploys.
