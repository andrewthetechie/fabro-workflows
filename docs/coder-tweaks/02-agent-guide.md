# 02. Put the tool guidance in the system prompt (C1)

## Outcome
Every agent session on the `openai` profile loads a short tool guide from
`<checkout>/.codex/instructions.md` into its system prompt, next to the repo's own
`AGENTS.md`. `agent.memory.loaded` lists both files (M6).

## Why
Tool guidance in our stage prompts arrives as the user message. Pebble's system prompt,
which the model weighs more, tells it to prefer `rg` and documents only the native tools.
Our `code_*` tools are bare `mcp__io__*` entries with no guidance at all. The instruction
file is the one place we control that pebble puts in the system prompt (overview: verified
facts).

## Change
1. `.fabro/workflows/_io/agent-guide.md`: the guide, at most 3,000 bytes, following C1's
   content rule. A suggested shape (keep it short; the model reads it on every turn):
   - one line: these tools answer code questions in one call and return only what you ask
     for;
   - a table of intent → tool (C1's mapping), including the `fabro-code <verb>` fallback;
   - "`grep`/`rg` are right for text that is not a code name";
   - "read a range, not a whole file: `code_show path:line`, or `read_file` with
     offset/limit";
   - git: "Do not run git commands that change files, the index or branches. Use
     `restore_file` and `baseline_check` instead";
   - nothing stage-specific, and nothing about scope or reviewers.
2. `ops/fabro-io-manifest.py`: `generate` also writes
   `FABRO_AGENT_GUIDE = '''<file>'''` into each of the four `workflow.toml`
   `[run.environment.env]` tables, between new markers
   `# BEGIN fabro agent guide (generated…)` and `# END fabro agent guide`. `check` fails
   on drift, on a guide over 3,000 bytes, and on `'''` in the guide.
3. The entry nodes: backlog `prep`, pr-review `claim`, arch-review `prep`, issue-triage
   `acquire`. Next to the existing `.codegraph/` exclude lines, add the C1 install:
   - if `FABRO_AGENT_GUIDE` is non-empty and `git ls-files --error-unmatch
     .codex/instructions.md` fails: run `mkdir -p .codex`, write the guide, and add the
     exclude line once;
   - otherwise, one stderr line;
   - never fail the node, and never print to stdout (pr-review `claim` is a routing node).
4. AGENTS.md: add a deployment-invariant row: the guide is generated; never hand-edit the
   block; an entry node never overwrites a tracked `.codex/instructions.md`.

## Acceptance
- `python3.11 ops/fabro-io-manifest.py check` passes, and fails on a hand-edited guide
  block (test it).
- `fabro validate` for all four graphs: same node and edge counts as before. Only
  scripts changed.
- In one real run per workflow, `agent.memory.loaded` lists `.codex/instructions.md`
  with `truncated: false`, and the checkpoint commits do not contain the file
  (`git show --stat` of the run branch).

## Tests
`ops/test-task-gates.sh`, in the "code-index entry fragments" section, for each of the
four entry nodes (backlog `prep` is tested through its extracted lines, the others whole):
- the guide is written and excluded, and running twice adds the exclude line once;
- with the variable empty, nothing is written and the node still succeeds;
- with a tracked `.codex/instructions.md` (commit one in the fixture repo), it is left
  byte-identical;
- stdout is unchanged (`claim` still prints exactly one routing object).

## Depends on
03 and 04, for the tool names the guide uses. It can land before the images are rebuilt:
the guide's fallback line covers that.
