//! The Test environment (docs/test-env C1 to C3): the parser's rules, the export quoting,
//! the fingerprint, and `prepare` with its stamp, log and budget. The `ci.sh` line is run
//! through the real binary.

mod common;

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

use fabro_io::testenv::{self, TestEnv};

/// The C1 example from docs/test-env/00-overview-and-contracts.md, with real values. `path`
/// is moved above `[env]`: the contract's rule says it must precede the first table, and the
/// contract's own example puts it after `[env]`, where TOML makes it a key of that table.
const C1_EXAMPLE: &str = r#"
version = 1

path = ["node_modules/.fabro-node/node-v24.11.0-linux-x64/bin"]

[env]
POSTGRES_SERVER = "localhost"
POSTGRES_PORT = "5432"

[prepare]
inputs = ["backend/app/alembic/versions/**", "backend/app/seed/**"]
timeout = 300
run = ["fabro-pg-ensure", "cd backend && uv run bash scripts/prestart.sh"]

[targets.backend]
about = "backend pytest; pass test files or node ids"
cwd = "backend"
run = "uv run pytest -n 1 -q"
flags = ["-x", "-v"]
value_flags = ["-k"]
timeout = 300
"#;

fn expect_err(text: &str) -> String {
    match testenv::parse(text) {
        Ok(_) => panic!("expected a C1 violation, but this parsed:\n{text}"),
        Err(e) => e,
    }
}

#[test]
fn c1_example_parses_with_its_values() {
    let env = testenv::parse(C1_EXAMPLE).expect("the C1 example is valid");
    assert_eq!(env.env.get("POSTGRES_PORT").map(String::as_str), Some("5432"));
    assert_eq!(env.prepare.timeout, 300);
    assert_eq!(env.prepare.run.len(), 2);
    let backend = &env.targets["backend"];
    assert_eq!(backend.cwd, PathBuf::from("backend"));
    assert_eq!(backend.value_flags, vec!["-k".to_string()]);
    assert_eq!(backend.timeout, 300);
}

#[test]
fn defaults_apply_when_tables_are_absent() {
    let env = testenv::parse("version = 1\n").expect("a bare version is valid");
    assert_eq!(env.prepare.timeout, 300);
    assert!(env.prepare.run.is_empty());
    assert!(env.targets.is_empty());

    let env = testenv::parse(
        "version = 1\n[targets.lint]\nabout = \"lint\"\nrun = \"make lint\"\n",
    )
    .expect("a target with only about and run is valid");
    let lint = &env.targets["lint"];
    assert_eq!(lint.cwd, PathBuf::from("."));
    assert_eq!(lint.timeout, 120);
}

#[test]
fn version_is_required_and_must_be_one() {
    let missing = expect_err("[env]\nA = \"1\"\n");
    assert!(missing.contains("version"), "{missing}");
    let two = expect_err("version = 2\n");
    assert!(two.contains("version must be 1, found 2"), "{two}");
}

#[test]
fn unknown_keys_are_errors_at_every_table() {
    let top = expect_err("version = 1\nbogus = 1\n");
    assert!(top.contains("bogus"), "{top}");
    let prepare = expect_err("version = 1\n[prepare]\nbogus = 1\n");
    assert!(prepare.contains("bogus"), "{prepare}");
    let target = expect_err("version = 1\n[targets.x]\nabout = \"x\"\nrun = \"x\"\nbogus = 1\n");
    assert!(target.contains("bogus"), "{target}");
}

#[test]
fn path_inside_env_is_rejected_and_names_the_key() {
    let e = expect_err("version = 1\n[env]\nPATH = \"/usr/bin\"\n");
    assert!(e.contains("PATH"), "{e}");
    assert!(e.contains("path"), "{e}");
}

#[test]
fn env_keys_must_match_the_pattern() {
    let e = expect_err("version = 1\n[env]\nlower = \"x\"\n");
    assert!(e.contains("`lower`"), "{e}");
    let e = expect_err("version = 1\n[env]\n\"1BAD\" = \"x\"\n");
    assert!(e.contains("`1BAD`"), "{e}");
}

#[test]
fn target_names_must_match_the_pattern() {
    let e = expect_err("version = 1\n[targets.Back_end]\nabout = \"x\"\nrun = \"x\"\n");
    assert!(e.contains("Back_end"), "{e}");
}

#[test]
fn timeouts_must_be_in_range() {
    let e = expect_err("version = 1\n[targets.x]\nabout = \"x\"\nrun = \"x\"\ntimeout = 0\n");
    assert!(e.contains("targets.x.timeout") && e.contains("found 0"), "{e}");
    let e = expect_err("version = 1\n[prepare]\ntimeout = 601\n");
    assert!(e.contains("prepare.timeout") && e.contains("found 601"), "{e}");
}

