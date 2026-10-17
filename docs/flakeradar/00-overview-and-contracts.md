# FlakeRadar in fabro: overview and contracts

**Status:** designed 2026-10-16. Tasks 01-04 done 2026-10-17: deployed (02022b8, fabro-io
0.7.0), lawncare-saas, womens-fantasy-sports and writers-app covered, and lawncare-saas live. ADR 0028 (proposed).
andrewthetechie/flakeradar#5 shipped 2026-10-17. #6 shipped and was deployed 2026-10-17, and
task 05 (`05-write-back.md`, F12-F15, C11) is planned, not built. Every
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
  Like `/api/tests` it hides the `stable` tier unless `include_stable=true` is passed.
- A job's tier is no evidence that its flakes are explained (2026-10-17): womens-fantasy-sports'
  `pre-commit` is `stable` in `/api/jobs` while GitHub's attempts show it failing and passing on
  rerun 9 times in 14 days, every one `unexplained` in the CI-run endpoint. F4 therefore asks
  the CI-run endpoint about each flake, never `/api/jobs`.
- An execution's `message` holds SGR colour codes whose `ESC` byte is already gone (`[2mexpect(`
  `[22m`), and is one line. `details` holds the same failure as a clean multi-line traceback; it
  is what an issue body shows.
- `/api/tests/{id}/history` also returns `test` (the full test row, `fingerprint` included) and
  `jobs` (`job_id`, `pipeline`, `name` of each job the test ran in). `executions` is newest
  first, mostly passes; a flaky test with score 0.7 had 8 failures in its last 47 executions.
  `limit` is at most 500.
- A test can stay `flaky` long after it stopped running: writers-app's "rewrite pane shows the
  loading state" was last seen 2026-09-26 and is still `flaky` with score 0.7.
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

### Issue links (flakeradar#6, verified 2026-10-17)

- `PUT /api/tests/{id}/issue` and `PUT /api/jobs/{id}/issue` set a link, and `DELETE` on the
  same path removes it. Both need `X-API-Key: <FLAKERADAR_API_TOKEN>`. Without a valid key the
  answer is `401 {"detail":"Invalid or missing X-API-Key"}`.
- The body is `{"number": N}`. `url` is optional, and a `url` that is not that issue in the
  entity's own repository is `422`. A `200` returns the test or job row with
  `github_issue_number` set. The same number again is `200` and changes nothing.
- A different issue already linked is `409` with
  `{"detail":"Test already links issue #M; pass replace=true to replace it"}` (`Job` for a
  job). `?replace=true` replaces it. An unknown id is `404`. A job whose pipeline is not
  GitHub Actions is `422`.
- `FLAKERADAR_API_TOKEN` is FlakeRadar's only token. It also uploads reports and sets
  quarantine. Its copies: FlakeRadar's `.env`, and the `flakeradar` MCP header in the
  operator's `~/.claude.json` on the Mac.
- FlakeRadar's closed-issue sync and its own filer have one switch, `FLAKERADAR_GITHUB_TOKEN`.
  It is unset (ADR 0028 D4 keeps filing off), so a link stays after its issue closes (F15).
- The CI-run endpoint's `failed_jobs[].job_id` is the id `/api/jobs/{id}/issue` takes
  (lawncare-saas run 38881352466: `job_id` 36, job `test` in `.github/workflows/frontend.yml`).
  The fixtures `ops/fabro-io/tests/fixtures/flakeradar/run_*.json` already carry it. A test
  candidate's id is C4b's `tests[].id`.
