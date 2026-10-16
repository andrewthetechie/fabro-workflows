//! `scout flaky`: GitHub's runs and jobs into Flaky jobs (ADR 0027 D5, C4).
//!
//! A **Flaky job** failed and then passed on a rerun of the same run. It is counted per
//! workflow file and job name, never per test: which test failed is evidence inside the
//! issue, not part of its identity. An attempt qualifies as failed only when the job's
//! conclusion is `failure` and a step other than `Set up job` failed, so a runner fault
//! is not a flake.
//!
//! This applies no threshold: task 03 does, because a regression counts only the
//! occurrences after the closed issue's `closed_at`.

// Rust guideline compliant 2026-07-21

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::{Deserialize, Serialize};

use super::{flaky_fp, is_github_ts};

/// One workflow run, with C4's fields. GitHub sends many more.
#[derive(Clone, Debug, Deserialize)]
pub struct Run {
    /// The run's id, the key of its jobs file.
    pub id: u64,
    /// The workflow file, relative to the repository root.
    #[serde(default)]
    pub path: String,
    /// The workflow's display name.
    #[serde(default)]
    pub name: String,
    /// How many times the run was attempted.
    pub run_attempt: u64,
    /// The head commit.
    #[serde(default)]
    pub head_sha: String,
    /// The head branch.
    #[serde(default)]
    pub head_branch: String,
    /// GitHub's UTC timestamp of the run's creation.
    pub created_at: String,
    /// The run's page.
    #[serde(default)]
    pub html_url: String,
}

/// One job of one attempt, with C4's fields.
#[derive(Clone, Debug, Deserialize)]
pub struct Job {
    /// The job attempt's id, and the name of its log file.
    pub id: u64,
    /// Which attempt this job belongs to.
    #[serde(default)]
    pub run_attempt: u64,
    /// The job's name, shared by every attempt.
    #[serde(default)]
    pub name: String,
    /// `success`, `failure`, `cancelled`, `null` while it runs.
    #[serde(default)]
    pub conclusion: Option<String>,
    /// The job attempt's page.
    #[serde(default)]
    pub html_url: String,
    /// The attempt's steps.
    #[serde(default)]
    pub steps: Vec<Step>,
}

/// One step of one job attempt.
#[derive(Clone, Debug, Deserialize)]
pub struct Step {
    /// The step's name.
    #[serde(default)]
    pub name: String,
    /// Its position, used to order `failed_steps`.
    #[serde(default)]
    pub number: u64,
    /// `failure` on a failed step, `null` while it runs.
    #[serde(default)]
    pub conclusion: Option<String>,
}

/// C4's output: every Flaky job, with its occurrences.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FlakyOut {
    /// The `--since` the detection used.
    pub since: String,
    /// One entry per job with at least one occurrence, sorted by count then fingerprint.
    pub jobs: Vec<FlakyJob>,
}

/// One Flaky job, counted per workflow file and job name.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FlakyJob {
    /// The C3 fingerprint.
    pub fp: String,
    /// The workflow file.
    pub workflow: String,
    /// The job name.
    pub job: String,
    /// How many occurrences there are in the window.
    pub count: usize,
    /// The occurrences, newest first.
    pub occurrences: Vec<Occurrence>,
}

/// One run of a job that failed and then passed.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Occurrence {
    /// The run the occurrence is in.
    pub run_id: u64,
    /// The run's page.
    pub run_url: String,
    /// The attempt used: the last qualifying failure before the first later success.
    pub attempt: u64,
    /// That attempt's job id, and the name of its log file.
    pub failed_job_id: u64,
    /// That attempt's page.
    pub failed_job_url: String,
    /// The names of its failed steps, in step-number order, without `Set up job`.
    pub failed_steps: Vec<String>,
    /// The run's creation timestamp.
    pub created_at: String,
    /// The run's head branch.
    pub head_branch: String,
    /// The run's head commit.
    pub head_sha: String,
}

/// Finds every Flaky job in `runs`, with the jobs of every attempt in `jobs` (C4).
///
/// Runs created before `since` and runs with `run_attempt < 2` are ignored. At most one
/// occurrence is counted per run and job name. The output is sorted by count descending,
/// then fingerprint, with occurrences newest first.
#[must_use]
pub fn detect(runs: &[Run], jobs: &BTreeMap<u64, Vec<Job>>, since: &str) -> FlakyOut {
    let mut grouped: BTreeMap<String, (String, String, Vec<Occurrence>)> = BTreeMap::new();
    for run in runs {
        if run.run_attempt < 2 || run.created_at.as_str() < since {
            continue;
        }
        let Some(run_jobs) = jobs.get(&run.id) else {
            continue;
        };
        let mut by_name: BTreeMap<&str, Vec<&Job>> = BTreeMap::new();
        for job in run_jobs {
            by_name.entry(job.name.as_str()).or_default().push(job);
        }
        for group in by_name.values_mut() {
            group.sort_by_key(|job| job.run_attempt);
            let Some(failed) = failed_attempt(group) else {
                continue;
            };
            let fp = flaky_fp(&run.path, &failed.name);
            let entry = grouped
                .entry(fp)
                .or_insert_with(|| (run.path.clone(), failed.name.clone(), Vec::new()));
            entry.2.push(occurrence(run, failed));
        }
    }
    let mut out: Vec<FlakyJob> = grouped
        .into_iter()
        .map(|(fp, (workflow, job, mut occurrences))| {
            occurrences.sort_by(|a, b| {
                b.created_at.cmp(&a.created_at).then_with(|| b.run_id.cmp(&a.run_id))
            });
            FlakyJob { fp, workflow, job, count: occurrences.len(), occurrences }
        })
        .collect();
    out.sort_by(|a, b| b.count.cmp(&a.count).then_with(|| a.fp.cmp(&b.fp)));
    FlakyOut { since: since.to_string(), jobs: out }
}

