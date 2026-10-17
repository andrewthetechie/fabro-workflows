//! `flakeradar classify`: whether a PR's red checks fail only on known flaky tests (ADR 0028
//! F8, docs/flakeradar C10).
//!
//! The merge phase reruns a red PR's failed jobs once (ADR 0011 D2). When it is still red,
//! this decides between a second rerun and then a hold (every failing check is known flaky),
//! and `ci_fix` as before. It reads the files `watch_checks` wrote and never the network. It
//! fails toward `ci_fix`: a check FlakeRadar cannot vouch for is not known flaky.

// Rust guideline compliant 2026-07-21

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::{Deserialize, Serialize};

use crate::scout::flakeradar::one_line;

const USAGE: &str = "usage: fabro-io flakeradar classify --checks FILE --failures-dir DIR [--ignore-jobs JSON] [--section FILE]\n";

/// One `gh pr checks --json name,state,bucket,link` entry.
#[derive(Clone, Debug, Deserialize)]
pub struct Check {
    /// The check's name; for an Actions job, the job's name.
    pub name: String,
    /// `pass`, `fail`, `pending`, `skipping` or `cancel`.
    #[serde(default)]
    pub bucket: String,
    /// The check's page: `.../actions/runs/<run>/job/<job>` for an Actions job.
    #[serde(default)]
    pub link: String,
}

/// One CI-run lookup (flakeradar#5), with the fields F8 reads.
#[derive(Clone, Debug, Deserialize)]
pub struct RunLookup {
    /// The attempt's report counts.
    #[serde(default)]
    pub reports: Reports,
    /// True when `failures` was cut at 500.
    #[serde(default)]
    pub truncated: bool,
    /// One entry per test that failed or errored in the attempt.
    #[serde(default)]
    pub failures: Vec<TestFailure>,
    /// The attempt's failed jobs.
    #[serde(default)]
    pub failed_jobs: Vec<FailedJob>,
}

/// An attempt's report counts.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Reports {
    /// Reports not processed yet.
    #[serde(default)]
    pub pending: u64,
    /// Reports that failed to process.
    #[serde(default)]
    pub failed: u64,
}

/// One failing test of the attempt.
#[derive(Clone, Debug, Deserialize)]
pub struct TestFailure {
    /// The test's name.
    pub name: String,
    /// The test's project.
    #[serde(default)]
    pub project: String,
    /// GitHub's job id, as a string.
    #[serde(default)]
    pub ci_job_id: Option<String>,
    /// The test's last try in the run: `passed` after a runner retry, else `failed` or `error`.
    pub status: String,
    /// The test's current tier.
    pub tier: String,
    /// The test's current score.
    #[serde(default)]
    pub flakiness_score: f64,
    /// The test's proven flakes.
    #[serde(default)]
    pub confirmed_flake_count: u32,
    /// FlakeRadar's guess at the cause.
    #[serde(default)]
    pub failure_category: Option<String>,
    /// The failure's message, with colour codes.
    #[serde(default)]
    pub message: Option<String>,
    /// The issue FlakeRadar links the test to.
    #[serde(default)]
    pub github_issue_number: Option<u64>,
}

/// One failed job of the attempt.
#[derive(Clone, Debug, Deserialize)]
pub struct FailedJob {
    /// GitHub's job id, as a string.
    #[serde(default)]
    pub ci_job_id: Option<String>,
    /// `explained` or `unexplained`.
    #[serde(default)]
    pub outcome: String,
}

/// C10's output.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Classified {
    /// True only when there is a failing check and every one is known flaky (F8).
    pub known_flaky: bool,
    /// True when it is not known flaky, yet no failing check is shown to be a real failure:
    /// every one lacks FlakeRadar data (no report, still processing, failed or cut reports).
    /// After a second rerun, F9 holds such a PR rather than send a likely flake to `ci_fix`.
    pub unknown: bool,
    /// One entry per failing check, in `--checks` order.
    pub checks: Vec<Judged>,
}

/// One failing check, judged.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Judged {
    /// The check's name.
    pub name: String,
    /// The Actions run, when the link names one.
    pub run: Option<u64>,
    /// The Actions job, when the link names one.
    pub job: Option<u64>,
    /// Whether this check is known flaky.
    pub known_flaky: bool,
    /// Why it is not, or why it is not judged.
    pub reason: Option<String>,
    /// Its failing tests, when FlakeRadar reports any.
    pub tests: Vec<FlakyFailure>,
}

