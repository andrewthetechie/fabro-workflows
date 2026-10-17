//! `scout plan`: what one run would do, and each issue's title and body (ADR 0027 C5 to C7).
//!
//! The plan is pure and does not change with `filing`: `filing` only decides whether the
//! graph writes. An open issue for a fingerprint is `known`; one closed as not planned or
//! duplicate is `suppressed` forever; any other close lets it come back as a regression.
//! GitHub is the only store: the markers in the issue bodies are the whole state (D6, D8).
//!
//! In a repository FlakeRadar covers (ADR 0028), the flaky source also files FlakeRadar's
//! flaky tests, ahead of the jobs and under the same caps, and a job is judged only on the
//! flakes no failing test explains. A failed FlakeRadar read plans no flaky action at all.

// Rust guideline compliant 2026-07-21

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::{Deserialize, Serialize};

use super::config::ConfigOut;
use super::flakeradar::{FlakeradarOut, FlakyTest};
use super::ratchet::{EntryOut, RatchetOut};
use super::text;
use super::{flaky::FlakyOut, is_fingerprint, is_github_ts};

/// The most findings a Ratchet rule may have and still be filed (D3).
pub const MAX_FINDINGS: usize = 15;
/// The most files a Ratchet rule may touch and still be filed (D3).
pub const MAX_FILES: usize = 8;
/// The most issues one source files or regresses in one run (D9).
pub const PER_RUN_CAP: usize = 3;
/// The most open issues of one source before it stops filing (D9).
pub const MAX_OPEN: usize = 3;

/// The labels a Ratchet rule issue carries (D10).
const RATCHET_LABELS: [&str; 5] = ["agent", "scout", "ai-generated", "bug", "fast-path"];
/// The labels a Flaky job issue carries (D10).
const FLAKY_LABELS: [&str; 4] = ["agent", "scout", "ai-generated", "flaky-test"];
/// The literal that opens a scout marker in an issue body (D6).
const MARKER_OPEN: &str = "<!-- fabro:scout fp=";
/// The literal that closes it.
const MARKER_CLOSE: &str = " -->";

/// One `scout` issue, as `gh api` returns it (C5).
#[derive(Clone, Debug, Deserialize)]
pub struct Issue {
    /// The issue number.
    pub number: u64,
    /// `open` or `closed`.
    pub state: String,
    /// `not_planned`, `duplicate`, `completed` or null (D8).
    #[serde(default)]
    pub state_reason: Option<String>,
    /// When it was closed, in GitHub's UTC form.
    #[serde(default)]
    pub closed_at: Option<String>,
    /// Its body, which holds the markers. GitHub sends `null` for an empty body, and any
    /// issue a human labels `scout` is listed, so `null` must read as empty, not fail.
    #[serde(default, deserialize_with = "null_as_empty")]
    pub body: String,
}

/// Reads a JSON string, or `null`, as a `String` (`null` becomes empty).
fn null_as_empty<'de, D: serde::Deserializer<'de>>(deserializer: D) -> Result<String, D::Error> {
    Ok(Option::<String>::deserialize(deserializer)?.unwrap_or_default())
}

/// What FlakeRadar contributes to one plan (ADR 0028 C5 amendment).
#[derive(Clone, Copy, Debug)]
pub enum Radar<'a> {
    /// No FlakeRadar input: the plan is the ADR 0027 one.
    Off,
    /// The repository is covered and the read failed, for this reason (F7).
    Skipped(&'a str),
    /// The repository is covered and this is C4b's output.
    Read(&'a FlakeradarOut),
}

/// Everything the plan reads.
pub struct PlanInput<'a> {
    /// The `.fabro/scout.toml` output (C2).
    pub config: &'a ConfigOut,
    /// The Ratchet source's output (C3), possibly a whole-source skip.
    pub ratchet: &'a RatchetOut,
    /// The Flaky source's output (C4).
    pub flaky: &'a FlakyOut,
    /// Every `scout` issue in the repository, any state.
    pub issues: &'a [Issue],
    /// Where the flaky log tails live.
    pub logs_dir: &'a Path,
    /// `owner/name`.
    pub repo: &'a str,
    /// The head commit, of which the body shows the first 8 characters.
    pub sha: &'a str,
    /// FlakeRadar's state. It is ignored unless the config says the repository is covered.
    pub flakeradar: Radar<'a>,
}

