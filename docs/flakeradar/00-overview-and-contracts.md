# FlakeRadar in fabro: overview and contracts

**Status:** designed 2026-10-16, not started. ADR 0028 (proposed). andrewthetechie/flakeradar#5
shipped 2026-10-17 (task 04 is unblocked; C10 below). Task 05 still waits for #6. Every
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
- `page_size` is at most 100 for `/api/tests` and `/api/jobs`. Execution `status` is `passed`,
  `failed`, `error` or `skipped`.
- FlakeRadar timestamps carry microseconds: `2026-10-16T20:58:11.683359Z`. Every scout contract
  compares GitHub's `YYYY-MM-DDTHH:MM:SSZ` as a string, so each FlakeRadar timestamp is
  normalised to its first 19 characters plus `Z` when it is read, and one that does not fit
  that shape is an invalid input (exit 1).
- The dashboard links one test as `<base>/?repo=O/N&project=P&test=<id>`
  (`frontend/src/urlState.ts`).
- The MCP is at `/mcp/` (trailing slash) with `Authorization: Bearer <FLAKERADAR_API_TOKEN>`.
  v1 does not use it (D5).

### One CI run's failures (flakeradar#5, verified 2026-10-17)

- `GET /api/ci-runs/{ci_run_id}/failures?repo=O/N&ci_run_attempt=N` needs no token. Without
  `ci_run_attempt` it answers for the latest attempt FlakeRadar has *seen*, which lags a rerun
  whose reports are still uploading, so the merge phase always passes the attempt.
- Response: `repo`, `ci_run_id` (a string), `ci_run_attempt`, `flake_threshold`,
  `reports {pending, processed, failed}`, `total_failures`, `truncated`, `failures[]` and
  `failed_jobs[]`.
- `failures[]` has one entry per test that failed or errored in that attempt: `test_id`,
  `project`, `suite`, `classname`, `name`, `run_id`, `ci_job_id` (a string), `status`,
  `failed_attempts`, `failure_category`, `message`, and the test's *current* `tier`,
  `flakiness_score`, `confirmed_flake_count`, `clean_streak`, `quarantined`,
  `github_issue_number` and `github_issue_url`. `status` is the test's last try in the run: a
  test that passed on a runner retry is `passed` with `failed_attempts > 0`, and did not turn
  the job red. `message` is the first 1 KB of the failure text, with raw ANSI escapes. At most
  500 entries; past that `truncated` is true.
- `failed_jobs[]`: `job_id`, `pipeline`, `name`, `ci_job_id`, `url`, `outcome`, `tier`,
  `flakiness_score`, `confirmed_flake_count`, `clean_streak`, `github_issue_*`. `outcome` is
  `explained` when a failing test in the same job explains the failure, `unexplained` for a
  failure in setup or infrastructure. A run can be red with no `failures` and one
  `unexplained` job.
- Ingest answers before it processes a report: until `reports.pending` is 0 an empty list
  proves nothing. `reports.failed > 0` means results are missing. An unknown repo, run or
  attempt is `404` (`{"detail": "CI run not found"}`), which is also what a rerun attempt
  returns before its first upload.
- `gh pr checks --json link` gives `https://github.com/O/N/actions/runs/<run>/job/<job>` for an
  Actions check, and the job is the latest attempt's. A rerun gives every rerun job a new id,
  so `<job>` matches `ci_job_id` only at that attempt; `gh api repos/O/N/actions/runs/<run>
  --jq .run_attempt` gives the attempt.
