# Triage task map and split: overview and canonical contracts

**Status:** proposed 2026-09-26. Not started. All operator decisions are made (see
"Decisions").

**Read this file first.** Every task in this folder assumes the decisions, contracts and
rules in this file. If a task and this file disagree, this file is correct. Stop and report
the conflict. Do not guess.

Source decision: `docs/adr/0015-triage-task-map-and-split.md` (ADR 0015). **When you write
or change a `.fabro` file or a `workflow.toml`, invoke the `/fabro-workflow` skill first.**

## What this is

The shared triage phase gains three nodes after `triage_gate`:

```
triage_gate ─(ready)→ plan → plan_gate ─┬─(map, ≤4 tasks)──→ apply_ready (+ map section) → done
                                        ├─(5–12 tasks)─────→ apply_split → done   [outcome: split]
                                        ├─(>12 tasks)──────→ post_questions → done [needs_info]
                                        └─(plan failed)────→ apply_ready (no map, as today)
```

The scheduler gains a promoter for **Held issues** (children) and a closer for **Split
parents**. `backlog`'s `decompose` prompt reads the map as a hint.

## Glossary (from `CONTEXT.md`)

- **Task map**: triage's proposed decomposition of one issue, stored on the issue. It is a
  hint only.
- **Split parent**: an issue that triage divided into Child issues. It is never worked.
- **Child issue**: one slice of a Split parent, created ready, with its own Task map.
- **Held issue**: ready, but in neither the queue nor progress, because it waits on another
  piece of work. A Child issue waits on its predecessor, and a Remainder issue waits on its
  parent's PR.

## Canonical contracts

### C1. `/tmp/fabro/plan.json` (written by `plan`, read by `plan_gate`)

```json
{
  "tasks": [
    { "id": "kebab-case-stable-id", "title": "short task title", "intent": "one line",
      "files": ["repo/relative/path"], "covers": ["criterion 2"] }
  ],
  "children": [
    { "title": "feat(scope): conventional title", "summary": "one prose paragraph",
      "tasks": ["kebab-case-stable-id"], "after": null }
  ],
  "summary": "one sentence for the operator"
}
```

`plan_gate` rules. Any failure is `invalid`, with the usual two-attempt counter
(`plan_attempts`):

1. `tasks` has 1 or more entries. Every `id`, `title` and `intent` is a non-empty string,
   `files` and `covers` are arrays, and the `id`s are unique.
2. With 1–4 tasks, `children` is `[]`. `plan_status=map`.
3. With 5–12 tasks, `children` has 2 or 3 entries. Every task `id` is in exactly one
   child. Each child has 1–4 tasks, listed in the same order as in `tasks`. `after` is
   `null` or the 0-based index of an **earlier** child. Each `title` matches the same
   Conventional-Commits regex as `triage_gate`, with no `!` and no `BREAKING CHANGE`.
   `summary` is not empty. `plan_status=split`.
4. With more than 12 tasks, `children` is ignored. `plan_status=too_large` (C4).
5. A second invalid file, or the `plan` agent failing: `plan_status=none`. `apply_ready`
   then runs with no map (ADR 0015 D1, fail-open).

On `map` and `split`, `plan_gate` writes `/tmp/fabro/plan_ok` (an empty file) and renders
`/tmp/fabro/task_map.md` (C2). `claim` deletes `plan.json`, `plan_ok`, `task_map.md` and
`split_children.json` and resets `plan_attempts` to 0, because the phase runs many times in
one `arch-review` run.

### C2. `## Proposed task map` body section (rendered by `plan_gate`, written by `apply_ready` / `apply_split`)

```markdown
## Proposed task map

Drafted by triage against `main` at `<short sha>` on <YYYY-MM-DD>. A hint for the implementer:
the checkout wins where they disagree.

1. **<title>** (`<id>`): <intent>
   - files: `a.py`, `b.ts`
   - covers: criterion 1, criterion 2
```

It is replaced the same way as `## Decisions made during triage`: an `awk` drops the old
section, up to the next `## ` heading, and appends the new one. A Child issue's body carries
only its own tasks.

### C3. Labels and markers

| Thing | Label | First line of body |
|---|---|---|
| Split parent | `agent-split` (never `agent`) | unchanged |
| Child issue, no predecessor | `agent` + the parent's labels, except the triage ones | `<!-- fabro:split-child parent=<P> after=0 -->` |
| Child issue, with predecessor | `agent-held` + the parent's labels, except the triage ones | `<!-- fabro:split-child parent=<P> after=<N> -->` |

