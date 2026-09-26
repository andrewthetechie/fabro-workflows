# Merge-gate hardening: overview and canonical contracts

**Status:** applied 2026-09-26. Tasks 01 to 05 are done, and task 06 records the first runs.

**Read this file first.** Every task in this folder assumes the decisions, contracts and
rules in this file. Each task repeats what it needs. If a task and this file disagree,
this file is correct. Stop and report the conflict. Do not guess.

Source decision: `docs/adr/0013-merge-gate-hardening.md` (ADR 0013). The roadmap records
are `docs/factory-roadmap/A4-diff-hygiene-gate.md` and `A5-cross-family-refuter.md`.

**When you write or change a `.fabro` file or a `workflow.toml`, invoke the
`/fabro-workflow` skill first.** It has the DOT attribute tables, the node types, the
condition grammar, the `import=` contract and the CLI. This file adds only the facts
that are specific to this change.

## What this is

The shared merge phase `.fabro/workflows/_shared/review-merge/review-merge.fabro` is
spliced into both `backlog` and `pr-review` with `import=`. Today its main path is:

```
prep_review → standards → spec → review_fix → validate → deliver → merge_gate → watch_checks → merge
```

This series inserts four nodes between `validate` and `deliver`:

```
validate ─(green)→ hygiene → refute_prep → refute → refute_gate → deliver → merge_gate …
```

- `hygiene` (command) counts test tampering and code erosion in the diff, and writes
  `hygiene.json`.
- `refute_prep` (command) fetches the linked issues' text and builds a capped diff.
- `refute` (agent) tries to prove the work is not done, and writes `refute.json`.
- `refute_gate` (command) validates `refute.json` and computes the verdict.

`merge_gate` gains check 13 (hygiene), check 13b (the tree is still the one the counters read) and check 14 (Refuter). `ci_fix_gate` runs the
hygiene counters again before it pushes a CI fix. The review comment gains two table
rows and two sections.

## Glossary (from `CONTEXT.md`)

- **Tamper counter**: a count, made by code, of a diff change that makes the tests test
  less. Any non-zero tamper counter blocks the auto-merge.
- **Erosion counter**: a count, made by code, of a diff change that makes the code
  harder to maintain. Report-only unless the repository sets `erosion_mode: "block"`.
- **Refuter**: the reviewer that runs last before delivery, sees only the issue, the
  diff and the counters, and is told to prove the work is not done. Its result is a
  checklist, never a score.

## Operator decisions (settled, do not change them)

| # | Decision |
|---|---|
| 1 | Build it now. No baseline, no scorecard, no seeded-defect suite. |
| 2 | `.refute` runs on **`glm-5.3`** in both importing stylesheets. It inherits the existing `"glm-5.3" = ["kimi:kimi-k3"]` fallback. Do not add a new fallback row, and do not add a catalog alias to avoid it. The operator moves it to a non-GLM provider later. |
| 3 | The Refuter runs on **every** PR that reaches a green `validate`, including PRs that cannot auto-merge (human PRs in `pr-review`, `architecture` PRs, PRs with auto-merge off). |
| 4 | A Refuter that fails (timeout, provider error, or invalid output twice) **blocks** the merge. `deliver` still runs. There is no fallback verdict. |
| 5 | The hygiene counters run again in `ci_fix_gate`, before the push. A blocking result refuses the push and reports `blocked`. The Refuter does not run again. |
| 6 | No override. There is no issue-body line and no label that clears a hygiene or Refuter block. A human merges by hand. |
| 7 | Erosion counters are report-only by default. A repository opts in to blocking with `.fabro/hygiene.json` on its base branch. |
| 8 | The per-repository config is **JSON**, `.fabro/hygiene.json`, read from `origin/<base>`, never from the PR head. |
| 9 | The Refuter **blocks from day one**. The operator accepts false blocks. |
| 10 | A Refuter `fail` does not loop back to `review_fix`. The checklist is posted as a section of the review comment (a plain comment, not a "request changes" review). |
| 11 | **Everything lands in one push** (task 06). Tasks 01 to 05 commit locally and do not push. A push to `main` is live on the next run. |
| 12 | No change to target repositories. Every repository runs on the defaults until someone adds a `.fabro/hygiene.json` there. |

