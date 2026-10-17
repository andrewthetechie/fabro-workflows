//! `scout`'s issue text and the one-line Discord summary (ADR 0027 C6, C7, C8).
//!
//! The title and body templates are the contract: the acceptance criteria and non-goals in
//! them are what triage would otherwise add, which is why a scout issue skips triage (D10).
//! A flaky log tail is untrusted text from a failing job, so [`clean_log`] strips its
//! timestamps and ANSI codes and neutralises backtick fences and `@` mentions before it
//! lands in a Markdown body.

// Rust guideline compliant 2026-07-21

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::Deserialize;

use super::flakeradar::FlakyTest;
use super::flaky::{FlakyJob, Occurrence};
use super::plan::{Action, Issue, PlanOut};
use super::ratchet::EntryOut;

/// The most characters a body may hold before log tails are dropped (C7).
const MAX_BODY_CHARS: usize = 60_000;
/// The most log tails the flaky body shows (C7).
const MAX_TAILS: usize = 3;
/// The most characters one cleaned log tail may hold (C7).
const MAX_TAIL_CHARS: usize = 3000;
/// The most lines one cleaned log tail may hold (C7).
const MAX_TAIL_LINES: usize = 60;
/// The line GitHub's runner writes when a step fails; the log tail ends at the first (C7).
const ERROR_MARK: &str = "##[error]";
/// The most characters of a test name a title or summary shows (C7b).
const MAX_TEST_NAME_CHARS: usize = 120;
/// The most characters the summary line may hold (C8).
const MAX_SUMMARY_CHARS: usize = 1500;

/// The graph's write log entry (C8).
#[derive(Clone, Debug, Deserialize)]
pub struct Filed {
    /// The fingerprint the write was for.
    pub fp: String,
    /// `filed`, `raced` or `failed`.
    pub outcome: String,
    /// The new issue's number, when it was filed.
    #[serde(default)]
    pub number: Option<u64>,
    /// The error, when it failed.
    #[serde(default)]
    pub error: Option<String>,
}

/// C6's title for a Ratchet rule.
#[must_use]
pub fn ratchet_title(entry: &EntryOut, detail: &str, regression: Option<&Issue>) -> String {
    let body = format!("fix and enforce {} rule {} ({detail})", entry.tool, entry.rule);
    match regression {
        Some(issue) => format!("scout (regression of #{}): {body}", issue.number),
        None => format!("scout: {body}"),
    }
}

/// C6's body for a Ratchet rule.
#[must_use]
pub fn ratchet_body(
    entry: &EntryOut,
    detail: &str,
    sha8: &str,
    regression: Option<&Issue>,
) -> String {
    let mut out = String::new();
    out.push_str(&format!("<!-- fabro:scout fp={} -->\n", entry.fp));
    out.push_str(&format!(
        "Found by the scout (ADR 0027) on `main` at `{sha8}`. The {} rule `{}` reports {detail}, and `./.fabro/ci.sh` does not enforce it yet.\n",
        entry.tool, entry.rule
    ));
    if let Some(issue) = regression {
        out.push_str(&format!(
            "Regression of #{}, which was closed on {}.\n",
            issue.number,
            closed_date(issue)
        ));
    }
    out.push_str("\n## Findings\n\n");
    for finding in &entry.findings {
        out.push_str(&format!(
            "- `{}:{}:{}` {}\n",
            finding.path,
            finding.line,
            finding.column,
            collapse(&finding.message)
        ));
    }
    out.push_str("\n## Acceptance criteria\n\n");
    out.push_str(&format!(
        "- [ ] Every finding listed above is fixed, so `{}` reports no `{}` finding.\n",
        entry.command, entry.rule
    ));
    out.push_str(&format!(
        "- [ ] `{}` is enforced in `{}`: {}. From then on `./.fabro/ci.sh` fails on a new `{}` finding.\n",
        entry.rule, entry.enforce_in, entry.enforce_how, entry.rule
    ));
    out.push_str("- [ ] `./.fabro/ci.sh` passes.\n");
    out.push_str("\n## Non-goals\n\n");
    out.push_str(&format!(
        "- Do not suppress a finding. No `noqa`, `eslint-disable`, `#[allow(...)]` or `#![allow(...)]`, and no per-file, path or directory ignore of `{}` in any config.\n",
        entry.rule
    ));
    out.push_str("- Do not change behaviour beyond what each fix needs, and do not fix other rules.\n");
    out.push_str("- Do not edit `.fabro/scout.toml`, `.fabro/ci.sh` or `.fabro/test.toml`.\n");
    out.push_str("\nFiled by the scout. Close as not planned to stop it filing this rule again.\n");
    out
}

