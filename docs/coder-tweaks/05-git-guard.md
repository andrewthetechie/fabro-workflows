# 05. Block mutating git outside the exempt stages (C2), and stop inviting it

## Outcome
A `git stash`, `git checkout -- x`, `git commit` or `git fetch` from an agent's `shell`
in any stage but `resolve_merge` and the two pr-review rebase agents fails at once. The
reason names the safe alternative. No prompt tells an agent to "revert" in a way that
suggests git.

## Why
15 mutating git calls in 13 stage visits per 20 runs (overview). The prompt rule "Agents
do not run git" has not held, and our own coder prompt tells the agent to "revert"
out-of-scope files. The operator decided to block (D2).

## Change
1. `ops/fabro-io/src/`: add a `git-guard` subcommand per C2.
   - Parse `tool_input.command` for every `git` word that sits at command position: the
     start, or after `&&`, `||`, `;`, `|`, `(`, `$(` or `xargs`.
   - Skip global options, then judge the subcommand and its first argument against the
     C2 allowlist.
   - Ignore anything inside single-quoted strings, so that `grep -n 'git stash'` is not a
     hit.
   - Add `git` to `manifest::Stage` as an optional string, and treat `"write"` as exempt.
   - Exit 0 on any error of its own, with a stderr warning.
2. `.fabro/workflows/_io/manifest.json`: add `"git": "write"` to backlog `resolve_merge`
   and pr-review `rebase_agent_t1`/`rebase_agent_t2`. Regenerate the manifest.
3. The hook (C2 exactly, including the wrapper) in `backlog`, `pr-review`, `arch-review`
   and `issue-triage` `workflow.toml`. The id is `git-guard`, with a comment that cites
   this task and the exit-code fact.
4. Prompts:
   - `backlog/prompts/coder.md.j2`, Process step 6: replace "→ revert it" with "→ undo
     your change with `restore_file`".
   - The "Missing dependency"/"About commits" sections stay.
   - Add one line under "Validation is external": "If a check fails in code you did not
     touch, note it in your completion message. To confirm it predates your change, use
     `baseline_check`. Never stash or check out files."
   - Mirror the line in `rework.md.j2`.
   - `improve.md.j2` (backlog) and `_shared/triage/prompts/improve.md.j2`: in Boundaries,
     add "Never edit a tracked file, not even temporarily; use `baseline_check` to test
     the base commit." Task 06 enforces this.
5. AGENTS.md: the "Agents do not run `git`" invariant row gains: "enforced by the
   `git-guard` pre_tool_use hook (C2); exempt stages carry `"git": "write"` in the
   manifest." Add an invariant row: "A blocking hook's script must exit 0 when its own
   program is missing: any non-zero exit blocks every call it matches."

## Acceptance
- `cargo test` covers each of these:
  - every allowlisted form passes;
  - `git stash && x; git stash pop`, `cd a && git checkout -- b`, `git -C x commit -m y`
    and `git fetch` are blocked outside exempt stages;
  - all of them pass in `resolve_merge`;
  - `grep -n 'git stash' f` and `echo "git commit"` pass;
  - a missing `stage.json` or manifest exits 0.
- The wrapper is tested in `ops/test-task-gates.sh` by extracting the hook script from
  `workflow.toml` and running it with no `fabro-io` on `PATH`: exit 0.
- `fabro validate` for all four graphs is unchanged (hooks are in `workflow.toml`), and
  `python3.11 -c 'import tomllib…'` parses each file.
- In one live run, a forced violation is blocked, and the reason appears in the agent's
  tool result. Use `fabro steer` to ask the coder to run `git stash list && git stash`.

## Depends on
04 (the tools the reason names). Deploy the images before you push the hook. The wrapper
makes an early push safe, but the reasons would point at tools that do not exist yet.
