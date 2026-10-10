//! The `run_tests` tool and `fabro-io run-tests` (ADR 0018, docs/test-env C4 and C5).
//!
//! An agent names a target from the repository's `.fabro/test.toml`. This module checks the
//! arguments, prepares the Test environment when its stamp is missing or stale, and runs the
//! target's own command with the environment `ci.sh` gets. The declaration belongs to the
//! repository; the stamp and the preparation log belong to `testenv`.

// Rust guideline compliant 2026-07-21

use std::path::Path;
use std::process::ExitCode;
use std::time::{Duration, Instant};

use crate::code;
use crate::gitsafe::{self, MAX_TIMEOUT_S};
use crate::testenv::{self, Target, TestEnv};

/// The MCP name of the tool.
pub const RUN_TESTS: &str = "run_tests";

/// What a repository without a usable declaration is told (C4).
pub const NO_DECLARATION: &str =
    "this repository declares no test targets; run the narrowest check through shell";

/// The largest total one call may spend, seconds (C4). The same ceiling as `baseline_check`.
const TOTAL_MAX_S: u64 = MAX_TIMEOUT_S;

/// The outcome of one target call: whether it passed, and the text the agent or the shell
/// sees.
#[derive(Debug)]
pub struct Report {
    /// True when the target exited 0 and nothing failed before it.
    pub success: bool,
    /// The C4 result text, or the reason the call was refused.
    pub text: String,
}

fn fail(text: impl Into<String>) -> Report {
    Report {
        success: false,
        text: text.into(),
    }
}

/// Whether this call must prepare: `fresh`, no stamp, or a stamp whose fingerprint differs
/// from the current one (C3, D4).
fn needs_prepare(root: &Path, env: &TestEnv, fresh: bool) -> bool {
    fresh
        || testenv::stamp_fingerprint().as_deref() != Some(testenv::fingerprint(root, env).as_str())
}

/// Runs the Test environment's preparation under `root`, with `extra` variables, and returns
/// the seconds it took.
///
/// # Errors
///
/// Returns the C4 `phase: prepare` text: the step's exit code, the failure, the tail of the
/// preparation log and the log's path.
pub fn prepare_now(root: &Path, env: &TestEnv, extra: &[(String, String)]) -> Result<u64, String> {
    let budget = Duration::from_secs(env.prepare.timeout.min(TOTAL_MAX_S));
    testenv::prepare_with(root, env, budget, extra)
        .map(|p| p.seconds)
        .map_err(|message| prepare_failure(&message))
}

/// The C4 text for a failed preparation.
fn prepare_failure(message: &str) -> String {
    let log = testenv::log_path();
    let tail = gitsafe::tail_lines(&std::fs::read(&log).unwrap_or_default(), false);
    let mut out = format!(
        "phase: prepare\nexit_code: {}\nerror: {message}\n",
        exit_code_of(message)
    );
    if !tail.is_empty() {
        out.push_str(&tail);
        out.push('\n');
    }
    out.push_str(&format!("full preparation output: {}", log.display()));
    out
}

/// The exit code a preparation message reports for the `exit_code` line. `testenv` words its
/// failures as `failed with exit code N`, `failed with a signal`, or as running past the budget.
fn exit_code_of(message: &str) -> String {
    if let Some(rest) = message.split("exit code ").nth(1) {
        return rest
            .chars()
            .take_while(|c| c.is_ascii_digit() || *c == '-')
            .collect();
    }
    if message.contains("failed with a signal") {
        "signal".to_string()
    } else if message.contains("budget") {
        "timed out".to_string()
    } else {
        "none".to_string()
    }
}

/// Checks one call's arguments against the target before anything runs (C4).
///
/// A plain argument is accepted. A `flags` entry is accepted alone. A `value_flags` entry takes
/// the next argument as its value. Anything else starting with `-` is refused, and the message
/// names the flags the target allows.
///
/// # Errors
///
/// Returns a sentence naming the refused argument and the allowed flags, or the value flag that
/// has no value.
pub fn check_args(name: &str, target: &Target, args: &[String]) -> Result<(), String> {
    let mut i = 0;
    while i < args.len() {
        let arg = args[i].as_str();
        if !arg.starts_with('-') || target.flags.iter().any(|f| f == arg) {
            i += 1;
        } else if target.value_flags.iter().any(|f| f == arg) {
            if i + 1 >= args.len() {
                return Err(format!(
                    "target `{name}`: flag {arg} takes a value; pass it as the next argument"
                ));
            }
            i += 2;
        } else {
            return Err(format!(
                "target `{name}` does not allow `{arg}`; {}",
                allowed_flags(target)
            ));
        }
    }
    Ok(())
}

