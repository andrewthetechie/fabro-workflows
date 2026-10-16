//! `scout flaky`: the C4 detection over hand-built run fixtures, plus the CLI's two exits.

mod common;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use fabro_io::scout::flaky::{self, FlakyJob, Run};
use fabro_io::scout::is_github_ts;

/// The window start every fixture is read against.
const SINCE: &str = "2026-10-02T02:00:00Z";

/// The flaky fixture directory.
fn dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/scout/flaky")
}

/// The runs file and every jobs file beside it, as the CLI would assemble them.
fn fixture(runs_file: &str) -> (Vec<Run>, BTreeMap<u64, Vec<flaky::Job>>) {
    let runs: Vec<Run> =
        serde_json::from_str(&std::fs::read_to_string(dir().join(runs_file)).unwrap()).unwrap();
    let mut jobs = BTreeMap::new();
    for run in &runs {
        if let Ok(text) = std::fs::read_to_string(dir().join(format!("jobs_{}.json", run.id))) {
            jobs.insert(run.id, serde_json::from_str(&text).unwrap());
        }
    }
    (runs, jobs)
}

/// The one job `out` holds for `fp`.
fn job<'a>(out: &'a flaky::FlakyOut, fp: &str) -> &'a FlakyJob {
    out.jobs.iter().find(|job| job.fp == fp).unwrap_or_else(|| panic!("no job {fp}"))
}

/// Runs the subcommand with the fixture directory as the jobs dir.
fn cli(runs: &str, since: &str) -> ExitCode {
    flaky::run_cli(&[
        "--runs".to_string(),
        dir().join(runs).display().to_string(),
        "--jobs-dir".to_string(),
        dir().display().to_string(),
        "--since".to_string(),
        since.to_string(),
    ])
}

#[test]
fn six_run_fixture_gives_two_occurrences() {
    let (runs, jobs) = fixture("runs.json");
    let out = flaky::detect(&runs, &jobs, SINCE);
    assert_eq!(out.since, SINCE);
    assert_eq!(out.jobs.len(), 1);
    let job = job(&out, "flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot");
    assert_eq!(job.workflow, ".github/workflows/ci.yml");
    assert_eq!(job.job, "UI end-to-end (tauri-pilot)");
    assert_eq!(job.count, 2);
    let ids: Vec<u64> = job.occurrences.iter().map(|o| o.run_id).collect();
    assert_eq!(ids, vec![38855093415, 38194309793], "newest first");
    let run5 = job.occurrences.iter().find(|o| o.run_id == 38194309793).unwrap();
    assert_eq!(run5.attempt, 2, "the last qualifying failure before the pass");
    assert_eq!(run5.failed_job_id, 5002);
    for gone in [38194309790, 38194309791, 38194309792, 38194309794] {
        assert!(
            !job.occurrences.iter().any(|o| o.run_id == gone),
            "run {gone} must give nothing"
        );
    }
}

#[test]
fn three_branches_give_one_count_three_fingerprint() {
    let (runs, jobs) = fixture("runs_three.json");
    let out = flaky::detect(&runs, &jobs, SINCE);
    assert_eq!(out.jobs.len(), 2);
    let test = job(&out, "flaky:.github/workflows/ci.yml:test");
    assert_eq!(test.count, 3);
    let ids: Vec<u64> = test.occurrences.iter().map(|o| o.run_id).collect();
    assert_eq!(ids, vec![4003, 4002, 4001], "newest first");
    let matrix = job(&out, "flaky:.github/workflows/ci.yml:test-3.12");
    assert_eq!(matrix.job, "test (3.12)");
    assert_eq!(matrix.count, 1);
    assert!(out.jobs[0].count >= out.jobs[1].count, "sorted by count descending");
}

#[test]
fn a_new_run_id_is_not_a_new_flake() {
    let (runs, jobs) = fixture("runs_three.json");
    let out = flaky::detect(&runs, &jobs, SINCE);
    let matches: Vec<&FlakyJob> = out.jobs.iter().filter(|job| job.job == "test").collect();
    assert_eq!(matches.len(), 1, "one fingerprint, not one per run");
    assert_eq!(matches[0].count, 3);
}

#[test]
fn failed_steps_drop_set_up_job_and_follow_step_numbers() {
    let (runs, jobs) = fixture("runs.json");
    let out = flaky::detect(&runs, &jobs, SINCE);
    let job = job(&out, "flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot");
    let run5 = job.occurrences.iter().find(|o| o.run_id == 38194309793).unwrap();
    assert_eq!(
        run5.failed_steps,
        vec!["Run scenario suite", "Probe - ping the app before running scenarios"]
    );
    assert!(!run5.failed_steps.iter().any(|step| step == "Set up job"));
    let run1 = job.occurrences.iter().find(|o| o.run_id == 38855093415).unwrap();
    assert_eq!(run1.failed_steps, vec!["Run scenario suite"]);
}

#[test]
fn a_null_conclusion_is_not_a_success() {
    let (runs, jobs) = fixture("runs.json");
    let out = flaky::detect(&runs, &jobs, SINCE);
    assert!(
        !out.jobs.iter().any(|job| job.job == "lint"),
        "the lint job's later attempt is still running"
    );
}

#[test]
fn github_timestamps_are_checked_by_shape() {
    assert!(is_github_ts("2026-10-02T02:00:00Z"));
    assert!(!is_github_ts("2026-10-02"));
    assert!(!is_github_ts("2026-10-02T02:00:00"));
    assert!(!is_github_ts("2026-10-02 02:00:00Z"));
    assert!(!is_github_ts("2026-1-02T02:00:00Z"));
}

#[test]
fn cli_reads_the_fixture_and_succeeds() {
    assert_eq!(cli("runs.json", SINCE), ExitCode::SUCCESS);
}

#[test]
fn cli_missing_jobs_file_is_exit_one() {
    let empty = common::temp_root("scout-flaky-missing");
    let code = flaky::run_cli(&[
        "--runs".to_string(),
        dir().join("runs.json").display().to_string(),
        "--jobs-dir".to_string(),
        empty.display().to_string(),
        "--since".to_string(),
        SINCE.to_string(),
    ]);
    assert_eq!(code, ExitCode::FAILURE);
    std::fs::remove_dir_all(&empty).ok();
}

#[test]
fn cli_bad_since_is_exit_one() {
    assert_eq!(cli("runs.json", "2026-10-02"), ExitCode::FAILURE);
}