- lawncare-saas #2919, #2920 and #2921 were filed before the write-back existed. Their test
  ids are 1869302, 9554 and 9525 (each fingerprint matches its issue's marker), and none is
  linked.

## Decisions

| # | Decision |
|---|---|
| F1 | The FlakeRadar base URL is the server variable `FLAKERADAR_URL`, read through `{{ vars.FLAKERADAR_URL }}` in each `workflow.toml` that needs it. `ops/provision-server-state.sh` creates it **before** the push that references it, because an unset `{{ vars.X }}` fails the run at compile (invariants-merge). An empty value means "no FlakeRadar" (D3 of the ADR). |
| F2 | Scout test candidates: `tier == "flaky"`, `quarantined == false`, `confirmed_flake_count >= min_proven` (default 1; same-commit fail and pass, the test-level D5), `last_seen_at` within the `[flaky]` window (a test that stopped running may be gone), from a repository listed by `/api/repos`. |
| F3 | Test fingerprint: `flaky:test-` + `slug(project)` + `:` + `slug(fingerprint)`, which leaves FlakeRadar's 40 lowercase hex unchanged and guarantees C3's pattern. It never changes with the test's file or line. |
| F4 | Job candidates in a covered repository: each occurrence of a GitHub-attempts job (C4) is looked up in the CI-run endpoint at its failed attempt. It is **explained** when that attempt's `failed_jobs` lists the occurrence's job id (`ci_job_id == failed_job_id`) with `outcome == "explained"`: a failing test explains it, and that test is the issue. A 404, or `unexplained`, leaves it counted. The job is then judged on its unexplained occurrences only, exactly as C5 judges any job. A job that reached `min_flakes` on all its occurrences and falls below it on the unexplained ones gets the action `explained` (new kind, silent, counted in the summary). In an uncovered repository nothing changes. |
| F5 | Tests and jobs share the flaky source's caps (`PER_RUN_CAP`, `MAX_OPEN`). Order: tests by score descending, then proven count descending, then fingerprint; then jobs in C4's order. `ignore_jobs` still applies to jobs. |
| F6 | D8 suppression and regression apply to tests unchanged. A test regression is filed only when its newest `failed` or `error` execution (normalised) is after the closed issue's `closed_at`; otherwise the candidate produces nothing. |
| F7 | Fail toward filing less (ADR 0028 D3). Coverage is the operator's declaration, `[flakeradar] covered = true`. A covered repository whose FlakeRadar read fails (`FLAKERADAR_URL` empty, a non-2xx, a timeout, unparseable JSON, or the repository missing from `/api/repos`) gets `sources.flaky = "skipped: flakeradar <reason>"`, and **no** flaky action of either kind is planned, because the job source cannot tell explained from unexplained. An uncovered repository runs C4 exactly as today and never calls FlakeRadar. C8 part 5 adds `flaky skipped: <reason>`. |
| F8 | Merge phase: after `watch_checks`' one rerun is still red, each failing check is classified with flakeradar#5's endpoint (C10), waiting up to 3 minutes for its reports to be processed. A **known-flaky test** is `tier == "flaky"`, or `tier == "suspect"` with `confirmed_flake_count >= 1`. A failing check is **known flaky** when it is an Actions job, its attempt's lookup succeeded with `reports.pending == 0`, `reports.failed == 0` and `truncated == false`, its `ci_job_id` is not in `failed_jobs` as `unexplained`, it has at least one failure with `status` `failed` or `error`, and every such failure is a known-flaky test. Anything else (a non-Actions check, lint, a job without JUnit, a 404, a timeout) is not known flaky. A failing check named in the repository's `.fabro/scout.toml` `[flaky] ignore_jobs` is an aggregator (it fails because another check failed) and is judged by the others; alone it is not known flaky. Found live: womens-fantasy-sports' `alls-green-playwright` is `unexplained` in every Playwright red run. |
| F9 | All failing checks known flaky → one more rerun (`ci_flaky_rerun_done`, cleared where `ci_rerun_done` is), when ≥ 15 minutes of `merge_deadline` are left and the node has run < 30 minutes. These are not the plain rerun's guards (30 and 20): the node has usually waited over 20 minutes by now, and they exist to leave `ci_fix` its time, which this path never spends. Green → the merge continues. Still red, or no time → `blocked`, with a reason naming the tests. Still red and FlakeRadar cannot judge the new attempt (C10's `unknown`: not reported yet, still processing, FlakeRadar down) → `blocked` too: after a second rerun only a failure shown to be real reaches `ci_fix`. The reason names the tests (and each one's `github_issue_number` when FlakeRadar has it, which task 05's write-back provides). `ci_fix` does not run on a known-flaky red in either case, including a first visit whose plain rerun the guards forbade. |
| F10 | Otherwise `ci_fix` runs as today, and `ci_fix.md` gains `## Known flaky (FlakeRadar)`, listing those failures with "not caused by this PR: do not change, skip or retry these tests". `ci_fix.md.j2` tells the agent to fix the other failures, and to return `cannot_fix` when only those are left. |
| F11 | `merge-queue` (queue mode) is unchanged in v1. |
| F12 | Write-back (ADR 0028 D4, task 05): in a covered repository, the scout links each flaky issue it files to its FlakeRadar test or job, right after `gh issue create`. A Ratchet issue gets no link, and neither does a job that FlakeRadar never saw (no `job_id` in any of its lookups). The write-back is advisory (D3). A failed link is recorded and reported. It never stops the filing and never changes the plan. |
| F13 | The token is the vault secret `FLAKERADAR_API_TOKEN`, put into the scout run only, as `{{ secrets.FLAKERADAR_API_TOKEN }}`. The run environment covers every node, so `setup.sh` and the Ratchet commands can read it. Accepted 2026-10-17: they already run with an `issues: write` GitHub token. The vault entry exists before the push that references it, because a missing secret aborts the run before its sandbox exists. `backlog`, `pr-review` and `dependabot` do not get the token. |
| F14 | A `409` on a `regression` whose detail names the closed issue it regresses (the action's `issue`) is retried once with `?replace=true`. That link is the scout's own, and it went stale under F15. Any other `409` is a `conflict`: someone else linked another issue, the scout leaves it, and the summary names it. |
| F15 | Stale links are accepted. FlakeRadar's sync stays off with its filer, and the scout never removes a link. A closed scout issue stays linked until a regression replaces it (F14), so F9's hold reason can name a closed issue. |

## Contracts (amendments to `docs/scout/`)

### C1 amendment: `[flakeradar]` in `.fabro/scout.toml`

```toml
[flakeradar]            # optional; absent = not covered
covered = true          # this repository uploads JUnit to FlakeRadar (F7's "declared covered")
min_proven = 1          # 0..=20
```

`[flakeradar]` is closed like every C1 table. `covered` is a bool (default false), `min_proven`
an integer in `0..=20` (default 1).

### C4b: `fabro-io scout flakeradar --tests FILE --history-dir DIR --runs-dir DIR --flaky FILE --config CONFIG_JSON --since TS --base URL --repo O/N`

Pure, like C4. `--tests` is the slurped `items[]` of every `/api/tests` page. `--history-dir`
holds `test_<id>.json`, each a `/api/tests/{id}/history?limit=100` response, for the
candidates the graph fetched; a missing history file drops that candidate and is not an error,
since the graph fetches at most 10 (the 10 highest scores after F2). `--runs-dir` holds
`run_<run>_<attempt>.json`, each a 200 response of the CI-run endpoint, for the occurrences the
graph looked up; a missing file means not explained. `--flaky` is C4's output, `--config`
C2's (`min_proven`, `window_days`), `--since` the window start the flaky node computed, and
`--base` builds each test's dashboard link.

The subcommand applies F2 itself. Output:
`{"tests": [{fp, id, url, project, suite, classname, name, file, line, score, proven,
category, last_seen_at, last_failing_sha, last_failing_branch, failures: [{created_at,
message, details, branch, ci_run_id}]}], "explained": [<failed_job_id>, ...]}`. Failures are
the `failed` and `error` executions, newest first, at most 3. Colour codes, with or without
their `ESC`, are removed from both texts. `message` is one line of at most 300 characters.
`details` is the traceback, empty when there is none; past 50 lines it keeps the first and
last 25 (at most 2,000 characters each) around a `... (N lines cut) ...` line, because pytest
puts the error last and vitest and Playwright put it first. An execution's `attempt` is the
runner's retry within the job, not the CI run attempt, so it is not carried. `explained` is
the job ids F4 found explained, ascending. Timestamps are normalised. Tests are in F5's order.

The graph fetches (task 02), when `FLAKERADAR_URL` is non-empty and `config.flakeradar.covered`:
`/api/repos` once; `/api/tests?repo=R&sort=score&page_size=100&page=P` until a page is empty or
`total` is reached (at most 20 pages); `/api/tests/{id}/history?limit=100` for the 10
highest-scored F2 candidates (picked with `jq`); then the CI-run endpoint for each occurrence of
every job at or above `min_flakes` and not ignored, newest first, at most 60. Every read is
`curl -sS -m 20 -o file -w '%{http_code}' URL`, never a pipe. A 200 keeps the file, a 404 from
the CI-run endpoint is "not explained", and any other code, a curl failure or JSON that does not
parse is F7's skip, and so is running past one 5-minute budget for all the reads. The log tails
are then fetched for unexplained occurrences only.

### C5 amendment

`scout plan` takes `--flakeradar FILE` (optional). Absent, or the repository uncovered, the plan
is byte-for-byte what it is today. A covered repository whose fetch failed passes
`--flakeradar-skipped REASON` instead, which F7 turns into the skip. Candidates per F2-F6. A
test's `detail` is `score 0.70, 2 proven flakes` and its summary subject is its name, cut to
120 characters. New action kind `explained` (`issue`, `title`, `body` and `labels` null,
`detail` `"E of N flakes explained by FlakeRadar tests"`): the job reached `min_flakes` and its
unexplained flakes do not. Its known and suppressed checks run first, on all its flakes, as
before. C8's header adds `explained N` after `known N`, and C8 part 5 adds `flaky skipped:
<reason>`.

### C7b: flaky-test issue text

Title: `scout: flaky test "<name>" in <project> (score S, N proven flakes)`, or the C6/C7
regression prefix. Body: the marker; a first paragraph naming the suite, the classname when
it differs from the suite, `file:line` when known, the failure category, the score and proven
count, and the dashboard link (`url`); `## Recent failures (newest first)`, one `###` heading
per failure (date, branch, run) with its message in a code span and, for the newest only, its
traceback in a ````text```` fence with C7's backtick and mention neutralising; `## Last
failing commit` (8 characters and the branch); the acceptance criteria and non-goals of C7,
plus "Do not quarantine the test in FlakeRadar." Names in code spans keep their `@`: GitHub
mentions no one from a code span. Labels as C7.

### C10: `fabro-io flakeradar classify --checks FILE --failures-dir DIR [--ignore-jobs JSON] [--section FILE]` (task 04)

fabro-io is already in every profile image (`backlog`'s `validate` runs `fabro-io task-check`),
so the F8 rule is Rust with unit tests, not `jq` in `watch_checks`. Pure: no network.

- `--checks` is `watch_checks`' `/tmp/fabro/checks.json` (`gh pr checks --json
  name,state,bucket,link`). Failing means `bucket` neither `pass` nor `skipping`, as the node
  defines it today.
- `--failures-dir` holds `run_<run>.json` for each failing Actions run, the C10 fetch below's
  last good response. A missing file means the lookup failed (404, timeout, still pending).
- `--ignore-jobs` is the JSON array `fabro-io scout config` prints as `flaky.ignore_jobs` for
  the base branch's `.fabro/scout.toml` (`git show origin/<base>:.fabro/scout.toml`, never the
  PR head's, as `hygiene.json`): the aggregator checks F8 judges by the others.
- Output on stdout: `{"known_flaky": bool, "unknown": bool, "checks": [{name, run, job, known_flaky, reason,
  tests: [{name, project, tier, score, proven, category, issue, message}]}]}`. `known_flaky`
  at the top is true only when there is at least one failing check and every one of them is
  known flaky (F8). `reason` is one short phrase for a check that is not (for example
  `not an Actions job`, `no FlakeRadar report`, `reports pending`, `job failed outside its
  tests`, `test X is stable`). `issue` is `github_issue_number`, which is null until
  task 05's write-back fills it. `message` is C4b's: colour codes dropped, one line, cut to
  300 characters. `unknown` is true when it is not known flaky and no judged check is shown
  to be a real failure, every one lacking FlakeRadar data (`FlakeRadar has no report of the
  run`, still processing, failed reports, truncated); F9 holds such a PR after its second rerun.
- `--section FILE` also writes F10's markdown: `## Known flaky (FlakeRadar)`, one bullet per
  known-flaky failure (test, project, tier, proven count, issue when known), and the line
  "These failures are not caused by this PR: do not change, skip or retry these tests." It
  writes an empty file when no failure is known flaky.
- Exit 0 on any well-formed input, including no FlakeRadar data at all (every check then
  `known_flaky: false`, F7's fail-toward-today). Exit 1 only on an unreadable `--checks`.

The fetch, in `watch_checks` once the plain rerun is spent (or forbidden) and CI is red, when
`FLAKERADAR_URL` is non-empty: `/api/repos` once (a repository it does not list is not looked
up, so jelly-swipe never waits); for each failing Actions run, `gh api
repos/$REPO/actions/runs/<run> --jq .run_attempt > file` (exit code checked), then
`curl -fsS -m 20 "$FLAKERADAR_URL/api/ci-runs/<run>/failures?repo=$REPO&ci_run_attempt=<n>" > tmp`,
kept as `run_<run>.json` only when it parses and `reports.pending == 0`. Poll every 20
seconds for at most 3 minutes, all runs together; a 404 counts as pending during the wait.
The wait comes out of the node's 47-minute cap and never runs past it, and is skipped when
`FLAKERADAR_URL` is empty.
The `ci_flaky_rerun_done` file sits beside `ci_rerun_done` and `merge_gate` clears both. Each
of `backlog`, `pr-review` and `dependabot` gains `FLAKERADAR_URL = "{{ vars.FLAKERADAR_URL }}"`,
after task 02 has provisioned the variable.

### C11: write-back (task 05, F12-F15)

**C4b gains `jobs`.** The output gains `"jobs": [{"fp": <job fp>, "id": <FlakeRadar job id>}]`,
sorted by `fp`. For each job in `--flaky`, take its occurrences newest first. The first one
whose lookup (`run_<run>_<attempt>.json`) has a `failed_jobs` entry with
`ci_job_id == failed_job_id` and a numeric `job_id` gives the id. `outcome` does not matter,
because both outcomes name the same job. A job with no such occurrence is left out.
`flakeradar::FailedJob` gains `job_id: Option<u64>` (`#[serde(default)]`). C10's output does
not change. `FlakeradarOut.jobs` is `#[serde(default)]`, so `scout plan` still reads a 0.7.0
`out.json`.

**C5: the action gains `flakeradar`.** When the plan is `Radar::Read`, each `file` and
`regression` action of source `flaky` gains `"flakeradar": {"entity": E, "id": N}`. A test
candidate gets `{"entity": "tests", "id": <tests[].id>}`. A job candidate gets
`{"entity": "jobs", "id": <jobs[].id>}` for its `fp`, and no key when `jobs` does not list it.
`entity` is the API collection, so the URL is `<base>/api/<entity>/<id>/issue`. No other action
has the key, and it is serialized only when set
(`#[serde(skip_serializing_if = "Option::is_none")]`). An uncovered plan therefore stays byte
for byte the same, and `an_uncovered_repository_plans_exactly_what_it_did_before` passes
unchanged.

**The write log gains `link`.** A `filed` entry whose action has `flakeradar` gains `link` and
`link_detail` (both strings, `#[serde(default)]` on `text::Filed`):

| `link` | When | `link_detail` |
|---|---|---|
| `linked` | the `PUT` answers 200 | null |
| `replaced` | F14: a `409` names the action's `issue`, and the `?replace=true` retry answers 200 | null |
| `conflict` | a `409` names any other issue | that issue's number, as a string (`"55"`) |
| `failed` | anything else | the reason: `FLAKERADAR_URL is empty`, `FLAKERADAR_API_TOKEN is empty`, `HTTP 502`, `HTTP 409 with no issue number`, `replace returned HTTP 409`, `no response (<curl error>)` |

`raced` and `failed` entries, and entries for actions without `flakeradar`, have no `link` key.

**C8: one suffix.** Part 2's live `filed #N <source> <short>` gains ` (FlakeRadar already links
#M)` for `conflict` and ` (FlakeRadar link failed: <link_detail>)` for `failed`. `linked`,
`replaced` and no `link` add nothing. The header and the D17 empty rule do not change. The
suffix is only ever added to a part that already exists.

**C9: the `file` node.** After `record "$FP" filed "$N" ''`, when the action has `.flakeradar`:

1. `B=${FLAKERADAR_URL%/}`. An empty `FLAKERADAR_URL` or `FLAKERADAR_API_TOKEN` is `failed`
   with its reason, and no request is sent.
2. `jq -cn --argjson n "$N" '{number: $n}' > $S/link_body.json`. A `.fabro` script allows no
   backslash except `\"` (R6), so the body never goes inline in the shell.
3. `curl -sS -m 20 -X PUT -H "X-API-Key: $FLAKERADAR_API_TOKEN" -H 'Content-Type: application/json' --data @$S/link_body.json -o $S/link.out -w '%{http_code}' "$B/api/$ENT/$ID/issue" < /dev/null 2> $S/link.err || echo 000`.
   This is the `|| echo 000` idiom of the `flaky` node's `fr_get`.
4. `200` is `linked`. `409`: `M=$(jq -r '.detail // ""' $S/link.out 2>/dev/null | grep -o '#[0-9][0-9]*' | head -n 1 | tr -d '#')`.
   When the action's `kind` is `regression` and `M` equals its `issue`, send the same request
   to `.../issue?replace=true`: `200` is `replaced`, and any other code is `failed`
   (`replace returned HTTP C`). Otherwise a non-empty `M` is `conflict` and an empty `M` is
   `failed`. A code starting `000` is `failed` with `no response (...)`, which is the first
   120 characters of `link.err` with control characters removed. Any other code is `failed`
   with `HTTP C`.
5. Add the fields to the entry just recorded:
   `jq -c --arg l "$L" --arg d "$D" '.[-1] += {link: $l, link_detail: (if $d == "" then null else $d end)}'`.
6. Print one line to stderr, `file: FlakeRadar <entity>/<id> #N: <link>`, and go on to the next
   action. A link outcome never `break`s the loop. The token never reaches a log, the write
   log or stderr: no `set -x`, and no echo of a curl command line.

`scout/workflow.toml` `[run.environment.env]` gains
`FLAKERADAR_API_TOKEN = "{{ secrets.FLAKERADAR_API_TOKEN }}"` (F13). The node count and the edges
do not change: `Scout (9 nodes, 11 edges)`.

## Tasks

| Task | What | Files |
|---|---|---|
| 01 | `scout flakeradar` (C4b), plan amendment (F2-F6, `explained`), C7b text; fabro-io 0.7.0 | `ops/fabro-io/src/scout/{flakeradar,plan,text,config}.rs`, tests, fixtures from the live API |
| 02 | Scout graph: the `flaky` node fetches FlakeRadar into `$S/flakeradar/` when `FLAKERADAR_URL` is set and the repository is covered; `FLAKERADAR_URL` variable provisioned first | `scout/workflow.{fabro,toml}`, `ops/provision-server-state.sh`, `ops/test-task-gates.sh` |
| 03 | `[flakeradar]` in the three covered repositories' `scout.toml`; dry runs; then go live (scout task 07 step 10) | target repositories (operator) |
| 04 | Merge phase F8-F10 (C10): `fabro-io flakeradar classify`, then `watch_checks`' fetch, second rerun, hold and `ci_fix.md` section. After task 02 (the variable and fabro-io 0.7.0); flakeradar#5 shipped | `ops/fabro-io/src/flakeradar/`, `_shared/review-merge/review-merge.fabro`, `backlog`/`pr-review`/`dependabot` `workflow.toml`, `ops/test-task-gates.sh`, invariants-merge |
| 05 | Scout write-back (F12-F15, C11): `05-write-back.md`. fabro-io 0.8.0, the `file` node, the vault secret, the backfill of lawncare-saas #2919-#2921 | `ops/fabro-io/src/{flakeradar,scout/flakeradar,scout/plan,scout/text}.rs`, tests, `scout/workflow.{fabro,toml}`, `ops/test-task-gates.sh`, ADR 0028, `ops/README.md` |
| 06 | ADR 0028 accepted after the scout's 14-day measurement and a week of F9 holds read by hand | docs |
