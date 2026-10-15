# Task 04: the `retro` subcommand

Read `00-overview-and-contracts.md` first (contract C9).

## Goal

`python -m fabro_scheduler retro ...` runs one retro inside the scheduler container. It is
the only part of the retro that does I/O: the store, the clock and GitHub.

## Steps

1. In `ops/scheduler/src/fabro_scheduler/app.py`, add `_retro(argv, *, now=None,
   environ=None, client=None) -> int` next to `_resummarize`, with an `argparse` parser
   for C9's flags. `--last-week` and `--week-of` are a required mutually exclusive group.
   `--repo` defaults to `retro.RETRO_REPO`. The three threshold flags default to the
   `retro.DEFAULT_*` constants. `now`, `environ` and `client` exist for the tests:
   default them to the current UTC time, `os.environ` and `None`.
2. Window: the Monday of the target ISO week at 00:00 UTC is `start`; `end = start + 7
   days`; the previous window is `[start - 7 days, start)`. For `--last-week`, the target is
   the week before the one that contains `now`. Reuse `_parse_date` for `--week-of`.
3. Load the config and open the store exactly as `_resummarize` does, with the same exit
   `1` messages. Read `GITHUB_TOKEN` from `environ`; if it is empty, print
   `scheduler: retro: GITHUB_TOKEN is not set` to stderr and return `1`.
4. Then follow C9's order:
   - `rows = store.scorecard_rows(start, end)` and `prev_rows = ...` for the previous
     window;
   - `card = scorecard.compute(rows, start, end)`, `prev_card = ...`;
   - `current = retro.classify(rows)`, `previous = retro.classify(prev_rows)`;
   - `still_leased` = the `store.lease_rows()` whose `dispatched_at` is in `[start, end)`;
   - `issues = github.fetch_label_all_states(repo, retro.RETRO_LABEL, token, client=client)`.
     On `GitHubError`: print
     `retro.discord_text(..., failure=f"cannot list retro issues: {exc}")` to stdout and
     return `3`;
   - `actions = retro.plan(current, previous, retro.known_classes(issues), week_start=start.date(),
     repos=[r.name for r in config.repos], ...)`;
   - unless `--dry-run`, run the actions in order. `comment` → `github.comment(repo,
     action.issue, action.body, token, client=client)`. `file` and `regression` →
     `github.create_issue(repo, action.title, action.body, retro.ISSUE_LABELS, token,
     client=client)`, recording the number it returns in `issued`. Stop at the first
     `GitHubError`.
   - print `retro.discord_text(...)` to stdout. Return `3` after a write failure, else `0`.
   - Close the store in a `finally`.
5. Log each write at info (`retro: commented on #N for KEY`, `retro: filed #N for KEY`), and
   each withheld action at warning with its `why`, to stderr only.
6. In `main`, dispatch `args_list[:1] == ["retro"]` to `_retro(args_list[1:])`, beside
   `resummarize`. Update `main`'s docstring.
7. Add the subcommand to `ops/README.md`'s `scheduler/src/fabro_scheduler/app.py` row, one
   sentence.

## Tests (`ops/scheduler/tests/test_app.py`)

Model them on `test_resummarize_dispatch_rebuilds_a_window`. Use a store in `tmp_path`, a
`repos.toml` written by the test, a fixed `now` (a Wednesday), and `respx` for GitHub.

- `--last-week --dry-run`: GitHub is read and never written (`respx` sees only the GET);
  stdout starts `[DRY RUN] Factory retro, week of <last Monday>`; exit `0`.
- A live run with one class over the threshold and nothing known: one `POST .../issues`
  with labels `retro` and `bug`, and a body whose first line is the class's marker; stdout
  names the new issue number; exit `0`.
- A live run where the class's issue is open: one `POST .../issues/N/comments` and no
  issue created.
- The list read answers `500`: no write, stdout starts `retro failed: cannot list retro
  issues`, exit `3`.
- The second of two writes answers `500`: the first write happened, stdout starts `retro
  failed:` and lists the second key as `Not written:`, exit `3`.
- No `GITHUB_TOKEN`: exit `1`, empty stdout.
- `--week-of 2026-10-08` selects 2026-10-05 to 2026-10-11, whatever `now` is.
- `--last-week` together with `--week-of`: exit `2`.

## Done when

`uv run pytest` in `ops/scheduler/` and `make check` pass. One commit:
`feat(scheduler): retro subcommand files and comments on failure classes (factory-retro 04, #33)`.
