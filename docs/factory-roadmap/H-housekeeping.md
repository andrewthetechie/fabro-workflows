# H · Housekeeping

**Status:** proposed. **Axis:** repeatability. **Effort:** S each. **Feasibility:** high.
Each item is independent. Items H2–H5 carry open items from `docs/research_improvements/`.

## H1 · `AGENTS.md` points at deleted docs

Commit `d5cd750` ("docs: updates", 2026-09-24) removed `docs/scheduler/`, `docs/perf/`,
`docs/auto-merge/`, `docs/pr-review-bridge/`, `docs/issue-triage/`, `docs/direct-providers/`,
`docs/run-history/`, `docs/plan/` and `docs/turn-it-on/`. `AGENTS.md`'s layout table, the
scheduler's module docstrings (for example `fabro.py` cites `docs/scheduler/07-fabro-client.md`)
and `docs/research_improvements/` still cite them. Agents working this repo read `AGENTS.md`
first and are sent to missing files. **Fix:** repoint each reference at what replaced it
(`docs/USER-GUIDE.md`, git history via `git show d5cd750^:<path>`), or drop the row.

## H2 · `rescue.md` is never reset between tasks

`research_improvements/01` item 4. `record_guidance` (`backlog/workflow.fabro:1467`) writes
`/tmp/fabro/feedback/rescue.md`, and nothing deletes it. Operator guidance for task *k*
overrides every later rework in the run. **Fix:** `rm -f /tmp/fabro/feedback/rescue.md` in
`next_task`, plus a gate test that stages the file and asserts it is gone.

## H3 · `loop_restart_signature_limit` on `backlog`

`research_improvements/01` item 1. It never fired in 38 runs, but `arch-review` already sets
20 for the same reason. **Fix:** one attribute, sized above the rework ladder's maximum gate
visits per run.

## H4 · A pre-push hook

`research_improvements/03` item 5. Pushing to `main` deploys. **Fix:** `.githooks/pre-push`
running `ops/test-task-gates.sh`, the `tomllib` parse of every `workflow.toml`, and `sh -n`
on `ops/*.sh`, installed with `git config core.hooksPath .githooks`. It is offline and takes
seconds.

## H5 · `issue-triage` and `arch-review` still create run branches

**Applied (2026-09-25, `docs/fabro-upgrade/` task 05).** Both now set
`[run.run_branch] enabled = false`.

## H6 · The scheduler reads the sandbox through the Docker socket

**Applied (2026-09-25, `docs/fabro-upgrade/` task 03).** `probe.py` now reads
`tasks.json` through `GET /api/v1/runs/{id}/sandbox/file` and the socket mount is gone. The
original diagnosis is below, kept for the record.

## H7 · Anchor the `discord-rescue` hook matcher

`backlog/workflow.toml` has `matcher = "human_rescue"`, unanchored. Nothing else starts with
that id today, so it works. **Fix:** `"^human_rescue$"`, per the `AGENTS.md` invariant, before
something named `human_rescue_*` arrives.