## Canonical contracts

### C1. Run-local files under `/tmp/fabro/`

| Path | Written by | Read by | Shape |
|---|---|---|---|
| `hygiene.sh` | `hygiene` | `hygiene`, `ci_fix_gate` | The counter program (task 01). No arguments. Always exits 0 after it writes `review/hygiene.json`. |
| `review/hygiene.json` | `hygiene.sh` | `merge_gate` checks 13 and 13b, `ci_fix_gate`, `refute`, the renderer | C2 |
| `review/hygiene_reason.txt` | `hygiene.sh`, only when `blocking` is non-empty | `merge_gate` check 13, `ci_fix_gate` | One line for a human. Deleted at the start of every run of `hygiene.sh`. |
| `_hygiene/` | `hygiene.sh` | nothing | Scratch directory. |
| `review/refute_issues.json` | `refute_prep` | `refute` | `[{number,title,body}]`, at most 3. An issue that could not be read is `{number,title:null,body:null,error:"could not be read"}`. May be `[]`. |
| `review/refute_diff.patch` | `refute_prep` | `refute` | `git diff origin/<base>...HEAD`, smallest file first, at most 300000 bytes. |
| `review/refute_omitted.txt` | `refute_prep` | `refute` | `<path> (<n> lines changed)` for each file that did not fit. May be empty. |
| `review/refute.json` | `refute` | `refute_gate`, the renderer | C3 |
| `refute_attempts` | `refute_gate` | `refute_gate` | Integer. Deleted by `refute_prep`. |
| `review/refute_verdict` | `refute_gate` | `merge_gate` check 14, the renderer | One word: `pass`, `fail` or `invalid`. **Absent** means the Refuter did not finish. |
| `review/refute_reason.txt` | `refute_gate`, on `fail` or `invalid` | `merge_gate` check 14 | One line for a human. |

`refute_prep` deletes `refute.json`, `refute_verdict`, `refute_reason.txt` and
`refute_attempts` before `refute` runs (`AGENTS.md`: delete a contract file before the
agent that writes it runs).

### C2. `hygiene.json`

```json
{
  "version": 1,
  "base": "<sha of origin/base>",
  "head": "<sha of HEAD>",
  "config": "default" | "repo" | "invalid",
  "erosion_mode": "report" | "block",
  "erosion_threshold": 3,
  "tamper":  {"tests_deleted": 0, "skips_added": 0, "asserts_removed": 0, "tautologies_added": 0, "config_changed": 0},
  "erosion": {"broad_except": 0, "type_escape": 0, "lint_disable": 0, "rust_unwrap": 0, "todo_unlinked": 0},
  "blocking": ["tests_deleted"],
  "samples": [{"counter": "skips_added", "file": "tests/test_x.py", "line": 2, "text": "@pytest.mark.skip"}]
}
```

- `blocking` is the decision. It lists every tamper counter above 0; every erosion
  counter above its threshold, but only when `erosion_mode` is `block`; `config_invalid`
  when `config` is `invalid`; and `hygiene_error` when the program could not compute the
  counters. `merge_gate` blocks when `blocking` is not an empty **array**, so a file
  with no `blocking` key blocks.
- When the program fails, the file is
  `{"version":1,"config":"default","error":"<why>","tamper":{},"erosion":{},"blocking":["hygiene_error"],"samples":[]}`.
- `samples` holds at most 20 entries, tamper counters first. `line` is the new-file
  line for an added line, the old-file line for a removed assertion, and `null` for a
  deleted file. `text` is at most 160 characters.

### C3. `refute.json` (written by the agent)

```json
{
  "summary": "string",
  "criteria": [{"criterion": "non-empty string", "status": "met" | "not_met" | "unverifiable", "evidence": "non-empty string"}],
  "defects":  [{"file": "string", "line": 42, "scenario": "non-empty string"}]
}
```

`criteria` has at least one entry. `defects` may be `[]`. `line` is a number or `null`.
There is no verdict key: `refute_gate` computes it. The verdict is `pass` when every
criterion is `met` and `defects` is empty, and `fail` otherwise.