fn allowed_flags(target: &Target) -> String {
    let all: Vec<&str> = target
        .flags
        .iter()
        .chain(&target.value_flags)
        .map(String::as_str)
        .collect();
    if all.is_empty() {
        "this target allows no flags".to_string()
    } else {
        format!("allowed flags: {}", all.join(", "))
    }
}

/// The target's command with each checked argument single-quoted and appended (C4).
pub fn command_line(target: &Target, args: &[String]) -> String {
    let mut line = target.run.clone();
    for arg in args {
        line.push(' ');
        line.push_str(&testenv::shell_quote(arg));
    }
    line
}

fn target_names(env: &TestEnv) -> String {
    if env.targets.is_empty() {
        "none".to_string()
    } else {
        env.targets.keys().cloned().collect::<Vec<_>>().join(", ")
    }
}

/// Runs one target: checks its arguments, prepares when needed, and runs the command (C4).
///
/// The budget is the target's `timeout`, plus the preparation's `timeout` when this call
/// prepares, and never more than [`TOTAL_MAX_S`] in all. `extra` is applied to preparation
/// and run alike; `run_tests` passes none, and `baseline_check` passes the shared-dependency
/// guard of its worktree.
pub async fn execute(
    root: &Path,
    env: &TestEnv,
    name: &str,
    args: &[String],
    fresh: bool,
    extra: &[(String, String)],
) -> Report {
    let Some(target) = env.targets.get(name) else {
        return fail(format!(
            "unknown target `{name}`; targets: {}",
            target_names(env)
        ));
    };
    if let Err(message) = check_args(name, target, args) {
        return fail(message);
    }
    let script = command_line(target, args);
    let prepares = needs_prepare(root, env, fresh);
    let started = Instant::now();
    let prepare_s = if prepares { env.prepare.timeout } else { 0 };
    let total = Duration::from_secs((target.timeout + prepare_s).min(TOTAL_MAX_S));
    let prepared = if prepares {
        match prepare_now(root, env, extra) {
            Ok(seconds) => Some(seconds),
            Err(text) => return fail(text),
        }
    } else {
        None
    };
    let left = total
        .saturating_sub(started.elapsed())
        .min(Duration::from_secs(target.timeout));
    if left.is_zero() {
        return fail(
            "phase: run\nexit_code: none\nerror: the call's time budget is spent before the target starts",
        );
    }

    let vars = testenv::command_env(root, env, extra);
    let mut cmd = tokio::process::Command::new("sh");
    cmd.args(["-c", &format!("exec 2>&1; {script}")])
        .current_dir(root.join(&target.cwd))
        .envs(vars.iter().map(|(k, v)| (k.as_str(), v.as_str())));
    let cap = match gitsafe::capture(cmd, left).await {
        Ok(cap) => cap,
        Err(message) => return fail(format!("phase: run\nexit_code: none\nerror: {message}")),
    };

    let exit = if cap.timed_out {
        "timed out".to_string()
    } else {
        cap.status.clone()
    };
    let mut text = format!("phase: run\nexit_code: {exit}\n");
    if let Some(seconds) = prepared {
        text.push_str(&format!("prepared in {seconds}s; cached for later calls\n"));
    }
    text.push_str(&gitsafe::tail_lines(&cap.bytes, cap.timed_out));
    Report {
        success: !cap.timed_out && exit == "0",
        text,
    }
}

/// Reads a JSON `args` value as a list of strings. Absent or null is an empty list.
///
/// # Errors
///
/// Returns a sentence for the model when the value is not a list of strings.
pub fn string_args(value: Option<&serde_json::Value>) -> Result<Vec<String>, String> {
    const BAD: &str = "run_tests `args` must be a list of strings";
    match value {
        None | Some(serde_json::Value::Null) => Ok(Vec::new()),
        Some(serde_json::Value::Array(items)) => items
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_string)
                    .ok_or_else(|| BAD.to_string())
            })
            .collect(),
        Some(_) => Err(BAD.to_string()),
    }
}

