//! `scout ratchet`: run each Ratchet rule's command and count its findings (ADR 0027 C3).
//!
//! One entry is one rule in the whole repository (D2). The command's stdout is the tool's
//! JSON; its exit code is ignored, because ruff and eslint exit 1 when they find something.
//! The whole subcommand has a 40-minute budget, and each entry's deadline is the smaller of
//! its own `timeout_secs` and what is left, so one hung tool cannot spend the run's slot.
//!
//! Parsing is pure; only [`run`] starts a process.

// Rust guideline compliant 2026-07-21

use std::collections::BTreeSet;
use std::io::Read;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use nix::sys::signal::{Signal, killpg};
use nix::unistd::Pid;
use serde::{Deserialize, Serialize};

use super::ratchet_fp;
use crate::scout::config::{ConfigOut, RatchetEntry};

/// The whole subcommand's budget, seconds (C3). R2's `stall_timeout` of 70m leaves 20m for
/// `setup.sh` on top of this.
const BUDGET_SECS: u64 = 40 * 60;
/// Most stdout read from one command. A tool that prints more is reporting something other
/// than findings, so the entry is an error (C3).
const MAX_STDOUT_BYTES: u64 = 64 * 1024 * 1024;
/// How often a running command is checked for exit. Short enough that the deadline is
/// honoured within a fraction of a second, and each poll is one `try_wait`.
const POLL_INTERVAL: Duration = Duration::from_millis(100);

/// One finding of the rule, after C3's path normalisation.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Finding {
    /// The path relative to the checkout root.
    pub path: String,
    /// The 1-based line.
    pub line: u64,
    /// The 1-based column.
    pub column: u64,
    /// The tool's message, uncollapsed.
    pub message: String,
}

/// C3's output for one entry.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EntryOut {
    /// The entry's position in the config.
    pub index: usize,
    /// `ruff`, `eslint` or `clippy`.
    pub tool: String,
    /// The rule as the tool reports it.
    pub rule: String,
    /// The C3 fingerprint.
    pub fp: String,
    /// `ok` or `error`.
    pub status: String,
    /// The one-line reason when `status` is `error`; `null` otherwise.
    pub error: Option<String>,
    /// The number of distinct findings.
    pub count: usize,
    /// The number of distinct paths.
    pub files: usize,
    /// The findings, sorted by path, line and column.
    pub findings: Vec<Finding>,
    /// The command the entry declared.
    pub command: String,
    /// The config file the fix adds the rule to.
    pub enforce_in: String,
    /// How the fix enforces it there.
    pub enforce_how: String,
}

/// C3's output: one entry per declared rule, in declaration order.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RatchetOut {
    /// `ok`. A whole-source skip is written by the graph, not here (C9).
    pub status: String,
    /// Always `null` from this subcommand.
    pub reason: Option<String>,
    /// The entries.
    pub entries: Vec<EntryOut>,
}

/// The ruff finding, in `--output-format json` shape.
#[derive(Deserialize)]
struct RawRuff {
    code: String,
    filename: String,
    location: RawRuffLocation,
    message: String,
}

/// Ruff's `location` object.
#[derive(Deserialize)]
struct RawRuffLocation {
    row: u64,
    column: u64,
}

/// One eslint file result.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawEslint {
    file_path: String,
    messages: Vec<RawEslintMessage>,
}

/// One eslint message.
#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawEslintMessage {
    rule_id: Option<String>,
    line: Option<u64>,
    column: Option<u64>,
    message: String,
    #[serde(default)]
    fatal: bool,
}

/// One `cargo clippy --message-format json` line.
#[derive(Deserialize)]
struct RawClippy {
    reason: Option<String>,
    message: Option<RawClippyMessage>,
}

/// A clippy `compiler-message`'s body.
#[derive(Deserialize)]
struct RawClippyMessage {
    message: String,
    code: Option<RawClippyCode>,
    #[serde(default)]
    spans: Vec<RawClippySpan>,
}

/// A clippy lint code.
#[derive(Deserialize)]
struct RawClippyCode {
    code: String,
}

/// One clippy span. `line_start` and `column_start` are absent on a span with no place.
#[derive(Deserialize)]
struct RawClippySpan {
    file_name: String,
    #[serde(default)]
    is_primary: bool,
    #[serde(default)]
    line_start: u64,
    #[serde(default)]
    column_start: u64,
}

