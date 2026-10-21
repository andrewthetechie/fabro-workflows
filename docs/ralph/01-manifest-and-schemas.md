# 01. Stage manifest and schemas

## Outcome

`_io/manifest.json` describes every `ralph` agent stage, and four new schemas exist. Nothing
runs them yet: the `ralph` package arrives in task 02, so the generator has no
`workflow.toml` to write into until then.

## Why

ADR 0016: an agent stage gets its inputs through `inputs`, writes its contract through
`submit`, and the gate checks the Input receipt. 00: O16, O20, C2, C4, C6, C9, C10.

## Change

Read `_io/README.md`, `ops/fabro-io-manifest.py` (how `phases` and `workflows` are
flattened and what a stage entry may hold), and the `backlog` entries for `plan`, `coder`
and `resolve_merge`, and the `review-merge` entries for `refute`, `standards` and `spec`.

### Schemas (`_io/schemas/`, draft 2020-12, `additionalProperties: false` everywhere)

- `task_list.schema.json`: C2's object. `disposition` enum; `summary` string; `contradictions`
  array of `{criterion, non_goal, needed_surface}` (all non-empty strings); `tasks` array of
  `{id, title, body, acceptance, files, covers, context}` with `id`, `title`, `body`,
  `context` non-empty strings, `acceptance` a non-empty array of non-empty strings, `files`
  and `covers` arrays of strings, `context` `maxLength` 12000. Cross-field rules (tasks only
  for `ready`, contradictions only for `contradiction`, unique ids) stay in `plan_gate`.
- `iteration.schema.json`: C4's object. `status` enum `done`/`progress`; `summary` string,
  `maxLength` 600; `learnings` array, `maxItems` 3, of strings `maxLength` 300.
- `ralph_spec.schema.json`: `spec.schema.json` plus a required `risk`, integer 1-5, with the
  description of `review_fix.md.j2`'s risk rubric.
- `learnings.schema.json`: C9's object. `learnings` array, `maxItems` 5, of
  `{rule, evidence, home}`; `home` enum `agents_md`, `test_toml`, `ci_sh`, `ralph_prompt`.

### Manifest

A `ralph` workflow with these stages. Copy the text of every shared input's `about` and
`absent` from the `backlog` or `review-merge` stage named; write new ones in the same voice.

| Stage | Inputs (in order) | Output | Sealed | Prompt |
|---|---|---|---|---|
| `plan` | `issue.json`, `task-map.md` (optional, `## Task map` cut from the issue body, written by `prep`), `repo-map.md` (optional) | `task_list` → `/tmp/fabro/task_list.json` | | `ralph/prompts/plan.md.j2` |
| `iterate_t1`..`iterate_t4` | `current_task.json`, `issue.json`, `progress.md` (optional), `task-context.md` (optional), `task-code.md` (optional) | `iteration` → `/tmp/fabro/iteration.json`; also may submit `task_blocked` the way `coder` does | | `ralph/prompts/iterate.md.j2` |
| `refute` | `review/refute_issues.json`, `review/refute_diff.patch`, `hygiene.json`, `review/refute_validation.md`, `review/refute_delta.patch` (optional), `review/refute_history.md` (optional) | `refute` → `/tmp/fabro/refute.json` | C10's first list | `ralph/prompts/refute.md.j2` |
| `standards` | the shared `standards` stage's inputs | `standards` → `/tmp/fabro/standards.json` | C10's both lists | `ralph/prompts/standards.md.j2` |
| `spec` | the shared `spec` stage's inputs | `ralph_spec` → `/tmp/fabro/spec.json` | C10's both lists | `ralph/prompts/spec.md.j2` |
| `resolve_merge` | as `backlog.resolve_merge` | as `backlog.resolve_merge` | | `ralph/prompts/resolve_merge.md.j2`, `"git": "write"` |
| `resolve_base` | as `review_merge.resolve_base` | as `review_merge.resolve_base` | | `ralph/prompts/resolve_base.md.j2`, `"git": "write"` |
| `learn` | `learn/inputs.md` | `learnings` → `/tmp/fabro/learnings.json` | | `ralph/prompts/learn.md.j2` |

Mark every `ralph` stage `live`. If the manifest format cannot express "the same stage
under four node ids", list the four ids separately with identical bodies and add a check in
`ops/fabro-io-manifest.py` that they stay identical.

If `task_blocked` cannot be a second output of a stage whose `output` is `iteration`, read
how `backlog`'s `coder` declares it (#17: `fabro-io submit` takes the contract name) and do
the same. Do not change `ops/fabro-io/` in this series; if a binary change is needed, stop
and report.

## Acceptance

- `cargo test` in `ops/fabro-io/` passes (the schema-compile gate).
- `make check` passes. The generator writes nothing for `ralph` yet, and reports no drift.

## Tests

The schema-compile test picks up the new files by directory; if it lists schemas by name,
add the four.

## Depends on

Nothing.
