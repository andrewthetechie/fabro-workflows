# 03. Make the code tools the obvious choice: descriptions and `code_search` (C3)

## Outcome
The `code_*` tool descriptions say which habit each one replaces. A new `code_search`
tool does everything `grep` does, plus it shows the enclosing function or class for each
match. A model that decides to "search" therefore loses nothing by choosing it.

## Why
DeepSeek picks a tool by matching its plan ("search for X", "look at file Y") to tool
names and descriptions. `grep` matches "search" and our tools don't. Prompt rules have not
moved it: 4 `fabro-code` calls against 1,392 text searches. A tool that is a strict
superset of `grep`, with a name that matches the intent, does not ask the model to change
its plan, only to pick a better-labelled tool.

## Change
1. `ops/profile-images/fabro-code`: a `search <pattern> [path]` verb per C3.
   - Search with `rg -n --no-heading` when present, else `grep -rnI`. Cap the raw matches
     at 400 before grouping.
   - Group matches by file. For each file, resolve every matched line to its narrowest
     enclosing node with **one** `sqlite3` query per file (the `resolve_location` query,
     generalised to an `IN` list of lines), not one query per match.
   - If `pattern` is a bare identifier (`^[A-Za-z_][A-Za-z0-9_]*$`) and `resolve_name`
     finds exactly one definition, print the `def` row first.
   - With no index (exit 2 from `require_index`), print plain `path:line:text` matches
     and exit 0. Searching must still work.
   - Keep `cap_out` (200 lines) and the footer.
2. `ops/fabro-io/src/code.rs`: add `code_search` (`pattern` required, `path` optional),
   and rewrite every description so its first clause names the habit it replaces:
   - `code_def`: "Find where a function, class or variable is defined. Exact, and faster
     than grep for a name."
   - `code_show`: "Read one function or class (by name, or `path:line`) with its callers
     and callees, instead of reading the whole file."
   - `code_search`: "Search the code for a regex, like grep, with each match grouped under
     the function or class it is in."
   - keep `LIMITS` on all of them.
   Bump the crate minor version, regenerate `tests/fixtures/refute.json` (it records the
   version), and update the fabro-io README table.
3. The eight prompts' "Finding code" sections: add `code_search` to the list, and change
   "before `grep`" to "for code names; `grep` is fine for other text". Don't add prohibitions
   (D1).

## Acceptance
- `cargo test` passes. `code_tests.rs` covers `code_search` argument checks and the
  exit-code mapping with a stub.
- In a profile image, against a real-git fixture with an index: `fabro-code search NAME`
  prints the definition first, then grouped matches; `fabro-code search 'some text'`
  prints grouped matches; and with the index removed it prints plain matches and exits 0.
  The test lives in `ops/test-task-gates.sh`'s image-only `fabro-code` section, so on the
  Mac it prints `SKIP`.
- The `tools/list` assertion in `serve_tests.rs` includes `code_search`.

## Depends on
01, for the baseline. It needs `make deploy-images` to take effect.

## Risk
A grouped result that is harder to read than `grep`'s would undo the point. Keep each match
line `path:line:text` exactly as `grep` prints it, under a one-line group header.
