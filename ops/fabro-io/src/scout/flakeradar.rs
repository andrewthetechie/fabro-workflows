//! `scout flakeradar`: FlakeRadar's view of a repository, as scout candidates (ADR 0028 C4b).
//!
//! The graph fetches; this only reads files. It picks the flaky-tier tests worth an issue (F2),
//! in the order the plan files them (F5), with each one's recent failures. It also finds the
//! Flaky job occurrences FlakeRadar explains with a failing test (F4): their flake is that
//! test's, which is filed on its own, so the plan does not count them against the job.
//!
//! FlakeRadar's timestamps carry microseconds. Every scout contract compares GitHub's
//! `YYYY-MM-DDTHH:MM:SSZ` as a string, so each one is normalised here, and one that does not
//! fit is an invalid input.

// Rust guideline compliant 2026-07-21

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::{Deserialize, Serialize};

use super::config::ConfigOut;
use super::flaky::FlakyOut;
use super::{is_github_ts, slug, text};

/// The most failures one candidate carries (C4b).
const MAX_FAILURES: usize = 3;
/// The lines a traceback keeps from each end (C4b): pytest puts the error last, vitest and
/// Playwright put it first.
const TRACE_END_LINES: usize = 25;
/// The characters a traceback keeps from each end (C4b).
const TRACE_END_CHARS: usize = 2000;
/// The most characters a failure's one-line message keeps (C4b).
const MAX_MESSAGE_CHARS: usize = 300;

/// One `/api/tests` item, with the fields C4b reads.
#[derive(Clone, Debug, Deserialize)]
pub struct TestItem {
    /// FlakeRadar's test id: the history file's key and the dashboard link's `test`.
    pub id: u64,
    /// The test suite's project in its repository, such as `backend`.
    pub project: String,
    /// FlakeRadar's stable identity of the test (40 hex).
    pub fingerprint: String,
    /// The JUnit suite.
    #[serde(default)]
    pub suite: Option<String>,
    /// The JUnit classname.
    #[serde(default)]
    pub classname: Option<String>,
    /// The test's name.
    pub name: String,
    /// The test's file, when the runner reports one.
    #[serde(default)]
    pub file: Option<String>,
    /// The test's line, when the runner reports one.
    #[serde(default)]
    pub line: Option<i64>,
    /// FlakeRadar's score, 0 to 1.
    pub flakiness_score: f64,
    /// `flaky`, `suspect` or `stable`.
    pub tier: String,
    /// How many commits both failed and passed the test.
    #[serde(default)]
    pub confirmed_flake_count: u32,
    /// FlakeRadar's guess at the cause, such as `timing`.
    #[serde(default)]
    pub failure_category: Option<String>,
    /// When the test last ran, with microseconds.
    pub last_seen_at: String,
    /// Whether the test runner skips it.
    #[serde(default)]
    pub quarantined: bool,
}

/// One `/api/tests/{id}/history` response, with the fields C4b reads.
#[derive(Clone, Debug, Deserialize)]
pub struct History {
    /// The executions, newest first.
    #[serde(default)]
    pub executions: Vec<Execution>,
    /// The commit of the newest failure.
    #[serde(default)]
    pub last_failing_sha: Option<String>,
    /// The branch of the newest failure.
    #[serde(default)]
    pub last_failing_branch: Option<String>,
}

/// One execution of one test.
#[derive(Clone, Debug, Deserialize)]
pub struct Execution {
    /// `passed`, `failed`, `error` or `skipped`.
    pub status: String,
    /// The failure's one-line message.
    #[serde(default)]
    pub message: Option<String>,
    /// The failure's traceback, often empty.
    #[serde(default)]
    pub details: Option<String>,
    /// When it ran, with microseconds.
    pub created_at: String,
    /// The branch it ran on.
    #[serde(default)]
    pub branch: Option<String>,
    /// The GitHub Actions run it ran in.
    #[serde(default)]
    pub ci_run_id: Option<String>,
}

/// One CI-run lookup (flakeradar#5), with the fields C4b reads.
#[derive(Clone, Debug, Deserialize)]
pub struct RunFailures {
    /// The failed jobs of that attempt.
    #[serde(default)]
    pub failed_jobs: Vec<FailedJob>,
}