/// One failing test of a judged check.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FlakyFailure {
    /// The test's name.
    pub name: String,
    /// The test's project.
    pub project: String,
    /// The test's current tier.
    pub tier: String,
    /// The test's score.
    pub score: f64,
    /// The test's proven flakes.
    pub proven: u32,
    /// FlakeRadar's guess at the cause.
    pub category: Option<String>,
    /// The issue FlakeRadar links the test to.
    pub issue: Option<u64>,
    /// The failure's message as one line.
    pub message: String,
}

/// True when the test is a known flaky test (F8): `flaky`, or `suspect` with a proven flake.
#[must_use]
pub fn is_known_flaky(failure: &TestFailure) -> bool {
    is_known(&failure.tier, failure.confirmed_flake_count)
}

/// F8's rule on a tier and a proven-flake count.
fn is_known(tier: &str, proven: u32) -> bool {
    tier == "flaky" || (tier == "suspect" && proven >= 1)
}

/// The run and job ids of an Actions check's link.
#[must_use]
pub fn run_and_job(link: &str) -> Option<(u64, u64)> {
    let rest = &link[link.find("/actions/runs/")? + "/actions/runs/".len()..];
    let (run, rest) = rest.split_once("/job/")?;
    let job = rest.split(['/', '?', '#']).next()?;
    Some((run.parse().ok()?, job.parse().ok()?))
}

/// Judges every failing check (F8). `ignore` names aggregator checks, judged by the others.
#[must_use]
pub fn classify(checks: &[Check], lookups: &BTreeMap<u64, RunLookup>, ignore: &[String]) -> Classified {
    let mut judged = Vec::new();
    for check in checks.iter().filter(|c| c.bucket != "pass" && c.bucket != "skipping") {
        judged.push(judge(check, lookups, ignore));
    }
    let aggregator = |j: &Judged| ignore.iter().any(|name| *name == j.name);
    let judged_ones: Vec<&Judged> = judged.iter().filter(|j| !aggregator(j)).collect();
    let known_flaky = !judged_ones.is_empty() && judged_ones.iter().all(|j| j.known_flaky);
    let unknown = !known_flaky
        && !judged_ones.is_empty()
        && judged_ones.iter().all(|j| j.known_flaky || j.reason.as_deref().is_some_and(is_missing_data));
    Classified { known_flaky, unknown, checks: judged }
}

/// The reasons that say FlakeRadar lacks data, not that the failure is real.
const MISSING_DATA: [&str; 4] = [
    NO_REPORT,
    "FlakeRadar is still processing the run's reports",
    "some of the run's reports failed to process",
    "the run has too many failures to judge",
];
/// The reason for a run FlakeRadar has no lookup of.
const NO_REPORT: &str = "FlakeRadar has no report of the run";

/// True when `reason` is one of [`MISSING_DATA`].
fn is_missing_data(reason: &str) -> bool {
    MISSING_DATA.contains(&reason)
}

/// One failing check, judged against its run's lookup.
fn judge(check: &Check, lookups: &BTreeMap<u64, RunLookup>, ignore: &[String]) -> Judged {
    let ids = run_and_job(&check.link);
    let mut out = Judged {
        name: check.name.clone(),
        run: ids.map(|(run, _)| run),
        job: ids.map(|(_, job)| job),
        known_flaky: false,
        reason: None,
        tests: Vec::new(),
    };
    if ignore.iter().any(|name| *name == check.name) {
        out.reason = Some("an aggregator, judged by the checks it waits on".to_string());
        return out;
    }
    let Some((run, job)) = ids else {
        out.reason = Some("not a GitHub Actions job".to_string());
        return out;
    };
    let Some(lookup) = lookups.get(&run) else {
        out.reason = Some(NO_REPORT.to_string());
        return out;
    };
    let reason = if lookup.reports.pending > 0 {
        Some("FlakeRadar is still processing the run's reports")
    } else if lookup.reports.failed > 0 {
        Some("some of the run's reports failed to process")
    } else if lookup.truncated {
        Some("the run has too many failures to judge")
    } else {
        None
    };
    if let Some(reason) = reason {
        out.reason = Some(reason.to_string());
        return out;
    }
    let job_id = job.to_string();
    let failing: Vec<&TestFailure> = lookup
        .failures
        .iter()
        .filter(|f| f.ci_job_id.as_deref() == Some(&job_id) && matches!(f.status.as_str(), "failed" | "error"))
        .collect();
    out.tests = failing
        .iter()
        .map(|f| FlakyFailure {
            name: f.name.clone(),
            project: f.project.clone(),
            tier: f.tier.clone(),
            score: f.flakiness_score,
            proven: f.confirmed_flake_count,
            category: f.failure_category.clone(),
            issue: f.github_issue_number,
            message: one_line(f.message.as_deref().unwrap_or_default()),
        })
        .collect();
    if lookup
        .failed_jobs
        .iter()
        .any(|j| j.ci_job_id.as_deref() == Some(&job_id) && j.outcome != "explained")
    {
        out.reason = Some("the job failed outside its tests".to_string());
        return out;
    }
    if failing.is_empty() {
        out.reason = Some("no failing test is reported for the job".to_string());
        return out;
    }
    if let Some(real) = failing.iter().find(|f| !is_known_flaky(f)) {
        out.reason = Some(if real.tier == "suspect" {
            format!("{} is suspect with no proven flake", real.name)
        } else {
            format!("{} is {}", real.name, real.tier)
        });
        return out;
    }
    out.known_flaky = true;
    out
}

