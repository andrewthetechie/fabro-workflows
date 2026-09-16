# Task 12 — Update the deployment log

**Depends on:** 10, 11. **Blocks:** nothing. **LLM level:** local is fine.

`~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac, mode 600, deliberately
outside this public tree.

**Append a dated section. Do not edit earlier ones** — they are a chronological record,
and this stage's whole value as evidence depends on that.

## What the section must contain

### What was deployed, and what was not

Workflow changes went live by merging to `main`. `discord-notify.sh` and the
`fire-pr-review.sh` wrapper were deployed by hand. `fire-pr-review.sh` itself stopped
being deployed at all. Record the date each happened, not "recently".

### Each E2E, and what it did and did not prove

This is the part the log exists for. Per the house pattern, be specific about the
negative space:

| Run | Proved | Did **not** prove |
|---|---|---|
| shakedown ×3, host switch off | `merge_gate` evaluates and blocks; the `blocked` report reads correctly; no Discord | nothing about the merge call itself |
| per-repo switch off | the label is load-bearing and independent of the host switch | — |
| first live merge, `jelly-swipe#378` | squash subject, `--delete-branch`, issue closed, `Review` removed, Discord fired, hand-added marker block extracted correctly | the `ci_fix` ladder; anything about a repo without branch protection |
| `ci_fix` ladder | the fix loop, the force-push, the re-watch, the scope rejection | whether `ci_fix_t2` is ever better than `t1` — one sample |
| full chain, one `backlog` run | all four stages as one chain | anything on `lawncare-saas`, `womens-fantasy-sports` or `writers-app` |

Name the run ids. `fabro events -p` output for the `merge_gate` routing decision is
worth pasting — a node whose routing went inert still looks correct from the PR
comment, and that distinction has already cost this deployment one debugging round.

### Bugs found along the way

Every one, with what it cost. If nothing broke, say that explicitly — a stage that
went in clean is itself a finding, and an empty bugs section reads like an omission.

### The state of the switches on the day you finish

Which repos are armed, which are not, and what the host variable is set to. The next
person to read this log during an incident needs to know what "normal" looked like.

### Two things that were true before this stage and are not any more

- `docs/pr-review-bridge/00` decision 5: "The chain ends at review posted. No
  auto-merge." Note where that changed and that its promise — "nothing here may
  foreclose adding it later" — held.
- AGENTS.md's `Backlog (37 nodes, 85 edges)` baseline was stale from the bridge
  onward. Record that it was wrong for that whole period and what the real numbers
  were, so a future reader does not mistrust the *new* baseline for the same reason.

### What is deliberately still manual

The other three repos. They are provisioned with auto-merge on but have not been
exercised, they have no branch protection, and on `lawncare-saas` a merge deploys.
State what evidence would justify turning them loose.

## Acceptance

- One new dated section, appended.
- No earlier section edited.
- Run ids, not descriptions, for every E2E.
- The "did not prove" column is filled in for every row. A row with an empty one means
  the run was not thought about.
- Mode is still 600 and the file is still outside this repository.