/// C5's output.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlanOut {
    /// Whether this repository files issues (D14).
    pub filing: bool,
    /// `owner/name`.
    pub repo: String,
    /// Each source's state: `ok`, `absent` or `skipped: <reason>`.
    pub sources: Sources,
    /// The actions, in D18's order.
    pub actions: Vec<Action>,
}

/// The two sources' states (C5).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Sources {
    /// `ok`, `absent`, or `skipped: <reason>`.
    pub ratchet: String,
    /// `ok`, or `skipped: flakeradar <reason>` (ADR 0028 F7).
    pub flaky: String,
}

/// One thing the run would do (C5).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Action {
    /// `file`, `regression`, `known`, `suppressed`, `too_big`, `backpressure`, `capped`,
    /// `error` or `explained`.
    pub kind: String,
    /// `ratchet` or `flaky`.
    pub source: String,
    /// The fingerprint the action is about.
    pub fp: String,
    /// The issue the action names: the open one for `known`, the closed one for
    /// `regression` and `suppressed`.
    pub issue: Option<u64>,
    /// The issue title, for `file` and `regression` only.
    pub title: Option<String>,
    /// The issue body, for `file` and `regression` only.
    pub body: Option<String>,
    /// The issue labels, for `file` and `regression` only.
    pub labels: Option<Vec<String>>,
    /// `N findings in M files` for a Ratchet rule, the entry's error, or `N flakes in D
    /// days` for a Flaky job.
    pub detail: String,
    /// The `<tool> <rule>` or `<job>` that [`text::summary`] prints. C5's shape does not
    /// carry either, and the summary has nothing else to read them from.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subject: Option<String>,
}

/// Every fingerprint an issue body carries, in order, invalid ones dropped (C5).
#[must_use]
pub fn markers(body: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut rest = body;
    while let Some(start) = rest.find(MARKER_OPEN) {
        let after = &rest[start + MARKER_OPEN.len()..];
        let Some(end) = after.find(MARKER_CLOSE) else {
            break;
        };
        let fp = &after[..end];
        if is_fingerprint(fp) {
            out.push(fp.to_string());
        }
        rest = &after[end + MARKER_CLOSE.len()..];
    }
    out
}

/// The issues per fingerprint, each list highest number first (C5).
#[must_use]
pub fn known(issues: &[Issue]) -> BTreeMap<String, Vec<&Issue>> {
    let mut map: BTreeMap<String, Vec<&Issue>> = BTreeMap::new();
    for issue in issues {
        for fp in markers(&issue.body) {
            map.entry(fp).or_default().push(issue);
        }
    }
    for list in map.values_mut() {
        list.sort_by(|a, b| b.number.cmp(&a.number));
    }
    map
}

