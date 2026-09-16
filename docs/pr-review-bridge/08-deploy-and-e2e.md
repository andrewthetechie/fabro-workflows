# Task 08 — Deploy and live E2E on jelly-swipe

**Depends on:** 01, 07. **Blocks:** 09, 10. **LLM level:** smarter model recommended
— live judgment.

Prove the whole chain once: a backlog run opens a PR, and a `pr-review` run starts on
that PR without anyone touching anything.

## Before pushing

Pushing to `main` is the deploy, on all four repos at once (decision 4 — accepted
because nothing fires on a schedule today). Confirm that is still true:

```sh
ssh andrew@10.10.0.32 "docker exec fabro-fabro-1 sh -lc '
  T=\$(cat /storage/server.dev-token)
  wget -q -O- --header=\"Authorization: Bearer \$T\" http://127.0.0.1:32276/api/v1/automations'" \
  | jq -r '.data[] | "\(.id)\t\([.triggers[]|"\(.type):\(.id):\(.enabled)"]|join(","))"'
```

Every `backlog-*` schedule must read `schedule:every-15m:false`. If one is enabled,
the first push goes live on a cron tick rather than when you are watching. Disable it
or accept that deliberately.

Also confirm task 01 is done — `FABRO_API_TOKEN` exists in the vault. `{{ secrets.* }}`
fails **closed**: with the entry missing, every backlog run aborts at startup before
its sandbox exists. That would take all four repos down, not just the bridge.

## Deploy

```sh
cd ~/Documents/code/fabro-workflows && git pull --ff-only
git push                       # workflow changes are live on the next fire

# the notify script is read from the container by absolute path
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'

# the operator copy of the fire script
scp .fabro/workflows/backlog/scripts/fire-pr-review.sh andrew@10.10.0.32:~/bin/fabro-fire-pr-review.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-fire-pr-review.sh'
```

`trigger_review` reads `fire-pr-review.sh` from its **own fresh clone of `main`**, not
from `~/bin`. The host copy is for you. They can drift; task 09 adds the diff check
that catches it.

Confirm the container copy matches the repo — this must print nothing:

```sh
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
```

## The E2E

Fire one backlog run against `jelly-swipe` and watch it all the way through:

```sh
curl -fsS -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  http://10.10.0.32:32276/api/v1/automations/backlog-jelly-swipe/runs
```

> Note the absence of a body. That endpoint ignores one — which is the finding that
> started this stage. `backlog` takes no inputs, so firing it this way is correct;
> firing `pr-review` this way is not.

Follow it with `fabro events <run> -p`, which shows the selected edge and why.

### What must be true at the end

| # | Check |
|---|---|
| 1 | `open_pr` succeeded and a PR exists with the `agent-authored` label |
| 2 | `/tmp/fabro/pr_number` held the right number — visible in `trigger_review`'s log |
| 3 | `trigger_review` succeeded and emitted a non-empty `review_run_id` |
| 4 | A **new `PrReview` run exists** for the same repo, with `inputs.pr_number` matching the PR |
| 5 | That run's `parent_id` is the backlog run, and the backlog run's `children_count` is 1 |
| 6 | Its sandbox image is `fabro-python:local`, not `buildpack-deps:noble` |
| 7 | Its clone is **not shallow** — the property task 03 proved, now proved end to end |
| 8 | Discord posted both the PR notification and the review-triggered line |
| 9 | The review runs to completion and comments on the PR |

Checks 5–7 are the ones that distinguish this from "a run started". A review that
starts and then dies at `prep_review` with `fatal: no merge base` means the workflow
version did not carry `pr-review`'s `workflow.toml`, and the design is wrong rather
than the plumbing.

### If backlog quiet-exits at `acquire`

Most runs do — there has to be an open issue labelled `agent` and not
`agent-in-progress`. That is not a failure of this stage; it just proves nothing.
Seed a small issue on `jelly-swipe` and fire again.

## Rollback

There is no staging branch and no rollback but another commit. The **fastest** kill
switch does not need a commit at all: remove the vault entry, and every backlog run
fails closed at startup. That is a blunt instrument — it stops backlog entirely, not
just the bridge.

The targeted revert is a commit removing the `trigger_review` node and its edge, and
restoring `open_pr -> exit`. It is live on the next fire.

## Acceptance

All nine checks pass on one run. Record the run ids of both the backlog run and the
review it spawned — task 10 needs them.
