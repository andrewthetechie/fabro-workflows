# Scout: overview and canonical contracts

**Status:** Tasks 01-06 done 2026-10-16; review fixes in 751e662. Task 07 steps 1-9 done
2026-10-16: deployed, `Scout (9 nodes, 11 edges)` verified, `scout.toml` committed dry in all
four repositories (jelly-swipe#460, lawncare-saas#2905, womens-fantasy-sports#2139,
writers-app#1083), a dry run of each read, schedules on. **Every repository stays dry** (step 10 waits)
until the scout's flaky source reads FlakeRadar per test (operator decision 2026-10-16): going
live first would file job-level flaky fingerprints that the per-test ones then supersede.
ADR 0027 (proposed). Roadmap B6, reduced.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. `CONTEXT.md` defines **Scout**, **Ratchet rule** and **Flaky job**. Read
ADR 0027 before you start. **Before you edit a `.fabro` file or a `workflow.toml`, invoke
the `/fabro-workflow` skill** and read `docs/agents/invariants-graph.md`. **Before you
write Rust, invoke the `/ms-rust` skill.**

## Hard rule for this series: do not push

The operator reviews before anything reaches `main` (pushing to `main` deploys; see
`AGENTS.md`). Commit each task locally on `main`, one commit per task, Conventional Commit
subjects (`feat(fabro-io): ...`, `feat(scout): ...`, `feat(ops): ...`, `docs(scout): ...`).
After each task run `make check` at the repository root, with `CHECK_CARGO=always` for any
task that touches `ops/fabro-io/`. It must pass. **Stop after task 06** and report. Do not run
`git push`, `make deploy*`, `make compose-up`, `make check-host`, `ssh`, or any `gh`
command that writes. Do not edit any target repository. Task 07 is the operator's.

## What this is

The **Scout** is a seventh workflow package, `.fabro/workflows/scout/`. It runs once a day in
each target repository and has **no agent stage** (like `merge-queue`). It does four things:

1. It reads the repository's `.fabro/scout.toml` (C1, C2).
2. **Ratchet source.** It runs `./.fabro/setup.sh`, then each declared **Ratchet rule**'s
   command, and counts the rule's findings (C3).
3. **Flaky source.** It reads the last 14 days of GitHub Actions runs and finds every
   **Flaky job**: a job that failed and then passed on a rerun of the same run (C4).
4. It plans issues against every `scout` issue already in the repository, open and closed
   (C5), and files them straight to the queue with the label `agent` (C6, C7). It posts one
   Discord line when there is something to say (C8).

A repository is **dry** until its `scout.toml` says `filing = true`. A dry run reads
everything, writes nothing, and posts the plan as `DRY RUN`.

| Piece | Path | Task |
|---|---|---|
| Config, ratchet runner, tool parsers | `ops/fabro-io/src/scout/{mod,config,ratchet}.rs` | 01 |
| Flaky detector | `ops/fabro-io/src/scout/flaky.rs` | 02 |
| Plan, issue text, summary line | `ops/fabro-io/src/scout/{plan,text}.rs` | 03 |
| The package | `.fabro/workflows/scout/{workflow.fabro,workflow.toml}` | 04 |
| `config_changed` gains `scout.toml` | `.fabro/workflows/_shared/review-merge/review-merge.fabro` | 04 |
| Notify mode, provisioning, host check, runbook | `discord-notify.sh`, `ops/provision-server-state.sh`, `ops/check-host.sh`, `ops/README.md` | 05 |
| Docs | ADR 0023 amendment, `AGENTS.md`, `docs/README.md`, `CONTEXT.md` (`Automation`), this file | 06 |
| **Operator only** | push, deploy, `scout.toml` in each repository, schedules, dry runs, go live, measure | 07 |

## Decisions (settled; do not re-open)

| # | Decision |
|---|---|
| D1 | Two sources only: **Ratchet rules** and **Flaky jobs**. Red `main` belongs to #24. Mutation survivors, production errors and `tsc` strictness flags are deferred (B6 stays the record). |
| D2 | A Ratchet rule is a lint rule CI does not enforce yet. One issue covers **one rule in the whole repository**. Its PR fixes every finding **and** enforces the rule in the repository's lint config, so `ci.sh` runs it from then on and the Refuter can credit it (refuter-evidence D7). |
| D3 | A Ratchet rule is filed only while it has **at most 15 findings in at most 8 files** (`MAX_FINDINGS`, `MAX_FILES`). A bigger rule is reported as `too_big` with its counts and never filed. The operator narrows it in `scout.toml`. |
| D4 | v1 tools: `ruff` (`--output-format json`), `eslint` (`-f json`), `clippy` (`cargo clippy --message-format json`). |
| D5 | A Flaky job is counted per workflow file and job name, from GitHub's run attempts (`/actions/runs/{id}/jobs?filter=all`). An attempt counts only when the job's conclusion is `failure` and at least one step other than `Set up job` failed, and a **later** attempt of the same run has the same job `success`. Every branch counts. A job is filed when it has **3 occurrences in 14 days** (`[flaky]` in `scout.toml` can override both). |
| D6 | GitHub is the only store. Each issue carries the label `scout` and the hidden marker `<!-- fabro:scout fp=FINGERPRINT -->`. Each run lists every `scout` issue, open and closed. No baseline, no database. Existing findings are filed over time, at the cap rate. |
| D7 | A fingerprint never contains a line number, a column, a run id or a SHA. The same finding at a new line is the same fingerprint (#32's offline fixture). |
| D8 | Known fingerprints: open issue → `known` (silent skip). Closed as `not_planned` or `duplicate` → `suppressed`, forever. Closed as anything else → a `regression` issue that links it. For a flaky regression, only occurrences **after** the closed issue's `closed_at` count toward the threshold. |
| D9 | Volume: at most 3 new issues (`file` + `regression`) per source per run (`PER_RUN_CAP`), and nothing new from a source while 3 or more of its issues are open in the repository (`MAX_OPEN`). |
| D10 | Scout issues skip triage. Labels: Ratchet rule `agent`, `scout`, `ai-generated`, `bug`, `fast-path`. Flaky job `agent`, `scout`, `ai-generated`, `flaky-test`. The template writes the acceptance criteria and non-goals that triage would otherwise add. `fast-path` amends ADR 0023 D1 (task 06). |
| D11 | Suppressions are non-goals in the issue text (`noqa`, `eslint-disable`, `#[allow(...)]`, and per-file or path ignores). No new hygiene counter. Task 07 measures them. The existing `lint_disable` erosion counter (`review-merge.fabro`, `LINT`) already counts the inline ones in report mode. |
| D12 | Scout only creates issues. It never comments, closes, labels, or reopens an issue it did not just create. |
| D13 | One Fabro workflow, command nodes only, one automation per repository in that repository's environment. The logic is a pure `fabro-io scout` subcommand (Rust, unit-tested with fixtures). The shell in the graph only fetches from GitHub, runs `setup.sh`, and makes the `gh` writes. |
| D14 | `.fabro/scout.toml` lives in each target repository, and a PR that touches it trips `config_changed` (no auto-merge). A repository with no `scout.toml` runs only the flaky source, dry. |
| D15 | Fail toward filing less. `setup.sh` fails → the ratchet source is skipped and reported, flaky still runs. A rule whose output does not parse → that rule is `error`. An invalid `scout.toml` → the run is dry, the ratchet source is skipped, flaky runs with defaults. Any GitHub read failure → nothing is filed. A write failure stops the remaining writes. |
| D16 | Daily, 02:00 UTC plus 15 minutes per repository, before `arch-review` and `dependabot`. Schedules are created **disabled** (`provision_automation`'s default). |
| D17 | One Discord line per run (`scout_summary`, the `merge-queue-summary` pattern), empty on a quiet run. Not empty when: a run files or would file, a write fails, a source is skipped or errors, or (dry runs only) a rule is `too_big`. |
| D18 | Order of filing: ratchet before flaky. Ratchet rules in `scout.toml` declaration order (the operator's priority). Flaky jobs by occurrence count descending, then fingerprint ascending. |

## Facts (verified 2026-10-16)

- All four `.fabro/ci.sh` already fail on lint and type findings (lawncare `uv run ruff check
  src tests`, jelly-swipe and writers-app `npm run lint` and `typecheck`, writers-app
  `cargo clippy --all-targets --all-features -- -D warnings`, womens-fantasy-sports through
  `prek`). That is why a Ratchet rule is one CI does not enforce yet.
- The Refuter never runs a command. A command criterion is `met` only when `ci.sh` runs that
  tool over every changed file (`docs/refuter-evidence/`, D7, D8). D2 relies on this.
- `config_changed` is computed at `review-merge.fabro` line 489:
  `CC=$(git diff --name-only "origin/$B...HEAD" 2>/dev/null | grep -cxE '[.]fabro/(hygiene[.]json|test[.]toml|ci[.]sh)' || true)`.
  Its fixtures are `ops/test-task-gates.sh` cases 13 and 13a. The report sentence is at line 167.
- `GET /repos/{r}/actions/runs/{id}/jobs?filter=all&per_page=100` returns the jobs of
  **every** attempt, each with `run_attempt`, `id`, `name`, `conclusion`, `html_url` and
  `steps[]` (`name`, `number`, `conclusion`). Live example, writers-app run 38194309790: job
  `UI end-to-end (tauri-pilot)` failed at step `Run scenario suite` in attempt 1, failed again
  at step `Probe — ping the app before running scenarios` in attempt 2, and failed in attempt 3.
  That run is **not** a flake, because no later attempt passed. Run 38855093415: the same job
  failed in attempt 1 and passed in attempt 2, which **is** one.
- Over the 14 days to 2026-10-16, runs with `run_attempt > 1`: womens-fantasy-sports 32 (of
  6,200 runs), writers-app 8 (of 200). Only these runs need a jobs call. **Corrected
  2026-10-16:** the first count (womens-fantasy-sports 6, lawncare-saas 2) came from a
  `created=` query, which GitHub caps at 1,000 runs; womens-fantasy-sports ran 1,033 on
  2026-10-14 alone. The `flaky` node pages the unfiltered list instead (C9).
- `GET /repos/{r}/actions/jobs/{job_id}/logs` returns the plain-text log of one job attempt
  (`gh api` follows the redirect). Each line starts with a timestamp like
  `2026-10-14T12:00:00.1234567Z `.
- `merge-queue` is the template for a package with no agent: no model, no MCP server, no
  `FABRO_IO_MANIFEST` block, and it is absent from `ops/fabro-io-manifest.py` `WF_ORDER`. Its
  `summarize` node publishes one line in `context_updates`, and the `finish` node is the hook's
  target. `discord-notify.sh` reads the line from the run state with
  `grep -o '"mq_summary":"[^"]*"'`, so the line must hold no `"` and no backslash, and it
  separates parts with ` | `.
- `fabro-io` is `ops/fabro-io/`, version `0.5.1`, edition 2024, and has no `regex` crate.
  Subcommands are dispatched in `src/cli.rs` (`"task-plan" => taskplan::run_cli(rest)`).
  Integration tests are `ops/fabro-io/tests/*_tests.rs` with fixtures in `tests/fixtures/`.
  `nix` (feature `signal`) is a dependency.
- The scheduler dispatches any open `agent` issue regardless of author. `backlog`'s fast lane
  is chosen by the `fast-path` label (`backlog/workflow.fabro` around line 356), and the issue
  body becomes the single task's body.
- Target repositories have no `.fabro/hygiene.json`, so erosion runs in `report` mode.
- jelly-swipe is public, and the other three are private. Scout writes only into the
  repository it scanned, so it needs no repository-name guard like the retro's.

## Contracts

All of `fabro-io scout` is pure, except `ratchet`, which runs the declared commands. No
subcommand calls GitHub or reads the clock. Every timestamp is GitHub's UTC form
`YYYY-MM-DDTHH:MM:SSZ` and is compared **as a string**. Input in any other form is an
invalid input. JSON output is one object on stdout, pretty-printing optional. Logs go to
stderr. Exit codes: `0` success; `1` an input file is missing or is not valid JSON of the
expected shape; `2` bad arguments.

### C1. `.fabro/scout.toml` (in the target repository)

```toml
version = 1
filing = false            # true: this repository files issues. Anything else: dry.

[[ratchet]]
tool = "ruff"             # ruff | eslint | clippy
rule = "B006"             # exactly as the tool reports it: "B006",
                          # "@typescript-eslint/no-floating-promises", "clippy::needless_collect"
command = "uv run ruff check --select B006 --output-format json src tests"
enforce_in = "pyproject.toml"
enforce_how = "add \"B006\" to `extend-select` under `[tool.ruff.lint]`"
timeout_secs = 900        # optional, default 1200, at most 3000

[flaky]                   # optional table, and every key optional
min_flakes = 3            # 1..=20
window_days = 14          # 1..=30
ignore_jobs = ["alls-green-playwright"]   # job names never filed, matched exactly; at most 20
```

`ignore_jobs` is for an aggregator job (`re-actors/alls-green` and the like) that runs with
`if: always()` and fails only because a job it `needs` failed. Its flake is that job's, which is
counted on its own. Without the list, an aggregator outranks the job it waits on and spends the
per-run cap (found on womens-fantasy-sports' first dry plan, 2026-10-16).

Every table and key is closed: an unknown key is invalid. `rule`, `command`, `enforce_in` and
`enforce_how` are non-empty strings. Two entries with the same `(tool, rule)` are invalid. At
most 10 `[[ratchet]]` entries. The command must print the tool's JSON on **stdout**. Its exit
code is ignored, because ruff and eslint exit 1 when they find something.

### C2. `fabro-io scout config [--file PATH]`

Default `--file` is `.fabro/scout.toml`. Always exits 0 (except 2 for bad arguments) and prints:

```json
{"present": true, "valid": true, "error": null, "filing": false,
 "ratchet": [{"index": 0, "tool": "ruff", "rule": "B006", "command": "...",
              "enforce_in": "pyproject.toml", "enforce_how": "...", "timeout_secs": 900}],
 "flaky": {"min_flakes": 3, "window_days": 14, "ignore_jobs": []}}
```

- Missing file: `present: false, valid: true, filing: false, ratchet: []`, default `flaky`.
- Invalid file (bad TOML, unknown key, a rule above): `present: true, valid: false`,
  `error` is a one-line reason, `filing: false`, `ratchet: []`, default `flaky` (D15).

### C3. `fabro-io scout ratchet --config CONFIG_JSON [--root DIR]`

Reads C2's JSON. For each entry in order, runs `sh -c <command>` with the working directory
`--root` (default `.`), in its own process group, with `timeout_secs`. On the deadline it kills
the group (`SIGKILL`) and the entry is `error: "timed out after N s"`. It captures stdout (at most
64 MiB, else `error`) and parses it by `tool`, keeping only findings whose rule equals `rule`
exactly:

| Tool | Stdout | A finding | Path |
|---|---|---|---|
| `ruff` | one JSON array | element with `code == rule`; `location.row`, `location.column`, `message` | `filename` |
| `eslint` | one JSON array of file results | each `messages[]` element with `ruleId == rule`; `line`, `column`, `message`. A message with `fatal: true` makes the entry `error: "eslint could not parse <path>"` | the result's `filePath` |
| `clippy` | JSON lines; skip a line that is not a JSON object | `reason == "compiler-message"` and `message.code.code == rule`; the primary span (`spans[].is_primary`), its `line_start`, `column_start`; `message.message` | the primary span's `file_name` |

Paths: an absolute path under `--root` is made relative to it. An absolute path outside it,
or a relative path that starts with `..`, is dropped (for example clippy's `~/.cargo/registry`).
Findings are de-duplicated on `(path, line, column)` (clippy reports a finding once per target),
then sorted by path, line, column. Stdout that does not parse as the tool's shape makes the
entry `error: "<tool> output is not valid JSON"`.

**Budget.** The whole subcommand has `RATCHET_BUDGET_SECS = 2400`. An entry's deadline is
`min(timeout_secs, what is left of the budget)`. Once the budget is spent, each remaining
entry is `error: "not run: the 40-minute ratchet budget is spent"` and is not started.

Output:

```json
{"status": "ok", "reason": null,
 "entries": [{"index": 0, "tool": "ruff", "rule": "B006", "fp": "ratchet:ruff:b006",
   "status": "ok", "error": null, "count": 2, "files": 1,
   "findings": [{"path": "src/x.py", "line": 12, "column": 5, "message": "Do not use mutable ..."}],
   "command": "...", "enforce_in": "...", "enforce_how": "..."}]}
```

`count` is the number of distinct findings and `files` the number of distinct paths. An `error`
entry has `count: 0, files: 0, findings: []`. The graph writes the whole-source skip itself
when `setup.sh` fails (C9 `ratchet` node): `{"status": "skipped", "reason": "setup.sh failed (exit N)", "entries": []}`.

**Fingerprint (D7).** `slug(text)`: lowercase. Every run of characters outside `[a-z0-9_./-]`
becomes one `-`. Leading and trailing `-` are removed. Empty becomes `unknown`. Then:

- Ratchet rule: `ratchet:` + `slug(tool)` + `:` + `slug(rule)`. So `B006` → `ratchet:ruff:b006`,
  `@typescript-eslint/no-floating-promises` → `ratchet:eslint:typescript-eslint/no-floating-promises`,
  `clippy::needless_collect` → `ratchet:clippy:clippy-needless_collect`.
- Flaky job: `flaky:` + `slug(workflow path)` + `:` + `slug(job name)`. So
  `.github/workflows/ci.yml` and `UI end-to-end (tauri-pilot)` →
  `flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot`.

Every fingerprint matches `^(ratchet|flaky)(:[a-z0-9_./-]+){2}$`.

### C4. `fabro-io scout flaky --runs FILE --jobs-dir DIR --since TIMESTAMP`

- `--runs`: a JSON array of workflow-run objects as GitHub returns them (the graph slurps the
  `workflow_runs[]` of every page). Used fields: `id`, `path`, `name`, `run_attempt`,
  `head_sha`, `head_branch`, `created_at`, `html_url`.
- `--jobs-dir`: holds `jobs_<run id>.json` for **each** run with `run_attempt >= 2`. Each is a
  JSON array of job objects from `filter=all`. A missing file for such a run is exit 1, since
  a read failed.
- Runs with `created_at < since` or `run_attempt < 2` are ignored.

For each run, group its jobs by `name`. Within one name, an **occurrence** exists when some
attempt `a` qualifies as failed (D5: `conclusion == "failure"` and at least one step with
`conclusion == "failure"` and `name != "Set up job"`) and some attempt `b > a` has
`conclusion == "success"`. Count at most one occurrence per run and job name, using the
**last** qualifying failed attempt before the first later success.

```json
{"since": "2026-10-02T02:00:00Z",
 "jobs": [{"fp": "flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot",
           "workflow": ".github/workflows/ci.yml", "job": "UI end-to-end (tauri-pilot)",
           "count": 1,
           "occurrences": [{"run_id": 38855093415, "run_url": "https://github.com/...",
             "attempt": 1, "failed_job_id": 116824837347, "failed_job_url": "https://github.com/...",
             "failed_steps": ["Run scenario suite"], "created_at": "2026-10-15T09:12:00Z",
             "head_branch": "fabro/run/...", "head_sha": "abc..."}]}]}
```

`jobs` lists every job with at least one occurrence, sorted by `count` descending and then
`fp`, with occurrences newest first. Thresholds are C5's job, not this one's.

### C5. `fabro-io scout plan`

```
fabro-io scout plan --config CONFIG_JSON --ratchet RATCHET_JSON --flaky FLAKY_JSON
    --issues ISSUES_JSON --logs-dir DIR --repo OWNER/NAME --sha SHA
```

`--issues` is a JSON array of `{number, state, state_reason, closed_at, body}`, every `scout`
issue in the repository, any state, pull requests dropped. Known fingerprints come from markers
in `body`: the literal `<!-- fabro:scout fp=`, then the fingerprint up to the literal ` -->`.
A body may hold more than one marker. A marker whose fingerprint fails C3's pattern is ignored.

**Open count per source.** An issue is open when `state == "open"`. `open[source]` is the number
of open issues with at least one marker for that source (`ratchet:` or `flaky:`).

**Candidates**, in D18's order:
- each ratchet entry with `status == "ok"` and `count >= 1`;
- each flaky job not named in `ignore_jobs` whose **effective count** is at least `min_flakes`. The effective count is
  `count`, or for D8's flaky regression, the number of occurrences with `created_at` later than
  the closed issue's `closed_at`.

Ratchet entries with `status == "error"` become `error` actions. Entries with `count == 0`
produce nothing.

**For each candidate, in order:**

1. Any known issue for `fp` is open → `known` (`issue` = the highest-numbered open one).
2. Else, if a closed one exists, look at the highest-numbered closed issue. Its `state_reason`
   is `not_planned` or `duplicate` → `suppressed`. Otherwise it is a regression candidate of
   that issue. Flaky: recompute the effective count after its `closed_at`, which must be
   present or the issue is treated as closed at `"0000"`. If that count is below `min_flakes`,
   the candidate produces nothing.
3. Else → a new candidate.
4. A ratchet candidate with `count > MAX_FINDINGS (15)` or `files > MAX_FILES (8)` → `too_big`.
5. `open[source]` plus the `file` and `regression` actions already planned for that source is
   at least `MAX_OPEN (3)` → `backpressure`.
6. The `file` and `regression` actions already planned for that source is at least
   `PER_RUN_CAP (3)` → `capped`.
7. Otherwise → `file` or `regression`, with title, body and labels (C6, C7).

The plan does not change with `filing`. `filing` only decides whether the graph writes.

```json
{"filing": false, "repo": "owner/name",
 "sources": {"ratchet": "ok", "flaky": "ok"},
 "actions": [{"kind": "file", "source": "ratchet", "fp": "ratchet:ruff:b006", "issue": null,
   "title": "...", "body": "...", "labels": ["agent", "scout", "ai-generated", "bug", "fast-path"],
   "detail": "2 findings in 1 file"}]}
```

- `kind` is one of `file`, `regression`, `known`, `suppressed`, `too_big`, `backpressure`,
  `capped` or `error`. `title`, `body` and `labels` are set for `file` and `regression` only
  (null otherwise). `issue` is the open issue for `known`, and the closed issue for
  `regression` and `suppressed`.
- `detail`: ratchet `"N findings in M files"` (use the singular `finding` and `file` for 1), or
  the entry's error. Flaky `"N flakes in D days"`.
- `sources.ratchet`: `"ok"`; `"absent"` when the config has no entries; `"skipped: <reason>"`
  for a skipped ratchet JSON or an invalid config (`"skipped: scout.toml is invalid: <error>"`).
  `sources.flaky`: `"ok"`.
- Labels per D10.

### C6. Ratchet issue text

Title: `scout: fix and enforce <tool> rule <rule> (<detail>)`. A regression's title starts
`scout (regression of #M): ` instead of `scout: `.

Body:

```
<!-- fabro:scout fp=FP -->
Found by the scout (ADR 0027) on `main` at `SHA8`. The <tool> rule `RULE` reports N
finding(s) in M file(s), and `./.fabro/ci.sh` does not enforce it yet.

## Findings

- `PATH:LINE:COLUMN` MESSAGE
...(every finding)

## Acceptance criteria

- [ ] Every finding listed above is fixed, so `COMMAND` reports no `RULE` finding.
- [ ] `RULE` is enforced in `ENFORCE_IN`: ENFORCE_HOW. From then on `./.fabro/ci.sh` fails on a new `RULE` finding.
- [ ] `./.fabro/ci.sh` passes.

## Non-goals

- Do not suppress a finding. No `noqa`, `eslint-disable`, `#[allow(...)]` or `#![allow(...)]`, and no per-file, path or directory ignore of `RULE` in any config.
- Do not change behaviour beyond what each fix needs, and do not fix other rules.
- Do not edit `.fabro/scout.toml`, `.fabro/ci.sh` or `.fabro/test.toml`.

Filed by the scout. Close as not planned to stop it filing this rule again.
```

A regression adds the line `Regression of #M, which was closed on DATE.` after the first
paragraph (`DATE` is `closed_at`'s first 10 characters, or `an unknown date`).
`MESSAGE` is collapsed to one line and cut to 200 characters.

### C7. Flaky-job issue text

Title: `scout: flaky CI job "<job>" in <basename of workflow> (<detail>)`. A regression starts
`scout (regression of #M): `.

Body:

```
<!-- fabro:scout fp=FP -->
Found by the scout (ADR 0027). The CI job `JOB` in `WORKFLOW` failed and then passed on a
rerun of the same commit N times in the last D days. A job that fails at random holds PRs
and sends the merge phase to CI fixes for faults no change caused.

## Occurrences (newest first)

- CREATED_AT_DATE: [run RUN_ID, attempt A](RUN_URL) failed at step(s) `STEP`, `STEP` ([job log](FAILED_JOB_URL))
...(every occurrence counted)

## Failing log tails

### Run RUN_ID, attempt A

````text
LOG TAIL
````

...(the newest 3 occurrences that have a log file)

## Acceptance criteria

- [ ] The PR description names the cause of the intermittent failure and cites the log lines above.
- [ ] The test or CI step that failed is changed so that cause can no longer fail it.
- [ ] `./.fabro/ci.sh` passes.

## Non-goals

- Do not skip, delete, quarantine or mark the failing test as flaky, and do not add a retry around it or around the job.
- Do not change the CI workflow's rerun or retry settings.
- Do not edit `.fabro/scout.toml`, `.fabro/ci.sh` or `.fabro/test.toml`.

Filed by the scout. Close as not planned to stop it filing this job again.
```

A regression counts and lists only the occurrences after the closed issue's `closed_at`, and
adds `Regression of #M, which was closed on DATE.` after the first paragraph.

**Log tails.** The file is `<logs-dir>/<failed_job_id>.txt`. A missing file leaves that
occurrence out of this section. If none have a file, the section reads
`No log was available.` Cleaning, in order:
1. Drop the leading timestamp: when a line starts with a digit and its first space comes
   within its first 40 characters, right after a `Z`, remove everything up to and including
   that space.
2. Remove ANSI escape sequences (ESC `[` ... final byte in `@`–`~`), then every other control
   character but newline and tab.
3. Drop every line after the first line that starts with `##[error]` (the runner's line for
   the first failed step; what follows is later steps and post-job cleanup). Keep the last 60
   lines, then the last 3000 characters, cut at a line start.
4. Replace every backtick run of 4 or more with `'''`, and every `@` with `@` + U+200B, so no
   one is mentioned.

The whole body is at most 60000 characters. If it is longer, drop log tails from the oldest
until it fits.

### C8. `fabro-io scout summary --plan PLAN_JSON [--filed FILED_JSON]`

`--filed` is the graph's write log: a JSON array of `{fp, outcome, number, error}`, where
`outcome` is `filed`, `raced` (the fingerprint appeared before the write) or `failed`. Missing
`--filed` means no writes were attempted.

Prints one line (no trailing newline), at most 1500 characters, with no `"` and no backslash
(strip both). Parts are joined with ` | `:

1. Header: `scout NAME` (the repository name without owner), plus ` DRY RUN` when `filing` is
   false. Then `: ` and the counts that are non-zero, comma-separated, in this order:
   `filed N` (live) or `would file N` (dry), `known N`, `suppressed N`, `too big N`,
   `capped N`, `held back N` (backpressure), `errors N`.
2. One part per `file` or `regression` action: dry `would file <source> <short>` and live
   `filed #N <source> <short>`, `raced <short>` or `write failed <short>: <error>`. `short` is
   `<tool> <rule> (<detail>)` or `<job> (<detail>)`, with ` regression of #M` added when it applies.
3. One part per `error` action: `error <tool> <rule>: <detail>`.
4. Dry runs only, one part per `too_big`: `too big <tool> <rule> (<detail>)`.
5. One part per source that is not `ok` or `absent`: `ratchet skipped: <reason>`.

The line is **empty** when D17 says nothing is worth posting: no `file`, `regression` or
`error` action, every source `ok` or `absent`, and, on a live run, nothing was written. When the
line is over 1500 characters, drop whole parts from the end and add `| ...and N more`.

The graph's own stop (a failed `prep` or `flaky` node) is not C8's job: `summarize` (C9)
writes `Stopped at <node>: <reason>` itself when `stop_reason` exists.

### C9. The graph

`.fabro/workflows/scout/workflow.fabro`, `digraph Scout`, every working file under
`/tmp/fabro/scout/` (`$S` below). Nodes in order, all `shape=parallelogram` command nodes. Use
`stop()` the way `merge-queue`'s `prep` does: it writes `$S/stop_reason` and `$S/stopped_at`,
then exits 1.

**Every GitHub read goes to a file first, and its exit code is checked before `jq` reads it.**
POSIX `sh` has no `pipefail`, so `gh api ... | jq -s . > out.json` succeeds when `gh` fails. Write
`gh api --paginate URL --jq FILTER > $S/x.raw || stop '...'`, then
`jq -s . $S/x.raw > $S/x.json || stop '...'`.

| Node | Timeout | What it does |
|---|---|---|
| `prep` | `3m` | `rm -rf $S; mkdir -p $S/logs $S/jobs`. Reads `REPO` (`gh repo view --json nameWithOwner`), `SHA` (`git rev-parse HEAD`) and `NOW` (`date -u +%Y-%m-%dT%H:%M:%SZ`) into `$S/env.json`. Runs `fabro-io scout config > $S/config.json`; if that fails, `stop 'fabro-io has no scout subcommand: run make deploy-images'`. Lists every `scout` issue: `gh api --paginate "repos/$REPO/issues?labels=scout&state=all&per_page=100" --jq '.[] \| select(.pull_request \| not) \| {number, state, state_reason, closed_at, body}'`, slurped with `jq -s` into `$S/issues.json`; a failure is `stop 'could not list scout issues'`. Prints `{"context_updates":{"scout":"run"}}`, so `output_schema="routing"`. |
| `ratchet` | `65m` | If `config.json` has no ratchet entries, writes `{"status":"ok","reason":null,"entries":[]}` and exits 0. Else runs `timeout 1200 ./.fabro/setup.sh > $S/setup.log 2>&1`. If that fails, writes the C3 skip with its exit code and exits 0. Else `fabro-io scout ratchet --config $S/config.json > $S/ratchet.json`; if that fails, writes the skip with reason `fabro-io scout ratchet failed (exit N)`. **Always exits 0** (D15). |
| `flaky` | `15m` | `SINCE` = `NOW` minus `window_days` days (`date -u -d "@$(( $(date +%s) - DAYS*86400 ))" +%Y-%m-%dT%H:%M:%SZ`). `gh api "repos/$REPO/actions/runs?per_page=100&page=$P"`, one page at a time, newest first, until a page is short or reaches a run older than `SINCE` (a `created=` filter caps at 1,000 runs), projected to C4's fields and de-duplicated by `id` into `$S/runs.json`. For each run with `run_attempt >= 2`: `gh api --paginate "repos/$REPO/actions/runs/$ID/jobs?filter=all&per_page=100" --jq '.jobs[]'` slurped into `$S/jobs/jobs_$ID.json`. Any read failure is `stop 'could not read Actions runs'`. Then `fabro-io scout flaky ... > $S/flaky.json`. For the first 10 jobs with `count >= min_flakes` that are not in `ignore_jobs`, it fetches the log of each of the newest 3 occurrences: `gh api --allow-escape-sequences "repos/$REPO/actions/jobs/$JID/logs" > $S/logs/$JID.txt` (without the flag gh refuses a log with ANSI codes, which is every CI log). A failed log read deletes that file and is not a stop. |
| `plan` | `2m` | A missing or unparseable `ratchet.json` (the `ratchet` node hit its own timeout) first becomes the C3 skip with reason `the ratchet node did not finish`. Then `fabro-io scout plan ... > $S/plan.json`. Prints each action's `kind`, `fp` and `detail` to stderr. |
| `file` | `10m` | Exits 0 at once when `filing` is false. Else it creates the labels once with `gh label create <name> --color <c> --description <d> 2>/dev/null \|\| true` (C9a). For each `file` or `regression` action in order: it re-lists the `scout` issues as `prep` does (a failure is a `failed` entry and stops). If the fingerprint is now present, it appends `raced`. Else it writes the body to a file and runs `gh issue create --title "$T" --body-file "$F" --label L1 --label L2 ...`. It appends `filed` with the number parsed from the returned URL, or `failed` with the last stderr line, and **stops** on `failed`. The log is `$S/filed.json` (C8). Exits 0. |
| `summarize` | `1m` | `fabro-io scout summary --plan $S/plan.json [--filed $S/filed.json]` when `plan.json` exists. If `stop_reason` exists, prefixes `Stopped at <stopped_at>: <reason>` (with ` \| ` before the plan line when there is one). Strips `"`, `{`, `}` and backslashes, cuts to 1500 characters, prints the line to stderr, then `jq -cn --arg m "$SUM" '{context_updates: {scout_summary: $m}}'`. `output_schema="routing"`. Always exits 0. |
| `finish` | `1m` | `echo done`. The summary hook's target. |

**C9a. Labels** (created if missing; an existing label is left as it is):

| Label | Color | Description |
|---|---|---|
| `scout` | `1d76db` | Filed by the scout (ADR 0027) |
| `flaky-test` | `fbca04` | A CI job that fails at random |
| `ai-generated` | `ededed` | Written by an automation |
| `fast-path` | `0e8a16` | Short backlog lane (ADR 0023) |
| `bug` | `d73a4a` | Something is not working |
| `agent` | `5319e7` | Queued for the coder agents |

Edges, exactly:

```
start -> prep
prep -> ratchet      [condition="outcome=succeeded && context.scout=run"]
prep -> summarize
ratchet -> flaky
flaky -> plan        [condition="outcome=succeeded"]
flaky -> summarize
plan -> file         [condition="outcome=succeeded"]
plan -> summarize
file -> summarize
summarize -> finish
finish -> exit
```

`ratchet -> flaky` is deliberately one unconditional edge, because a ratchet failure must not
stop the flaky source (D15). Say so in a `//` comment. That makes 9 nodes and 11 edges. Graph
attributes: `default_fidelity="truncate"`, `stall_timeout="70m"` (above `ratchet`'s 65m, R2: 20m of `setup.sh` plus the 40m budget fits).
No cycle and no import, so no `loop_restart_signature_limit`.

`workflow.toml`: `merge-queue`'s, minus `FABRO_API_*` and `FABRO_AUTO_MERGE`, so no
`[run.environment.env]` at all. It has `[run.run_branch] enabled = true, push = false`. The
permissions are `contents = "read"`, `issues = "write"`, `actions = "read"`. The hooks are
`discord-failed` and `discord-scout-summary` (`stage_start`, matcher `^finish$`,
`/storage/scripts/discord-notify.sh scout-summary`).

### C10. The automations

```
provision_automation scout-jelly-swipe python andrewthetechie/jelly-swipe scout daily "0 2 * * *" || FAILED=$((FAILED+1))
provision_automation scout-lawncare-saas python-node andrewthetechie/lawncare-saas scout daily "15 2 * * *" || FAILED=$((FAILED+1))
provision_automation scout-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports scout daily "30 2 * * *" || FAILED=$((FAILED+1))
provision_automation scout-writers-app rust-node andrewthetechie/writers-app scout daily "45 2 * * *" || FAILED=$((FAILED+1))
```

No 7th or 8th argument: the schedule is created disabled (D16).

## What this does not cover

- Red `main` (#24), mutation testing, production errors and `tsc` flags (D1).
- A deterministic suppression counter (D11). One is built only if task 07 finds a merged
  suppression.
- Closing an issue whose finding went away without a PR (D12).
- The scheduler. It needs no change: scout issues are ordinary `agent` issues.

## Tasks

| Task | What | Files |
|---|---|---|
| 01 | `scout config`, `scout ratchet`, fingerprints | `ops/fabro-io/src/scout/{mod,config,ratchet}.rs`, `src/cli.rs`, `src/lib.rs`, `tests/scout_ratchet_tests.rs`, fixtures |
| 02 | `scout flaky` | `src/scout/flaky.rs`, `tests/scout_flaky_tests.rs`, fixtures |
| 03 | `scout plan`, issue text, `scout summary`; version 0.6.0 | `src/scout/{plan,text}.rs`, `tests/scout_plan_tests.rs`, fixtures, `Cargo.toml` |
| 04 | The package; `config_changed` gains `scout.toml` | `.fabro/workflows/scout/`, `review-merge.fabro`, `ops/test-task-gates.sh` |
| 05 | Notify mode, provisioning, host check, runbook | `discord-notify.sh`, `ops/provision-server-state.sh`, `ops/check-host.sh`, `ops/README.md` |
| 06 | Docs | `docs/adr/0023-fast-path-lane.md`, `backlog/workflow.fabro` comment, `AGENTS.md`, `docs/README.md`, `CONTEXT.md`, this file's status |
| 07 | **Operator only.** Deploy, `scout.toml` per repository, dry runs, go live, measure | host, target repositories |