/// F10's `ci_fix.md` section, or empty when no failing test is known flaky.
#[must_use]
pub fn section(out: &Classified) -> String {
    let mut lines = Vec::new();
    for judged in &out.checks {
        for test in judged.tests.iter().filter(|t| is_known(&t.tier, t.proven)) {
            let mut line = format!(
                "- `{}` in `{}`: {}, {} {} (check `{}`)",
                code(&test.name),
                code(&test.project),
                test.tier,
                test.proven,
                if test.proven == 1 { "proven flake" } else { "proven flakes" },
                code(&judged.name)
            );
            if let Some(issue) = test.issue {
                line.push_str(&format!(", issue #{issue}"));
            }
            if !lines.contains(&line) {
                lines.push(line);
            }
        }
    }
    if lines.is_empty() {
        return String::new();
    }
    format!(
        "## Known flaky (FlakeRadar)\n\n{}\n\nThese failures are not caused by this PR: do not change, skip or retry these tests.\n",
        lines.join("\n")
    )
}

/// Text for a code span: one line, backticks replaced.
fn code(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ").replace('`', "'")
}

/// Runs `fabro-io flakeradar <command>`.
#[must_use]
pub fn run_cli(args: &[String]) -> ExitCode {
    match args.first().map(String::as_str) {
        Some("classify") => classify_cli(&args[1..]),
        _ => {
            eprint!("{USAGE}");
            ExitCode::from(2)
        }
    }
}

/// Runs `fabro-io flakeradar classify` (C10). Exit 0 on any well-formed input, 1 when
/// `--checks` cannot be read, 2 on bad arguments.
fn classify_cli(args: &[String]) -> ExitCode {
    let mut flags: BTreeMap<&str, String> = BTreeMap::new();
    let names = ["--checks", "--failures-dir", "--ignore-jobs", "--section"];
    let mut i = 0;
    while i < args.len() {
        let Some(name) = names.iter().find(|name| **name == args[i]) else {
            eprintln!("flakeradar classify: unexpected argument '{}'", args[i]);
            return ExitCode::from(2);
        };
        let Some(value) = args.get(i + 1) else {
            eprintln!("flakeradar classify: {name} needs a value");
            return ExitCode::from(2);
        };
        flags.insert(name, value.clone());
        i += 2;
    }
    let (Some(checks_path), Some(dir)) = (flags.get("--checks"), flags.get("--failures-dir")) else {
        eprint!("{USAGE}");
        return ExitCode::from(2);
    };
    let checks: Vec<Check> = match std::fs::read_to_string(checks_path)
        .map_err(|e| e.to_string())
        .and_then(|text| serde_json::from_str(&text).map_err(|e| e.to_string()))
    {
        Ok(checks) => checks,
        Err(e) => {
            eprintln!("flakeradar classify: cannot read {checks_path}: {e}");
            return ExitCode::FAILURE;
        }
    };
    let ignore: Vec<String> = flags
        .get("--ignore-jobs")
        .and_then(|json| serde_json::from_str(json).ok())
        .unwrap_or_default();
    let dir = PathBuf::from(dir);
    let mut lookups = BTreeMap::new();
    for check in &checks {
        if let Some((run, _)) = run_and_job(&check.link)
            && let Some(lookup) = read_lookup(&dir.join(format!("run_{run}.json")))
        {
            lookups.insert(run, lookup);
        }
    }
    let out = classify(&checks, &lookups, &ignore);
    if let Some(path) = flags.get("--section")
        && let Err(e) = std::fs::write(path, section(&out))
    {
        eprintln!("flakeradar classify: cannot write {path}: {e}");
        return ExitCode::FAILURE;
    }
    match serde_json::to_string(&out) {
        Ok(json) => {
            println!("{json}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("flakeradar classify: cannot serialize the result: {e}");
            ExitCode::FAILURE
        }
    }
}

/// One lookup file, or `None` when it is missing or not a lookup: no report, F7's fail-toward.
fn read_lookup(path: &Path) -> Option<RunLookup> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}