/// C7's title for a Flaky job.
#[must_use]
pub fn flaky_title(job: &FlakyJob, detail: &str, regression: Option<&Issue>) -> String {
    let basename = job.workflow.rsplit('/').next().unwrap_or(&job.workflow);
    let body = format!("flaky CI job \"{}\" in {} ({detail})", job.job, basename);
    match regression {
        Some(issue) => format!("scout (regression of #{}): {body}", issue.number),
        None => format!("scout: {body}"),
    }
}

/// C7's body for a Flaky job. `occurrences` are newest first and already filtered to the
/// ones the issue counts.
#[must_use]
pub fn flaky_body(
    job: &FlakyJob,
    occurrences: &[&Occurrence],
    window_days: u32,
    logs_dir: &Path,
    regression: Option<&Issue>,
) -> String {
    let mut tails: Vec<String> = Vec::new();
    for occurrence in occurrences {
        if tails.len() >= MAX_TAILS {
            break;
        }
        let path = logs_dir.join(format!("{}.txt", occurrence.failed_job_id));
        if let Ok(raw) = std::fs::read_to_string(&path) {
            tails.push(format!(
                "### Run {}, attempt {}\n\n````text\n{}\n````\n",
                occurrence.run_id,
                occurrence.attempt,
                clean_log(&raw)
            ));
        }
    }
    let mut body = render_flaky(job, occurrences, window_days, regression, &tails);
    while body.chars().count() > MAX_BODY_CHARS && !tails.is_empty() {
        tails.pop();
        body = render_flaky(job, occurrences, window_days, regression, &tails);
    }
    body
}

/// One pass of C7's body with the given log tails, newest first.
fn render_flaky(
    job: &FlakyJob,
    occurrences: &[&Occurrence],
    window_days: u32,
    regression: Option<&Issue>,
    tails: &[String],
) -> String {
    let mut out = String::new();
    out.push_str(&format!("<!-- fabro:scout fp={} -->\n", job.fp));
    out.push_str(&format!(
        "Found by the scout (ADR 0027). The CI job `{}` in `{}` failed and then passed on a rerun of the same commit {} in the last {window_days} days. A job that fails at random holds PRs and sends the merge phase to CI fixes for faults no change caused.\n",
        job.job,
        job.workflow,
        plural(occurrences.len(), "time")
    ));
    if let Some(issue) = regression {
        out.push_str(&format!(
            "Regression of #{}, which was closed on {}.\n",
            issue.number,
            closed_date(issue)
        ));
    }
    out.push_str("\n## Occurrences (newest first)\n\n");
    for occurrence in occurrences {
        let steps = occurrence
            .failed_steps
            .iter()
            .map(|step| format!("`{step}`"))
            .collect::<Vec<_>>()
            .join(", ");
        out.push_str(&format!(
            "- {}: [run {}, attempt {}]({}) failed at step(s) {} ([job log]({}))\n",
            date_of(&occurrence.created_at),
            occurrence.run_id,
            occurrence.attempt,
            occurrence.run_url,
            steps,
            occurrence.failed_job_url
        ));
    }
    out.push_str("\n## Failing log tails\n\n");
    if tails.is_empty() {
        out.push_str("No log was available.\n");
    } else {
        for tail in tails {
            out.push_str(tail);
            out.push('\n');
        }
    }
    out.push_str("\n## Acceptance criteria\n\n");
    out.push_str("- [ ] The PR description names the cause of the intermittent failure and cites the log lines above.\n");
    out.push_str("- [ ] The test or CI step that failed is changed so that cause can no longer fail it.\n");
    out.push_str("- [ ] `./.fabro/ci.sh` passes.\n");
    out.push_str("\n## Non-goals\n\n");
    out.push_str("- Do not skip, delete, quarantine or mark the failing test as flaky, and do not add a retry around it or around the job.\n");
    out.push_str("- Do not change the CI workflow's rerun or retry settings.\n");
    out.push_str("- Do not edit `.fabro/scout.toml`, `.fabro/ci.sh` or `.fabro/test.toml`.\n");
    out.push_str("\nFiled by the scout. Close as not planned to stop it filing this job again.\n");
    out
}