### C4. `.fabro/hygiene.json` (in a target repository, on its base branch)

Every key is optional. A key that is present replaces the default (a shallow merge):

```json
{
  "erosion_mode": "report",
  "erosion_threshold": 3,
  "erosion_thresholds": {"lint_disable": 5},
  "test_paths": ["(^|/)(tests?|__tests__|spec)/", "_test[.][a-z]+$", "[.](test|spec)[.][a-z]+$", "(^|/)test_[^/]*[.]py$"],
  "exclude_paths": ["(^|/)(package-lock[.]json|yarn[.]lock|pnpm-lock[.]yaml|bun[.]lockb?|Cargo[.]lock|poetry[.]lock|uv[.]lock)$", "[.]min[.](js|css)$", "(^|/)(dist|build|vendor|node_modules)/"]
}
```

The values shown are the defaults. Patterns are POSIX extended regular expressions,
matched by `awk` against repository-relative paths. They are written with bracket
expressions (`[.]`) and never with a backslash, because the defaults live in a `.fabro`
file. The file is **invalid** when it is not exactly one JSON object (empty, `null` or
two documents are all invalid), when `erosion_mode` is not
`report` or `block`, when `erosion_threshold` is not a number, when
`erosion_thresholds` is not an object of numbers, when `test_paths` is not a non-empty array of
strings, or when `exclude_paths` is not an array of strings.

### C5. The counters

The counters read only `git diff -U0 origin/<base>...HEAD`. A file that matches
`exclude_paths` is ignored entirely. A "test file" matches `test_paths`. An
"assertion-bearing file" is a test file or any `.rs` file, because Rust keeps unit
tests next to the code.

| Counter | Kind | Counts |
|---|---|---|
| `tests_deleted` | tamper | a deleted test file (a rename is not a deletion) |
| `skips_added` | tamper | an added line, in any file, matching `#[ignore]`, `@pytest.mark.skip`/`xfail`, `pytest.skip(`/`xfail(`, `@unittest.skip`, `@Disabled`, `@Ignore`; or, in a test file only, `it`/`describe`/`test`/`context`/`suite` `.skip(`/`.only(`/`.todo(`, or `xit(`/`xdescribe(`/`xtest(` |
| `asserts_removed` | tamper | removed assertion lines in assertion-bearing files that are not accounted for. A removed line is **moved**, and not counted, when an added assertion line anywhere in the PR has the same text after trimming whitespace (each added line excuses one removal). The rest are netted per hunk: unmoved removed lines minus the hunk's added assertion lines not used to excuse a move, when positive. An assertion line matches `assert…` followed by space, `.`, `(` or `!`, or `expect(` not preceded by `.` (Rust's `Result::expect` is not an assertion). Removed lines of a deleted file are not counted (that is `tests_deleted`). |
| `tautologies_added` | tamper | in an assertion-bearing file, an added `expect(<literal>)`, `assert True`, `assert 1 == 1`, `assert!(true)`, or a same-argument `expect(x).toBe/toEqual/toStrictEqual(x)`, `assert_eq!(x, x)`, `assertEqual(x, x)`, `assert x == x` |
| `config_changed` | tamper | the PR touches `.fabro/hygiene.json` |
| `broad_except` | erosion | `except Exception`, `except BaseException`, bare `except:`, an empty `catch {}` |
| `type_escape` | erosion | `# type: ignore`, `as any`, `as unknown as`, `@ts-ignore`, `@ts-expect-error`, `cast(Any` |
| `lint_disable` | erosion | `eslint-disable`, `noqa`, `#[allow(`, `#![allow(`, `pylint: disable`, `biome-ignore` |
| `rust_unwrap` | erosion | `.unwrap()` or `.expect(` in a `.rs` file that is not a test file |
| `todo_unlinked` | erosion | `TODO`, `FIXME` or `XXX` on a line with no `#<n>` and no `/issues/<n>` |

## Rules every task must follow

These come from `AGENTS.md`. Each one cost a live incident, and `fabro validate` catches
none of them.

