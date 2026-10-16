# Task 07: deploy, dry runs, go live, and the 14-day measurement (operator only)

Not for the implementing agent. Every step writes to `main`, the host, or a target repository.

## Deploy

1. Review the commits from tasks 01-06. Run `make check` with `CHECK_CARGO=always`, then
   `git push` (pre-push runs `make check-fast`).
2. From a clean checkout at `origin/main`: `make deploy-images` (the profile images get
   `fabro-io` 0.6.0), `make deploy-notify` (`scout-summary`), then `make provision` (the four
   `scout-<repo>` rows, schedules disabled). Then `make verify-host`.
3. `make check-host`. Confirm `Scout (9 nodes, 11 edges)` and that the other baselines are
   unchanged. If the counts differ, fix `ops/check-host.sh` and the `AGENTS.md` baseline to
   what `fabro validate` prints, and remove "unverified" from the `AGENTS.md` sentence.
4. In the sandbox image of each environment, check that `fabro-io scout config` prints C2's
   missing-file JSON:
   `ssh andrew@10.10.0.32 'docker run --rm fabro-python:local fabro-io scout config'` (repeat
   for `fabro-python-node`, `fabro-ts` and `fabro-rust-node`).

## First dry run, flaky source only

5. Fire one repository with no `scout.toml` yet. writers-app had 8 multi-attempt runs over
   the last 14 days, including the `UI end-to-end (tauri-pilot)` flake:
   ```sh
   ssh andrew@10.10.0.32 'curl -fsS -X POST -H "Authorization: Bearer $(docker exec fabro-fabro-1 cat /storage/server.dev-token)" http://127.0.0.1:32276/api/v1/automations/scout-writers-app/runs'
   ```
   Check the path prefix against `fire-pr-review.sh` first, as dependabot task 10 says.
6. Read `fabro events <run> -p` and the `plan` node's stderr. Check that the Discord line
   starts `scout writers-app DRY RUN`, that the flaky jobs and their counts match the Actions
   UI, and that the `ratchet` node did not run `setup.sh`.

## `scout.toml` in each repository

7. In each target repository, commit `.fabro/scout.toml` with `version = 1` and
   `filing = false`. Then add candidate Ratchet rules, one at a time, from the suggestions
   below. For each one, run its `command` locally in that repository and check that it prints
   the tool's JSON and that the rule appears in it. Prefer rules that catch bugs over style.
   The command is a TOML literal string (`'...'`), so its inner quotes need no escapes.

   | Tool | Candidate rules (not enforced by today's `ci.sh`; check each) | Command shape |
   |---|---|---|
   | ruff | `B006`, `B008`, `B017`, `B023`, `B904`, `RUF006`, `PLW0603`, `ASYNC230` | `uv run ruff check --select B006 --output-format json src tests` |
   | eslint | `@typescript-eslint/no-floating-promises`, `@typescript-eslint/no-misused-promises`, `@typescript-eslint/switch-exhaustiveness-check`, `require-atomic-updates`, `no-unsafe-finally` (the `@typescript-eslint` ones need typed linting in the repository's config) | `npx eslint -f json --rule '{"@typescript-eslint/no-floating-promises": "error"}' src` |
   | clippy | `clippy::unwrap_used`, `clippy::indexing_slicing`, `clippy::cast_possible_truncation`, `clippy::cast_sign_loss` (default clippy groups are already denied by `-D warnings`) | `cargo clippy --all-targets --all-features --message-format json -- -W clippy::cast_possible_truncation` |

   Write `enforce_in` and `enforce_how` as the sentence a coder needs, for example:
   ```toml
   [[ratchet]]
   tool = "ruff"
   rule = "B006"
   command = 'uv run ruff check --select B006 --output-format json src tests'
   enforce_in = "pyproject.toml"
   enforce_how = 'add "B006" to extend-select under [tool.ruff.lint]'
   ```
   For clippy, check whether the crate uses `[lints.clippy]` or `[workspace.lints.clippy]`
   before you write it.
8. Fire each repository by hand (step 5's command with its id). For every rule, read the
   result: `too_big` means narrow the command's paths or drop the rule; `error` means fix the
   command. Repeat until every rule you keep is `file` or `known` in the plan.

## Go live

9. Enable the four schedules: `ops/fabro-automation-schedule.sh scout-<repo> on`, once per
   repository (preview first with `DRY_RUN=1`).
10. Read the first scheduled day's four Discord lines. Then, one repository at a time, set
    `filing = true` in its `scout.toml`. Start with the one whose dry plan looks best. Record
    the date: the 14-day measurement starts from the first repository that goes live.
11. Append a dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`.

## The 14-day measurement (ADR 0027 acceptance)

12. Per repository: `gh issue list -R <repo> -l scout --state all --json number,state,stateReason,title,labels --limit 200`.
    For each issue, find its PR (`gh pr list -R <repo> --state all --search "<number> in:body"`)
    and record: filed date, lane (`fast-path` or not), PR state, merged by the workflow or by
    hand (the scheduler's history page, or the PR's merge actor), and the hold reason if held
    (the review-merge report comment on the PR).
13. Suppressions: for each merged scout PR, run
    `gh pr diff <n> -R <repo> | grep -nE '^[+].*(noqa|eslint-disable|allow[(]|per-file-ignores|ignorePatterns|extend-exclude)'`.
    One real hit is the trigger (D11) to build a deterministic counter.
14. Accept ADR 0027 when **at least half of the scout PRs that finished were merged without a
    human, and no suppression merged**. Otherwise, amend it: change the ratchet design if
    ratchet PRs failed, or the triage bypass if flaky PRs failed, whichever it was. Then update
    `docs/README.md`'s row and this folder's status, and close #32.