/// One failed job of one CI run attempt.
#[derive(Clone, Debug, Deserialize)]
pub struct FailedJob {
    /// GitHub's job id, as a string.
    #[serde(default)]
    pub ci_job_id: Option<String>,
    /// `explained` when a failing test in the job explains its failure.
    #[serde(default)]
    pub outcome: String,
}

/// C4b's output.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FlakeradarOut {
    /// The candidates, in F5's order.
    pub tests: Vec<FlakyTest>,
    /// The Flaky job occurrences FlakeRadar explains, by failed job id, ascending (F4).
    pub explained: Vec<u64>,
}

/// One flaky test worth an issue (F2).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FlakyTest {
    /// The scout fingerprint: `flaky:test-<slug(project)>:<fingerprint>` (F3).
    pub fp: String,
    /// FlakeRadar's test id.
    pub id: u64,
    /// The test's page on the FlakeRadar dashboard.
    pub url: String,
    /// The project.
    pub project: String,
    /// The JUnit suite.
    pub suite: Option<String>,
    /// The JUnit classname.
    pub classname: Option<String>,
    /// The test's name.
    pub name: String,
    /// The test's file, when known.
    pub file: Option<String>,
    /// The test's line, when known.
    pub line: Option<i64>,
    /// FlakeRadar's score.
    pub score: f64,
    /// The proven flakes.
    pub proven: u32,
    /// FlakeRadar's guess at the cause.
    pub category: Option<String>,
    /// When the test last ran, normalised.
    pub last_seen_at: String,
    /// The commit of the newest failure.
    pub last_failing_sha: Option<String>,
    /// The branch of the newest failure.
    pub last_failing_branch: Option<String>,
    /// The newest failures, newest first.
    pub failures: Vec<Failure>,
}

/// One failure of a flaky test.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Failure {
    /// When it failed, normalised.
    pub created_at: String,
    /// The failure's one-line message, cut to 300 characters.
    pub message: String,
    /// The traceback, its middle cut when it is long; empty when FlakeRadar has none.
    pub details: String,
    /// The branch it failed on.
    pub branch: Option<String>,
    /// The GitHub Actions run it failed in.
    pub ci_run_id: Option<String>,
}

/// Everything the digest reads.
pub struct Inputs<'a> {
    /// Every `/api/tests` item.
    pub tests: &'a [TestItem],
    /// The histories the graph fetched, by test id.
    pub histories: &'a BTreeMap<u64, History>,
    /// The CI-run lookups the graph fetched, by run id and attempt.
    pub runs: &'a BTreeMap<(u64, u64), RunFailures>,
    /// C4's output.
    pub flaky: &'a FlakyOut,
    /// C2's output.
    pub config: &'a ConfigOut,
    /// The window start, GitHub's form.
    pub since: &'a str,
    /// FlakeRadar's base URL, for the dashboard links.
    pub base: &'a str,
    /// `owner/name`.
    pub repo: &'a str,
}

/// FlakeRadar's timestamp in GitHub's `YYYY-MM-DDTHH:MM:SSZ`, or `None` when it is not UTC
/// in ISO 8601 form.
#[must_use]
pub fn normalise_ts(ts: &str) -> Option<String> {
    let head = ts.get(..19)?;
    let rest = &ts[19..];
    let candidate = format!("{head}Z");
    if !is_github_ts(&candidate) {
        return None;
    }
    let rest = rest
        .strip_prefix('.')
        .map_or(rest, |frac| frac.trim_start_matches(|c: char| c.is_ascii_digit()));
    matches!(rest, "Z" | "+00:00").then_some(candidate)
}

/// True when the test is an F2 candidate (the history file aside).
#[must_use]
pub fn is_candidate(test: &TestItem, config: &ConfigOut, last_seen: &str, since: &str) -> bool {
    test.tier == "flaky"
        && !test.quarantined
        && test.confirmed_flake_count >= config.flakeradar.min_proven
        && last_seen >= since
}

