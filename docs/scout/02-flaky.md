# Task 02: `fabro-io scout flaky`

Read `00-overview-and-contracts.md` first (D5, D7; contract C4, and the Facts about the
Actions API). Invoke `/ms-rust` before you write Rust.

## Goal

A pure function that turns GitHub's workflow runs and their jobs from every attempt into a
list of **Flaky jobs**, each with its occurrences. It applies no threshold: task 03 does,
because a regression counts only occurrences after a closed issue's `closed_at`.

## Steps

1. Create `ops/fabro-io/src/scout/flaky.rs` and add `pub mod flaky;` to `scout/mod.rs`. Add
   `flaky` to `run_cli`'s dispatch.
2. Deserialize only the fields C4 names. Use `#[serde(default)]` for `steps` and any optional
   string, and ignore unknown fields (GitHub sends many). A job's `conclusion` can be `null`
   while it runs: treat `null` as neither failure nor success.
3. `pub fn detect(runs: &[Run], jobs: &BTreeMap<u64, Vec<Job>>, since: &str) -> FlakyOut`:
   - skip runs with `created_at < since` (string comparison) or `run_attempt < 2`;
   - group the run's jobs by `name`, and sort each group by `run_attempt`;
   - `qualifies(job)`: `conclusion == "failure"` and at least one step with
     `conclusion == "failure"` and `name != "Set up job"`;
   - find the first attempt `b` with `success` that has an earlier qualifying attempt. The
     occurrence uses the **last** qualifying attempt before `b`. That attempt's job gives
     `failed_job_id`, `failed_job_url` (its `html_url`) and `failed_steps` (the names of its
     failed steps, in step-number order, `Set up job` left out);
   - `fp` is `flaky_fp(run.path, job.name)` from task 01;
   - output sorted as C4 says.
4. Validate every timestamp you compare (`created_at`, `--since`) as
   `YYYY-MM-DDTHH:MM:SSZ`: exactly 20 characters, digits where digits belong, and `T` and `Z`
   in place. Write a small `pub fn is_github_ts(s: &str) -> bool` in `mod.rs` (task 03 uses it
   too). An invalid one is exit 1, with the field named on stderr.
5. CLI: `scout flaky --runs FILE --jobs-dir DIR --since TS`. Read `jobs_<id>.json` for every
   run that has `run_attempt >= 2` and `created_at >= since`. A missing or invalid file is
   exit 1 (`missing jobs for run <id>`). Print `FlakyOut`, exit 0.

## Fixtures: `ops/fabro-io/tests/fixtures/scout/flaky/`

Build them from the two live runs in the Facts. Copy the field names exactly. The ids can be
made up.

- `runs.json`: six runs:
  1. 38855093415-shaped: attempt 2, `UI end-to-end (tauri-pilot)` fails in attempt 1 at
     `Run scenario suite` and passes in 2. **One occurrence.**
  2. 38194309790-shaped: attempt 3, the job fails in all three attempts. **No occurrence.**
  3. Attempt 2, where attempt 1's job failed only at `Set up job` (a runner fault) and passed
     in 2. **No occurrence.**
  4. Attempt 2, where attempt 1's job is `cancelled`. **No occurrence.**
  5. Attempt 3, where the job fails in 1 and 2 (both qualify) and passes in 3. **One
     occurrence, using attempt 2.**
  6. Attempt 2, created before `since`. **Ignored**, and it has no jobs file.
- `jobs_<id>.json` for runs 1-5.
- `runs_three.json` plus jobs files: three runs on three branches, where the same
  `(path, name)` flakes once each and a matrix job `test (3.12)` flakes once.

## Tests: `ops/fabro-io/tests/scout_flaky_tests.rs`

- The six-run fixture gives exactly two occurrences (runs 1 and 5), with run 5's `attempt == 2`.
  Runs 2, 3, 4 and 6 give nothing.
- `runs_three.json`: one job with `count == 3`, occurrences newest first, and a separate job
  `flaky:.github/workflows/ci.yml:test-3.12` with `count == 1`. The output is sorted by count.
- **A new run id is not a new flake:** the same job in different runs gives one fingerprint
  whose `count` grows, never a second fingerprint.
- `failed_steps` leaves out `Set up job` and is in step-number order.
- A `null` conclusion in a later attempt is not a success.
- CLI: a run that needs a jobs file without one is exit 1. A bad `--since` is exit 1.

## Done when

- `cargo test` passes, and `make check` with `CHECK_CARGO=always` passes.
- One commit: `feat(fabro-io): scout flaky subcommand (scout 02, #32)`.