/// Decides every candidate's action and writes each new issue's title and body (C5).
#[must_use]
pub fn plan(input: &PlanInput<'_>) -> PlanOut {
    let known = known(input.issues);
    let mut open: BTreeMap<&str, usize> = BTreeMap::new();
    for issue in input.issues.iter().filter(|issue| issue.state == "open") {
        for source in sources_of(issue) {
            *open.entry(source).or_default() += 1;
        }
    }
    let mut planned: BTreeMap<&str, usize> = BTreeMap::new();
    let mut actions: Vec<Action> = Vec::new();
    let sha8 = &input.sha[..8.min(input.sha.len())];

    for entry in &input.ratchet.entries {
        if entry.status == "error" {
            actions.push(plain(
                "error",
                "ratchet",
                entry,
                None,
                &ratchet_detail(entry),
            ));
            continue;
        }
        if entry.status != "ok" || entry.count == 0 {
            continue;
        }
        let detail = ratchet_detail(entry);
        let list = known.get(&entry.fp);
        let Some(open_issue) = list.and_then(|list| list.iter().find(|i| i.state == "open"))
        else {
            let closed =
                list.and_then(|list| list.iter().find(|issue| issue.state != "open").copied());
            let regression = closed.filter(|issue| !suppresses(issue));
            if let Some(issue) = closed.filter(|issue| suppresses(issue)) {
                actions.push(plain("suppressed", "ratchet", entry, Some(issue.number), &detail));
                continue;
            }
            if entry.count > MAX_FINDINGS || entry.files > MAX_FILES {
                actions.push(plain("too_big", "ratchet", entry, None, &detail));
                continue;
            }
            let state = censored("ratchet", &open, &planned);
            if let Some(state) = state {
                actions.push(plain(state, "ratchet", entry, None, &detail));
                continue;
            }
            *planned.entry("ratchet").or_default() += 1;
            let kind = if regression.is_some() { "regression" } else { "file" };
            let labels = RATCHET_LABELS.iter().map(|l| (*l).to_string()).collect();
            actions.push(with_body(
                kind,
                "ratchet",
                entry,
                regression,
                &detail,
                text::ratchet_title(entry, &detail, regression),
                text::ratchet_body(entry, &detail, sha8, regression),
                labels,
            ));
            continue;
        };
        actions.push(plain("known", "ratchet", entry, Some(open_issue.number), &detail));
    }

    let radar = if input.config.flakeradar.covered { input.flakeradar } else { Radar::Off };
    let mut explained: BTreeSet<u64> = BTreeSet::new();
    match radar {
        Radar::Off => {}
        Radar::Skipped(reason) => {
            return PlanOut {
                filing: input.config.filing,
                repo: input.repo.to_string(),
                sources: Sources {
                    ratchet: ratchet_source(input),
                    flaky: format!("skipped: flakeradar {reason}"),
                },
                actions,
            };
        }
        Radar::Read(out) => {
            explained.extend(out.explained.iter().copied());
            for test in &out.tests {
                if let Some(action) = test_action(test, &known, &open, &mut planned) {
                    actions.push(action);
                }
            }
        }
    }

    for job in {
        let mut jobs: Vec<&super::flaky::FlakyJob> = input.flaky.jobs.iter().collect();
        jobs.sort_by(|a, b| b.count.cmp(&a.count).then_with(|| a.fp.cmp(&b.fp)));
        jobs
    } {
        if input.config.flaky.ignore_jobs.iter().any(|name| *name == job.job) {
            continue;
        }
        let list = known.get(&job.fp);
        let open_issue = list.and_then(|list| list.iter().find(|i| i.state == "open").copied());
        let closed = if open_issue.is_none() {
            list.and_then(|list| list.iter().find(|issue| issue.state != "open").copied())
        } else {
            None
        };
        let (effective, occurrences) = match closed.filter(|issue| !suppresses(issue)) {
            Some(issue) => {
                let cut = issue.closed_at.as_deref().unwrap_or("0000");
                let after: Vec<&super::flaky::Occurrence> = job
                    .occurrences
                    .iter()
                    .filter(|occurrence| occurrence.created_at.as_str() > cut)
                    .collect();
                let count = after.len();
                (count, after)
            }
            None => (job.count, job.occurrences.iter().collect()),
        };
        if effective < input.config.flaky.min_flakes as usize {
            continue;
        }
        let detail = flaky_detail(effective, input.config.flaky.window_days);
        if let Some(issue) = open_issue {
            actions.push(plain_flaky("known", job, Some(issue.number), &detail));
            continue;
        }
        if let Some(issue) = closed.filter(|issue| suppresses(issue)) {
            actions.push(plain_flaky("suppressed", job, Some(issue.number), &detail));
            continue;
        }
        // F4: an occurrence whose failed job FlakeRadar explains with a failing test is that
        // test's flake. With no FlakeRadar input the set is empty and nothing changes here.
        let unexplained: Vec<&super::flaky::Occurrence> = occurrences
            .iter()
            .copied()
            .filter(|occurrence| !explained.contains(&occurrence.failed_job_id))
            .collect();
        if unexplained.len() < input.config.flaky.min_flakes as usize {
            let detail = format!(
                "{} of {} explained by FlakeRadar tests",
                effective - unexplained.len(),
                plural(effective, "flake")
            );
            actions.push(plain_flaky("explained", job, None, &detail));
            continue;
        }
        let detail = flaky_detail(unexplained.len(), input.config.flaky.window_days);
        let occurrences = unexplained;
        let state = censored("flaky", &open, &planned);
        if let Some(state) = state {
            actions.push(plain_flaky(state, job, None, &detail));
            continue;
        }
        *planned.entry("flaky").or_default() += 1;
        let kind = if closed.is_some() { "regression" } else { "file" };
        let labels = FLAKY_LABELS.iter().map(|l| (*l).to_string()).collect();
        actions.push(Action {
            kind: kind.to_string(),
            source: "flaky".to_string(),
            fp: job.fp.clone(),
            issue: closed.map(|issue| issue.number),
            title: Some(text::flaky_title(job, &detail, closed)),
            body: Some(text::flaky_body(
                job,
                &occurrences,
                input.config.flaky.window_days,
                input.logs_dir,
                closed,
            )),
            labels: Some(labels),
            detail,
            subject: Some(job.job.clone()),
        });
    }

    PlanOut {
        filing: input.config.filing,
        repo: input.repo.to_string(),
        sources: Sources {
            ratchet: ratchet_source(input),
            flaky: "ok".to_string(),
        },
        actions,
    }
}

