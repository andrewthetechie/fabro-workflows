# Task 11 — Correct AGENTS.md and `ops/README.md`

**Depends on:** 10. **Blocks:** 12. **LLM level:** local is fine.

Do this **after** the first live merge, not before. Documenting behaviour that has not
run is how the stale baselines below got written.

## AGENTS.md

### The baselines are wrong and were wrong before this stage

AGENTS.md records:

> Baselines as of 2026-09-15: `Backlog (37 nodes, 85 edges)` clean, and
> `PrReview (19 nodes, 40 edges)` with exactly one warning

`Backlog` has been **38 nodes, 86 edges** since the pr-review bridge added
`trigger_review`. The 37/85 figure predates it. Counted directly from the tree on
2026-09-15: 38 node declarations, 86 edge arrows.

New baseline:

> Baselines as of <date of the first live merge>: `Backlog (38 nodes, 86 edges)` clean,
> and `PrReview (28 nodes, 64 edges)` with exactly two warnings — `pr_number` and
> `auto_merge` unbound in `validate_input`. Both are deliberate. Binding either would
> silence a guard: `pr_number` would let a no-input fire review PR #1, and `auto_merge`
> would let a `[run.inputs]` table replace the whole input map.

### New rows for the deployment-invariants table

These pass `fabro validate` and fail at runtime.

| Rule | What happens otherwise |
|---|---|
| Both auto-merge switches fail closed — absent, empty or unparseable means **off** | A `gh` call that returns nothing, or a broken injection read as "not disabled", merges an unreviewed PR into `main`. On `lawncare-saas` that is also a deploy. |
| `[run.environment.env]` interpolates `{{ vars.X }}` only — never `${X}`, never `{{ env.X }}` | `${X}` reaches the sandbox as literal text. A kill switch spelled that way is pinned off forever, and the shakedown cannot tell it from a correctly disarmed one. |
| The `FABRO_AUTO_MERGE` server variable must exist | An unset `{{ vars.X }}` fails the RunIntent at compile time, so no `pr-review` run is created at all. `provision-server-state.sh` creates it; `off` writes `0` and never deletes. |
| `POST /variables` upserts | A re-provision that POSTs unconditionally silently re-arms a switch an operator killed mid-incident. Create-if-absent, report drift otherwise. |
| `risk` is gate-enforced in `fix_gate`, not merely documented | Without the check the field is optional in practice, PRs quietly stop auto-merging, and the report still says "complete". |
| The merge node re-reads PR `state` from GitHub immediately before merging | The bridge's double-fire marker is per-run and does not stop a second review fired by hand. Two runs reach `merge`; the loser must report "already handled", not fail. |
| Every merge-phase command node's unconditional edge lands on `mark_needs_human`; expected blocks are the *conditional* edges | A broken merge — a token without `contents: write`, an API 5xx — otherwise lands in the same quiet "not auto-merged" bucket as a risk-4 PR and stays invisible. |
| Commit-body markers are `:start` / `:end`, never `<!-- /fabro:commit-body -->` | A closing marker with a slash forces `\/` into the extraction pattern, and `\"` is the only backslash a `.fabro` file may contain. |
| The title derivation never emits `!` or `BREAKING CHANGE:` | `release-please` runs on `jelly-swipe` and `lawncare-saas`. Either one cuts a major release from an agent's guess. |

### The deploy runbook shrinks

`fire-pr-review.sh` is **no longer deployed**. Replace its `scp` block with the
one-time wrapper install from task 07, and change its verification `diff` to target
`docs/auto-merge/fabro-fire-pr-review-wrapper.sh`.

Add the host switch to the deploy section, because a `.env` change needs
`docker compose up -d` to reach the container and a switch that never arrived is worse
than no switch.

### The auto-merge row in "what this repo is"

The layout table describes `docs/<workflow>/` as "the numbered task series each
workflow was built from". Add `docs/auto-merge/` alongside `docs/pr-review-bridge/` as
a cross-cutting series rather than a per-workflow one.

## `ops/README.md`

### The automations table

The `pr-review-*` rows gain an `auto_merge` column. They are still config-only — read
by `fire-pr-review.sh`, never fired — and now they are read for one more thing. The
existing note that they "look dead, do not delete them" becomes more load-bearing, not
less.

### Both kill switches, written down where an operator will look

Under a new heading, with the exact commands. During an incident nobody reads a task
series in `docs/`.

```sh
# one repo
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 ./ops/fabro-auto-merge-switch.sh andrewthetechie/<repo> off

# all four
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 ./ops/fabro-auto-merge-switch.sh host off
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro variable get FABRO_AUTO_MERGE'
```

Neither needs a deploy or a restart; both take effect on the next run. Do **not**
document a `PATCH /automations/{id}` recipe — it returns 405, and the row has no
`labels` field either (422). Do not document reading the switch back with
`docker exec … echo $FABRO_AUTO_MERGE`: that is the server container, not the run
sandbox.

State plainly that neither undoes a merge that already happened.

Also note that `provision-server-state.sh` reports drift rather than rewriting a row,
so a hand-flipped `auto_merge=false` **survives** a re-provision. That is the
behaviour an operator most needs to be able to rely on mid-incident, and it is the
exact scenario the script's no-silent-rewrite rule was written for.

### The concurrency warning

`docs/adr/0001-concurrency-posture.md` (accepted 2026-09-16) already owns this: one
global cap at `max_concurrent_runs = 3`, schedules disabled and staggered three minutes
apart, and the measured finding that **fabro queues at the cap rather than rejecting**.
Do not restate it. Add the one thing it predates:

> A `pr-review` run that reaches the merge phase holds a slot until CI settles, bounded
> by the 60-minute merge budget. Because fabro queues rather than rejects (ADR 0001), a
> `trigger_review` fire behind three merging runs is delayed, not dropped. **Re-enabling
> the staggered backlog schedules should be accompanied by revisiting the cap.**

Add a line to ADR 0001's consequences pointing at the merge phase as a new source of
long-held slots, rather than opening a second ADR.

### `discord-notify.sh` is now cross-package

It lives in `.fabro/workflows/backlog/scripts/` and is called by hooks in **both**
workflows, by absolute path inside the container. Say so, so nobody later tidies it
into `pr-review/scripts/` and breaks four hooks that reference the old path.

## Acceptance

- Both files describe what the deployment actually does, verified against the run from
  task 10.
- The node and edge counts match `fabro validate` output pasted from that run, not from
  this document.
- The kill-switch commands are copy-pasteable and were actually run in task 10 §4.
- No token, webhook URL or secret value appears in either file. This repo is public.