1. **`.fabro` files are Graphviz DOT.** A node's shell script is a `script="..."`
   attribute. Inside it:
   - The **only** backslash allowed is `\"`. Never write `\n`, `\t`, `\\`, `\.` or
     `\(`. The DOT parser turns `\n` into a real newline. In a regular expression use a
     bracket expression: `[.]`, `[(]`, `[[]`, `[]]`, `[+]`. To get a tab or a unit
     separator in `awk`, use `sprintf("%c", 9)` or `sprintf("%c", 31)`. In `jq`, use
     `([31] | implode)`.
   - Write every `"` in the script as `\"`. The code blocks in these tasks are
     **already escaped** for the `.fabro` file. Paste them as they are.
   - Do not add `#` comments to a script. `merge_gate` is the one exception: its checks
     are numbered with `# N.` lines, and new checks keep that style. A `#` inside a
     string or a regular expression is fine.
   - A script must not contain `{{`. Fabro substitutes `{{ … }}` in `script` values.
   - Scripts run under POSIX `sh`: no `[[ ]]`, no `pipefail`, no arrays, no `local`.
     The sandbox `awk` is **mawk**, and one profile image has **jq 1.6**. Everything in
     this series was tested on both.
2. **Routing.** A command node that prints `{"context_updates":{...}}` must declare
   `output_schema="routing"`. A node that prints no routing object must not declare it.
   `hygiene` and `refute_prep` print none. `refute_gate` prints one.
3. **Every command node has an unconditional edge to the failure node**
   (`mark_needs_human` in this phase). The one exception is the existing gate pattern
   (`standards_gate`, `spec_gate`, `fix_gate`), where the unconditional edge is the
   happy path and `outcome=failed` is caught by a weighted edge first. `refute_gate`
   follows that pattern.
4. **Delete a contract file before the agent that writes it runs** (`refute_prep`
   does it for `refute`).
5. **Agents never run `git` or `gh`.** Command nodes do.
6. **A class that a node in `_shared/` uses needs an explicit rule in both importing
   stylesheets** (`backlog` and `pr-review`). A missing rule silently inherits `*`,
   and in `backlog` that is a local coder box.
7. **A root graph's `stall_timeout` (60m) is larger than every node timeout.** `refute`
   uses 15m.
8. **Any merge-phase path into `mark_needs_human` leaves a true `needs_human_reason`.**
   `validate`'s success placeholder ("CI passed, but the run stopped after validate and
   before the review was delivered") stays true through all four new nodes, because
   none of them delivers anything. `deliver` overwrites it, as it does today. Do not
   add a new placeholder.
9. Never put a token, key or webhook URL in a tracked file. This repository is public.

## How to check your work offline (no server needed)

Run these from the root of the `fabro-workflows` checkout:

```sh
./ops/test-task-gates.sh      # before this series: "PASS: 335 checks"
```

It needs `jq`, `python3` and `git`. Each task that adds checks raises the `PASS` number:

| After task | `PASS` |
|---|---|
| 01 | 372 |
| 02 | 376 |
| 03 | 399 |
| 04 | 402 |
| review fixes (2026-09-26) | 415 |

`fabro validate` and `ops/check-routing-schemas.py` need the `fabro` binary, which exists
only in the container on the fabro host. Task 06 runs them. You cannot see a routing
mismatch offline, so follow rule 2 exactly.

## Task index

The tasks run in order, one at a time. **Do not push** until task 06.

| # | File | Blocked by |
|---|---|---|
| 01 | `01-hygiene-gate.md` | none |
| 02 | `02-hygiene-after-ci-fix.md` | 01 |
| 03 | `03-refuter.md` | 01 |
| 04 | `04-report-sections.md` | 01, 03 |
| 05 | `05-docs.md` | 01–04 |
| 06 | `06-validate-and-push.md` (operator, on the host) | 01–05 |

## Deploy notes (operator)

- Everything in this series is a graph, prompt or document change. Graph and prompt
  changes go live on the next run after they reach `main`. Nothing is copied to the
  host, and no container restarts.
- The one host-side check is that `glm-5.3` still resolves to `zai` (`fabro model test
  -m glm-5.3`). It has resolved to `zai` since 2026-09-25.
