# FlakeRadar in fabro: overview and contracts

**Status:** designed 2026-10-16, not started. ADR 0028 (proposed). Depends on
andrewthetechie/flakeradar#5 (tasks 04-05) and #6 (task 03's write-back, optional). Every
scout repository stays dry (`filing = false`) until tasks 01-03 are live, so the scout never
files a job-level fingerprint that a per-test one then supersedes.

**Read this file first**, then ADR 0028, ADR 0027 and `docs/scout/00-overview-and-contracts.md`
(C1-C9 below are that file's contracts). Before you edit a `.fabro` file, invoke
`/fabro-workflow` and read `docs/agents/invariants-graph.md`. Before task 04, also read
`docs/agents/invariants-merge.md`. Before you write Rust, invoke `/ms-rust`.

## Facts (verified 2026-10-16)

- FlakeRadar is at `https://flakes.herrington.services`; source in
  `~/Documents/code/flakeradar` (its README has the API, MCP and issue-automation sections).
  The fabro sandbox image reaches it (`docker run fabro-python:local curl .../api/health` → 200).
- Its `GET` endpoints need no token. Tests: `GET /api/tests?repo=O/N&sort=score&page_size=100&page=P`
  returns `{items, total, page, page_size}`, and by default only the `flaky` and `suspect` tiers.
  `sort` is `score`, `last_seen` or `proven`. An item has `id`, `project`, `fingerprint` (40 hex,
  FlakeRadar's stable test identity), `suite`, `classname`, `name`, `file`, `line`,
  `flakiness_score`, `tier`, `confirmed_flake_count`, `failure_category`, `trend`,
  `last_seen_at`, `quarantined` and `github_issue_number`.
- `GET /api/tests/{id}/history?limit=N` adds `location`, `last_failing_sha`,
  `last_failing_branch` and `executions[]` (`status`, `message`, `created_at`, `commit_sha`,
  `branch`, `ci_run_id`, `attempt`, `failure_category`).
- Jobs: `GET /api/jobs?repo=O/N` lists `pipeline` (the workflow path), `name` (the job name),
  `flakiness_score` and `tier`. A job's score counts only the failures that no test explains.
- `GET /api/repos` lists the covered repositories: lawncare-saas, womens-fantasy-sports and
  writers-app. jelly-swipe is not covered.
- Flaky-tier tests on 2026-10-16: lawncare-saas 2, womens-fantasy-sports 1, writers-app 4.
  Flaky-tier jobs: womens-fantasy-sports `test-playwright` shards and `alls-green-playwright`,
  writers-app `UI end-to-end (tauri-pilot)`. womens-fantasy-sports' `pre-commit` job flakes
  (9 in 14 days by GitHub attempts) and has no JUnit report, so FlakeRadar does not know it.
- A flake in lawncare-saas#2905 (2026-10-16, `LockNotAvailableError` in two
  `tests/test_migration.py` tests) was `suspect` in FlakeRadar with scores below 0.02. A gate
  keyed on `tier == "flaky"` alone would not have recognised it (task 04).
- FlakeRadar can file GitHub issues itself. No `flakeradar`-labelled issue exists in any target
  repository, so that feature is off. Keep it off (ADR 0028 D4).
- The MCP is at `/mcp/` (trailing slash) with `Authorization: Bearer <FLAKERADAR_API_TOKEN>`.
  v1 does not use it (D5).

## Decisions

| # | Decision |
|---|---|
| F1 | The FlakeRadar base URL is the server variable `FLAKERADAR_URL`, read through `{{ vars.FLAKERADAR_URL }}` in each `workflow.toml` that needs it. `ops/provision-server-state.sh` creates it **before** the push that references it, because an unset `{{ vars.X }}` fails the run at compile (invariants-merge). An empty value means "no FlakeRadar" (D3 of the ADR). |
| F2 | Scout test candidates: `tier == "flaky"`, `quarantined == false`, `confirmed_flake_count >= min_proven` (default 1; same-commit fail and pass, the test-level D5), from a repository listed by `/api/repos`. |
| F3 | Test fingerprint: `flaky:test-` + `slug(project)` + `:` + the FlakeRadar `fingerprint`. It matches C3's pattern. It never changes with the test's file or line. |
| F4 | Job candidates in a covered repository: a GitHub-attempts job (C4) is filed only when FlakeRadar has no job with the same `pipeline` and `name`, or has one with `tier == "flaky"`. Otherwise the action is `explained` (new kind, silent, counted in the summary). In an uncovered repository nothing changes. |
| F5 | Tests and jobs share the flaky source's caps (`PER_RUN_CAP`, `MAX_OPEN`). Order: tests by score descending, then proven count descending, then fingerprint; then jobs in C4's order. `ignore_jobs` still applies to jobs. |
| F6 | D8 suppression and regression apply to tests unchanged. A test regression counts only when FlakeRadar's `last_seen_at` is after the closed issue's `closed_at`. |
| F7 | Fail toward filing less (ADR 0028 D3): a failed FlakeRadar read marks the test source `skipped: flakeradar unreachable`, and in a repository `scout.toml` declares covered, the job source then files nothing either (it cannot tell explained from unexplained). |
| F8 | Merge phase: after `watch_checks`' one rerun is still red, every failing check that is an Actions run is looked up with flakeradar#5's endpoint, waiting up to 3 minutes for its reports to be processed. **Known flaky** means each failing check has at least one FlakeRadar failure, and every failure's test is `flaky`, or `suspect` with `confirmed_flake_count >= 1`. A failing check with no report (lint, a job without JUnit) is not known flaky. |
| F9 | All failing checks known flaky → one more rerun (`ci_flaky_rerun_done`, reset where `ci_rerun_done` is), under the same budget guards. Green → the merge continues. Still red → `blocked`, with a reason naming the tests (and their scout issue numbers when the marker is found). `ci_fix` does not run. |
| F10 | Otherwise `ci_fix` runs as today, and `ci_fix.md` gains `## Known flaky (FlakeRadar)`, listing those failures with "not caused by this PR: do not change, skip or retry these tests". |
| F11 | `merge-queue` (queue mode) is unchanged in v1. |

## Contracts (amendments to `docs/scout/`)

### C1 amendment: `[flakeradar]` in `.fabro/scout.toml`

```toml
[flakeradar]            # optional; absent = not covered
covered = true          # this repository uploads JUnit to FlakeRadar (F7's "declared covered")
min_proven = 1          # 0..=20
```

### C4b: `fabro-io scout flakeradar --tests FILE --history-dir DIR --jobs FILE --repo O/N`

Pure, like C4. `--tests` is the slurped `items[]` of every `/api/tests` page, `--history-dir`
holds `test_<id>.json` for each candidate, and `--jobs` is the slurped `/api/jobs` `items[]`.
Output: `{"tests": [{fp, project, suite, classname, name, file, line, score, proven,
category, last_seen_at, last_failing_sha, last_failing_branch, failures: [{created_at,
message, branch, ci_run_id, attempt}]}], "jobs": [{pipeline, name, tier, score}]}`. Failures
newest first, at most 10, each `message` collapsed and cut to 500 characters.

### C5 amendment

`scout plan` takes `--flakeradar FILE` (optional). Candidates per F2-F6. New action kind
`explained` (`issue` null, `detail` `"explained by FlakeRadar tests"`). C8's header adds
`explained N`.

### C7b: flaky-test issue text

Title: `scout: flaky test "<name>" in <project> (score S, N proven flakes)`. Body: the marker; a
first paragraph naming the suite, classname, `file:line` when known, the failure category and
FlakeRadar's link (`<FLAKERADAR_URL>/tests/<id>` if the UI has one; task 01 checks); `##
Recent failures` (date, branch, message in a ````text```` fence cleaned as C7's log tails);
`## Last failing commit`; the acceptance criteria and non-goals of C7, plus "Do not quarantine
the test in FlakeRadar." Labels as C7.

## Tasks

| Task | What | Files |
|---|---|---|
| 01 | `scout flakeradar` (C4b), plan amendment (F2-F6, `explained`), C7b text; fabro-io 0.7.0 | `ops/fabro-io/src/scout/{flakeradar,plan,text,config}.rs`, tests, fixtures from the live API |
| 02 | Scout graph: the `flaky` node fetches FlakeRadar into `$S/flakeradar/` when `FLAKERADAR_URL` is set and the repository is covered; `FLAKERADAR_URL` variable provisioned first | `scout/workflow.{fabro,toml}`, `ops/provision-server-state.sh`, `ops/test-task-gates.sh` |
| 03 | `[flakeradar]` in the three covered repositories' `scout.toml`; dry runs; then go live (scout task 07 step 10) | target repositories (operator) |
| 04 | Merge phase F8-F10 in `watch_checks` and `ci_fix.md`, after flakeradar#5 ships | `_shared/review-merge/review-merge.fabro`, `backlog`/`pr-review`/`dependabot` `workflow.toml`, `ops/test-task-gates.sh`, invariants-merge |
| 05 | Scout write-back of each filed issue's link, after flakeradar#6 ships | scout `file` node |
| 06 | ADR 0028 accepted after the scout's 14-day measurement and a week of F9 holds read by hand | docs |