/// The action for one FlakeRadar test candidate, or `None` when it has nothing to do (C5
/// amendment, F6).
fn test_action<'a>(
    test: &FlakyTest,
    known: &BTreeMap<String, Vec<&Issue>>,
    open: &BTreeMap<&'a str, usize>,
    planned: &mut BTreeMap<&'a str, usize>,
) -> Option<Action> {
    let detail = test_detail(test);
    let list = known.get(&test.fp);
    let action = |kind: &str, issue: Option<u64>| Action {
        kind: kind.to_string(),
        source: "flaky".to_string(),
        fp: test.fp.clone(),
        issue,
        title: None,
        body: None,
        labels: None,
        detail: detail.clone(),
        subject: Some(text::test_subject(test)),
    };
    if let Some(issue) = list.and_then(|list| list.iter().find(|i| i.state == "open")) {
        return Some(action("known", Some(issue.number)));
    }
    let closed = list.and_then(|list| list.iter().find(|issue| issue.state != "open").copied());
    if let Some(issue) = closed.filter(|issue| suppresses(issue)) {
        return Some(action("suppressed", Some(issue.number)));
    }
    if let Some(issue) = closed {
        // F6: a closed issue comes back only for a failure after it was closed.
        let cut = issue.closed_at.as_deref().unwrap_or("0000");
        let newest = test.failures.first().map(|failure| failure.created_at.as_str());
        if !newest.is_some_and(|newest| newest > cut) {
            return None;
        }
    }
    if let Some(state) = censored("flaky", open, planned) {
        return Some(action(state, None));
    }
    *planned.entry("flaky").or_default() += 1;
    let mut out = action(if closed.is_some() { "regression" } else { "file" }, closed.map(|i| i.number));
    out.title = Some(text::test_title(test, &detail, closed));
    out.body = Some(text::test_body(test, closed));
    out.labels = Some(FLAKY_LABELS.iter().map(|l| (*l).to_string()).collect());
    Some(out)
}

/// `score 0.70, 2 proven flakes` (the C5 amendment's detail for a test).
fn test_detail(test: &FlakyTest) -> String {
    format!("score {:.2}, {}", test.score, plural(test.proven as usize, "proven flake"))
}

/// `capped` or `backpressure` when `source` has reached a limit, else `None` (C5 steps 5, 6).
///
/// The cap is checked first: with `PER_RUN_CAP == MAX_OPEN == 3` and no open issues, a
/// candidate at the cap would otherwise read as `backpressure` and `capped` would be
/// unreachable, which is not what C5's own examples expect.
fn censored<'a>(
    source: &str,
    open: &BTreeMap<&'a str, usize>,
    planned: &BTreeMap<&'a str, usize>,
) -> Option<&'static str> {
    let open = open.get(source).copied().unwrap_or(0);
    let planned = planned.get(source).copied().unwrap_or(0);
    if planned >= PER_RUN_CAP {
        Some("capped")
    } else if open + planned >= MAX_OPEN {
        Some("backpressure")
    } else {
        None
    }
}

