# Coder tweaks: handoff for the follow-up measurement

> The next series, on tool steering and git, is planned in `00-overview-and-contracts.md`
> in this folder. This file covers only the check of `24eb95c`.

Your job is to check recent `backlog` runs and decide whether the coder changes
of 2026-10-01 worked. This doc gives the baseline, what changed, how to measure,
and what counts as success. It does not repeat the change itself. For that, read
`git show 24eb95c` (the commit message carries the rationale) and `git show aeb94e4`.

## The problem, measured

Two scheduler runs on 2026-10-01, both on the old graph:

| | 01M3WRYJBFJVWJCEGDV6T4YRVF (womens-fantasy-sports#1278) | 01M3WS83Z1736Y75GNZM89KV59 (lawncare-saas#2659) |
|---|---|---|
| `improve` (local box) | 14.7 min | 11.9 min |
| coder start → first `edit_file` | **17.0 min** | **16.7 min** |
| reading before first edit | ~7 min, 20 `read_file`, ~120 KB, mostly whole files | ~5 min, ~100 KB |
| largest single reasoning turn | 9.3 min, 8,214 output tokens (263 lines of drafted code) | 7.6 min, 6,953 tokens (370 lines) |
| `fabro-code` calls in `improve` + `coder` | 0 | 0 |

The box (`coders-a`/`coders-b`, llama.cpp) runs at about 250 tok/s prefill and
about 15 tok/s decode. Output tokens cost the most. The coder re-read the files
`improve` had just read, and it re-derived facts the dossier already stated. The
lawncare coder spent ~10 min confirming an RLS/GUC rule that the dossier's Traps
already gave. Then it drafted the whole change in its reasoning and wrote it a
second time with `edit_file`.

## What changed (live state as of 2026-10-02)

| # | Change | Live? |
|---|---|---|
| 1 | New `excerpts` command node, `improve_gate -> excerpts -> coder`. It copies each `path:N-M` / `path:N` from `task-context.md` into `/tmp/fabro/task-code.md`. | Yes: every `backlog` run created after `24eb95c` reached `main` (2026-10-02T02:34Z) |
| 2 | Coder prompt and manifest: treat the dossier as verified, and don't re-read ranges `task-code.md` shows | Same |
| 3 | Coder prompt: plan in a few lines, never draft code in reasoning | Same |
| 4 | `repomap.md` dropped from the coder's inputs; `current_task.json` first | Same |
| 5 | fabro-io 0.2.0: six `code_*` MCP tools. Eight prompts name them, with a `fabro-code` shell fallback. | **Only after the profile images are rebuilt.** On 2026-10-02 `fabro-io version` in `fabro-ts:local` printed `0.1.2`. Check before you judge item 5. |

`improve` was also told that its cited ranges are now copied for the coder, so it
must use full repo-relative paths and put confirmed facts in Traps.

Unrelated but in the same window: `aeb94e4` stopped the scheduler probe from
restarting stopped sandboxes. If `fabro-run-*` containers of terminal runs are
again `Up`, that fix regressed.

## Finding runs to measure

- Only runs **created after 2026-10-02T02:34Z** use the new graph. A run used it
  if its events contain a `stage.started` with `node_id == "excerpts"`. Run
  `01M3X2GDWNYWTR4Q5SDW6SK3F3` started at 01:08Z and does **not**.
- Item 5 counts only for runs whose sandbox image has `fabro-io` ≥ 0.2.0:
  `ssh andrew@10.10.0.32 'docker run --rm --entrypoint fabro-io fabro-ts:local version'`
  (also `fabro-python-node`, `fabro-python`, `fabro-rust-node`). The image build
  time is in `docker image inspect -f '{{.Created}}' <image>`, and the
  deployment log names the rebuild.
- List recent runs. The CLI has no list verb, so use the API (`.data[]` carries `id`,
  `lifecycle`, `timestamps`, `repository`, `labels`):
  `ssh andrew@10.10.0.32 'curl -s -H "Authorization: Bearer $(docker exec fabro-fabro-1 cat /storage/server.dev-token)" http://10.10.0.32:32276/api/v1/runs'`.
  `docker ps -a --filter name=fabro-run-` also lists recent sandboxes. The sweeper
  removes them after 48 h. A run's issue is in `fabro inspect <run>` at
  `.[0].run_spec.settings.run.metadata.issue`.
- Raw events, which carry tool names, arguments, timings and token usage:
  `fabro events <run> --json`. `-p` hides tool names (it shows `?`).

## How to measure

Save `fabro events <run> --json` to a file, then for the `coder` node (visit 1;
tasks after the first are later visits):

- **time to first edit**: from the coder's `stage.started` `ts` to the first
  `agent.tool.started` whose `properties.event.ToolCallStarted.tool_name` is
  `edit_file` or `write_file`.
- **reads**: count and output bytes of `read_file` / `grep` / `glob` / `shell`
  calls before the first edit. Output is on `agent.tool.completed` at
  `properties.event.ToolCallCompleted.output`, joined by `tool_call_id`. Note
  whether a `read_file` hit a range `task-code.md` already showed.
- **long reasoning turns**: on each `agent.message`,
  `properties.event.AssistantMessage.usage.tokens.output`, and the length of
  `.reasoning.trace`. Decode time ≈ the gap from `agent.llm.first_output` to
  `agent.message`. A turn over ~2,000 output tokens before the first edit, whose
  trace holds fenced code, means item 3 did not take.
- **excerpts worked**: the `excerpts` node's stdout (`command.completed`, or
  `fabro logs`) says `excerpts: copied N range(s), B bytes`, or why it copied
  nothing. The coder's first `mcp__io__inputs` output should contain a
  `=== task-code.md` section.