#[test]
fn about_and_run_are_required_and_about_is_one_line() {
    let e = expect_err("version = 1\n[targets.x]\nrun = \"x\"\n");
    assert!(e.contains("about"), "{e}");
    let e = expect_err("version = 1\n[targets.x]\nabout = \"  \"\nrun = \"x\"\n");
    assert!(e.contains("targets.x.about"), "{e}");
    let e = expect_err("version = 1\n[targets.x]\nabout = \"a\\nb\"\nrun = \"x\"\n");
    assert!(e.contains("targets.x.about"), "{e}");
    let e = expect_err("version = 1\n[targets.x]\nabout = \"x\"\n");
    assert!(e.contains("run"), "{e}");
}

#[test]
fn paths_must_stay_inside_the_checkout() {
    let e = expect_err("version = 1\npath = [\"/usr/bin\"]\n");
    assert!(e.contains("`/usr/bin`"), "{e}");
    let e = expect_err("version = 1\npath = [\"../outside\"]\n");
    assert!(e.contains("`../outside`"), "{e}");
    let e = expect_err("version = 1\n[targets.x]\nabout = \"x\"\nrun = \"x\"\ncwd = \"/tmp\"\n");
    assert!(e.contains("targets.x.cwd"), "{e}");
}

#[test]
fn path_after_a_table_is_rejected() {
    // A top-level key written after `[env]` belongs to that table, so it fails there.
    let e = expect_err("version = 1\n[env]\nA = \"1\"\npath = [\"bin\"]\n");
    assert!(e.contains("path"), "{e}");
}

#[test]
fn invalid_input_globs_are_rejected() {
    let e = expect_err("version = 1\n[prepare]\ninputs = [\"[\"]\n");
    assert!(e.contains("prepare.inputs[0]"), "{e}");
}

#[test]
fn exports_quote_each_value_and_put_path_last() {
    let env = testenv::parse(
        "version = 1\npath = [\"bin\"]\n[env]\nSECRET_WORD = \"it's\"\nPLAIN = \"x\"\n",
    )
    .expect("valid");
    let root = Path::new("/checkout");
    let out = testenv::exports(root, &env);
    let lines: Vec<&str> = out.lines().collect();
    assert_eq!(lines[0], "export PLAIN='x'");
    assert_eq!(lines[1], "export SECRET_WORD='it'\\''s'");
    assert!(lines[2].starts_with("export PATH='/checkout/bin:"), "{}", lines[2]);
    assert_eq!(lines.len(), 3);
}

#[test]
fn an_exported_value_round_trips_through_sh() {
    let _g = lock(); // a sibling test may swap PATH (common::NoKillPath)
    let env = testenv::parse("version = 1\n[env]\nTRICKY = \"a'b c\"\n").expect("valid");
    let out = testenv::exports(Path::new("/checkout"), &env);
    let script = format!("{out}printf '%s' \"$TRICKY\"");
    let got = Command::new("sh").args(["-c", &script]).output().expect("sh runs");
    assert_eq!(String::from_utf8_lossy(&got.stdout), "a'b c");
}

/// A checkout with `.fabro/test.toml`, one input file, one outside file and one unrelated file.
struct Checkout {
    root: PathBuf,
    outside: PathBuf,
}

impl Checkout {
    fn new(tag: &str) -> Checkout {
        let root = common::temp_root(tag);
        let outside = common::temp_root(&format!("{tag}-outside"));
        std::fs::create_dir_all(root.join(".fabro")).unwrap();
        std::fs::create_dir_all(root.join("backend/app")).unwrap();
        let c = Checkout { root, outside };
        c.write(".fabro/test.toml", INPUT_TOML);
        c.write("backend/app/schema.sql", "v1");
        c.write("notes.txt", "v1");
        std::fs::write(c.outside.join("secret.txt"), "v1").unwrap();
        std::os::unix::fs::symlink(
            c.outside.join("secret.txt"),
            c.root.join("backend/app/linked.sql"),
        )
        .unwrap();
        c
    }

    fn write(&self, rel: &str, content: &str) {
        std::fs::write(self.root.join(rel), content).unwrap();
    }

    fn env(&self) -> TestEnv {
        TestEnv::load(&self.root).unwrap().expect("test.toml exists")
    }

    fn fp(&self) -> String {
        testenv::fingerprint(&self.root, &self.env())
    }