/// The C5 state of the Ratchet source.
fn ratchet_source(input: &PlanInput<'_>) -> String {
    if !input.config.valid {
        let error = input.config.error.clone().unwrap_or_default();
        return format!("skipped: scout.toml is invalid: {error}");
    }
    if input.ratchet.status != "ok" {
        let reason = input.ratchet.reason.clone().unwrap_or_default();
        return format!("skipped: {reason}");
    }
    if input.config.ratchet.is_empty() {
        "absent".to_string()
    } else {
        "ok".to_string()
    }
}

/// The sources an issue's markers name (C5's open count).
fn sources_of(issue: &Issue) -> BTreeSet<&'static str> {
    let mut set = BTreeSet::new();
    for fp in markers(&issue.body) {
        if fp.starts_with("ratchet:") {
            set.insert("ratchet");
        } else if fp.starts_with("flaky:") {
            set.insert("flaky");
        }
    }
    set
}

/// True when the issue's close means "do not file this again" (D8).
fn suppresses(issue: &Issue) -> bool {
    matches!(issue.state_reason.as_deref(), Some("not_planned" | "duplicate"))
}

/// `N findings in M files`, or the entry's error (C5's detail).
fn ratchet_detail(entry: &EntryOut) -> String {
    if entry.status == "error" {
        return entry.error.clone().unwrap_or_default();
    }
    format!("{} in {}", plural(entry.count, "finding"), plural(entry.files, "file"))
}

/// `N flakes in D days` (C5's detail).
fn flaky_detail(count: usize, window_days: u32) -> String {
    format!("{} in {window_days} days", plural(count, "flake"))
}

/// `1 finding`, `2 findings`.
fn plural(count: usize, word: &str) -> String {
    if count == 1 { format!("1 {word}") } else { format!("{count} {word}s") }
}

/// An action without a title, body or labels (C5).
fn plain(
    kind: &str,
    source: &str,
    entry: &EntryOut,
    issue: Option<u64>,
    detail: &str,
) -> Action {
    Action {
        kind: kind.to_string(),
        source: source.to_string(),
        fp: entry.fp.clone(),
        issue,
        title: None,
        body: None,
        labels: None,
        detail: detail.to_string(),
        subject: Some(format!("{} {}", entry.tool, entry.rule)),
    }
}

/// A Flaky action without a title, body or labels.
fn plain_flaky(
    kind: &str,
    job: &super::flaky::FlakyJob,
    issue: Option<u64>,
    detail: &str,
) -> Action {
    Action {
        kind: kind.to_string(),
        source: "flaky".to_string(),
        fp: job.fp.clone(),
        issue,
        title: None,
        body: None,
        labels: None,
        detail: detail.to_string(),
        subject: Some(job.job.clone()),
    }
}

/// An action that files or regresses a Ratchet rule (C5 step 7).
fn with_body(
    kind: &str,
    source: &str,
    entry: &EntryOut,
    regression: Option<&Issue>,
    detail: &str,
    title: String,
    body: String,
    labels: Vec<String>,
) -> Action {
    Action {
        kind: kind.to_string(),
        source: source.to_string(),
        fp: entry.fp.clone(),
        issue: regression.map(|issue| issue.number),
        title: Some(title),
        body: Some(body),
        labels: Some(labels),
        detail: detail.to_string(),
        subject: Some(format!("{} {}", entry.tool, entry.rule)),
    }
}

