//! `run_tests` and `baseline_check` with a target (docs/test-env C4, C5), against a real-git
//! temp repository. The targets are shell commands that append marker lines to files, so no
//! Postgres or project tooling is needed: the counts show what ran and what prepared.

mod common;

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

use fabro_io::gitsafe;
use fabro_io::runtests;
use fabro_io::testenv::TestEnv;

/// Two targets and one preparation step. `unit` appends to `runs.txt` and echoes its arguments,
/// so a test can read both what ran and what the shell received. `prepare` appends to
/// `prepares.txt`; its input is `data.txt`.
const TOML: &str = r#"version = 1

[prepare]
inputs = ["data.txt"]
timeout = 60
run = ["echo prepared >> prepares.txt"]

[targets.unit]
about = "unit tests; pass test names"
run = "echo ran >> runs.txt; echo args:"
flags = ["-x", "-v"]
value_flags = ["-k"]
timeout = 60

[targets.lint]
about = "lint the tree"
run = "echo lint"
timeout = 30

[targets.broken]
about = "always fails"
run = "echo boom; exit 3"
timeout = 30
"#;

fn git(dir: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(["-c", "user.email=t@t", "-c", "user.name=t"])
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// A committed checkout holding `.fabro/test.toml` (`toml`) and `data.txt`.
fn checkout(root: &Path, toml: &str) -> PathBuf {
    let dir = root.join("workspace").join("repo");
    std::fs::create_dir_all(dir.join(".fabro")).unwrap();
    git(&dir, &["init", "-q", "-b", "main", "."]);
    std::fs::write(dir.join(".fabro/test.toml"), toml).unwrap();
    std::fs::write(dir.join("data.txt"), "one\n").unwrap();
    git(&dir, &["add", "-A"]);
    git(&dir, &["commit", "-qm", "base"]);
    dir
}

fn count_lines(path: &Path) -> usize {
    std::fs::read_to_string(path)
        .map(|s| s.lines().count())
        .unwrap_or(0)
}

fn args(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| s.to_string()).collect()
}

/// Points FABRO_IO_ROOT at a fresh directory and returns it. Callers hold `common::LOCK`.
fn io_root_for(tag: &str) -> PathBuf {
    let dir = common::temp_root(tag);
    unsafe {
        std::env::set_var("FABRO_IO_ROOT", &dir);
    }
    dir
}

fn lock() -> std::sync::MutexGuard<'static, ()> {
    common::LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

fn block_on<F: std::future::Future>(f: F) -> F::Output {
    tokio::runtime::Runtime::new().unwrap().block_on(f)
}

fn load(root: &Path) -> TestEnv {
    TestEnv::load(root).unwrap().expect("a declaration")
}

#[test]
fn description_lists_both_targets_with_their_about_text_and_flags() {
    let _g = lock();
    let root = common::temp_root("rt-desc");
    let dir = checkout(&root, TOML);
    unsafe {
        std::env::set_var("FABRO_CODE_ROOT", &dir);
    }
    let (description, schema) = runtests::tool();
    unsafe {
        std::env::remove_var("FABRO_CODE_ROOT");
    }
    assert!(
        description.contains("- unit: unit tests; pass test names"),
        "{description}"
    );
    assert!(
        description.contains("- lint: lint the tree"),
        "{description}"
    );
    assert!(
        description.contains("-x, -v, -k (takes a value)"),
        "{description}"
    );
    let names = schema["properties"]["target"]["enum"].as_array().unwrap();
    assert_eq!(names.len(), 3, "{schema:?}");
    assert!(names.iter().any(|n| n == "unit") && names.iter().any(|n| n == "lint"));
}

