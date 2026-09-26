# 03 · `fabro-code`: the only interface agents use

Read `00-overview-and-contracts.md` first. The contracts for this task are C1, C2 and C3.

## Why a wrapper, and not raw `codegraph`

The bench (ADR 0014 D1) found two codegraph behaviours that give a small model a confident
wrong answer:

- `codegraph node LIVE_SCORING_ENABLED` returned `readScores`, an unrelated method, because
  name resolution is fuzzy.
- `callers` returns call edges only. For a settings field it found 1 of 18 files.

`fabro-code` fixes both at the interface: exact names only, and a footer on every reference
answer. It also makes each answer fresh (`sync` first) and small (text, capped). Prompts name
`fabro-code` only, never `codegraph`.

## File

`ops/profile-images/fabro-code`, POSIX `sh`, `jq` and `sqlite3` only. Implement the verbs and
rules in C2 exactly. Notes for each verb:

- **Exact resolution.** Run `codegraph query "<name>" --json --limit 50`, then
  `jq '[.[] | select(.node.name == $n)]'`. For `path:line` input, select the node whose
  `filePath` is equal to the path and whose `startLine <= line <= endLine`. Take the
  narrowest match.
- **`show`** runs `codegraph node <name>` only after exact resolution gives exactly one
  match. If codegraph's `node` accepts an id or a `file:line`, pass that and not the bare
  name, so that it cannot re-resolve fuzzily. Check `codegraph node --help` in the pinned
  version. If it accepts only a name, compare the `**Location:**` line of the output with
  the resolved path and line, and on a mismatch print the resolved source range with
  `sed -n` instead.
- **`map`** uses `sqlite3 -readonly .codegraph/codegraph.db`. The schema was checked on
  1.6.0: `nodes(id, kind, name, file_path, start_line, signature, …)`,
  `edges(source, target, kind, …)` with the kinds `calls`, `contains`, `extends`,
  `imports`, `instantiates` and `references`, and `files(path, generated, …)`. Before the
  query, read `schema_versions` and refuse (exit 2, `map: unknown codegraph schema <v>`)
  on a version that the script does not know.
- **`index`** runs `timeout 120 codegraph init --yes </dev/null`. On success it writes `ok`
  to `/tmp/fabro/code_index`, and on failure `failed: <last stderr line>`. It exits 0 in
  both cases.

## Tests in `ops/test-task-gates.sh`

Add a section `fabro-code`. It runs only when `codegraph` is on `PATH`, and it prints `SKIP`
when it is not. So on the Mac it is skipped, and inside every profile image it runs. Build a
fixture repository with **real git** (the `open_pr_prep` pattern) that has:

- a Python module that defines `get_thing()` and a class `Config` with a field `FLAG`;
- a second module that calls `get_thing()` and reads `Config.FLAG`;
- two functions named `helper`, in different files;
- a test file that imports the first module;
- a TS file with a `get` method (the cross-language probe).

Assert the following:

1. `def get_thing` prints one line and exits 0.
2. `def FLAG` exits 1 and prints the "fields and attributes are not indexed" line.
3. `def helper` and `show helper` exit 1 and list both paths.
4. `def get_thin` exits 1. There is no fuzzy match.
5. `callers get_thing` lists the calling file and ends with the footer line.
6. Append a new caller to a file. With **no** explicit sync, `callers get_thing` shows it.
7. `tests <first module>` lists the test file.
8. `map` writes a file of 16,000 bytes or less whose first line matches `^# Repo map (`.
9. After `index`, `git add -A && git status --porcelain` does not show `.codegraph`. Test
   this with the `.git/info/exclude` line from C4.
10. With `/tmp/fabro/code_index` holding `failed: x`, every verb exits 2.

Rebase `/tmp/fabro` onto the scratch directory, as the other sections do.

## Acceptance

`./ops/test-task-gates.sh` passes on the Mac (with the section skipped) and inside all four
images (with the section run). The check count grows by at least 10.