/// The test's name as a title or the summary shows it: one line, cut to 120 characters.
#[must_use]
pub fn test_subject(test: &FlakyTest) -> String {
    let name = collapse_all(&test.name);
    if name.chars().count() > MAX_TEST_NAME_CHARS {
        let cut: String = name.chars().take(MAX_TEST_NAME_CHARS - 3).collect();
        format!("{cut}...")
    } else {
        name
    }
}

/// C7b's title for a flaky test.
#[must_use]
pub fn test_title(test: &FlakyTest, detail: &str, regression: Option<&Issue>) -> String {
    let body = format!("flaky test \"{}\" in {} ({detail})", test_subject(test), test.project);
    match regression {
        Some(issue) => format!("scout (regression of #{}): {body}", issue.number),
        None => format!("scout: {body}"),
    }
}

/// C7b's body for a flaky test.
#[must_use]
pub fn test_body(test: &FlakyTest, regression: Option<&Issue>) -> String {
    let mut out = String::new();
    out.push_str(&format!("<!-- fabro:scout fp={} -->\n", test.fp));
    let mut whereabouts = Vec::new();
    if let Some(suite) = test.suite.as_deref().filter(|s| !s.is_empty()) {
        whereabouts.push(format!("suite `{}`", inline(suite)));
    }
    if let Some(classname) =
        test.classname.as_deref().filter(|c| !c.is_empty() && Some(*c) != test.suite.as_deref())
    {
        whereabouts.push(format!("class `{}`", inline(classname)));
    }
    if let Some(file) = test.file.as_deref().filter(|f| !f.is_empty()) {
        match test.line {
            Some(line) => whereabouts.push(format!("`{}:{line}`", inline(file))),
            None => whereabouts.push(format!("`{}`", inline(file))),
        }
    }
    let place = if whereabouts.is_empty() {
        String::new()
    } else {
        format!(" ({})", whereabouts.join(", "))
    };
    out.push_str(&format!(
        "Found by the scout (ADR 0027, ADR 0028) in FlakeRadar. The test `{}` in project `{}`{place} fails at random: FlakeRadar scores it {:.2} and has seen it fail and pass on the same commit {}.",
        inline(&test.name),
        inline(&test.project),
        test.score,
        plural(test.proven as usize, "time")
    ));
    if let Some(category) = test.category.as_deref().filter(|c| !c.is_empty()) {
        out.push_str(&format!(" It files the failures under `{}`.", inline(category)));
    }
    out.push_str(&format!(" Its history is on the [FlakeRadar dashboard]({}).\n", test.url));
    if let Some(issue) = regression {
        out.push_str(&format!(
            "Regression of #{}, which was closed on {}.\n",
            issue.number,
            closed_date(issue)
        ));
    }
    out.push_str("\n## Recent failures (newest first)\n\n");
    if test.failures.is_empty() {
        out.push_str("FlakeRadar holds no failure in the test's recent history.\n");
    }
    // Each failure gets its message; only the newest shows its traceback, which the others
    // usually repeat.
    for (index, failure) in test.failures.iter().enumerate() {
        let mut heading = format!("### {}", date_of(&failure.created_at));
        if let Some(branch) = failure.branch.as_deref().filter(|b| !b.is_empty()) {
            heading.push_str(&format!(" on `{}`", inline(branch)));
        }
        if let Some(run) = failure.ci_run_id.as_deref().filter(|r| !r.is_empty()) {
            heading.push_str(&format!(", run {}", inline(run)));
        }
        out.push_str(&format!("{heading}\n\n"));
        if !failure.message.is_empty() {
            out.push_str(&format!("`{}`\n\n", inline(&failure.message)));
        }
        if index == 0 && !failure.details.is_empty() {
            out.push_str(&format!("````text\n{}\n````\n\n", sanitize(&failure.details)));
        }
    }
    out.push_str("## Last failing commit\n\n");
    match test.last_failing_sha.as_deref().filter(|sha| !sha.is_empty()) {
        Some(sha) => {
            let sha8: String = sha.chars().take(8).collect();
            match test.last_failing_branch.as_deref().filter(|b| !b.is_empty()) {
                Some(branch) => out.push_str(&format!("`{}` on `{}`.\n", inline(&sha8), inline(branch))),
                None => out.push_str(&format!("`{}`.\n", inline(&sha8))),
            }
        }
        None => out.push_str("Not known.\n"),
    }
    out.push_str("\n## Acceptance criteria\n\n");
    out.push_str("- [ ] The PR description names the cause of the intermittent failure and cites the failures above.\n");
    out.push_str("- [ ] The test, or the code it tests, is changed so that cause can no longer fail it.\n");
    out.push_str("- [ ] `./.fabro/ci.sh` passes.\n");
    out.push_str("\n## Non-goals\n\n");
    out.push_str("- Do not skip, delete, quarantine or mark the failing test as flaky, and do not add a retry around it or around the job.\n");
    out.push_str("- Do not quarantine the test in FlakeRadar.\n");
    out.push_str("- Do not change the CI workflow's rerun or retry settings.\n");
    out.push_str("- Do not edit `.fabro/scout.toml`, `.fabro/ci.sh` or `.fabro/test.toml`.\n");
    out.push_str("\nFiled by the scout. Close as not planned to stop it filing this test again.\n");
    out
}

