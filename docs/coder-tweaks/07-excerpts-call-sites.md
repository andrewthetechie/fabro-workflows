# 07. Precompute call sites into `task-code.md` (C6)

## Outcome
`task-code.md` ends with a `## Where these are used` section listing the call sites of
every symbol the dossier names. The coder's most common search ("who calls this?") is
answered before it asks.

## Why
Asking the model to use a better search tool is the weaker lever. Not making it search is
the stronger one. `excerpts` (from `24eb95c`) already removed most reads of code the
dossier cites. The next largest share is `grep` for usages before a signature change,
which `fabro-code callers` answers exactly. This only works where the model doesn't need
the tool; it is not a substitute for 02 and 03.

## Change
`backlog/workflow.fabro`, node `excerpts`, after the code blocks and before the "Not
copied" list:
- Collect symbol names: backtick spans under `## Files and symbols` that match
  `^[A-Za-z_][A-Za-z0-9_]*` (the part before any `(`). Take at most 12, de-duplicated, in
  dossier order.
- With `fabro-code` on `PATH`, for each one run `fabro-code callers NAME`. Keep the
  `path:line  kind  name` rows (drop the footer and any `(N lines cut)` line), at most 15
  per symbol. Under each row, print the call line itself:
  `awk 'NR==L' path`, trimmed to 160 characters.
- Write the section only when at least one symbol has callers. Count its bytes against the
  same 40,000 cap, after the code blocks (code first).
- Without `fabro-code`, add no section.

Update the node's `//` comment, and C6 in the overview if anything changes.

## Acceptance
- `ops/test-task-gates.sh`, `excerpts` section:
  - a stub `fabro-code` whose `callers` prints two rows and a footer: the section lists
    both rows, each followed by the call line, and no footer;
  - with no stub, there is no section;
  - with the code blocks near the cap, the call sites are cut first, never a code block.
- In a profile image with the real wrapper: the #1278 dossier (copy it from `handoff.md`'s
  runs) produces call sites for `resolve_access_token` that include `deps.py` and
  `login_flow.py`.

## Depends on
Nothing; graph-only. It is live on the next fire after push.
