//! `task-plan`: what Task validation will run for the current diff (ADR 0025 C1, C4).
//!
//! The per-task gate (`backlog`'s `validate` node) used to run the repository's whole
//! `setup.sh && ci.sh`. Task validation instead runs the repository's checks and the tests
//! that cover the files the task changed, and falls back to the full suite for anything it
//! cannot attribute. This module is the selection: it computes the changed set (C1), asks
//! `fabro-code tests --json` for each seed's tests and conftests (C2), maps them to the
//! `.fabro/test.toml` targets that own them, and prints one JSON plan (C4).
//!
//! Everything here is a fallback to `mode: full`, never an error: the gate must never be
//! misled into running less than the change needs. The rules are in [`plan`], which is pure
//! apart from reading the filesystem, so the cargo tests cover them without git or an index.
//!
//! Contracts: `docs/task-validation/00-overview-and-contracts.md` C1, C4.

// Rust guideline compliant 2026-07-21

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::process::{Command, ExitCode};
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::testenv::{self, TestEnv};
/// Where the task's base commit is written when the gate does not pass `--base` (C1).
pub const TASK_BASE_FILE: &str = "/tmp/fabro/task_base_sha";
/// How long one `fabro-code tests --json` call may take. The wrapper walks the index once
/// per seed; a large diff against a large repository stays well under this.
const CODE_TIMEOUT: Duration = Duration::from_secs(60);
/// P2: a target with more selected tests than this runs whole, with no file arguments.
const WHOLE_TARGET_TESTS: usize = 200;

/// The C4 plan. In `mode: full` the checks and targets are empty and `reason` says why.
#[derive(Clone, Debug, Default, Serialize)]
pub struct Plan {
    /// The schema version, always 1.
    pub version: u32,
    /// `scoped` or `full`.
    pub mode: String,
    /// Empty when scoped; a C4 reason string when full.
    pub reason: String,
    /// The resolved base commit (C1).
    pub base: String,
    /// The changed set, sorted (C1).
    pub changed: Vec<String>,
    /// The checks to run, in file order.
    pub checks: Vec<PlannedCheck>,
    /// The targets to run, in file order.
    pub targets: Vec<PlannedTarget>,
    /// Changed source files the index knows with no importing test (D7).
    pub untested: Vec<String>,
    /// Changed files no check watches and no target covers (D7).
    pub unchecked: Vec<String>,
    /// Selected test files dropped by `[task] skip`.
    pub skipped: Vec<String>,
}

/// One check the plan will run (C4 step 9).
#[derive(Clone, Debug, Serialize)]
pub struct PlannedCheck {
    /// The check's `[checks.<name>]` name.
    pub name: String,
    /// Files appended when the check declares `files = true`; empty otherwise.
    pub args: Vec<String>,
}

/// One target the plan will run (C4 steps 7 and 8).
#[derive(Clone, Debug, Serialize)]
pub struct PlannedTarget {
    /// The target's `[targets.<name>]` name.
    pub name: String,
    /// Test files to pass, relative to the target's `cwd`; empty when `whole`.
    pub args: Vec<String>,
    /// Run the whole target with no arguments.
    pub whole: bool,
    /// Each argument mapped to the changed seeds that selected it.
    pub why: BTreeMap<String, Vec<String>>,
}

/// The C2 object `fabro-code tests --json` prints.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct CodeAnswer {
    /// Per requested seed: whether it is indexed, its tests, and its conftests.
    #[serde(default)]
    pub seeds: BTreeMap<String, SeedAnswer>,
    /// For each reached conftest, every tracked test under its directory.
    #[serde(default)]
    pub conftest_tests: BTreeMap<String, Vec<String>>,
}

/// One seed's answer inside [`CodeAnswer`].
#[derive(Clone, Debug, Default, Deserialize)]
pub struct SeedAnswer {
    /// Whether the seed has a row in the index's `files` table.
    #[serde(default)]
    pub indexed: bool,
    /// The seed's closure files that match `TEST_RE`.
    #[serde(default)]
    pub tests: Vec<String>,
    /// The seed's closure files whose basename is `conftest.py`.
    #[serde(default)]
    pub conftests: Vec<String>,
}

