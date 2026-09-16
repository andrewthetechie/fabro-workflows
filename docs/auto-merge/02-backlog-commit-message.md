# Task 02 — `backlog`: Conventional Commits title and the commit-body block

**Depends on:** nothing. **Blocks:** 03. **LLM level:** yes — the derivation is small
but every branch of it is load-bearing.

Rewrite the tail of `open_pr_prep` and the title handling in `open_pr`. **No node and
no edge is added.** `backlog` stays at 38 nodes, 86 edges.

## Why this is first-order, not cosmetic

`open_pr_prep` currently writes
(`.fabro/workflows/backlog/workflow.fabro:314`):

```sh
echo 'agent: '$T' (#'$N')' > /tmp/fabro/pr_title.txt
```

`agent` is not a Conventional Commits type. `jelly-swipe` and `lawncare-saas` both run
`amannn/action-semantic-pull-request`, whose allowed types are
`feat fix docs chore refactor test ci build perf revert`. Measured on 2026-09-15:
`jelly-swipe#378` (`agent: [Chore] Remove the unreferenced…`) has `lint` **fail**;
`#376` (`fix(frontend): …`) has `lint` **pass**.

Until this lands, `watch_checks` can never go green on those two repos. Auto-merge
would be blocked for the right reason and the wrong cause.

## Deriving the subject

Deterministic. **No model is asked to pick a type**, because `release-please` runs on
two of these repos and turns that choice into a version bump.

Order of attempts, first match wins:

1. **The issue title already carries a prefix.** If it matches
   `^(feat|fix|docs|chore|refactor|test|ci|build|perf|revert)([(][a-z0-9 ._/-]+[)])?[!]?: .`
   use it as-is. This is the common case — `fix(frontend): button "Open in Jellyfin"…`
   went through untouched.
2. **A bracket prefix names a type _or a label_.** Lowercase the bracketed word, map
   it through the same vocabulary the labels use (`bug`→`fix`, `feature`/`enhancement`
   →`feat`, `documentation`→`docs`, `performance`→`perf`, `task`→`chore`), accept it
   if the result is in the allowlist, and strip the bracket and any following space.

   Both vocabularies, because a bracket word is as often a label name as a type.
   Across the four repos there are 26 bracket-prefixed issue titles — **13 `[Feature]`,
   12 `[BUG]`, 1 `[Chore]`** — and type-only matching handles exactly one of them. The
   other 25 fell through to step 3, where a label supplied the right type but the
   bracket stayed in the subject: `fix: [BUG] Committed card's vote is lost…`, which
   lint accepts and `release-please` publishes.
3. **An issue label maps to a type.** `bug` → `fix`; `enhancement`, `feature` →
   `feat`; `documentation`, `docs` → `docs`; `performance` → `perf`; everything else
   is ignored. Labels are read from `/tmp/fabro/issue.json`, which `claim` already
   writes. `agent`, `agent-in-progress`, `agent-stuck`, `Review` and `agent-authored`
   are fabro's own bookkeeping and never map.
4. **Default `chore:`.** Always terminates. The derivation cannot fail.

Then append ` (#N)` and truncate the description so the whole subject is at most 72
characters, cutting at a word boundary where one exists within the last 12 characters.
The appended reference is ` (#N)` — **four characters plus the digits**, so the budget
is `72 - ${#N} - 4`. Off by one there yields a 73-character subject.
The issue reference is never truncated away — it is appended after the cut.

Write the result to `/tmp/fabro/commit_subject.txt` and keep
`/tmp/fabro/pr_title.txt` as the same value, so `open_pr` (line 334) needs no change
beyond the file it reads.

The regex uses `[(]` and `[)]`, not `\(` and `\)`. `\"` is the only backslash a
`.fabro` file may contain.

### Two things the derivation must never do

- **Never emit `!`** and never write a `BREAKING CHANGE:` footer. Step 1 passes a `!`
  through if a human already wrote one in the issue title; nothing else may add one.
  On `jelly-swipe` and `lawncare-saas` that character cuts a major release.
- **Never emit a scope it invented.** Step 1 preserves a scope that already exists.
  Steps 2-4 produce a bare `type:`. `requireScope: false`, so a bare type passes lint,
  and a wrong scope is worse than no scope in a changelog.