/// Parses one tool's stdout and keeps only `rule`'s findings (C3).
///
/// Paths are made relative to `root`; a path outside it is dropped. Findings are
/// de-duplicated on `(path, line, column)` and sorted by path, line, column.
///
/// # Errors
///
/// Returns `<tool> output is not valid JSON` when stdout is not the tool's shape, or
/// `eslint could not parse <path>` when a result carries a fatal message.
pub fn parse(tool: &str, rule: &str, stdout: &[u8], root: &Path) -> Result<Vec<Finding>, String> {
    let text = std::str::from_utf8(stdout)
        .map_err(|_| format!("{tool} output is not valid JSON"))?;
    let mut findings = match tool {
        "ruff" => parse_ruff(rule, text, root)?,
        "eslint" => parse_eslint(rule, text, root)?,
        "clippy" => parse_clippy(rule, text, root)?,
        other => return Err(format!("unknown tool '{other}'")),
    };
    findings.sort_by(|a, b| (&a.path, a.line, a.column).cmp(&(&b.path, b.line, b.column)));
    findings.dedup_by(|a, b| a.path == b.path && a.line == b.line && a.column == b.column);
    Ok(findings)
}

/// Ruff's one JSON array, elements with `code == rule` (C3).
fn parse_ruff(rule: &str, text: &str, root: &Path) -> Result<Vec<Finding>, String> {
    let raw: Vec<RawRuff> =
        serde_json::from_str(text).map_err(|_| "ruff output is not valid JSON".to_string())?;
    Ok(raw
        .into_iter()
        .filter(|finding| finding.code == rule)
        .filter_map(|finding| {
            let path = relative(&finding.filename, root)?;
            Some(Finding {
                path,
                line: finding.location.row,
                column: finding.location.column,
                message: finding.message,
            })
        })
        .collect())
}

/// eslint's array of file results (C3).
fn parse_eslint(rule: &str, text: &str, root: &Path) -> Result<Vec<Finding>, String> {
    let raw: Vec<RawEslint> =
        serde_json::from_str(text).map_err(|_| "eslint output is not valid JSON".to_string())?;
    let mut findings = Vec::new();
    for file in raw {
        if file.messages.iter().any(|message| message.fatal) {
            return Err(format!("eslint could not parse {}", file.file_path));
        }
        let Some(path) = relative(&file.file_path, root) else {
            continue;
        };
        for message in file.messages {
            if message.rule_id.as_deref() != Some(rule) {
                continue;
            }
            findings.push(Finding {
                path: path.clone(),
                line: message.line.unwrap_or(0),
                column: message.column.unwrap_or(0),
                message: message.message,
            });
        }
    }
    Ok(findings)
}

/// clippy's JSON lines, keeping `compiler-message` findings at their primary span (C3).
fn parse_clippy(rule: &str, text: &str, root: &Path) -> Result<Vec<Finding>, String> {
    let mut findings = Vec::new();
    for line in text.lines() {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(line) else {
            continue;
        };
        if !value.is_object() {
            continue;
        }
        let Ok(raw) = serde_json::from_value::<RawClippy>(value) else {
            continue;
        };
        if raw.reason.as_deref() != Some("compiler-message") {
            continue;
        }
        let Some(message) = raw.message else {
            continue;
        };
        if message.code.as_ref().map(|code| code.code.as_str()) != Some(rule) {
            continue;
        }
        let Some(span) = message.spans.iter().find(|span| span.is_primary) else {
            continue;
        };
        let Some(path) = relative(&span.file_name, root) else {
            continue;
        };
        findings.push(Finding {
            path,
            line: span.line_start,
            column: span.column_start,
            message: message.message,
        });
    }
    Ok(findings)
}

/// Makes `path` relative to `root`, or `None` when it lies outside it (C3).
///
/// An absolute path is stripped of `root`, and of `root` canonicalised when that works:
/// clippy reports `~/.cargo/registry` paths for dependencies, which must be dropped. A
/// relative path that starts with `..` is dropped too.
fn relative(path: &str, root: &Path) -> Option<String> {
    let candidate = Path::new(path);
    if candidate.is_absolute() {
        if let Ok(rel) = candidate.strip_prefix(root) {
            return Some(rel.to_string_lossy().into_owned());
        }
        if let Ok(canonical) = root.canonicalize()
            && let Ok(rel) = candidate.strip_prefix(&canonical)
        {
            return Some(rel.to_string_lossy().into_owned());
        }
        return None;
    }
    if path.starts_with("..") {
        return None;
    }
    Some(path.to_string())
}

/// Runs every entry in declaration order under C3's budget.
#[must_use]
pub fn run(config: &ConfigOut, root: &Path) -> RatchetOut {
    let started = Instant::now();
    let mut entries = Vec::with_capacity(config.ratchet.len());
    for entry in &config.ratchet {
        let remaining = BUDGET_SECS.saturating_sub(started.elapsed().as_secs());
        let result = if remaining == 0 {
            Err("not run: the 40-minute ratchet budget is spent".to_string())
        } else {
            let limit = entry.timeout_secs.min(remaining);
            execute(&entry.command, root, limit)
                .and_then(|stdout| parse(&entry.tool, &entry.rule, &stdout, root))
        };
        entries.push(entry_out(entry, result));
    }
    RatchetOut { status: "ok".to_string(), reason: None, entries }
}