    fn cleanup(self) {
        let _ = std::fs::remove_dir_all(&self.root);
        let _ = std::fs::remove_dir_all(&self.outside);
    }
}

const INPUT_TOML: &str = "version = 1\n[prepare]\ninputs = [\"backend/app/**\"]\nrun = [\"true\"]\n";

#[test]
fn fingerprint_moves_when_an_input_changes_and_not_otherwise() {
    let c = Checkout::new("fp-inputs");
    let before = c.fp();
    assert_eq!(before, c.fp(), "the fingerprint is stable");

    c.write("notes.txt", "edited outside the globs");
    assert_eq!(before, c.fp(), "a file outside the globs does not count");

    c.write("backend/app/schema.sql", "v2");
    assert_ne!(before, c.fp(), "an edited input changes the fingerprint");
    c.cleanup();
}

#[test]
fn fingerprint_moves_when_the_declaration_changes() {
    let c = Checkout::new("fp-toml");
    let before = c.fp();
    c.write(".fabro/test.toml", &format!("{INPUT_TOML}# touched\n"));
    assert_ne!(before, c.fp());
    c.cleanup();
}

#[test]
fn fingerprint_moves_with_the_checkout_root() {
    let a = Checkout::new("fp-root-a");
    let b = Checkout::new("fp-root-b");
    b.write("backend/app/schema.sql", "v1");
    assert_ne!(a.fp(), b.fp(), "identical contents under another root differ");
    a.cleanup();
    b.cleanup();
}

#[test]
fn symlinks_are_never_followed_into_the_fingerprint() {
    let c = Checkout::new("fp-symlink");
    let before = c.fp();
    std::fs::write(c.outside.join("secret.txt"), "changed far away").unwrap();
    assert_eq!(before, c.fp(), "a symlink to a file outside the checkout is not hashed");
    c.cleanup();
}

#[test]
fn load_returns_none_without_a_file() {
    let root = common::temp_root("load-none");
    assert!(TestEnv::load(&root).unwrap().is_none());
    let _ = std::fs::remove_dir_all(&root);
}

/// Points the io root at a fresh directory for the rest of a test. Callers hold `common::LOCK`.
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

fn with_run(run: &[&str], tag: &str) -> (PathBuf, TestEnv) {
    let root = common::temp_root(tag);
    let steps: Vec<String> = run.iter().map(|s| format!("\"{s}\"")).collect();
    std::fs::create_dir_all(root.join(".fabro")).unwrap();
    std::fs::write(
        root.join(".fabro/test.toml"),
        format!("version = 1\n[prepare]\nrun = [{}]\n", steps.join(", ")),
    )
    .unwrap();
    let env = TestEnv::load(&root).unwrap().unwrap();
    (root, env)
}