/// The plan for one diff (C4). Pure apart from `root.join(path).exists()`.
///
/// Steps run in order and the first fallback wins, so a `mode: full` plan names the first
/// thing that made scoping unsafe. `env` is `None` when the repository has no
/// `.fabro/test.toml`, which is itself the `no_config` fallback.
#[must_use]
pub fn plan(root: &Path, env: Option<&TestEnv>, changed: &[String], code: &CodeAnswer) -> Plan {
    // Step 1: no declaration, or a declaration without any Task validation key.
    let Some(env) = env.filter(|env| env.declares_task_keys()) else {
        return full(changed, "no_config");
    };

    // Step 2: an empty diff has nothing to run.
    if changed.is_empty() {
        return Plan {
            version: 1,
            mode: "scoped".to_string(),
            changed: Vec::new(),
            ..Plan::default()
        };
    }

    // Step 3: a built-in or declared `full` glob.
    let mut full_globs: Vec<String> = testenv::BUILTIN_FULL_GLOBS
        .iter()
        .map(|g| (*g).to_string())
        .collect();
    full_globs.extend(env.task_full().iter().cloned());
    let full_set = testenv::glob_set(&full_globs);
    if let Some(path) = changed.iter().find(|p| full_set.is_match(p.as_str())) {
        return full(changed, &format!("full_glob:{path}"));
    }

    let skip_set = testenv::glob_set(env.task_skip());

    // Step 4: a deleted or renamed non-test source file (P1). A deleted test file does not
    // escalate: removing a test is the diff's own business, and diff hygiene judges it.
    for path in changed {
        if skip_set.is_match(path.as_str()) || root.join(path).exists() || is_test(path, code) {
            continue;
        }
        return full(changed, &format!("deleted:{path}"));
    }

    // Step 6: the union of every seed's tests, the tests the conftest rule adds, minus
    // `skip`, minus files that no longer exist. `why` keeps the attribution (step 7).
    let mut why: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (seed, answer) in &code.seeds {
        for test in &answer.tests {
            push_why(&mut why, test, seed.clone());
        }
        for conftest in &answer.conftests {
            let Some(tests) = code.conftest_tests.get(conftest) else {
                continue;
            };
            for test in tests {
                push_why(&mut why, test, format!("{seed} via {conftest}"));
            }
        }
    }
    let mut selected: Vec<String> = Vec::new();
    let mut skipped: Vec<String> = Vec::new();
    for test in why.keys() {
        if skip_set.is_match(test.as_str()) {
            skipped.push(test.clone());
        } else if root.join(test).exists() {
            selected.push(test.clone());
        }
    }

    // Step 7: map each selected test to the first target, in file order, that owns it.
    let mut owned: BTreeMap<&str, Vec<&String>> = BTreeMap::new();
    for test in &selected {
        let Some((name, target)) = env
            .targets
            .iter()
            .find(|(_, target)| glob_matches(target.tests.as_deref(), test))
        else {
            return full(changed, &format!("unowned_test:{test}"));
        };
        if relative_to_cwd(test, &target.cwd).is_none() {
            return full(changed, &format!("path_outside_cwd:{test}"));
        }
        owned.entry(name.as_str()).or_default().push(test);
    }

    // Steps 7 and 8, in file order so the plan reads as the declaration does.
    let mut targets: Vec<PlannedTarget> = Vec::new();
    for (name, target) in &env.targets {
        let tests = owned.get(name.as_str()).cloned().unwrap_or_default();
        let watched = changed
            .iter()
            .any(|p| glob_matches(target.watch.as_deref(), p));
        if watched {
            targets.push(PlannedTarget {
                name: name.clone(),
                args: Vec::new(),
                whole: true,
                why: BTreeMap::new(),
            });
            continue;
        }
        if tests.is_empty() {
            continue;
        }
        // P2: the result is the same whole, and the argument list stays inside the limit.
        let whole = tests.len() > WHOLE_TARGET_TESTS;
        let mut args = Vec::new();
        let mut target_why = BTreeMap::new();
        if !whole {
            for test in &tests {
                let rel = relative_to_cwd(test, &target.cwd).unwrap_or_else(|| (*test).clone());
                args.push(rel);
                target_why.insert((*test).clone(), why.get(*test).cloned().unwrap_or_default());
            }
        }
        targets.push(PlannedTarget {
            name: name.clone(),
            args,
            whole,
            why: target_why,
        });
    }

    // Step 9: a check runs when a changed path matches its watch. With `files = true` the
    // existing matching paths are appended, and a check with none is not run.
    let mut checks: Vec<PlannedCheck> = Vec::new();
    for (name, check) in &env.checks {
        let matching: Vec<&String> = changed
            .iter()
            .filter(|p| glob_matches(Some(&check.watch), p))
            .collect();
        if matching.is_empty() {
            continue;
        }
        let args = if check.files {
            let existing: Vec<String> = matching
                .iter()
                .filter(|p| root.join(p.as_str()).exists())
                .filter_map(|p| relative_to_cwd(p, &check.cwd))
                .collect();
            if existing.is_empty() {
                continue;
            }
            existing
        } else {
            Vec::new()
        };
        checks.push(PlannedCheck {
            name: name.clone(),
            args,
        });
    }

    // Step 10: what the selection could not attribute, recorded and never escalated (D7).
    let untested: Vec<String> = code
        .seeds
        .iter()
        .filter(|(seed, answer)| {
            answer.indexed
                && !answer.tests.iter().any(|t| t == *seed)
                && answer.tests.is_empty()
                && answer.conftests.is_empty()
        })
        .map(|(seed, _)| seed.clone())
        .collect();
    let unchecked: Vec<String> = changed
        .iter()
        .filter(|path| {
            !env.checks
                .values()
                .any(|check| glob_matches(Some(&check.watch), path))
                && !env.targets.values().any(|target| {
                    glob_matches(target.tests.as_deref(), path)
                        || glob_matches(target.watch.as_deref(), path)
                })
        })
        .cloned()
        .collect();

    Plan {
        version: 1,
        mode: "scoped".to_string(),
        reason: String::new(),
        base: String::new(),
        changed: changed.to_vec(),
        checks,
        targets,
        untested,
        unchecked,
        skipped,
    }
}