/// Runs `scout plan` (C5).
#[must_use]
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut config_arg: Option<PathBuf> = None;
    let mut ratchet_arg: Option<PathBuf> = None;
    let mut flaky_arg: Option<PathBuf> = None;
    let mut issues_arg: Option<PathBuf> = None;
    let mut logs_dir = PathBuf::from(".");
    let mut repo: Option<String> = None;
    let mut sha: Option<String> = None;
    let mut radar_arg: Option<PathBuf> = None;
    let mut radar_skipped: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--config" | "--ratchet" | "--flaky" | "--issues" | "--logs-dir" | "--repo"
            | "--sha" | "--flakeradar" | "--flakeradar-skipped" => {
                let flag = args[i].clone();
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("scout plan: {flag} needs a value");
                    return ExitCode::from(2);
                };
                match flag.as_str() {
                    "--config" => config_arg = Some(PathBuf::from(value)),
                    "--ratchet" => ratchet_arg = Some(PathBuf::from(value)),
                    "--flaky" => flaky_arg = Some(PathBuf::from(value)),
                    "--issues" => issues_arg = Some(PathBuf::from(value)),
                    "--logs-dir" => logs_dir = PathBuf::from(value),
                    "--repo" => repo = Some(value.clone()),
                    "--flakeradar" => radar_arg = Some(PathBuf::from(value)),
                    "--flakeradar-skipped" => radar_skipped = Some(value.clone()),
                    _ => sha = Some(value.clone()),
                }
            }
            other => {
                eprintln!("scout plan: unexpected argument '{other}'");
                return ExitCode::from(2);
            }
        }
        i += 1;
    }
    let (Some(config_arg), Some(ratchet_arg), Some(flaky_arg), Some(issues_arg), Some(repo), Some(sha)) =
        (config_arg, ratchet_arg, flaky_arg, issues_arg, repo, sha)
    else {
        eprintln!(
            "scout plan: --config, --ratchet, --flaky, --issues, --repo and --sha are required"
        );
        return ExitCode::from(2);
    };
    if repo.split('/').count() != 2 || repo.starts_with('/') || repo.ends_with('/') {
        eprintln!("scout plan: --repo must be owner/name, found \"{repo}\"");
        return ExitCode::from(2);
    }
    if sha.len() < 8 || !sha.bytes().all(|b| b.is_ascii_hexdigit()) {
        eprintln!("scout plan: --sha must be at least 8 hex characters, found \"{sha}\"");
        return ExitCode::from(2);
    }
    if radar_arg.is_some() && radar_skipped.is_some() {
        eprintln!("scout plan: --flakeradar and --flakeradar-skipped exclude each other");
        return ExitCode::from(2);
    }
    let radar_out: Option<FlakeradarOut> = match radar_arg.as_deref().map(read_json) {
        None => None,
        Some(Ok(value)) => Some(value),
        Some(Err(message)) => {
            eprintln!("scout plan: {message}");
            return ExitCode::FAILURE;
        }
    };
    let flakeradar = match (&radar_out, &radar_skipped) {
        (Some(out), _) => Radar::Read(out),
        (None, Some(reason)) => Radar::Skipped(reason),
        (None, None) => Radar::Off,
    };
    let config: ConfigOut = match read_json(&config_arg) {
        Ok(value) => value,
        Err(message) => {
            eprintln!("scout plan: {message}");
            return ExitCode::FAILURE;
        }
    };
    let ratchet: RatchetOut = match read_json(&ratchet_arg) {
        Ok(value) => value,
        Err(message) => {
            eprintln!("scout plan: {message}");
            return ExitCode::FAILURE;
        }
    };
    let flaky: FlakyOut = match read_json(&flaky_arg) {
        Ok(value) => value,
        Err(message) => {
            eprintln!("scout plan: {message}");
            return ExitCode::FAILURE;
        }
    };
    let issues: Vec<Issue> = match read_json(&issues_arg) {
        Ok(value) => value,
        Err(message) => {
            eprintln!("scout plan: {message}");
            return ExitCode::FAILURE;
        }
    };
    for issue in &issues {
        if let Some(closed_at) = issue.closed_at.as_deref()
            && !is_github_ts(closed_at)
        {
            eprintln!(
                "scout plan: issue {}: closed_at must be YYYY-MM-DDTHH:MM:SSZ, found \"{closed_at}\"",
                issue.number
            );
            return ExitCode::FAILURE;
        }
    }
    let out = plan(&PlanInput {
        config: &config,
        ratchet: &ratchet,
        flaky: &flaky,
        issues: &issues,
        logs_dir: &logs_dir,
        repo: &repo,
        sha: &sha,
        flakeradar,
    });
    let json = serde_json::to_string(&out).unwrap_or_else(|e| {
        eprintln!("scout plan: cannot serialize the result: {e}");
        String::new()
    });
    println!("{json}");
    ExitCode::SUCCESS
}

/// Reads one input file as JSON of the expected shape.
fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{} is not valid JSON: {e}", path.display()))
}
