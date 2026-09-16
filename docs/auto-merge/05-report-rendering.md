# Task 05 — Report rendering: the `merged` and `blocked` kinds

**Depends on:** 03. **Blocks:** 09. **LLM level:** yes — the renderer is a jq program
inside a shell script inside a DOT string.

`render_comment.sh` already takes a kind (`complete` / `needs-human`) and is built in
`validate_input` as a heredoc, alongside `render.jq`
(`.fabro/workflows/pr-review/workflow.fabro:25-113`). Add two kinds. This is an
extension of an existing shape, not a rewrite.

**Task 03 already restored the comment**, because moving it out of `deliver` without
moving it in anywhere left every successful run finishing with no comment on the PR at
all. `report_merged` and `report_blocked` currently render the existing `complete` kind
and append a `### Merged` or `### Not auto-merged` section built in shell. This task
replaces those shell-built sections with real kinds inside `render.jq` and deletes the
appends — it is not adding reporting to nodes that have none.

## What changes

`$kind` gains `merged` and `blocked`. `complete` stays, because `mark_needs_human`
and the fallback paths still use the existing two.

### The heading

```
## AI PR review complete - risk 2/5              (complete, unchanged)
## AI PR review stopped - human needed           (needs-human, unchanged)
## Merged automatically - risk 2/5               (merged)
## AI PR review complete - risk 4/5              (blocked, same as complete)
```

`blocked` reuses the `complete` heading deliberately. Operator decision 11: an
expected block keeps `ai-review-complete` and is not an alarm. A PR rated 4 that got a
clean review is a *successful* review with a merge that correctly did not happen, and
giving it a scarier heading than the same PR rated 3 would train the reader to ignore
the heading.

### The new section

Both kinds append one section after `### Commits with changes`.

For `merged`:

```
### Merged

Squash-merged as `fix(frontend): button "Open in Jellyfin" opens a blank new tab (#375)`.
Head branch deleted. Resolves #375.
```

For `blocked`:

```
### Not auto-merged

**Reason:** risk is rated 4/5; automatic merge requires 3 or below.

Everything else about this review stands. Merge it by hand when you are happy with it.
```

The reason is `/tmp/fabro/merge_block_reason`, verbatim, written by whichever node
blocked. That is why task 03 insists the reason lines are written for a human reading
the PR — this is where they surface.

The second line is a constant, and it earns its place: without it a reader sees a
clean review, a "Not auto-merged" heading and no statement of what they are supposed
to do.

### `ci_fix` rounds

When `/tmp/fabro/review/ci_fix_result.json` exists, both kinds render:

```
### CI fixes

- **lint** - removed the unused import in `frontend/src/App.tsx` that `ruff` flagged;
  re-ran the check locally against the same rule set.
```

`summary` verbatim, `checks` as the label. On `cannot_fix`, render `reason` instead
and prefix it `_Could not fix:_`.

This section exists because the PR's code changed *after* the reviewers looked at it.
A reader must be able to see that, and see what, without diffing commits.

## jq mechanics

`render.jq` is slurped four files at a time via `--slurpfile`. Add a fifth for
`ci_fix_result.json` and a sixth `--arg` for the block reason, following the existing
pattern in `render_comment.sh`:

```sh
for f in standards spec fix_result ci_fix_result; do
  if [ -s \"$R/$f.json\" ] && jq -e 'type == \"object\"' \"$R/$f.json\" >/dev/null 2>&1; then cp \"$R/$f.json\" \"$T/$f.json\"; else echo '{}' > \"$T/$f.json\"; fi
done
```

The `{}` fallback is what makes every section optional without a conditional at the
call site — an absent `ci_fix_result.json` slurps to `{}`, `nz(.checks)` is `[]`, and
the section renders as nothing. Keep that property.

Two rules the existing program already follows and the additions must not break:

- **Newlines come from `def NL: [10] | implode;`.** `"\n"` inside a `.fabro` string
  is an unterminated jq string, because the DOT parser turns `\n` into a real newline
  before jq ever sees it. This is in AGENTS.md's invariant table and it has bitten
  this file before.
- **Compile the jq program standalone** before embedding it. `sh -n` is blind inside
  `jq '...'`.

## What does not change

- `mark_needs_human` keeps rendering its own comment with kind `needs-human`, and
  keeps its `ai-review-needs-human` label. A run that failed unexpectedly cannot
  depend on a reporting node it never reached.
- The fallback in each caller — "could not render the detailed report, here is a
  minimal one" — stays. A renderer bug must not swallow the whole outcome, and on the
  `merged` path the merge has already happened by the time this runs.
- The `| Axis | Verdict |` table, the findings list, the fixes list and the commits
  list are untouched.

## Acceptance

- `merged` renders the heading, the squash subject, and `Resolves #N`.
- `blocked` renders the `complete` heading and a `### Not auto-merged` section
  carrying the reason line verbatim.
- Both render the `### CI fixes` section when `ci_fix_result.json` is present and omit
  it entirely when it is not.
- `complete` and `needs-human` render byte-identically to today when no merge files
  exist — diff against a captured sample from a current run.
- The jq program compiles standalone.
- No `"\n"` anywhere in the program; every newline is `NL`.
- No backslash other than `\"` in the `.fabro` file.