- **code tools used** (only once item 5 is live): calls to `mcp__io__code_*`
  vs `shell` commands containing `fabro-code`, in `improve` and `coder`.
- **no quality regression**: per task, did `review` pass first time, or did it
  climb the rework ladder (`rework_t1..t4`)? Compare `review_gate`/`rework_router`
  edges in `fabro events -p`. Track the merge outcome too
  (`review_merge.report_merged` vs `report_blocked`). Faster but worse is a fail.

The original analysis built this with jq plus a ~30-line Python script
(prefill time = `llm.started`→`first_output`, decode time = `first_output`→`message`).
Rebuild it rather than look for it: it lived in a session scratchpad.

## What success looks like

Judge on several tasks (at least 5 coder visits on the new graph, across 2+ repos),
not one:

- median coder start → first edit **well under 17 min**. A realistic target is
  ≤ 6 min when a dossier exists.
- before the first edit, roughly ≤ 3 `read_file` calls, and no whole-file
  reads of files `task-code.md` covers.
- no pre-edit reasoning turn of thousands of tokens that drafts code.
- review first-pass rate and rework escalations no worse than before. Pull
  earlier runs from the same repos for the comparison.
- once images carry 0.2.0: `code_*` calls appear in `improve`/`coder`.

## Known risks to look for

- **`excerpts` copies nothing**: `improve` cites `x.py:40` or `:40-62` without
  the full path. Those are skipped by design. Fix: tighten
  `backlog/prompts/improve.md.j2`, not the node.
- **Inputs paging**: `task-code.md` can reach 40 KB, and `inputs` pages at
  49,152 bytes, so the coder may get `MORE:` lines. Check that it requests them
  and does not skip `task-code.md`.
- **Over-trust**: the coder now treats the dossier as verified. A wrong Trap now
  gets acted on instead of re-checked. Look for review failures that trace back
  to a dossier error.
- **edit_file mismatches**: if `edit_file` fails on text copied from
  `task-code.md` (whitespace, tabs, CRLF, a file with no trailing newline), the
  block is not exact. The node is in `backlog/workflow.fabro`, and its tests are
  the `excerpts` section of `ops/test-task-gates.sh`.
- **Scope**: items 1–4 are coder-only. `rework_t*` still get `repomap.md` and
  read files as before. That was deliberate, because their excerpts would be
  stale after the coder's edits.

## Where things are

- Graph: `.fabro/workflows/backlog/workflow.fabro`. Search for `excerpts` (node,
  comment, edges) and `task-code.md` (the `next_task` delete).
- Prompts: `.fabro/workflows/backlog/prompts/coder.md.j2`, `improve.md.j2`.
- Manifest: `.fabro/workflows/_io/manifest.json`, `backlog` → `coder`.
  Regenerate with `python3.11 ops/fabro-io-manifest.py generate`, never by
  hand-editing `workflow.toml`.
- Tools: `ops/fabro-io/src/code.rs`, `src/serve.rs`, `tests/code_tests.rs`, and
  the README section on `code_*`.
- Deploy history: the 2026-10-02 entry in `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`
  (operator's Mac only, outside this repo).

Write findings as a dated section appended to this file. If a change needs
reverting or tuning, follow AGENTS.md: validate in the container, run the
routing checker, the manifest check and `ops/test-task-gates.sh`, then commit
to `main`.

## Suggested skills

- `fabro-workflow`: load it first. It covers the CLI (`fabro events`,
  `fabro inspect`, `fabro logs`), the DOT grammar, and the validate loop, which you
  need if a node or prompt must change.
- `diagnose`: if a metric regressed or a run failed in `excerpts`/`coder`.
- `ms-rust`: required before any edit to `ops/fabro-io/` (item 5).
- `writing-for-agents`: when you revise `coder.md.j2` / `improve.md.j2`.

## 2026-10-03: result for `24eb95c`

Measured over 43 coder visits in 11 `backlog` runs on four repos (10 with the 17fe4f2 rule).
The details and the run list are in `result-2026-10-03.txt`.

| Criterion | Result |
|---|---|
| median coder start → first edit ≤ 6 min | **met**: 4.1 min (17 min before). The tail is long: 9 of 43 visits took over 10 min. |
| ≤ 3 `read_file` before the first edit | **met at the median** (3), but 21 of 43 visits read more |
| no whole-file reads of files `task-code.md` covers | **mostly**: 13 in 37 visits after 17fe4f2, against 58 in 22 before |
| no pre-edit reasoning turn of thousands of tokens that drafts code | **missed**: 10 of 37 visits; 62.6K of 154.2K pre-edit output tokens. writers-app is the worst. |
| review first-pass and reworks no worse | **met**: 95.3% first-pass. Reworks are 0.19 per task without wfs#1300, whose failures are environmental. |
| `code_*` calls in `improve`/`coder` | **met**: 89 calls (code_show 37, code_def 27, code_search 21) |

`excerpts` gave a `task-code.md` with ranges to 41 of 43 coder visits.