/// The F3 fingerprint of one test.
#[must_use]
pub fn test_fp(project: &str, fingerprint: &str) -> String {
    format!("flaky:test-{}:{}", slug(project), slug(fingerprint))
}

/// Applies F2, F4 and F5 to the fetched files (C4b).
///
/// # Errors
///
/// Returns the first timestamp that is not FlakeRadar's UTC form, naming it.
pub fn digest(input: &Inputs<'_>) -> Result<FlakeradarOut, String> {
    let mut tests = Vec::new();
    for test in input.tests {
        let last_seen = normalise(&test.last_seen_at, test.id)?;
        if !is_candidate(test, input.config, &last_seen, input.since) {
            continue;
        }
        let Some(history) = input.histories.get(&test.id) else {
            continue;
        };
        let mut failures = Vec::new();
        for execution in &history.executions {
            if !matches!(execution.status.as_str(), "failed" | "error") {
                continue;
            }
            failures.push(Failure {
                created_at: normalise(&execution.created_at, test.id)?,
                message: one_line(execution.message.as_deref().unwrap_or_default()),
                details: traceback(execution.details.as_deref().unwrap_or_default()),
                branch: execution.branch.clone(),
                ci_run_id: execution.ci_run_id.clone(),
            });
        }
        failures.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        failures.truncate(MAX_FAILURES);
        tests.push(FlakyTest {
            fp: test_fp(&test.project, &test.fingerprint),
            id: test.id,
            url: format!(
                "{}/?repo={}&project={}&test={}",
                input.base.trim_end_matches('/'),
                input.repo,
                test.project,
                test.id
            ),
            project: test.project.clone(),
            suite: test.suite.clone(),
            classname: test.classname.clone(),
            name: test.name.clone(),
            file: test.file.clone(),
            line: test.line,
            score: test.flakiness_score,
            proven: test.confirmed_flake_count,
            category: test.failure_category.clone(),
            last_seen_at: last_seen,
            last_failing_sha: history.last_failing_sha.clone(),
            last_failing_branch: history.last_failing_branch.clone(),
            failures,
        });
    }
    tests.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then_with(|| b.proven.cmp(&a.proven))
            .then_with(|| a.fp.cmp(&b.fp))
    });
    let mut explained = BTreeSet::new();
    for job in &input.flaky.jobs {
        for occurrence in &job.occurrences {
            let Some(lookup) = input.runs.get(&(occurrence.run_id, occurrence.attempt)) else {
                continue;
            };
            let id = occurrence.failed_job_id.to_string();
            if lookup
                .failed_jobs
                .iter()
                .any(|failed| failed.ci_job_id.as_deref() == Some(&id) && failed.outcome == "explained")
            {
                explained.insert(occurrence.failed_job_id);
            }
        }
    }
    Ok(FlakeradarOut { tests, explained: explained.into_iter().collect() })
}

/// One timestamp normalised, or the error that names it.
fn normalise(ts: &str, test_id: u64) -> Result<String, String> {
    normalise_ts(ts).ok_or_else(|| {
        format!("test {test_id}: timestamp must be ISO 8601 UTC, found \"{ts}\"")
    })
}

/// A message as one line with its colour codes removed, cut to C4b's size.
fn one_line(raw: &str) -> String {
    let clean = strip_sgr(&text::strip_ansi(raw));
    let line = clean.split_whitespace().collect::<Vec<_>>().join(" ");
    line.chars().take(MAX_MESSAGE_CHARS).collect()
}

/// A traceback with its colour codes removed, keeping each end and naming the cut middle.
fn traceback(raw: &str) -> String {
    let clean = strip_sgr(&text::strip_ansi(raw));
    let lines: Vec<&str> = clean.trim_end().split('\n').collect();
    if clean.trim().is_empty() {
        return String::new();
    }
    let (head, tail, omitted) = if lines.len() > 2 * TRACE_END_LINES {
        let tail_start = lines.len() - TRACE_END_LINES;
        (lines[..TRACE_END_LINES].join("\n"), lines[tail_start..].join("\n"), tail_start - TRACE_END_LINES)
    } else {
        (lines.join("\n"), String::new(), 0)
    };
    let head = first_chars(&head, TRACE_END_CHARS);
    let tail = last_chars(&tail, TRACE_END_CHARS);
    if omitted == 0 && tail.is_empty() {
        head
    } else {
        format!("{head}\n... ({omitted} lines cut) ...\n{tail}")
    }
}

