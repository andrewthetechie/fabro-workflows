//! `task-check`: run the plan Task validation selected (ADR 0025 C5, C6).
//!
//! `task-plan` decides what Task validation will run; this module runs it. One
//! implementation serves the gate (`--gate`: always prepare fresh, no total budget) and the
//! agent (`task_check`, `fabro-test --task`: use the cached Test environment, 600 s total).
//! The gate's `validate` node reads `mode`, `reason` and `ok` from the result file; the agent
//! gets the same result rendered short.
//!
//! P3: every check and every target runs even after one fails, so the rework feedback lists
//! every failure. A preparation failure does stop the targets, but the checks have already
//! run.

// Rust guideline compliant 2026-07-21

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::gitsafe;
use crate::runtests;
use crate::taskplan::{self, Plan};
use crate::testenv::{self, Check, TestEnv};

/// The MCP name of the tool (C6).
pub const TASK_CHECK: &str = "task_check";
/// The log under the io root: every item's full output, with headers (C5).
pub const LOG_FILE: &str = "task_check.log";
/// The total budget an agent call may spend, seconds (C5). Under the MCP `tool_timeout` of
/// 660 s, so a slow selection returns this module's own budget answer, not a silent timeout.
const TOOL_MAX_S: u64 = 600;
/// The gate's own ceiling, seconds: the `validate` node's `timeout="20m"` (C7).
const GATE_MAX_S: u64 = 1200;

/// The C5 result: the plan, plus what ran.
#[derive(Clone, Debug, Serialize)]
pub struct CheckResult {
    /// The plan this result is for (C4).
    #[serde(flatten)]
    pub plan: Plan,
    /// True when everything that ran passed; `null` in `mode: full`, where nothing runs.
    pub ok: Option<bool>,
    /// Whole seconds the preparation took, 0 when it was cached or not needed.
    pub prepared_seconds: u64,
    /// The preparation's outcome, absent when no target was selected or it was cached.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prepare: Option<PrepareResult>,
    /// One entry per check and target, in the order they ran.
    pub results: Vec<ItemResult>,
    /// The path of the full output log.
    pub log: String,
}

