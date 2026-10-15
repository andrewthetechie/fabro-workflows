# Task 03: the plan, the guard and the text

Read `00-overview-and-contracts.md` first (contracts C3 to C7).

## Goal

Given this week's classes, last week's classes and the known retro issues, `retro.plan`
decides each write and renders its text. `retro.discord_text` renders the weekly message.
Pure code: the week's dates come in as arguments.

## Steps

All in `ops/scheduler/src/fabro_scheduler/retro.py`:

1. `RETRO_REPO`, `RETRO_LABEL`, `ISSUE_LABELS = ("retro", "bug")`, `MARKER` (C3), and
   `known_classes(issues)` (C3). Import `TrackedIssue` from `github.py`.
2. `guard(text, repos)` (C5). Build the patterns once per call from `repos`, with
   `re.escape` on every name, and return the first match's description, for example
   `"bare repository name 'jelly-swipe'"` or `"cross-repository reference 'wfs#2080'"`.
   Make the description safe itself: it goes to Discord, not to GitHub, so naming the repo
   there is allowed.
3. `safe_sample(reason)` (C5 steps 1 to 3). In `classify`, replace the `# made safe by
   task 03` comment with a call to it. Keep at most 3 distinct samples (C5 step 4).
4. `Action` (C4), and the renderers:
   - `issue_title(stat, week_start, regression_of=None)`;
   - `issue_body(stat, prev_stat, week_start, regression=None)`, where `regression` is the
     closed `TrackedIssue` or `None`;
   - `comment_body(stat, prev_stat, week_start)`.

   Follow C7 exactly. The marker is the body's first line, so `MARKER` finds it again.
   `SUNDAY` is `week_start + 6 days`. Box-hours and per-run lease hours have one decimal.
   The runs list is the first 10 of `stat.runs` (already longest first).
5. `plan(...)` (C4), in rank order. An action that is `suppressed` or `capped` has
   `body=None`. Check the rendered title and body with `guard`: a match makes the action
   `withheld`, with `why` set and `title`/`body` kept for the log only. Write those to
   stderr, never to Discord or GitHub. A withheld `file` or `regression` gives its cap slot
   to the next class.
6. `discord_text(card, prev_card, actions, *, week_start, dry_run, still_leased, unsummarized,
   failure=None, done=None) -> str` (C6).
   - `failure` is the `GitHubError` text, or `None`.
   - `done` is the set of action indexes actually written, for the partial-failure case.
     `None` means all of them. A planned write that was not done is listed as
     `Not written: <key>`.
   - Real runs show the issue number GitHub returned for a new issue. Accept them through
     an `issued: dict[int, int]` argument (action index → issue number). Dry runs show none.
   - Enforce the 2000-character limit as C6 describes, and assert it at the end.

## Tests (`ops/scheduler/tests/test_retro.py`)

- `known_classes`: an issue with two markers maps under both keys; a key's list has the
  highest number first; an issue without a marker is ignored.
- `plan`, one case per row of C4:
  - open issue → `comment` on it, even when a closed one with the same key also exists;
  - closed `completed` → `regression`, and the title starts `retro (regression of #N): `;
  - closed `not_planned`, and closed `duplicate` → `suppressed`;
  - nothing known → `file`;
  - 5 new classes with cap 3 → 3 `file`s by box-hours, then 2 `capped`;
  - a class under the threshold has no action;
  - a withheld class gives its slot to the next (cap 1: the first class names
    `jelly-swipe` in a sample, so the second class is filed).
- `guard`: matches `andrewthetechie/lawncare-saas`, `LAWNCARE-SAAS` (case), `lawncare-saas`
  as a whole word, `https://github.com/andrewthetechie/writers-app/pull/3`, and `wfs#2080`.
  It does not match `#41`, `(#41)`, `https://github.com/andrewthetechie/fabro-workflows/issues/41`,
  or `lawncare-saasy`. Use a repos list of the four names from `ops/scheduler/repos.toml`.
- `safe_sample`: `closes #1326` → `closes issue 1326`; `@andrew` gets the zero-width
  space; a 400-character reason is cut to 160 plus `…`.
- Rendering: the body's first line is the marker, and `MARKER` reads the key back; the
  previous-week column is `0` for a class absent last week; the body contains no repository
  name from the test config; a regression body names the closed issue and its date.
- `discord_text`: the dry-run prefix and the `Would ...` wording; the signed deltas
  (`+2`, `-3`, `+5pp`, `n/a`); an empty list's line is left out; with 200 filed actions the
  text is at most 2000 characters and ends its list with `…and N more`; a `failure` puts
  `retro failed:` on the first line and lists `Not written:` keys.

## Done when

`uv run pytest` in `ops/scheduler/` and `make check` pass. One commit:
`feat(scheduler): plan the retro's issues and render its text (factory-retro 03, #33)`.