- Checked on two runs. lawncare-saas 38881352466 attempt 1: one `failed` frontend test, `flaky`,
  `confirmed_flake_count` 2, its `test` job `explained`. writers-app 38965640313 (the
  writers-app#1083 rerun): attempt 1 had one `flaky` tauri-pilot test (2 proven flakes) and
  the `UI end-to-end (tauri-pilot)` job `explained`; attempt 2 was green, with an empty
  `failures`. The F8 rule below would have given that PR its second rerun instead of `ci_fix`.

## Decisions

| # | Decision |
|---|---|
| F1 | The FlakeRadar base URL is the server variable `FLAKERADAR_URL`, read through `{{ vars.FLAKERADAR_URL }}` in each `workflow.toml` that needs it. `ops/provision-server-state.sh` creates it **before** the push that references it, because an unset `{{ vars.X }}` fails the run at compile (invariants-merge). An empty value means "no FlakeRadar" (D3 of the ADR). |
| F2 | Scout test candidates: `tier == "flaky"`, `quarantined == false`, `confirmed_flake_count >= min_proven` (default 1; same-commit fail and pass, the test-level D5), from a repository listed by `/api/repos`. |
| F3 | Test fingerprint: `flaky:test-` + `slug(project)` + `:` + the FlakeRadar `fingerprint`. It matches C3's pattern. It never changes with the test's file or line. |
| F4 | Job candidates in a covered repository: a GitHub-attempts job (C4) is filed only when FlakeRadar has no job with the same `pipeline` and `name`, or has one with `tier == "flaky"`. Otherwise the action is `explained` (new kind, silent, counted in the summary). In an uncovered repository nothing changes. |
| F5 | Tests and jobs share the flaky source's caps (`PER_RUN_CAP`, `MAX_OPEN`). Order: tests by score descending, then proven count descending, then fingerprint; then jobs in C4's order. `ignore_jobs` still applies to jobs. |
| F6 | D8 suppression and regression apply to tests unchanged. A test regression is filed only when its newest `failed` or `error` execution (normalised) is after the closed issue's `closed_at`; otherwise the candidate produces nothing. |
| F7 | Fail toward filing less (ADR 0028 D3). Coverage is the operator's declaration, `[flakeradar] covered = true`. A covered repository whose FlakeRadar read fails (`FLAKERADAR_URL` empty, a non-2xx, a timeout, unparseable JSON, or the repository missing from `/api/repos`) gets `sources.flaky = "skipped: flakeradar <reason>"`, and **no** flaky action of either kind is planned, because the job source cannot tell explained from unexplained. An uncovered repository runs C4 exactly as today and never calls FlakeRadar. C8 part 5 adds `flaky skipped: <reason>`. |
| F8 | Merge phase: after `watch_checks`' one rerun is still red, each failing check is classified with flakeradar#5's endpoint (C10), waiting up to 3 minutes for its reports to be processed. A **known-flaky test** is `tier == "flaky"`, or `tier == "suspect"` with `confirmed_flake_count >= 1`. A failing check is **known flaky** when it is an Actions job, its attempt's lookup succeeded with `reports.pending == 0`, `reports.failed == 0` and `truncated == false`, its `ci_job_id` is not in `failed_jobs` as `unexplained`, it has at least one failure with `status` `failed` or `error`, and every such failure is a known-flaky test. Anything else (a non-Actions check, lint, a job without JUnit, a 404, a timeout) is not known flaky. |
| F9 | All failing checks known flaky → one more rerun (`ci_flaky_rerun_done`, cleared where `ci_rerun_done` is), under the same guards (≥ 30 minutes of `merge_deadline` left, the node has run < 20 minutes). Green → the merge continues. Still red, or the guards forbid the rerun → `blocked`, with a reason naming the tests (and each one's `github_issue_number` when FlakeRadar has it, which task 05's write-back provides). `ci_fix` does not run on a known-flaky red in either case. |
| F10 | Otherwise `ci_fix` runs as today, and `ci_fix.md` gains `## Known flaky (FlakeRadar)`, listing those failures with "not caused by this PR: do not change, skip or retry these tests". |
| F11 | `merge-queue` (queue mode) is unchanged in v1. |

## Contracts (amendments to `docs/scout/`)

### C1 amendment: `[flakeradar]` in `.fabro/scout.toml`

```toml
[flakeradar]            # optional; absent = not covered
covered = true          # this repository uploads JUnit to FlakeRadar (F7's "declared covered")
min_proven = 1          # 0..=20
```

`[flakeradar]` is closed like every C1 table. `covered` is a bool (default false), `min_proven`
an integer in `0..=20` (default 1).

### C4b: `fabro-io scout flakeradar --tests FILE --history-dir DIR --jobs FILE --repo O/N --config CONFIG_JSON --base URL`

Pure, like C4. `--tests` is the slurped `items[]` of every `/api/tests` page, `--history-dir`
holds `test_<id>.json` for each candidate the graph fetched, and `--jobs` is the slurped
`/api/jobs` `items[]`. The subcommand applies F2 itself (tier, quarantine, `min_proven` from
`--config`) and keeps a candidate only when its history file exists; a missing history file
is not an error, since the graph fetches at most 10 (the 10 highest scores after F2). `--base`
builds each test's dashboard link.
Output: `{"tests": [{fp, id, url, project, suite, classname, name, file, line, score, proven,
category, last_seen_at, last_failing_sha, last_failing_branch, failures: [{created_at,
message, branch, ci_run_id, attempt}]}], "jobs": [{pipeline, name, tier, score}]}`. Failures
are the `failed` and `error` executions, newest first, at most 10, each `message` collapsed
and cut to 500 characters. Timestamps are normalised. Tests are in F5's order.

The graph fetches (task 02): `/api/repos` once; `/api/tests?repo=R&sort=score&page_size=100&page=P`
until a page is empty or `total` is reached (at most 20 pages); `/api/jobs` the same way; then
`/api/tests/{id}/history?limit=20` for the 10 highest-scored F2 candidates (the graph applies
F2 with `jq` to pick them). Every read is `curl -fsS -m 20 URL > file || <F7 skip>`, never a
pipe, as C9 requires for `gh`.

### C5 amendment

`scout plan` takes `--flakeradar FILE` (optional). Absent, or the repository uncovered, the plan
is byte-for-byte what it is today. A covered repository whose fetch failed passes
`--flakeradar-skipped REASON` instead, which F7 turns into the skip. Candidates per F2-F6. New action kind
`explained` (`issue` null, `detail` `"explained by FlakeRadar tests"`). C8's header adds
`explained N`.

### C7b: flaky-test issue text

Title: `scout: flaky test "<name>" in <project> (score S, N proven flakes)`. Body: the marker; a
first paragraph naming the suite, classname, `file:line` when known, the failure category, the
score and proven count, and the dashboard link (`url`); `##
Recent failures` (date, branch, message in a ````text```` fence cleaned as C7's log tails);
`## Last failing commit`; the acceptance criteria and non-goals of C7, plus "Do not quarantine
the test in FlakeRadar." Labels as C7.

### C10: `fabro-io flakeradar classify --checks FILE --failures-dir DIR` (task 04)

fabro-io is already in every profile image (`backlog`'s `validate` runs `fabro-io task-check`),
so the F8 rule is Rust with unit tests, not `jq` in `watch_checks`. Pure: no network.

- `--checks` is `watch_checks`' `/tmp/fabro/checks.json` (`gh pr checks --json
  name,state,bucket,link`). Failing means `bucket` neither `pass` nor `skipping`, as the node
  defines it today.
- `--failures-dir` holds `run_<run>.json` for each failing Actions run, the C10 fetch below's
  last good response. A missing file means the lookup failed (404, timeout, still pending).
- Output on stdout: `{"known_flaky": bool, "checks": [{name, run, job, known_flaky, reason,
  tests: [{name, project, tier, score, proven, category, issue, message}]}]}`. `known_flaky`
  at the top is true only when there is at least one failing check and every one of them is
  known flaky (F8). `reason` is one short phrase for a check that is not (for example
  `not an Actions job`, `no FlakeRadar report`, `reports pending`, `job failed outside its
  tests`, `test X is stable`). `issue` is `github_issue_number`, which is null until
  task 05's write-back fills it. `message` is cleaned as C7's log tails (ANSI and control
  bytes dropped) and cut to 500 characters.
- `--section FILE` also writes F10's markdown: `## Known flaky (FlakeRadar)`, one bullet per
  known-flaky failure (test, project, tier, proven count, issue when known), and the line
  "These failures are not caused by this PR: do not change, skip or retry these tests." It
  writes an empty file when no failure is known flaky.
- Exit 0 on any well-formed input, including no FlakeRadar data at all (every check then
  `known_flaky: false`, F7's fail-toward-today). Exit 1 only on an unreadable `--checks`.

The fetch, in `watch_checks` after the first rerun settles red, when `FLAKERADAR_URL` is
non-empty: for each failing Actions run, `gh api repos/$REPO/actions/runs/<run> --jq
.run_attempt > file` (exit code checked), then
`curl -fsS -m 20 "$FLAKERADAR_URL/api/ci-runs/<run>/failures?repo=$REPO&ci_run_attempt=<n>" > tmp`,
kept as `run_<run>.json` only when it parses and `reports.pending == 0`. Poll every 20
seconds for at most 3 minutes, all runs together; a 404 counts as pending during the wait.
The wait comes out of the node's 47-minute cap, and is skipped when `FLAKERADAR_URL` is empty.
The `ci_flaky_rerun_done` file sits beside `ci_rerun_done` and `merge_gate` clears both. Each
of `backlog`, `pr-review` and `dependabot` gains `FLAKERADAR_URL = "{{ vars.FLAKERADAR_URL }}"`,
after task 02 has provisioned the variable.

## Tasks

| Task | What | Files |
|---|---|---|
| 01 | `scout flakeradar` (C4b), plan amendment (F2-F6, `explained`), C7b text; fabro-io 0.7.0 | `ops/fabro-io/src/scout/{flakeradar,plan,text,config}.rs`, tests, fixtures from the live API |
| 02 | Scout graph: the `flaky` node fetches FlakeRadar into `$S/flakeradar/` when `FLAKERADAR_URL` is set and the repository is covered; `FLAKERADAR_URL` variable provisioned first | `scout/workflow.{fabro,toml}`, `ops/provision-server-state.sh`, `ops/test-task-gates.sh` |
| 03 | `[flakeradar]` in the three covered repositories' `scout.toml`; dry runs; then go live (scout task 07 step 10) | target repositories (operator) |
| 04 | Merge phase F8-F10 (C10): `fabro-io flakeradar classify`, then `watch_checks`' fetch, second rerun, hold and `ci_fix.md` section. After task 02 (the variable and fabro-io 0.7.0); flakeradar#5 shipped | `ops/fabro-io/src/flakeradar/`, `_shared/review-merge/review-merge.fabro`, `backlog`/`pr-review`/`dependabot` `workflow.toml`, `ops/test-task-gates.sh`, invariants-merge |
| 05 | Scout write-back of each filed issue's link, after flakeradar#6 ships | scout `file` node |
| 06 | ADR 0028 accepted after the scout's 14-day measurement and a week of F9 holds read by hand | docs |