/// The preparation's outcome (C5).
#[derive(Clone, Debug, Serialize)]
pub struct PrepareResult {
    /// 0 on success, 1 on failure.
    pub exit_code: i32,
    /// The failure text, when it failed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// One check or target (C5).
#[derive(Clone, Debug, Serialize)]
pub struct ItemResult {
    /// `check` or `target`.
    pub kind: &'static str,
    /// The declaration's name for it.
    pub name: String,
    /// Its exit code; absent when it was not run.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i32>,
    /// Whole seconds it took.
    pub seconds: u64,
    /// The last lines of output, on a failing item.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tail: Option<String>,
    /// Why it did not run (`budget`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub not_run: Option<String>,
}

/// The log's path under the io root (C5).
#[must_use]
pub fn log_path() -> PathBuf {
    crate::common::io_root().join(LOG_FILE)
}

/// Runs the plan (C5). `--gate` always prepares fresh and applies no total budget; without
/// it the Test environment is reused when its stamp matches, and the call stops at
/// [`TOOL_MAX_S`].
///
/// # Errors
///
/// Returns a sentence when the plan is scoped but the declaration is gone, the environment
/// lock cannot be taken within the call's ceiling, the log cannot be created, or a command
/// cannot be started or waited on.
pub async fn check(
    root: &Path,
    env: Option<&TestEnv>,
    plan: &Plan,
    gate: bool,
    base: &str,
) -> Result<CheckResult, String> {
    let ceiling = if gate { GATE_MAX_S } else { TOOL_MAX_S };
    check_with_budget(root, env, plan, gate, base, ceiling).await
}

/// [`check`] with an explicit total ceiling in seconds.
///
/// The tests use it to exercise the budget cut without spending ten minutes; `--gate` never
/// applies a total budget, so its ceiling is only the environment lock's.
///
/// # Errors
///
/// As [`check`].
pub async fn check_with_budget(
    root: &Path,
    env: Option<&TestEnv>,
    plan: &Plan,
    gate: bool,
    base: &str,
    ceiling: u64,
) -> Result<CheckResult, String> {
    let mut result = CheckResult {
        plan: plan.clone(),
        ok: Some(true),
        prepared_seconds: 0,
        prepare: None,
        results: Vec::new(),
        log: log_path().display().to_string(),
    };
    if plan.mode == "full" {
        result.ok = None;
        return Ok(result);
    }
    let Some(env) = env else {
        return Err("the plan is scoped but .fabro/test.toml is gone".to_string());
    };

    let deadline = Instant::now() + Duration::from_secs(ceiling);
    let _lock = testenv::EnvLock::acquire_async(deadline).await?;

    let path = log_path();
    let mut log = std::fs::File::create(&path)
        .map_err(|e| format!("cannot create {}: {e}", path.display()))?;

    // Checks first, then preparation, then targets (C5). Every item runs even when an
    // earlier one failed (P3).
    for planned in &plan.checks {
        let Some(check) = env.checks.get(&planned.name) else {
            continue;
        };
        let left = item_limit(check.timeout, gate, deadline);
        if left.is_zero() {
            result.results.push(not_run("check", &planned.name));
            continue;
        }
        let script = check_command_line(check, &planned.args);
        let vars = testenv::command_env(
            root,
            env,
            &[("FABRO_TASK_BASE".to_string(), base.to_string())],
        );
        let item = run_item(
            "check",
            &planned.name,
            &script,
            &root.join(&check.cwd),
            &vars,
            left,
            &mut log,
        )
        .await?;
        if item.exit_code != Some(0) {
            result.ok = Some(false);
        }
        result.results.push(item);
    }

    if !plan.targets.is_empty() {
        let prepares = gate || runtests::needs_prepare(root, env, false).await;
        if prepares {
            let budget = Duration::from_secs(env.prepare.timeout).min(
                deadline.saturating_duration_since(Instant::now()),
            );
            match runtests::prepare_now(root, env, &[], budget).await {
                Ok(seconds) => {
                    result.prepared_seconds = seconds;
                    result.prepare = Some(PrepareResult {
                        exit_code: 0,
                        error: None,
                    });
                    let _ = writeln!(log, "# prepare ok in {seconds}s");
                }
                Err(text) => {
                    result.prepare = Some(PrepareResult {
                        exit_code: 1,
                        error: Some(text.clone()),
                    });
                    result.ok = Some(false);
                    let _ = writeln!(log, "# prepare failed\n{text}");
                    return Ok(result);
                }
            }
        }
    }

    for planned in &plan.targets {
        let Some(target) = env.targets.get(&planned.name) else {
            continue;
        };
        let left = item_limit(target.timeout, gate, deadline);
        if left.is_zero() {
            result.results.push(not_run("target", &planned.name));
            continue;
        }
        // Exactly what `run_tests` builds: the args are paths, so no flag is ever added.
        let script = runtests::command_line(target, &planned.args);
        let vars = testenv::command_env(root, env, &[]);
        let item = run_item(
            "target",
            &planned.name,
            &script,
            &root.join(&target.cwd),
            &vars,
            left,
            &mut log,
        )
        .await?;
        if item.exit_code != Some(0) {
            result.ok = Some(false);
        }
        result.results.push(item);
    }

    Ok(result)
}

/// The check's command with each planned path single-quoted and appended.
fn check_command_line(check: &Check, args: &[String]) -> String {
    let mut line = check.run.clone();
    for arg in args {
        line.push(' ');
        line.push_str(&testenv::shell_quote(arg));
    }
    line
}

/// An item's own `timeout`, cut to the call's remaining budget unless this is the gate.
fn item_limit(timeout: u64, gate: bool, deadline: Instant) -> Duration {
    let own = Duration::from_secs(timeout);
    if gate {
        own
    } else {
        own.min(deadline.saturating_duration_since(Instant::now()))
    }
}

fn not_run(kind: &'static str, name: &str) -> ItemResult {
    ItemResult {
        kind,
        name: name.to_string(),
        exit_code: None,
        seconds: 0,
        tail: None,
        not_run: Some("budget".to_string()),
    }
}

/// Runs one item, tee's its output into the log, and returns its result.
async fn run_item(
    kind: &'static str,
    name: &str,
    script: &str,
    cwd: &Path,
    vars: &[(String, String)],
    limit: Duration,
    log: &mut std::fs::File,
) -> Result<ItemResult, String> {
    let _ = writeln!(log, "\n# {kind} {name}");
    let mut cmd = tokio::process::Command::new("sh");
    cmd.args(["-c", &format!("exec 2>&1; {script}")])
        .current_dir(cwd)
        .envs(vars.iter().map(|(k, v)| (k.as_str(), v.as_str())));
    let started = Instant::now();
    let cap = gitsafe::capture(cmd, limit).await?;
    let seconds = started.elapsed().as_secs();
    let _ = log.write_all(&cap.bytes);
    let _ = writeln!(log);
    let code = exit_code(&cap);
    let tail = if code == 0 {
        None
    } else {
        Some(gitsafe::tail_lines(&cap.bytes, cap.timed_out))
    };
    Ok(ItemResult {
        kind,
        name: name.to_string(),
        exit_code: Some(code),
        seconds,
        tail,
        not_run: None,
    })
}

/// The exit code `capture` reported: its number, 124 for a timeout, 1 otherwise.
fn exit_code(cap: &gitsafe::Captured) -> i32 {
    if let Ok(code) = cap.status.parse::<i32>() {
        code
    } else if cap.timed_out {
        124
    } else {
        1
    }
}

/// The short rendering the `task_check` tool returns (C6): one line per item with its exit
/// code, the failing items' tails, and the `untested` list.
#[must_use]
pub fn render(result: &CheckResult) -> String {
    if result.plan.mode == "full" {
        return format!(
            "This change needs the full suite ({}); the gate will run `setup.sh && ci.sh`. \
             Run the targets that cover your change with run_tests.",
            result.plan.reason
        );
    }
    let mut out = String::new();
    for item in &result.results {
        if let Some(reason) = &item.not_run {
            out.push_str(&format!("{} {}: not run ({reason})\n", item.kind, item.name));
            continue;
        }
        if let Some(code) = item.exit_code {
            out.push_str(&format!("{} {}: exit {code}\n", item.kind, item.name));
            if code != 0
                && let Some(tail) = &item.tail
            {
                out.push_str(tail);
                out.push('\n');
            }
        }
    }
    if let Some(prepare) = &result.prepare
        && prepare.exit_code != 0
    {
        out.push_str("prepare: failed\n");
        if let Some(error) = &prepare.error {
            out.push_str(error);
            out.push('\n');
        }
    }
    if result.results.is_empty() && result.prepare.is_none() {
        out.push_str("nothing to run\n");
    }
    if !result.plan.untested.is_empty() {
        out.push_str(&format!(
            "untested (no importing test): {}\n",
            result.plan.untested.join(", ")
        ));
    }
    out.push_str(if result.ok == Some(true) {
        "task_check: ok"
    } else {
        "task_check: not ok"
    });
    out
}

/// The `task_check` tool (C6). A red result is still an answer: the model acts on it.
///
/// # Errors
///
/// Returns a sentence when no checkout is found, the declaration cannot be read, or the run
/// cannot be started.
pub async fn call(args: &serde_json::Map<String, serde_json::Value>) -> Result<String, String> {
    let root = crate::code::checkout()?;
    let base_arg = args
        .get("base")
        .and_then(|v| v.as_str())
        .filter(|base| !base.trim().is_empty());
    let plan = taskplan::current_plan(&root, base_arg).await;
    let env = TestEnv::load(&root)?;
    let result = check(&root, env.as_ref(), &plan, false, &plan.base).await?;
    Ok(render(&result))
}

/// `fabro-io task-check [--gate] [--base SHA] [--out FILE] [--root DIR]` (C5).
///
/// Exits 0 when the plan is full or everything passed, 1 when something failed, 2 on an
/// internal error.
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut gate = false;
    let mut base_arg: Option<String> = None;
    let mut out_arg: Option<PathBuf> = None;
    let mut root_arg: Option<PathBuf> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--gate" => gate = true,
            "--base" | "--out" | "--root" => {
                let flag = args[i].clone();
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("task-check: {flag} needs a value");
                    return ExitCode::from(64);
                };
                match flag.as_str() {
                    "--base" => base_arg = Some(value.clone()),
                    "--out" => out_arg = Some(PathBuf::from(value)),
                    _ => root_arg = Some(PathBuf::from(value)),
                }
            }
            other => {
                eprintln!("task-check: unexpected argument '{other}'");
                return ExitCode::from(64);
            }
        }
        i += 1;
    }
    let root = match root_arg {
        Some(root) => root,
        None => match testenv::checkout_root() {
            Ok(root) => root,
            Err(message) => {
                eprintln!("task-check: {message}");
                return ExitCode::from(64);
            }
        },
    };
    let rt = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("task-check: cannot start a runtime: {e}");
            return ExitCode::from(2);
        }
    };
    let result = rt.block_on(async {
        let plan = taskplan::current_plan(&root, base_arg.as_deref()).await;
        let env = TestEnv::load(&root).ok().flatten();
        check(&root, env.as_ref(), &plan, gate, &plan.base).await
    });
    let result = match result {
        Ok(result) => result,
        Err(message) => {
            eprintln!("task-check: {message}");
            return ExitCode::from(2);
        }
    };
    let json = match serde_json::to_string(&result) {
        Ok(json) => json,
        Err(e) => {
            eprintln!("task-check: cannot serialize the result: {e}");
            return ExitCode::from(2);
        }
    };
    if let Some(path) = out_arg
        && let Err(e) = std::fs::write(&path, &json)
    {
        eprintln!("task-check: cannot write {}: {e}", path.display());
        return ExitCode::from(2);
    }
    println!("{json}");
    if result.plan.mode == "full" || result.ok == Some(true) {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