"Triage labels" are `needs-triage`, `needs-info` and `triage-in-progress`. `architecture`
is **always** carried over. A child's body is: the marker, a blank line, the child's
`summary` as the first prose paragraph (the same shape as a Remainder issue, whose marker-first
body `open_pr_prep` already handles), then `Part of #<P>.`, the parent's acceptance
criteria that the child covers, and its Task map section.

### C4. Phase outcomes

The exit contract becomes `ready | split | needs_info | not_actionable | skipped | released`.
On `too_large`, `plan_gate` rewrites `/tmp/fabro/triage.json` to `readiness: needs_info`
with one question, and appends `### Proposed task map (too large to split)` plus the map to
`/tmp/fabro/triage.md`. It then routes to the existing `post_questions`, which is not
changed.

### C5. Scheduler rules

- **Held child promotion** (each minute, per repo, next to `promote_remainders`). For each
  open `agent-held` issue, parse the C3 marker, then read issue `after` with
  `GET /repos/{r}/issues/{N}`:
  - `closed` with `state_reason: completed`: add `priority`, add `agent`, remove
    `agent-held`.
  - `closed` with any other reason: add `agent-stuck`, remove `agent-held`.
  - open: do nothing.
  - `after=0`, or no marker: log it and leave the issue alone.

  Past-promotion cleanup works as `remainder.py` does: `agent`, `agent-in-progress` or
  `agent-stuck` already present means only remove `agent-held`.
- **Parent close.** For each open `agent-split` issue, run
  `GET /repos/{r}/issues/{P}/sub_issues`:
  - every sub-issue is `closed`/`completed`: comment `All child issues landed.`, then
    close the parent with `state_reason: completed`;
  - any sub-issue is `closed` with another reason: add `agent-stuck`, once;
  - an empty sub-issue list: log it and leave the issue alone.

## Rules this series inherits

`AGENTS.md` deployment invariants. These rules apply most often here:

- `\"` is the only backslash in a `.fabro` file. There is no `#` in a `script=`. Get a
  newline in jq with `([10]|implode)`.
- `output_schema="routing"` on every new command node that prints `context_updates`. The
  routing checker must be clean.
- Reset per-iteration context keys in `claim` (`plan_status`), and write every key on
  both branches.
- Delete a contract file before the agent that writes it runs (`plan.json`).
- A class used by a node in a shared phase has an explicit rule in **both** importing
  stylesheets (`.plan`).
- `set -e`, with every agent-authored label created before `gh issue edit` (the phase's
  existing rule).
- `loop_restart_signature_limit` in `arch-review` (20) must stay above the number of times
  one gate can fail in a run. It counts per signature, so `plan_gate` needs its own
  failure line (task 04).

## Tasks (in execution order)

| # | File | Where | Depends on |
|---|---|---|---|
| 01 | `01-scheduler-held-and-parent.md` | `ops/scheduler/` | — |
| 02 | `02-plan-agent-and-gate.md` | `_shared/triage/`, both importing stylesheets | — |
| 03 | `03-apply-ready-and-apply-split.md` | `_shared/triage/triage.fabro` | 02 |
| 04 | `04-importers.md` | `arch-review/workflow.fabro`, `issue-triage/` | 03 |
| 05 | `05-prompts.md` | `backlog/prompts/decompose.md.j2`, `_shared/triage/prompts/improve.md.j2` | 03 |
| 06 | `06-docs.md` | `AGENTS.md`, roadmap C4 | 01–05 |
| 07 | `07-validate-deploy-verify.md` | host | 01–06 |

## Decisions (2026-09-26)

1. Both goals: smaller runs **and** a head start for `decompose`.
2. At most 3 children. Children never split again, and are created ready.
3. Split when the map has more than 4 tasks, with each child at most 4. The gate enforces
   this.
4. Ordered children are held, and the scheduler promotes each one when its predecessor
   closes as completed.
5. The parent tracks its children through sub-issues, and the scheduler closes it.
6. The map is a visible body section.
7. Split only on `ready`.
8. Any issue can be split. The split is recorded as a triage decision, and labels are
   inherited.
9. `decompose` always runs, and the map is a hint only.
10. There is no skip, and so no drift check.
11. A separate `plan` agent and `plan_gate` draft and check the map.
12. Promoted children get `priority`, and the first child does not.
13. The glossary terms above.
14. The map is a skeleton, not full drafts.
15. More than 12 tasks becomes `needs_info`, with one scoping question.
16. `arch-review` keeps its 6-hour cap.