/// The first `limit` characters of `text`.
fn first_chars(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// The last `limit` characters of `text`.
fn last_chars(text: &str, limit: usize) -> String {
    let count = text.chars().count();
    text.chars().skip(count.saturating_sub(limit)).collect()
}

/// Removes `[<digits and ;>m` SGR sequences whose `ESC` byte was already dropped.
fn strip_sgr(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '[' {
            let mut j = i + 1;
            while j < chars.len() && (chars[j].is_ascii_digit() || chars[j] == ';') {
                j += 1;
            }
            if j > i + 1 && j < chars.len() && chars[j] == 'm' {
                i = j + 1;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// Runs `scout flakeradar` (C4b).
#[must_use]
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut flags: BTreeMap<&str, String> = BTreeMap::new();
    let names = [
        "--tests",
        "--history-dir",
        "--runs-dir",
        "--flaky",
        "--config",
        "--since",
        "--base",
        "--repo",
    ];
    let mut i = 0;
    while i < args.len() {
        let Some(name) = names.iter().find(|name| **name == args[i]) else {
            eprintln!("scout flakeradar: unexpected argument '{}'", args[i]);
            return ExitCode::from(2);
        };
        i += 1;
        let Some(value) = args.get(i) else {
            eprintln!("scout flakeradar: {name} needs a value");
            return ExitCode::from(2);
        };
        flags.insert(name, value.clone());
        i += 1;
    }
    if let Some(missing) = names.iter().find(|name| !flags.contains_key(**name)) {
        eprintln!("scout flakeradar: {missing} is required");
        return ExitCode::from(2);
    }
    let since = &flags["--since"];
    if !is_github_ts(since) {
        eprintln!("scout flakeradar: --since must be YYYY-MM-DDTHH:MM:SSZ, found \"{since}\"");
        return ExitCode::FAILURE;
    }
    match load_and_digest(&flags) {
        Ok(out) => match serde_json::to_string(&out) {
            Ok(json) => {
                println!("{json}");
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("scout flakeradar: cannot serialize the result: {e}");
                ExitCode::FAILURE
            }
        },
        Err(message) => {
            eprintln!("scout flakeradar: {message}");
            ExitCode::FAILURE
        }
    }
}

/// Reads every file the flags name, then digests them.
fn load_and_digest(flags: &BTreeMap<&str, String>) -> Result<FlakeradarOut, String> {
    let tests: Vec<TestItem> = read_json(Path::new(&flags["--tests"]))?;
    let flaky: FlakyOut = read_json(Path::new(&flags["--flaky"]))?;
    let config: ConfigOut = read_json(Path::new(&flags["--config"]))?;
    let history_dir = PathBuf::from(&flags["--history-dir"]);
    let mut histories = BTreeMap::new();
    for test in &tests {
        let path = history_dir.join(format!("test_{}.json", test.id));
        if path.exists() {
            histories.insert(test.id, read_json::<History>(&path)?);
        }
    }
    let runs_dir = PathBuf::from(&flags["--runs-dir"]);
    let mut runs = BTreeMap::new();
    for occurrence in flaky.jobs.iter().flat_map(|job| &job.occurrences) {
        let key = (occurrence.run_id, occurrence.attempt);
        let path = runs_dir.join(format!("run_{}_{}.json", key.0, key.1));
        if !runs.contains_key(&key) && path.exists() {
            runs.insert(key, read_json::<RunFailures>(&path)?);
        }
    }
    digest(&Inputs {
        tests: &tests,
        histories: &histories,
        runs: &runs,
        flaky: &flaky,
        config: &config,
        since: &flags["--since"],
        base: &flags["--base"],
        repo: &flags["--repo"],
    })
}

/// Reads one input file as JSON of the expected shape.
fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{} is not valid JSON: {e}", path.display()))
}