/// Text for inline code: one line, with backticks replaced so it cannot close the span. An
/// `@` is left alone: GitHub does not mention anyone from inside a code span, and a test
/// named `test_x@migration` must stay searchable.
fn inline(text: &str) -> String {
    collapse_all(text).replace('`', "'")
}

/// Every whitespace or control run as one space, with no length limit.
fn collapse_all(text: &str) -> String {
    text.split(|c: char| c.is_whitespace() || c.is_control())
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// C7's cleaning steps, in order: timestamps, ANSI codes, the cut at the first `##[error]`,
/// size, then backticks and mentions.
#[must_use]
pub fn clean_log(raw: &str) -> String {
    let mut without_ts = String::with_capacity(raw.len());
    for (index, line) in raw.split('\n').enumerate() {
        if index > 0 {
            without_ts.push('\n');
        }
        without_ts.push_str(strip_timestamp(line));
    }
    let without_ansi = strip_ansi(&without_ts);
    let mut lines: Vec<&str> = without_ansi.split('\n').collect();
    // The tail ends at the first `##[error]` line, which closes the first failed step. What
    // follows it is later steps and post-job cleanup, which say nothing about the failure.
    if let Some(error) = lines.iter().position(|line| line.starts_with(ERROR_MARK)) {
        lines.truncate(error + 1);
    }
    let kept = lines[lines.len().saturating_sub(MAX_TAIL_LINES)..].join("\n");
    let capped = last_chars_at_line_start(&kept, MAX_TAIL_CHARS);
    sanitize(&capped)
}

/// C7 step 1: drop a leading `2026-10-14T12:00:00.1234567Z ` timestamp.
fn strip_timestamp(line: &str) -> &str {
    if !line.starts_with(|c: char| c.is_ascii_digit()) {
        return line;
    }
    // A byte index, so the slices below stay on a char boundary; the 40 is counted in chars.
    let Some(space) = line.find(' ') else {
        return line;
    };
    if line[..space].chars().count() < 40 && line[..space].ends_with('Z') {
        &line[space + 1..]
    } else {
        line
    }
}

/// C7 step 2: remove `ESC [ ... <final in @..~>` sequences.
///
/// Every other control byte but newline and tab goes too, so a stray `ESC` from a non-CSI
/// sequence, or a carriage return, never reaches the issue body.
#[must_use]
pub fn strip_ansi(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == 0x1b && bytes.get(i + 1) == Some(&b'[') {
            i += 2;
            while i < bytes.len() && !(0x40..=0x7e).contains(&bytes[i]) {
                i += 1;
            }
            if i < bytes.len() {
                i += 1;
            }
        } else {
            let b = bytes[i];
            if !((b < 0x20 && b != b'\n' && b != b'\t') || b == 0x7f) {
                out.push(b);
            }
            i += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// C7 step 3's second half: the last `limit` characters, cut at a line start.
fn last_chars_at_line_start(text: &str, limit: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= limit {
        return text.to_string();
    }
    let tail = &chars[chars.len() - limit..];
    match tail.iter().position(|c| *c == '\n') {
        Some(start) => tail[start + 1..].iter().collect(),
        None => tail.iter().collect(),
    }
}

/// C7 step 4: a 4+ backtick run becomes `'''`, and every `@` gains a zero-width space.
fn sanitize(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut run = 0;
    for c in text.chars() {
        if c == '`' {
            run += 1;
            continue;
        }
        if run > 0 {
            if run >= 4 {
                out.push_str("'''");
            } else {
                out.extend(std::iter::repeat_n('`', run));
            }
            run = 0;
        }
        if c == '@' {
            out.push('@');
            out.push('\u{200b}');
        } else {
            out.push(c);
        }
    }
    if run > 0 {
        if run >= 4 {
            out.push_str("'''");
        } else {
            out.extend(std::iter::repeat_n('`', run));
        }
    }
    out
}

/// C6 and C7's `MESSAGE`: one line, at most 200 characters.
fn collapse(text: &str) -> String {
    let mut out = String::new();
    let mut space = false;
    for c in text.chars() {
        if c.is_whitespace() || c.is_control() {
            space = !out.is_empty();
        } else {
            if space {
                out.push(' ');
                space = false;
            }
            out.push(c);
        }
    }
    out.chars().take(200).collect()
}

/// The first 10 characters of `closed_at`, or `an unknown date` (C6, C7).
fn closed_date(issue: &Issue) -> String {
    issue
        .closed_at
        .as_deref()
        .filter(|closed| closed.chars().count() >= 10)
        .map_or_else(|| "an unknown date".to_string(), |closed| closed.chars().take(10).collect())
}

/// The first 10 characters of a timestamp, its date.
fn date_of(timestamp: &str) -> String {
    timestamp.chars().take(10).collect()
}

/// `1 finding`, `2 findings`.
fn plural(count: usize, word: &str) -> String {
    if count == 1 { format!("1 {word}") } else { format!("{count} {word}s") }
}

/// C8's one line, empty when nothing is worth posting.
#[must_use]
pub fn summary(plan: &PlanOut, filed: Option<&[Filed]>) -> String {
    let mut action_parts: Vec<String> = Vec::new();
    for action in &plan.actions {
        let short = short(action);
        match action.kind.as_str() {
            "file" | "regression" => {
                if plan.filing {
                    let Some(entry) =
                        filed.and_then(|list| list.iter().find(|entry| entry.fp == action.fp))
                    else {
                        continue;
                    };
                    match entry.outcome.as_str() {
                        "filed" => action_parts.push(format!(
                            "filed #{} {} {short}",
                            entry.number.unwrap_or(0),
                            action.source
                        )),
                        "raced" => action_parts.push(format!("raced {short}")),
                        "failed" => action_parts.push(format!(
                            "write failed {short}: {}",
                            entry.error.clone().unwrap_or_default()
                        )),
                        _ => {}
                    }
                } else {
                    action_parts.push(format!("would file {} {short}", action.source));
                }
            }
            "error" => action_parts.push(format!("error {}: {}", subject(action), action.detail)),
            "too_big" if !plan.filing => {
                action_parts.push(format!("too big {} ({})", subject(action), action.detail));
            }
            _ => {}
        }
    }
    if let Some(reason) = plan.sources.ratchet.strip_prefix("skipped: ") {
        action_parts.push(format!("ratchet skipped: {reason}"));
    }
    if let Some(reason) = plan.sources.flaky.strip_prefix("skipped: ") {
        action_parts.push(format!("flaky skipped: {reason}"));
    }
    if action_parts.is_empty() {
        return String::new();
    }
    let name = plan.repo.rsplit('/').next().unwrap_or(&plan.repo);
    let mut header = format!("scout {name}");
    if !plan.filing {
        header.push_str(" DRY RUN");
    }
    let counts = counts(plan, filed);
    if !counts.is_empty() {
        header.push_str(&format!(": {}", counts.join(", ")));
    }
    let mut parts = vec![header];
    parts.extend(action_parts);
    fit(&parts).replace('"', "").replace('\\', "")
}

/// `<tool> <rule> (<detail>)` or `<job> (<detail>)`, plus the regression suffix (C8).
fn short(action: &Action) -> String {
    let suffix = match action.kind.as_str() {
        "regression" => format!(" regression of #{}", action.issue.unwrap_or(0)),
        _ => String::new(),
    };
    format!("{} ({}){suffix}", subject(action), action.detail)
}

/// The action's `<tool> <rule>` or `<job>`.
fn subject(action: &Action) -> String {
    action.subject.clone().unwrap_or_default()
}

/// C8's header counts: only the non-zero ones, in C8's order.
///
/// A live `filed N` counts the write log's `filed` outcomes, not the planned actions: after a
/// raced or failed write, the planned number would overstate what reached GitHub.
fn counts(plan: &PlanOut, filed: Option<&[Filed]>) -> Vec<String> {
    let count = |kind: &str| plan.actions.iter().filter(|a| a.kind == kind).count();
    let mut out = Vec::new();
    if plan.filing {
        let n = filed.map_or(0, |list| list.iter().filter(|e| e.outcome == "filed").count());
        if n > 0 {
            out.push(format!("filed {n}"));
        }
    } else {
        let n = count("file") + count("regression");
        if n > 0 {
            out.push(format!("would file {n}"));
        }
    }
    for (kind, label) in [
        ("known", "known"),
        ("explained", "explained"),
        ("suppressed", "suppressed"),
        ("too_big", "too big"),
        ("capped", "capped"),
        ("backpressure", "held back"),
        ("error", "errors"),
    ] {
        let n = count(kind);
        if n > 0 {
            out.push(format!("{label} {n}"));
        }
    }
    out
}

/// C8's truncation: drop whole parts from the end, then `...and N more`.
fn fit(parts: &[String]) -> String {
    let joined = parts.join(" | ");
    if joined.chars().count() <= MAX_SUMMARY_CHARS || parts.len() < 2 {
        return joined.chars().take(MAX_SUMMARY_CHARS).collect();
    }
    let mut kept = parts.len() - 1;
    while kept > 1 {
        let dropped = parts.len() - kept;
        let candidate = format!("{} | ...and {dropped} more", parts[..kept].join(" | "));
        if candidate.chars().count() <= MAX_SUMMARY_CHARS {
            return candidate;
        }
        kept -= 1;
    }
    format!("{} | ...and {} more", parts[0], parts.len() - 1)
        .chars()
        .take(MAX_SUMMARY_CHARS)
        .collect()
}

/// Runs `scout summary --plan FILE [--filed FILE]` (C8).
#[must_use]
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut plan_arg: Option<PathBuf> = None;
    let mut filed_arg: Option<PathBuf> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--plan" | "--filed" => {
                let flag = args[i].clone();
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("scout summary: {flag} needs a value");
                    return ExitCode::from(2);
                };
                if flag == "--plan" {
                    plan_arg = Some(PathBuf::from(value));
                } else {
                    filed_arg = Some(PathBuf::from(value));
                }
            }
            other => {
                eprintln!("scout summary: unexpected argument '{other}'");
                return ExitCode::from(2);
            }
        }
        i += 1;
    }
    let Some(plan_path) = plan_arg else {
        eprintln!("scout summary: --plan <path> is required");
        return ExitCode::from(2);
    };
    let plan: PlanOut = match read_json(&plan_path) {
        Ok(plan) => plan,
        Err(message) => {
            eprintln!("scout summary: {message}");
            return ExitCode::FAILURE;
        }
    };
    let filed: Option<Vec<Filed>> = match filed_arg {
        Some(path) => match read_json(&path) {
            Ok(filed) => Some(filed),
            Err(message) => {
                eprintln!("scout summary: {message}");
                return ExitCode::FAILURE;
            }
        },
        None => None,
    };
    let line = summary(&plan, filed.as_deref());
    if !line.is_empty() {
        print!("{line}");
    }
    ExitCode::SUCCESS
}

/// Reads one input file as JSON of the expected shape.
fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{} is not valid JSON: {e}", path.display()))
}
