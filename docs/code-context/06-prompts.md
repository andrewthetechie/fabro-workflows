# 06 · Tell the agents: the map first, `fabro-code` before grep

Read `00-overview-and-contracts.md` first. Decision 2 in that file sets which prompts this
task edits (ADR 0014 D6). Phase 1:

- `backlog/prompts/`: `decompose.md.j2`, `improve.md.j2`, `coder.md.j2`, `rework.md.j2`;
- `_shared/review-merge/prompts/review_fix.md.j2` (reaches `backlog` and `pr-review`);
- `arch-review/prompts/scan.md.j2`;
- `_shared/triage/prompts/improve.md.j2` and `triage.md.j2` (reach `issue-triage` and
  `arch-review`).

Phase 2 is a later task. Do not edit its prompts here.

For `scan.md.j2`, the Repo map is the direct input to its job. A file with many inbound
edges and a thin interface is the shallow-module signal that `scan` is looking for. Add one
sentence that says this, and add `fabro-code impact NAME` as the way to size a candidate's
blast radius.

## Change

In each prompt in the set:

1. Make `/tmp/fabro/repomap.md` the **first** item of `## Inputs`:

   > `/tmp/fabro/repomap.md`: a ranked outline of this checkout (the most-referenced files
   > and their top-level symbols). Read it before you search. It can be missing. If it is,
   > continue without it.

2. Add this section after `## Inputs`. Keep the wording the same in each prompt, so that one
   later edit can change them all:

   > ## Finding code
   >
   > Use `fabro-code` through the shell before `grep`, `glob` or paging a file with
   > `read_file`:
   >
   > - `fabro-code def NAME`: where NAME is defined.
   > - `fabro-code show NAME`: its source, what it calls, and what calls it. Use this
   >   instead of reading a large file in pages.
   > - `fabro-code callers NAME` / `callees NAME` / `impact NAME`: the call and import graph.
   > - `fabro-code tests PATH…`: test files that exercise these files.
   >
   > Its answers include your own edits. It knows calls and imports only, not attribute
   > reads, string keys or config. So before you conclude that something is unused, or
   > before you delete or rename it, confirm with `grep -rnw NAME`. If it prints
   > `no code index`, use grep.

Do not tell the agent to stop using grep. Grep is still the proof that a name is not used
(ADR 0014 D1).

## Acceptance

- The rendered prompts contain the section once each. Check with `fabro validate`, which
  resolves `@prompts`, and with `grep -c 'fabro-code def'` on each file.
- No prompt names `codegraph`.
- Both graphs that import each shared prompt validate: `backlog` and `pr-review` for
  `review_fix`, and `issue-triage` and `arch-review` for the triage prompts.
