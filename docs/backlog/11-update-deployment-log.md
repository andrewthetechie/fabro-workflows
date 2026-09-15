# Task 11 — Record the redesign in `FABRO-DEPLOYMENT-LOG.md`

**Depends on:** tasks 09, 10, 12 (you are writing down what actually happened).
**Do before:** nothing. This is the last task.
**LLM level:** local is fine.

## Goal

Append one section to the deployment log so the next person — or the next model —
reading it sees the current state of the system rather than the broken state the log
currently describes. Do not rewrite the existing log; it is a chronological record and
the earlier entries stay true as history.

**File:** `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` (mode 600). Task 10 item 5 has
already moved it out of the public repo working tree — do not recreate a copy inside
the fabro checkout.

## What to write

Append a dated section titled something like
`## 2026-09-__ — Backlog workflow redesign (file-backed contracts)`. Cover these, in
this order. Keep it factual and short; this is a log, not a narrative.

### 1. What changed and why

One paragraph: the `backlog` workflow was rebuilt around file-backed contracts because
fabro's `output_schema` path disables the routing-JSON scan, so every agent verdict was
invisible to edge conditions. Agents now write JSON to `/tmp/fabro/*.json` and small
`jq` command gates emit `{"context_updates": …}` for routing. Link to the plan series
at `.scratch/fabro-implementation-fixup/` (note that it lives in a scratch directory
and may not survive; if the operator wants it preserved, say so rather than moving it
yourself).

### 2. Close out the known issues

The log records these as open. Mark each resolved, with the one-line reason:

| Issue | Resolution |
|---|---|
| Review routing never fires; every review lands on `human_rescue` | `output_schema` removed everywhere; `review_gate` emits `review_decision` via `context_updates` |
| `decompose` fails deterministically (~500s, ~360k tokens per run) | array-root schema removed; the agent writes `decomposition.json` and a `jq` gate validates it |
| Plan-stage output invisible to the coder (~50 min re-planning per run) | plan stage deleted; `improve` produces the spec and the coder reads `current_task.json` |
| Discord fires on every run (~384/day) | `discord-complete` moved from `run_complete` (no matcher support) to `stage_start` with matcher `open_pr` |
| PRs have no real title or body | `open_pr_prep` writes a deterministic title and body; `open_pr` applies `agent-authored` and swaps the issue label to `Review` |

### 3. New: the branch-leak finding

This one is **not** in the log yet. Record it as a fabro **upstream bug that is
mitigated here, not fixed at source**:

- Fabro pushes `fabro/run/<run_id>` (per checkpoint) and `fabro/meta/<run_id>` (from the
  server process) into every repository it works on, and there is **no deletion path
  anywhere in the fabro codebase** — no `git push --delete`, no retention, nothing
  `fabro system prune` reaps.
- Quiet-exit runs leak both branches. The run branch in that case is a single empty
  commit (`fabro(<id>): acquire (failed)`) with a zero-byte diff against `main`.
- Measured 2026-09-13, ~2 days into the `*/15` cron: **~300 leaked branches** across the
  four repositories (jelly-swipe 88, womens-fantasy-sports 70, lawncare-saas 70,
  writers-app 72), growing at roughly 768/day.
- Mitigation: `[run.meta_branch] push = false` in `workflow.toml` (stops the metadata
  half at source), plus `~/bin/fabro-branch-sweep.sh` on the Docker host, run daily at
  04:17 by cron, which deletes `fabro/run/*` and `fabro/meta/*` branches that are not
  an open PR's head and are past their grace window. Deterministic shell; no agent.
- `[run.run_branch] push` is deliberately left **on**, so that in-progress work survives
  a server crash. Turning it off is a one-line change in `workflow.toml` and would stop
  the run-branch leak at source, at the cost of losing a crashed run's commits — the
  redesigned `open_pr` stage pushes the branch itself, so PRs would still work. Record
  this as the available next step if the sweeper ever proves insufficient.
- Record that this bug is still live upstream: any **new** repository the fabro server
  is pointed at starts leaking immediately until it is added to `FABRO_SWEEP_REPOS` in
  the sweeper.

### 4. The E2E result

From task 09 step 5 — the seven checks and what each one showed, with the run id and
the PR link. If any of the seven was not proven, say which and why; a log entry that
claims a clean pass it did not get is worse than no entry.

### 5. Deployment facts that changed

- Models added: `glm-4.7` and `kimi-for-coding` in LiteLLM and in the fabro server's
  `/storage/.home/settings.toml`; the `fabro` LiteLLM virtual key's model scope extended
  to all six.
- The model map and escalation ladder (copy the two tables from
  `00-overview-and-contracts.md` — they are the operational reference someone will want).
- `discord-notify.sh` now lives in the `fabro-storage` **Docker volume** at
  `/storage/scripts/discord-notify.sh`, not on the Docker host. Note that it is not in
  git and survives container recreation only because the volume is named.
- The workflow repo layout is now 12 files; `schemas/` is gone.

### 6. Open operator decisions

From task 10, all now settled rather than open:

- **Schedules are deliberately disabled** (operator decision, 2026-09-14) while dev and
  testing continue. All four automations keep `*/15 * * * *` but with the schedule
  trigger `enabled: false`; `backlog-jelly-swipe` keeps an enabled `api`/`manual`
  trigger for test fires. Last scheduled batch was `2026-09-14T00:15:00Z`. Note the
  consequence: nothing works the backlog until they are re-enabled, and task 12 should
  be done before that happens.
- **`rust-node` was raised to 4 CPU / 8 GB** (host has 16 cores / 30 GB).
- **`run_title_generation` is cosmetic and unfixable by configuration.** Root cause:
  `run_title_generation.rs:49` hardcodes a 64-token output budget, and every model in
  this LiteLLM stack is a reasoning model that spends the whole budget before emitting
  content. Verified at 64 tokens: all six return empty or no `choices`; the same models
  succeed at 512. Run titles fall back to truncated goal text. Revisit only if fabro
  makes the budget configurable or a non-reasoning model joins the stack.

## Done when

- The section exists, is dated, and reads as a record of what happened rather than a
  plan for what should happen.
- All five previously-open known issues are marked resolved with their reason.
- The branch-leak finding is recorded, including that it is unfixed upstream.
- The E2E run id and PR link are in it.
- **No credential appears anywhere in the text you added.** Reference where a secret
  lives (`~/.fabro-deploy/env`, the fabro vault, `/storage/secrets/`), never its value.
  This applies even though the file is untracked — task 10 item 5 exists because
  untracked is not the same as safe.

## Pitfalls

- Do not edit the existing log entries. They describe a system state that was true at
  the time; overwriting history makes the log useless for working out when something
  broke.
- Do not claim the branch leak is fixed. It is mitigated. The distinction matters to
  whoever reads this next.
- If task 10 item 5 moved the file, update any other document that points at the old
  path before you finish.