## The commit body

`open_pr_prep` currently builds `pr_body.md` as `## Summary`, `Resolves #N: <title>`,
the contents of `completed.md`, and a `## Validation` section. That whole thing is
wrong for a squash commit: `completed.md` entries are task titles that often look like
Conventional Commits themselves (`### Task: fix(api): …`), and `release-please` parses
the entire message.

Split it in two.

`/tmp/fabro/commit_body.md` — what the squash commit gets:

```
<the Summary paragraph, prose, no headings>

Resolves #N
```

`/tmp/fabro/pr_body.md` — what the PR gets, unchanged in substance, plus the block:

```
## Summary

<!-- fabro:commit-body:start -->
<the Summary paragraph, prose, no headings>

Resolves #N
<!-- fabro:commit-body:end -->

<completed.md>

## Validation

- ./.fabro/setup.sh && ./.fabro/ci.sh green after final merge with origin/main
```

`:start` / `:end` markers, never a `<!-- /fabro:commit-body -->` closer — a slash in
the close tag forces `\/` into the extraction pattern. See
`00-overview-and-contracts.md`.

The Summary paragraph itself: today there is no prose summary, only
`Resolves #N: <title>`. Keep that as the minimum — `Resolves #N: <title>` is a valid
one-line body — but prefer the first paragraph of the issue body when it exists and is
under ~500 characters, since that is the closest thing to a human-written description
the run has. Strip markdown headings and list markers from it; a squash body is prose.

## Assertions before the PR opens

`open_pr_prep` runs under `set -e`. Add three checks at the end, so a derivation bug
fails the run into `human_rescue` rather than opening an unmergeable PR:

```sh
grep -qE '^(feat|fix|docs|chore|refactor|test|ci|build|perf|revert)([(][a-z0-9 ._/-]+[)])?[!]?: .' /tmp/fabro/commit_subject.txt
grep -q '^Resolves #' /tmp/fabro/commit_body.md
grep -q 'fabro:commit-body:start' /tmp/fabro/pr_body.md
```

These can only fire on a bug — the derivation always terminates at `chore:` and always
appends `Resolves #N`. That is exactly why they belong here: an assertion that can only
fail on a bug is the cheapest bug detector available, and `open_pr_prep` already routes
to `human_rescue` on failure.

## Acceptance

- `fix(frontend): button "Open in Jellyfin" opens a blank new tab` + issue 375 →
  `fix(frontend): button "Open in Jellyfin" opens a blank new tab (#375)`, under 72
  characters after truncation.
- `[Chore] Remove the unreferenced 1 MB frontend/public/favicon.png` + issue 377 →
  `chore: Remove the unreferenced 1 MB frontend/public/favicon.png (#377)`. This is
  #378's exact input and the reason its `lint` check fails today.
- An issue titled `Make the thing faster` labelled `enhancement` → `feat: Make the
  thing faster (#N)`.
- An issue titled `Make the thing faster` with no mappable label → `chore: Make the
  thing faster (#N)`.
- A 200-character issue title yields a subject of at most 72 characters that still
  ends in ` (#N)`. Test the boundary directly: a subject one character too long must
  come back at 72, not 73.
- `[BUG] Committed card's vote is lost on reconnect` labelled `bug` →
  `fix: Committed card's vote is lost on reconnect (#N)` — bracket stripped.
- `[Feature] Tie Wheel` labelled only `triage` → `feat: Tie Wheel (#N)`; the bracket
  supplies the type when no label does.
- `[Unknown] something else entirely` → `chore: [Unknown] something else entirely (#N)`;
  an unrecognised bracket word is left alone rather than guessed at.
- No output of the derivation contains `!` before the colon unless the issue title
  did, and none contains `BREAKING CHANGE`.
- `pr_body.md` contains exactly one `fabro:commit-body:start` and one
  `fabro:commit-body:end`, in that order.
- Extracting with the task 03 `awk` one-liner round-trips `commit_body.md` byte for
  byte.
- `sh -n` passes on the node script, and every embedded `jq` program compiles
  standalone.
