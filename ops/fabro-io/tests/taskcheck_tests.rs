//! `task-check`: running a plan (ADR 0025 C5). Each test uses a real `.fabro/test.toml`
//! whose commands are `sh` one-liners, and an isolated `FABRO_IO_ROOT`.

mod common;

use std::future::Future;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use fabro_io::taskcheck;
use fabro_io::taskplan::{Plan, PlannedCheck, PlannedTarget};
use fabro_io::testenv::TestEnv;

fn lock() -> std::sync::MutexGuard<'static, ()> {
    common::LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

fn write(root: &Path, rel: &str, content: &str) {
    let path = root.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn io_root_for(tag: &str) -> PathBuf {
    let dir = common::temp_root(tag);
    unsafe {
        std::env::set_var("FABRO_IO_ROOT", &dir);
    }
    dir
}

fn load(root: &Path) -> TestEnv {
    TestEnv::load(root).unwrap().expect("test.toml exists")
}

fn block<F: Future>(future: F) -> F::Output {
    tokio::runtime::Runtime::new().unwrap().block_on(future)
}

fn plan(checks: &[(&str, &[&str])], targets: &[(&str, &[&str])]) -> Plan {
    Plan {
        version: 1,
        mode: "scoped".to_string(),
        reason: String::new(),
        base: "BASE".to_string(),
        changed: Vec::new(),
        checks: checks
            .iter()
            .map(|(name, args)| PlannedCheck {
                name: (*name).to_string(),
                args: args.iter().map(|a| (*a).to_string()).collect(),
            })
            .collect(),
        targets: targets
            .iter()
            .map(|(name, args)| PlannedTarget {
                name: (*name).to_string(),
                args: args.iter().map(|a| (*a).to_string()).collect(),
                whole: false,
                why: Default::default(),
            })
            .collect(),
        untested: Vec::new(),
        unchecked: Vec::new(),
        skipped: Vec::new(),
    }
}

#[test]
fn a_green_run_marks_every_item_ok() {
    let _g = lock();
    let io = io_root_for("tc-green");
    let root = common::temp_root("tc-green-root");
    write(
        &root,
        ".fabro/test.toml",
        "version = 1\n[checks.c]\nrun=\"exit 0\"\nwatch=[\"**\"]\n\
         [targets.t]\nabout=\"t\"\nrun=\"exit 0\"\ntests=[\"src/**\"]\n",
    );
    let env = load(&root);
    let result = block(taskcheck::check(
        &root,
        Some(&env),
        &plan(&[("c", &[])], &[("t", &[])]),
        false,
        "BASE",
    ))
    .unwrap();
    assert_eq!(result.ok, Some(true));
    assert_eq!(result.results.len(), 2);
    assert_eq!(result.results[0].kind, "check");
    assert_eq!(result.results[1].kind, "target");
    assert!(result.results.iter().all(|i| i.exit_code == Some(0)));
    assert!(result.results.iter().all(|i| i.tail.is_none()));

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn every_item_runs_even_after_one_fails() {
    let _g = lock();
    let io = io_root_for("tc-p3");
    let root = common::temp_root("tc-p3-root");
    write(
        &root,
        ".fabro/test.toml",
        "version = 1\n[checks.red]\nrun=\"exit 1\"\nwatch=[\"**\"]\n\
         [checks.green]\nrun=\"exit 0\"\nwatch=[\"**\"]\n\
         [targets.t]\nabout=\"t\"\nrun=\"exit 0\"\ntests=[\"src/**\"]\n",
    );
    let env = load(&root);
    let result = block(taskcheck::check(
        &root,
        Some(&env),
        &plan(&[("red", &[]), ("green", &[])], &[("t", &[])]),
        false,
        "BASE",
    ))
    .unwrap();
    assert_eq!(result.ok, Some(false));
    assert_eq!(result.results.len(), 3, "every item ran (P3)");
    assert_eq!(result.results[0].exit_code, Some(1));
    assert_eq!(result.results[0].name, "red");
    assert_eq!(result.results[1].exit_code, Some(0));
    assert_eq!(result.results[2].exit_code, Some(0), "the target still ran");

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn a_preparation_failure_runs_the_checks_but_not_the_targets() {
    let _g = lock();
    let io = io_root_for("tc-prepfail");
    let root = common::temp_root("tc-prepfail-root");
    write(
        &root,
        ".fabro/test.toml",
        "version = 1\n[prepare]\nrun=[\"false\"]\n\
         [checks.c]\nrun=\"exit 0\"\nwatch=[\"**\"]\n\
         [targets.t]\nabout=\"t\"\nrun=\"touch ran_target\"\ntests=[\"src/**\"]\n",
    );
    let env = load(&root);
    let result = block(taskcheck::check(
        &root,
        Some(&env),
        &plan(&[("c", &[])], &[("t", &[])]),
        false,
        "BASE",
    ))
    .unwrap();
    assert_eq!(result.ok, Some(false));
    assert_eq!(result.results.len(), 1, "only the check ran");
    assert_eq!(result.results[0].kind, "check");
    assert_eq!(result.prepare.as_ref().unwrap().exit_code, 1);
    assert!(!root.join("ran_target").exists(), "the target did not run");

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn gate_always_prepares_and_a_cached_call_does_not() {
    let _g = lock();
    let io = io_root_for("tc-gate");
    let root = common::temp_root("tc-gate-root");
    write(
        &root,
        ".fabro/test.toml",
        "version = 1\n[prepare]\nrun=[\"echo x >> counter\"]\n\
         [targets.t]\nabout=\"t\"\nrun=\"exit 0\"\ntests=[\"src/**\"]\n",
    );
    let p = plan(&[], &[("t", &[])]);
    let calls = |gate: bool| {
        let env = load(&root);
        block(taskcheck::check(&root, Some(&env), &p, gate, "BASE")).unwrap()
    };
    // Only the first non-gate call prepares; the stamp matches after it.
    calls(false);
    calls(false);
    assert_eq!(
        std::fs::read_to_string(root.join("counter")).unwrap().lines().count(),
        1,
        "a cached call does not prepare"
    );
    calls(true);
    calls(true);
    assert_eq!(
        std::fs::read_to_string(root.join("counter")).unwrap().lines().count(),
        3,
        "--gate always prepares"
    );

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn items_that_do_not_fit_the_budget_are_reported_not_run() {
    let _g = lock();
    let io = io_root_for("tc-budget");
    let root = common::temp_root("tc-budget-root");
    write(
        &root,
        ".fabro/test.toml",
        "version = 1\n[targets.slow]\nabout=\"slow\"\nrun=\"sleep 5\"\ntimeout=5\ntests=[\"src/**\"]\n\
         [targets.second]\nabout=\"second\"\nrun=\"exit 0\"\ntests=[\"src/**\"]\n",
    );
    let env = load(&root);
    let result = block(taskcheck::check_with_budget(
        &root,
        Some(&env),
        &plan(&[], &[("slow", &[]), ("second", &[])]),
        false,
        "BASE",
        2,
    ))
    .unwrap();
    assert_eq!(result.results[0].exit_code, Some(124), "the slow target timed out");
    assert_eq!(result.results[1].not_run.as_deref(), Some("budget"));
    assert_eq!(result.results[1].exit_code, None);
    assert_eq!(result.ok, Some(false));

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn a_full_plan_runs_nothing() {
    let _g = lock();
    let io = io_root_for("tc-full");
    let root = common::temp_root("tc-full-root");
    write(
        &root,
        ".fabro/test.toml",
        "version = 1\n[targets.t]\nabout=\"t\"\nrun=\"touch marker\"\ntests=[\"src/**\"]\n",
    );
    let env = load(&root);
    let mut p = plan(&[], &[("t", &[])]);
    p.mode = "full".to_string();
    p.reason = "no_config".to_string();
    let result = block(taskcheck::check(&root, Some(&env), &p, false, "BASE")).unwrap();
    assert_eq!(result.ok, None);
    assert!(result.results.is_empty());
    assert!(result.prepare.is_none());
    assert!(!root.join("marker").exists(), "mode: full runs nothing");

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn the_render_is_the_short_tool_answer() {
    let _g = lock();
    let io = io_root_for("tc-render");
    let root = common::temp_root("tc-render-root");
    write(
        &root,
        ".fabro/test.toml",
        "version = 1\n[checks.c]\nrun=\"echo boom; exit 1\"\nwatch=[\"**\"]\n",
    );
    let env = load(&root);
    let mut p = plan(&[("c", &[])], &[]);
    p.untested = vec!["backend/app/lonely.py".to_string()];
    let result = block(taskcheck::check(&root, Some(&env), &p, false, "BASE")).unwrap();
    let text = taskcheck::render(&result);
    assert!(text.contains("check c: exit 1"), "{text}");
    assert!(text.contains("boom"), "the failing tail: {text}");
    assert!(text.contains("untested (no importing test): backend/app/lonely.py"), "{text}");
    assert!(text.ends_with("task_check: not ok"), "{text}");

    let mut full = p.clone();
    full.mode = "full".to_string();
    full.reason = "full_glob:bun.lock".to_string();
    let mut full_result = result.clone();
    full_result.plan = full;
    full_result.ok = None;
    let text = taskcheck::render(&full_result);
    assert!(text.contains("full suite (full_glob:bun.lock)"), "{text}");
    assert!(text.contains("setup.sh && ci.sh"), "{text}");

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

/// `fabro-test --task` routes to `fabro-io task-check` with `--task` removed; every other
/// call goes to `run-tests`.
#[test]
fn fabro_test_task_reaches_task_check() {
    let _g = lock();
    let dir = common::temp_root("tc-alias");
    let stub = dir.join("fabro-io");
    std::fs::write(&stub, "#!/bin/sh\necho \"args=$*\"\n").unwrap();
    std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).unwrap();
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("../profile-images/fabro-test");
    if !script.exists() {
        // The image build mounts only ops/fabro-io, so the wrapper is not beside the crate.
        // `make check` runs this assertion; the image build does not need to.
        eprintln!("skipped: {} is not present", script.display());
        return;
    }
    let path = format!(
        "{}:{}",
        dir.display(),
        std::env::var("PATH").unwrap_or_default()
    );

    let task = Command::new("sh")
        .arg(&script)
        .args(["--task", "--base", "abc"])
        .env("PATH", &path)
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8_lossy(&task.stdout).trim(),
        "args=task-check --base abc"
    );

    let plain = Command::new("sh")
        .arg(&script)
        .args(["backend", "tests/a.py"])
        .env("PATH", &path)
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8_lossy(&plain.stdout).trim(),
        "args=run-tests backend tests/a.py"
    );

    let _ = std::fs::remove_dir_all(&dir);
}