fn full(changed: &[String], reason: &str) -> Plan {
    Plan {
        version: 1,
        mode: "full".to_string(),
        reason: reason.to_string(),
        changed: changed.to_vec(),
        ..Plan::default()
    }
}

fn push_why(why: &mut BTreeMap<String, Vec<String>>, test: &str, by: String) {
    let entry = why.entry(test.to_string()).or_default();
    if !entry.contains(&by) {
        entry.push(by);
    }
}

/// Whether `code` classifies `path` as a test file: its own closure lists it (C2).
fn is_test(path: &str, code: &CodeAnswer) -> bool {
    code.seeds
        .get(path)
        .is_some_and(|seed| seed.tests.iter().any(|t| t == path))
}

fn glob_matches(globs: Option<&[String]>, path: &str) -> bool {
    globs.is_some_and(|globs| testenv::glob_set(globs).is_match(path))
}

/// `path` relative to a target's `cwd`, or `None` when it is outside it.
fn relative_to_cwd(path: &str, cwd: &Path) -> Option<String> {
    let cwd = cwd.to_string_lossy();
    let cwd = cwd.trim_start_matches("./").trim_end_matches('/');
    if cwd.is_empty() || cwd == "." {
        return Some(path.to_string());
    }
    path.strip_prefix(&format!("{cwd}/")).map(str::to_string)
}

/// The changed set (C1): the worktree against `base`, plus untracked files, sorted.
///
/// `--no-renames` makes a rename show its old path as deleted, which step 4 escalates (P1).
pub fn changed_set(root: &Path, base: &str) -> Result<Vec<String>, String> {
    let diff = git(root, &["diff", "--name-only", "--no-renames", base])?;
    let untracked = git(root, &["ls-files", "--others", "--exclude-standard"])?;
    let mut set: BTreeSet<String> = BTreeSet::new();
    for line in diff.lines().chain(untracked.lines()) {
        let line = line.trim();
        if !line.is_empty() {
            set.insert(line.to_string());
        }
    }
    Ok(set.into_iter().collect())
}

/// The C1 base: `--base`, then [`TASK_BASE_FILE`], then `git merge-base HEAD origin/main`.
pub fn resolve_base(root: &Path, explicit: Option<&str>) -> Result<String, String> {
    if let Some(base) = explicit.filter(|b| !b.trim().is_empty()) {
        return Ok(base.trim().to_string());
    }
    if let Ok(text) = std::fs::read_to_string(TASK_BASE_FILE) {
        let text = text.trim();
        if !text.is_empty() {
            return Ok(text.to_string());
        }
    }
    let out = git(root, &["merge-base", "HEAD", "origin/main"])?;
    let base = out.trim();
    if base.is_empty() {
        return Err("git merge-base HEAD origin/main printed nothing".to_string());
    }
    Ok(base.to_string())
}