/// The attempt an occurrence uses: the last qualifying failure before the first later
/// success (C4).
fn failed_attempt<'a>(group: &[&'a Job]) -> Option<&'a Job> {
    let mut last_failed: Option<&Job> = None;
    for job in group {
        if qualifies(job) {
            last_failed = Some(job);
        } else if job.conclusion.as_deref() == Some("success") && last_failed.is_some() {
            return last_failed;
        }
    }
    None
}

/// True when the attempt is a failure a rerun can excuse (D5).
fn qualifies(job: &Job) -> bool {
    job.conclusion.as_deref() == Some("failure")
        && job
            .steps
            .iter()
            .any(|step| step.conclusion.as_deref() == Some("failure") && step.name != "Set up job")
}

/// The failed steps of one attempt, in step-number order, without `Set up job`.
fn failed_steps(job: &Job) -> Vec<String> {
    let mut steps: Vec<&Step> = job
        .steps
        .iter()
        .filter(|step| step.conclusion.as_deref() == Some("failure") && step.name != "Set up job")
        .collect();
    steps.sort_by_key(|step| step.number);
    steps.iter().map(|step| step.name.clone()).collect()
}

/// One occurrence from the run and the failed attempt it chose.
fn occurrence(run: &Run, failed: &Job) -> Occurrence {
    Occurrence {
        run_id: run.id,
        run_url: run.html_url.clone(),
        attempt: failed.run_attempt,
        failed_job_id: failed.id,
        failed_job_url: failed.html_url.clone(),
        failed_steps: failed_steps(failed),
        created_at: run.created_at.clone(),
        head_branch: run.head_branch.clone(),
        head_sha: run.head_sha.clone(),
    }
}

/// Runs `scout flaky --runs FILE --jobs-dir DIR --since TIMESTAMP` (C4).
#[must_use]
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut runs_arg: Option<PathBuf> = None;
    let mut jobs_dir = PathBuf::from(".");
    let mut since_arg: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--runs" | "--jobs-dir" | "--since" => {
                let flag = args[i].clone();
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("scout flaky: {flag} needs a value");
                    return ExitCode::from(2);
                };
                match flag.as_str() {
                    "--runs" => runs_arg = Some(PathBuf::from(value)),
                    "--jobs-dir" => jobs_dir = PathBuf::from(value),
                    _ => since_arg = Some(value.clone()),
                }
            }
            other => {
                eprintln!("scout flaky: unexpected argument '{other}'");
                return ExitCode::from(2);
            }
        }
        i += 1;
    }
    let (Some(runs_path), Some(since)) = (runs_arg, since_arg) else {
        eprintln!("scout flaky: --runs <path> and --since <timestamp> are required");
        return ExitCode::from(2);
    };
    if !is_github_ts(&since) {
        eprintln!("scout flaky: --since must be YYYY-MM-DDTHH:MM:SSZ, found \"{since}\"");
        return ExitCode::FAILURE;
    }
    let runs: Vec<Run> = match std::fs::read_to_string(&runs_path)
        .map_err(|e| format!("cannot read {}: {e}", runs_path.display()))
        .and_then(|text| {
            serde_json::from_str(&text)
                .map_err(|e| format!("{} is not a JSON array of runs: {e}", runs_path.display()))
        }) {
        Ok(runs) => runs,
        Err(message) => {
            eprintln!("scout flaky: {message}");
            return ExitCode::FAILURE;
        }
    };
    let mut jobs: BTreeMap<u64, Vec<Job>> = BTreeMap::new();
    for run in &runs {
        if run.run_attempt < 2 {
            continue;
        }
        if !is_github_ts(&run.created_at) {
            eprintln!(
                "scout flaky: run {}: created_at must be YYYY-MM-DDTHH:MM:SSZ, found \"{}\"",
                run.id, run.created_at
            );
            return ExitCode::FAILURE;
        }
        if run.created_at.as_str() < since.as_str() {
            continue;
        }
        let path = jobs_dir.join(format!("jobs_{}.json", run.id));
        let Ok(text) = std::fs::read_to_string(&path) else {
            eprintln!("scout flaky: missing jobs for run {}", run.id);
            return ExitCode::FAILURE;
        };
        let list: Vec<Job> = match serde_json::from_str(&text) {
            Ok(list) => list,
            Err(e) => {
                eprintln!("scout flaky: jobs for run {} are not valid JSON: {e}", run.id);
                return ExitCode::FAILURE;
            }
        };
        jobs.insert(run.id, list);
    }
    let out = detect(&runs, &jobs, &since);
    let json = serde_json::to_string(&out).unwrap_or_else(|e| {
        eprintln!("scout flaky: cannot serialize the result: {e}");
        String::new()
    });
    println!("{json}");
    ExitCode::SUCCESS
}