#[test]
fn arguments_are_checked_before_anything_runs() {
    let _g = lock();
    let io = io_root_for("rt-args-io");
    let root = common::temp_root("rt-args");
    let dir = checkout(&root, TOML);
    let env = load(&dir);
    let unit = &env.targets["unit"];

    let err = runtests::check_args("unit", unit, &args(&["-n0"])).unwrap_err();
    assert!(
        err.contains("-x") && err.contains("-v") && err.contains("-k"),
        "{err}"
    );
    assert!(runtests::check_args("unit", unit, &args(&["-k", "careers"])).is_ok());
    assert!(runtests::check_args("unit", unit, &args(&["-x", "tests/a.py::t[1]"])).is_ok());
    assert!(
        runtests::check_args("unit", unit, &args(&["-k"])).is_err(),
        "a value flag needs a value"
    );
    let lint = &env.targets["lint"];
    let err = runtests::check_args("lint", lint, &args(&["-x"])).unwrap_err();
    assert!(err.contains("allows no flags"), "{err}");

    let report = block_on(runtests::execute(
        &dir,
        &env,
        "unit",
        &args(&["-n0"]),
        false,
        &[],
    ));
    assert!(!report.success);
    assert!(report.text.contains("-k"), "{}", report.text);
    assert!(
        !dir.join("runs.txt").exists(),
        "a refused call runs nothing"
    );
    assert!(
        !dir.join("prepares.txt").exists(),
        "a refused call prepares nothing"
    );

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn quoted_arguments_reach_the_command_intact() {
    let _g = lock();
    let io = io_root_for("rt-quote-io");
    let root = common::temp_root("rt-quote");
    let dir = checkout(&root, TOML);
    let env = load(&dir);
    let report = block_on(runtests::execute(
        &dir,
        &env,
        "unit",
        &args(&["-k", "it's"]),
        false,
        &[],
    ));
    assert!(report.success, "{}", report.text);
    assert!(report.text.contains("args: -k it's"), "{}", report.text);

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn first_call_prepares_then_cached_then_stale_then_fresh() {
    let _g = lock();
    let io = io_root_for("rt-cache-io");
    let root = common::temp_root("rt-cache");
    let dir = checkout(&root, TOML);
    let env = load(&dir);
    let run = |fresh: bool| {
        block_on(runtests::execute(
            &dir,
            &env,
            "unit",
            &args(&["-k", "careers"]),
            fresh,
            &[],
        ))
    };

    let first = run(false);
    assert!(first.success, "{}", first.text);
    assert!(first.text.contains("prepared in "), "{}", first.text);
    assert!(first.text.contains("args: -k careers"), "{}", first.text);
    assert_eq!(count_lines(&dir.join("prepares.txt")), 1);

    let second = run(false);
    assert!(second.success, "{}", second.text);
    assert!(!second.text.contains("prepared in"), "{}", second.text);
    assert_eq!(
        count_lines(&dir.join("prepares.txt")),
        1,
        "cached: no second preparation"
    );
    assert_eq!(count_lines(&dir.join("runs.txt")), 2);

    std::fs::write(dir.join("data.txt"), "two\n").unwrap();
    let third = run(false);
    assert!(
        third.text.contains("prepared in "),
        "an input changed: {}",
        third.text
    );
    assert_eq!(count_lines(&dir.join("prepares.txt")), 2);

    let fourth = run(true);
    assert!(
        fourth.text.contains("prepared in "),
        "fresh prepares: {}",
        fourth.text
    );
    assert_eq!(count_lines(&dir.join("prepares.txt")), 3);

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn a_failing_run_reports_its_exit_code_and_output() {
    let _g = lock();
    let io = io_root_for("rt-fail-io");
    let root = common::temp_root("rt-fail");
    let dir = checkout(&root, TOML);
    let env = load(&dir);
    let report = block_on(runtests::execute(&dir, &env, "broken", &[], false, &[]));
    assert!(!report.success);
    assert!(
        report.text.starts_with("phase: run\nexit_code: 3\n"),
        "{}",
        report.text
    );
    assert!(report.text.contains("boom"), "{}", report.text);

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn an_unknown_target_names_the_known_ones() {
    let _g = lock();
    let io = io_root_for("rt-unknown-io");
    let root = common::temp_root("rt-unknown");
    let dir = checkout(&root, TOML);
    let env = load(&dir);
    let report = block_on(runtests::execute(&dir, &env, "nope", &[], false, &[]));
    assert!(!report.success);
    assert!(
        report.text.contains("unknown target `nope`") && report.text.contains("unit"),
        "{}",
        report.text
    );

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn a_preparation_past_its_timeout_reports_phase_prepare_and_returns_early() {
    let _g = lock();
    let io = io_root_for("rt-timeout-io");
    let root = common::temp_root("rt-timeout");
    let toml = "version = 1\n[prepare]\ntimeout = 1\nrun = [\"echo starting; sleep 30\"]\n\
                [targets.unit]\nabout = \"unit\"\nrun = \"echo ran\"\n";
    let dir = checkout(&root, toml);
    let env = load(&dir);
    let started = Instant::now();
    let report = block_on(runtests::execute(&dir, &env, "unit", &[], false, &[]));
    assert!(
        started.elapsed() < Duration::from_secs(20),
        "returned after {:?}",
        started.elapsed()
    );
    assert!(!report.success);
    assert!(
        report.text.starts_with("phase: prepare\n"),
        "{}",
        report.text
    );
    assert!(
        report.text.contains("starting"),
        "the tail of the log is in the result: {}",
        report.text
    );
    assert!(
        !io.join("test-env.stamp").exists(),
        "a failed preparation leaves no stamp"
    );

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn a_repository_without_a_declaration_or_with_an_invalid_one_is_told_so() {
    let _g = lock();
    let io = io_root_for("rt-decl-io");
    let root = common::temp_root("rt-decl");
    let dir = root.join("workspace").join("bare");
    std::fs::create_dir_all(&dir).unwrap();
    git(&dir, &["init", "-q", "-b", "main", "."]);
    git(&dir, &["commit", "-q", "--allow-empty", "-m", "base"]);
    unsafe {
        std::env::set_var("FABRO_CODE_ROOT", &dir);
    }
    let (description, schema) = runtests::tool();
    assert!(
        description.contains("declares no test targets"),
        "{description}"
    );
    assert!(
        schema["properties"]["target"].get("enum").is_none(),
        "a free string without a file"
    );
    let no_file = block_on(runtests::call(
        &serde_json::json!({"target": "unit"})
            .as_object()
            .unwrap()
            .clone(),
    ));
    assert!(no_file.unwrap_err().contains("declares no test targets"));

    std::fs::create_dir_all(dir.join(".fabro")).unwrap();
    std::fs::write(dir.join(".fabro/test.toml"), "version = 2\n").unwrap();
    let (description, _) = runtests::tool();
    assert!(
        description.contains("is invalid") && description.contains("version must be 1"),
        "{description}"
    );
    let bad = block_on(runtests::call(
        &serde_json::json!({"target": "unit"})
            .as_object()
            .unwrap()
            .clone(),
    ));
    assert!(bad.unwrap_err().contains("version must be 1"));
    unsafe {
        std::env::remove_var("FABRO_CODE_ROOT");
    }

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn baseline_target_prepares_the_base_and_the_next_run_tests_prepares_again() {
    let _g = lock();
    let io = io_root_for("rt-base-io");
    let root = common::temp_root("rt-base");
    let dir = checkout(&root, TOML);
    let base = git(&dir, &["rev-parse", "HEAD"]);

    let out = block_on(gitsafe::baseline_target(
        &io,
        &dir,
        &base,
        "unit",
        &args(&["-k", "careers"]),
    ))
    .unwrap();
    assert!(
        out.contains("prepared in ") && out.contains("args: -k careers"),
        "{out}"
    );
    let wt = io.join("base-tree");
    assert_eq!(
        count_lines(&wt.join("prepares.txt")),
        1,
        "the base worktree was prepared"
    );
    assert_eq!(
        count_lines(&dir.join("prepares.txt")),
        0,
        "the checkout was not touched"
    );

    // The stamp now carries the base worktree's fingerprint, so the agent's call prepares again.
    let env = load(&dir);
    let next = block_on(runtests::execute(
        &dir,
        &env,
        "unit",
        &args(&["-k", "careers"]),
        false,
        &[],
    ));
    assert!(next.success, "{}", next.text);
    assert!(next.text.contains("prepared in "), "{}", next.text);
    assert_eq!(count_lines(&dir.join("prepares.txt")), 1);

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn the_shell_command_runs_a_target_and_exits_with_its_result() {
    let _g = lock();
    let io = io_root_for("rt-cli-io");
    let root = common::temp_root("rt-cli");
    let dir = checkout(&root, TOML);
    let run = |extra: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_fabro-io"))
            .arg("run-tests")
            .args(extra)
            .current_dir(&dir)
            .env("FABRO_IO_ROOT", &io)
            .output()
            .unwrap()
    };
    let ok = run(&["unit", "-k", "careers", "--fresh"]);
    let text = String::from_utf8_lossy(&ok.stdout);
    assert!(
        ok.status.success(),
        "{text}{}",
        String::from_utf8_lossy(&ok.stderr)
    );
    assert!(
        text.contains("args: -k careers") && text.contains("prepared in "),
        "{text}"
    );

    let failed = run(&["broken"]);
    assert!(!failed.status.success());
    assert!(String::from_utf8_lossy(&failed.stdout).contains("exit_code: 3"));

    let refused = run(&["unit", "-n0"]);
    assert!(!refused.status.success());
    assert!(String::from_utf8_lossy(&refused.stdout).contains("allowed flags"));

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}