#[test]
fn a_failing_second_step_names_it_and_leaves_no_stamp() {
    let _g = lock();
    let io = io_root_for("prep-fail");
    let (root, env) = with_run(&["true", "echo hi > marker", "false"], "prep-fail-root");

    let err = testenv::prepare(&root, &env, Duration::from_secs(30)).unwrap_err();
    assert!(err.contains("prepare.run[2]"), "{err}");
    assert!(err.contains("`false`"), "{err}");
    assert!(root.join("marker").exists(), "the steps before the failure ran");
    assert!(!io.join(testenv::STAMP_FILE).exists(), "a failed preparation writes no stamp");
    assert!(io.join(testenv::LOG_FILE).exists(), "the log is kept");

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn success_writes_the_stamp_and_the_log_holds_the_output() {
    let _g = lock();
    let io = io_root_for("prep-ok");
    let (root, env) = with_run(&["echo one", "echo two > marker"], "prep-ok-root");

    let done = testenv::prepare(&root, &env, Duration::from_secs(30)).expect("prepares");
    assert_eq!(done.fingerprint, testenv::fingerprint(&root, &env));
    let stamp: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(io.join(testenv::STAMP_FILE)).unwrap())
            .unwrap();
    assert_eq!(stamp["fingerprint"], done.fingerprint.as_str());
    assert!(stamp["prepared_at"].is_string());
    assert!(stamp["seconds"].is_u64());
    let log = std::fs::read_to_string(io.join(testenv::LOG_FILE)).unwrap();
    assert!(log.contains("one"), "{log}");
    assert_eq!(std::fs::read_to_string(root.join("marker")).unwrap().trim(), "two");

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn a_failure_removes_a_stamp_left_by_an_earlier_success() {
    let _g = lock();
    let io = io_root_for("prep-stale");
    let (root, ok) = with_run(&["true"], "prep-stale-root");
    testenv::prepare(&root, &ok, Duration::from_secs(30)).expect("prepares");
    assert!(io.join(testenv::STAMP_FILE).exists());

    let (_, bad) = with_run(&["false"], "prep-stale-bad");
    let err = testenv::prepare(&root, &bad, Duration::from_secs(30)).unwrap_err();
    assert!(err.contains("prepare.run[0]"), "{err}");
    assert!(!io.join(testenv::STAMP_FILE).exists());

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn the_budget_kills_a_step_that_runs_too_long() {
    let _g = lock();
    let io = io_root_for("prep-budget");
    let (root, env) = with_run(&["sleep 5"], "prep-budget-root");

    let started = Instant::now();
    let err = testenv::prepare(&root, &env, Duration::from_secs(1)).unwrap_err();
    assert!(err.contains("budget"), "{err}");
    assert!(started.elapsed() < Duration::from_secs(4), "killed well before the sleep ends");
    assert!(!io.join(testenv::STAMP_FILE).exists());

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn the_budget_kills_the_whole_step_even_without_a_kill_binary() {
    let _g = lock();
    let io = io_root_for("prep-nokill");
    let (root, env) = with_run(&["sleep 30 & echo $! > child.pid; wait"], "prep-nokill-root");
    let path = common::NoKillPath::new("prep");

    let started = Instant::now();
    let err = testenv::prepare(&root, &env, Duration::from_secs(1)).unwrap_err();
    let took = started.elapsed();
    drop(path);
    assert!(took < Duration::from_secs(5), "returned after {took:?}");
    assert!(err.contains("budget"), "{err}");
    let pid = common::read_pid(&root.join("child.pid"));
    assert!(common::process_gone(&pid), "the step's backgrounded child {pid} was killed with it");

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

/// Runs the `ci.sh` line from docs/test-env C2 through the real binary.
fn ci_line(root: &Path, io: &Path, tail: &str) -> std::process::Output {
    let script = format!(
        "set -euo pipefail; test_env=$(\"$FABRO_IO\" test-env --root \"$ROOT\"); eval \"$test_env\"; {tail}"
    );
    Command::new("bash")
        .args(["-c", &script])
        .env("FABRO_IO", env!("CARGO_BIN_EXE_fabro-io"))
        .env("ROOT", root)
        .env("FABRO_IO_ROOT", io)
        .output()
        .expect("bash runs")
}

#[test]
fn the_ci_line_exports_variables_and_stops_when_preparation_fails() {
    let _g = lock();
    let io = common::temp_root("ci-line-io");
    let (root, _) = with_run(&["true"], "ci-line-ok");
    std::fs::write(
        root.join(".fabro/test.toml"),
        "version = 1\n[env]\nGREETING = \"hi there\"\n[prepare]\nrun = [\"true\"]\n",
    )
    .unwrap();

    let ok = ci_line(&root, &io, "echo \"$GREETING\"");
    assert!(ok.status.success(), "{}", String::from_utf8_lossy(&ok.stderr));
    assert_eq!(String::from_utf8_lossy(&ok.stdout).trim(), "hi there");

    std::fs::write(
        root.join(".fabro/test.toml"),
        "version = 1\n[prepare]\nrun = [\"true\", \"false\"]\n",
    )
    .unwrap();
    let failed = ci_line(&root, &io, "echo x");
    assert!(!failed.status.success());
    assert!(!String::from_utf8_lossy(&failed.stdout).contains('x'), "x must not print");
    assert!(String::from_utf8_lossy(&failed.stderr).contains("prepare.run[1]"));

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn test_env_with_no_file_prints_nothing_and_exits_zero() {
    let _g = lock();
    let io = common::temp_root("no-file-io");
    let root = common::temp_root("no-file-root");
    let out = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["test-env", "--root"])
        .arg(&root)
        .env("FABRO_IO_ROOT", &io)
        .output()
        .unwrap();
    assert!(out.status.success());
    assert!(out.stdout.is_empty());
    assert!(!io.join(testenv::STAMP_FILE).exists(), "no declaration, no stamp");

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&io);
}

#[test]
fn test_env_check_fails_and_names_a_bad_key() {
    let _g = lock(); // a sibling test may swap PATH (common::NoKillPath)
    let root = common::temp_root("check-bad");
    std::fs::create_dir_all(root.join(".fabro")).unwrap();
    std::fs::write(root.join(".fabro/test.toml"), "version = 2\n").unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["test-env", "--check", "--root"])
        .arg(&root)
        .output()
        .unwrap();
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("version must be 1"));

    let _ = std::fs::remove_dir_all(&root);
}