fn git(root: &Path, args: &[&str]) -> Result<String, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .map_err(|e| format!("cannot run git: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// One `fabro-code tests --json` call for every seed (C2, C4 step 5).
///
/// Deleted paths are included so step 4 can classify them: a seed that is itself a test is
/// listed in its own `tests`, so an absent `tests` entry is the "not a test" answer.
async fn code_answer(root: &Path, seeds: &[String]) -> Result<CodeAnswer, String> {
    let bin = std::env::var("FABRO_CODE_BIN").unwrap_or_else(|_| "fabro-code".to_string());
    let child = tokio::process::Command::new(&bin)
        .args(["tests", "--json"])
        .args(seeds)
        .current_dir(root)
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true)
        .output();
    let out = match tokio::time::timeout(CODE_TIMEOUT, child).await {
        Err(_) => {
            return Err(format!(
                "fabro-code tests --json timed out after {}s",
                CODE_TIMEOUT.as_secs()
            ));
        }
        Ok(Err(e)) => return Err(format!("cannot run {bin}: {e}")),
        Ok(Ok(out)) => out,
    };
    if !out.status.success() {
        let how = out
            .status
            .code()
            .map_or_else(|| "a signal".to_string(), |c| format!("exit code {c}"));
        return Err(format!("fabro-code tests --json failed with {how}"));
    }
    serde_json::from_slice(&out.stdout)
        .map_err(|e| format!("fabro-code tests --json is not the C2 object: {e}"))
}

/// The plan for the repository's current diff, with every fallback already applied (C4).
///
/// This is the one pipeline `task-plan`, `task-check` and the `task_check` tool share: load
/// the declaration, resolve the base, compute the changed set, ask `fabro-code`, and plan.
/// It never errors; an unusable input becomes a `mode: full` plan naming the reason.
pub async fn current_plan(root: &Path, base_arg: Option<&str>) -> Plan {
    let env = match TestEnv::load(root) {
        Ok(env) => env,
        Err(message) => {
            eprintln!("task-plan: {message}");
            return full(&[], "invalid_config");
        }
    };
    if env.as_ref().is_none_or(|env| !env.declares_task_keys()) {
        return full(&[], "no_config");
    }
    let base = match resolve_base(root, base_arg) {
        Ok(base) => base,
        Err(message) => {
            eprintln!("task-plan: {message}");
            return full(&[], "index_unavailable");
        }
    };
    let changed = match changed_set(root, &base) {
        Ok(changed) => changed,
        Err(message) => {
            eprintln!("task-plan: {message}");
            return full(&[], "index_unavailable");
        }
    };
    let skip = testenv::glob_set(env.as_ref().map_or(&[], |env| env.task_skip()));
    let seeds: Vec<String> = changed
        .iter()
        .filter(|path| !skip.is_match(path.as_str()))
        .cloned()
        .collect();
    let code = if seeds.is_empty() {
        CodeAnswer::default()
    } else {
        match code_answer(root, &seeds).await {
            Ok(code) => code,
            Err(message) => {
                eprintln!("task-plan: {message}");
                return full(&changed, "index_unavailable");
            }
        }
    };
    let mut plan = plan(root, env.as_ref(), &changed, &code);
    plan.base = base;
    plan
}

/// The `fabro-io task-plan` subcommand (C4). Always exits 0 unless the arguments are invalid.
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut base_arg: Option<String> = None;
    let mut root_arg: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--base" => {
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("task-plan: --base needs a commit");
                    return ExitCode::from(64);
                };
                base_arg = Some(value.clone());
            }
            "--root" => {
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("task-plan: --root needs a directory");
                    return ExitCode::from(64);
                };
                root_arg = Some(value.clone());
            }
            other => {
                eprintln!("task-plan: unexpected argument '{other}'");
                return ExitCode::from(64);
            }
        }
        i += 1;
    }
    let root = match root_arg {
        Some(dir) => std::path::PathBuf::from(dir),
        None => match testenv::checkout_root() {
            Ok(root) => root,
            Err(message) => {
                eprintln!("task-plan: {message}");
                return ExitCode::from(64);
            }
        },
    };
    let rt = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("task-plan: cannot start a runtime: {e}");
            return ExitCode::FAILURE;
        }
    };
    let plan = rt.block_on(current_plan(&root, base_arg.as_deref()));
    print_plan(&plan)
}

fn print_plan(plan: &Plan) -> ExitCode {
    match serde_json::to_string(plan) {
        Ok(json) => {
            println!("{json}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("task-plan: cannot serialize the plan: {e}");
            ExitCode::FAILURE
        }
    }
}