/// The description the tool lists, built from the declaration (C4).
fn describe(env: &TestEnv) -> String {
    let mut out = String::from(
        "Run one named test target from this repository's .fabro/test.toml, inside its Test \
         environment: the services, variables and preparation the target needs, which a bare \
         test command does not get. Preparation runs once per sandbox and is cached; fresh: \
         true prepares again. Each entry of args is passed to the target's command.\nTargets:\n",
    );
    for (name, target) in &env.targets {
        out.push_str(&format!("- {name}: {}", target.about));
        let mut allowed: Vec<String> = target.flags.clone();
        allowed.extend(
            target
                .value_flags
                .iter()
                .map(|f| format!("{f} (takes a value)")),
        );
        if !allowed.is_empty() {
            out.push_str(&format!(" (flags: {})", allowed.join(", ")));
        }
        out.push('\n');
    }
    out.trim_end().to_string()
}

/// The `run_tests` description and input schema, built from the checkout's declaration at
/// `list_tools` time. With no checkout, no file or an invalid file, the description says so
/// and `target` is a free string.
pub fn tool() -> (String, serde_json::Map<String, serde_json::Value>) {
    let (description, names) = match code::checkout() {
        Err(e) => (
            format!("No repository checkout was found ({e}), so the targets are unknown."),
            Vec::new(),
        ),
        Ok(root) => match TestEnv::load(&root) {
            Ok(None) => (NO_DECLARATION.to_string(), Vec::new()),
            Err(e) => (
                format!("This repository's .fabro/test.toml is invalid: {e}. Use the narrowest check through shell until it is fixed."),
                Vec::new(),
            ),
            Ok(Some(env)) if env.targets.is_empty() => (
                "This repository's .fabro/test.toml declares no targets; run the narrowest check through shell.".to_string(),
                Vec::new(),
            ),
            Ok(Some(env)) => (describe(&env), env.targets.keys().cloned().collect()),
        },
    };
    let target_schema = if names.is_empty() {
        serde_json::json!({"type": "string", "description": "The name of a test target."})
    } else {
        serde_json::json!({"type": "string", "enum": names, "description": "The name of a test target."})
    };
    let schema = serde_json::json!({
        "type": "object",
        "properties": {
            "target": target_schema,
            "args": {"type": "array", "items": {"type": "string"},
                     "description": "Arguments for the target: plain values such as test paths or node ids, and only the flags the target allows."},
            "fresh": {"type": "boolean",
                      "description": "Prepare the Test environment again before running, even when it is cached."}
        },
        "required": ["target"],
        "additionalProperties": false
    });
    let map = schema.as_object().cloned().unwrap_or_default();
    (description, map)
}

/// Runs the tool for one call's arguments. A failing target or a failed preparation is an
/// error result, so the model sees it as one.
///
/// # Errors
///
/// Returns the reason the call was refused, or the failing result text.
pub async fn call(args: &serde_json::Map<String, serde_json::Value>) -> Result<String, String> {
    let root = code::checkout()?;
    let target = args
        .get("target")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if target.is_empty() {
        return Err("run_tests needs a `target` naming one of the test targets".to_string());
    }
    let argv = string_args(args.get("args"))?;
    let fresh = args.get("fresh").and_then(|v| v.as_bool()).unwrap_or(false);
    let Some(env) = TestEnv::load(&root)? else {
        return Err(NO_DECLARATION.to_string());
    };
    let report = execute(&root, &env, target, &argv, fresh, &[]).await;
    if report.success {
        Ok(report.text)
    } else {
        Err(report.text)
    }
}

/// `fabro-io run-tests TARGET [args…] [--fresh]` (C4). Prints the result text; exits 0 only
/// when the target passed.
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut fresh = false;
    let mut positional: Vec<String> = Vec::new();
    for arg in args {
        if arg == "--fresh" {
            fresh = true;
        } else {
            positional.push(arg.clone());
        }
    }
    if positional.is_empty() {
        eprintln!("usage: fabro-io run-tests TARGET [args...] [--fresh]");
        return ExitCode::from(64);
    }
    let target = positional.remove(0);
    let root = match testenv::checkout_root() {
        Ok(root) => root,
        Err(message) => {
            eprintln!("run-tests: {message}");
            return ExitCode::from(64);
        }
    };
    let env = match TestEnv::load(&root) {
        Ok(Some(env)) => env,
        Ok(None) => {
            eprintln!("run-tests: {NO_DECLARATION}");
            return ExitCode::FAILURE;
        }
        Err(message) => {
            eprintln!("run-tests: {message}");
            return ExitCode::FAILURE;
        }
    };
    let rt = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("run-tests: could not start tokio runtime: {e}");
            return ExitCode::FAILURE;
        }
    };
    let report = rt.block_on(execute(&root, &env, &target, &positional, fresh, &[]));
    println!("{}", report.text);
    if report.success {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