/// Builds C3's entry from the parse result.
fn entry_out(entry: &RatchetEntry, result: Result<Vec<Finding>, String>) -> EntryOut {
    let fp = ratchet_fp(&entry.tool, &entry.rule);
    let mut out = EntryOut {
        index: entry.index,
        tool: entry.tool.clone(),
        rule: entry.rule.clone(),
        fp,
        status: "error".to_string(),
        error: None,
        count: 0,
        files: 0,
        findings: Vec::new(),
        command: entry.command.clone(),
        enforce_in: entry.enforce_in.clone(),
        enforce_how: entry.enforce_how.clone(),
    };
    match result {
        Ok(findings) => {
            out.status = "ok".to_string();
            out.files = findings.iter().map(|f| f.path.as_str()).collect::<BTreeSet<_>>().len();
            out.count = findings.len();
            out.findings = findings;
        }
        Err(error) => out.error = Some(error),
    }
    out
}

/// Runs one command in its own process group, capped by `limit_secs` (C3).
///
/// The exit code is ignored: ruff and eslint exit 1 when they find something. On the
/// deadline the whole group is killed, so a tool's own children cannot outlive it.
fn execute(command: &str, root: &Path, limit_secs: u64) -> Result<Vec<u8>, String> {
    let mut child = Command::new("sh")
        .arg("-c")
        .arg(command)
        .current_dir(root)
        .process_group(0)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("could not run the command: {e}"))?;
    let Some(stdout) = child.stdout.take() else {
        return Err("could not read the command output".to_string());
    };
    let pid = child.id();
    let reader = thread::spawn(move || read_capped(stdout, MAX_STDOUT_BYTES, pid));
    let deadline = Instant::now() + Duration::from_secs(limit_secs);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => {
                if Instant::now() >= deadline {
                    // SAFETY: `pid` is the child this call spawned, and `process_group(0)`
                    // made it its own group leader, so the negative-pid group kill cannot
                    // reach any other process.
                    let _ = killpg(Pid::from_raw(pid as i32), Signal::SIGKILL);
                    let _ = child.wait();
                    let _ = reader.join();
                    return Err(format!("timed out after {limit_secs} s"));
                }
                thread::sleep(POLL_INTERVAL);
            }
            Err(e) => {
                let _ = killpg(Pid::from_raw(pid as i32), Signal::SIGKILL);
                let _ = child.wait();
                return Err(format!("could not wait for the command: {e}"));
            }
        }
    }
    reader.join().map_err(|_| "the output reader failed".to_string())?
}

/// Reads `reader` into a buffer of at most `limit` bytes (C3).
///
/// On overflow the process group is killed, so the tool cannot sit in `write` on a full
/// pipe until its own deadline.
fn read_capped(mut reader: impl Read, limit: u64, pid: u32) -> Result<Vec<u8>, String> {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 8192];
    loop {
        match reader.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => {
                if buf.len() as u64 + n as u64 > limit {
                    // SAFETY: as in `execute`: the pid is this call's own process group.
                    let _ = killpg(Pid::from_raw(pid as i32), Signal::SIGKILL);
                    return Err(format!("the command printed more than {limit} bytes"));
                }
                buf.extend_from_slice(&chunk[..n]);
            }
            Err(e) => return Err(format!("could not read the command output: {e}")),
        }
    }
    Ok(buf)
}

/// Runs `scout ratchet --config FILE [--root DIR]` (C3).
#[must_use]
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut config_arg: Option<PathBuf> = None;
    let mut root = PathBuf::from(".");
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--config" | "--root" => {
                let flag = args[i].clone();
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("scout ratchet: {flag} needs a value");
                    return ExitCode::from(2);
                };
                if flag == "--config" {
                    config_arg = Some(PathBuf::from(value));
                } else {
                    root = PathBuf::from(value);
                }
            }
            other => {
                eprintln!("scout ratchet: unexpected argument '{other}'");
                return ExitCode::from(2);
            }
        }
        i += 1;
    }
    let Some(config_path) = config_arg else {
        eprintln!("scout ratchet: --config <path> is required");
        return ExitCode::from(2);
    };
    let config = match std::fs::read_to_string(&config_path)
        .map_err(|e| format!("cannot read {}: {e}", config_path.display()))
        .and_then(|text| {
            serde_json::from_str::<ConfigOut>(&text)
                .map_err(|e| format!("{} is not valid scout config JSON: {e}", config_path.display()))
        }) {
        Ok(config) => config,
        Err(message) => {
            eprintln!("scout ratchet: {message}");
            return ExitCode::FAILURE;
        }
    };
    let out = run(&config, &root);
    let json = serde_json::to_string(&out).unwrap_or_else(|e| {
        eprintln!("scout ratchet: cannot serialize the result: {e}");
        String::new()
    });
    println!("{json}");
    ExitCode::SUCCESS
}
