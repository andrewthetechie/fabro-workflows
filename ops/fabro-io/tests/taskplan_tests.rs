//! `task-plan`: the C4 selection rules over pure fixtures, plus one real-git `changed_set`.

mod common;

use std::path::{Path, PathBuf};
use std::process::Command;

use fabro_io::taskplan::{self, CodeAnswer};
use fabro_io::testenv;

fn write(root: &Path, rel: &str) {
    let path = root.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, "x").unwrap();
}

/// A temp root with the named files present.
fn repo(tag: &str, files: &[&str]) -> PathBuf {
    let dir = common::temp_root(tag);
    for file in files {
        write(&dir, file);
    }
    dir
}

fn env(text: &str) -> testenv::TestEnv {
    testenv::parse(text).expect("the test declaration is valid")
}

fn code(json: &str) -> CodeAnswer {
    serde_json::from_str(json).expect("the fixture code answer is valid")
}

fn changed(paths: &[&str]) -> Vec<String> {
    paths.iter().map(|p| (*p).to_string()).collect()
}

fn reasons(plan: &taskplan::Plan) -> (String, String) {
    (plan.mode.clone(), plan.reason.clone())
}

#[test]
fn no_declaration_or_no_task_keys_is_no_config() {
    let root = repo("tp-noconfig", &["backend/app/a.py"]);
    let answer = code(r#"{"seeds": {}}"#);
    let none = taskplan::plan(&root, None, &changed(&["backend/app/a.py"]), &answer);
    assert_eq!(reasons(&none), ("full".into(), "no_config".into()));
    let plain = env("version = 1\n[targets.x]\nabout=\"x\"\nrun=\"x\"\n");
    let plan = taskplan::plan(&root, Some(&plain), &changed(&["backend/app/a.py"]), &answer);
    assert_eq!(reasons(&plan), ("full".into(), "no_config".into()));
}

#[test]
fn an_empty_diff_is_scoped_with_nothing_to_run() {
    let root = repo("tp-empty", &[]);
    let env = env("version = 1\n[targets.x]\nabout=\"x\"\nrun=\"x\"\ntests=[\"a/**\"]\n");
    let plan = taskplan::plan(&root, Some(&env), &[], &code(r#"{"seeds": {}}"#));
    assert_eq!(plan.mode, "scoped");
    assert!(plan.targets.is_empty() && plan.checks.is_empty());
}

#[test]
fn declared_and_builtin_full_globs_escalate() {
    let root = repo("tp-fullglobs", &["backend/uv.lock", "frontend/package.json", "backend/tests/conftest.py", "alembic/x.py"]);
    let env = env("version = 1\n[task]\nfull=[\"alembic/**\"]\n[targets.x]\nabout=\"x\"\nrun=\"x\"\ntests=[\"a/**\"]\n");
    let answer = code(r#"{"seeds": {}}"#);
    for path in ["backend/uv.lock", "frontend/package.json", "backend/tests/conftest.py", "alembic/x.py"] {
        let plan = taskplan::plan(&root, Some(&env), &changed(&[path]), &answer);
        assert_eq!(plan.reason, format!("full_glob:{path}"), "for {path}");
    }
}

#[test]
fn a_deleted_source_file_escalates_and_a_deleted_test_file_does_not() {
    let root = repo("tp-deleted", &[]);
    let env = env("version = 1\n[targets.x]\nabout=\"x\"\nrun=\"x\"\ntests=[\"backend/tests/**\"]\n");
    let source = code(r#"{"seeds": {"backend/app/gone.py": {"indexed": true, "tests": [], "conftests": []}}}"#);
    let plan = taskplan::plan(&root, Some(&env), &changed(&["backend/app/gone.py"]), &source);
    assert_eq!(plan.reason, "deleted:backend/app/gone.py");

    let test = code(r#"{"seeds": {"backend/tests/test_gone.py": {"indexed": true, "tests": ["backend/tests/test_gone.py"], "conftests": []}}}"#);
    let plan = taskplan::plan(&root, Some(&env), &changed(&["backend/tests/test_gone.py"]), &test);
    assert_eq!(plan.mode, "scoped", "{}", plan.reason);
    assert!(plan.targets.is_empty());
}

#[test]
fn a_skipped_path_never_escalates_and_is_listed_as_skipped() {
    let root = repo("tp-skip", &[]);
    let env = env("version = 1\n[task]\nskip=[\"e2e/**\"]\n[targets.x]\nabout=\"x\"\nrun=\"x\"\ntests=[\"backend/tests/**\"]\n");
    let answer = code(r#"{"seeds": {}}"#);
    // A deleted skipped file does not trip step 4.
    let plan = taskplan::plan(&root, Some(&env), &changed(&["e2e/gone.spec.ts"]), &answer);
    assert_eq!(plan.mode, "scoped", "{}", plan.reason);
    // A selected test in skip is dropped and recorded.
    let answer = code(r#"{"seeds": {"backend/app/a.py": {"indexed": true, "tests": ["e2e/a.spec.ts"], "conftests": []}}}"#);
    let root2 = repo("tp-skip2", &["backend/app/a.py"]);
    let plan = taskplan::plan(&root2, Some(&env), &changed(&["backend/app/a.py"]), &answer);
    assert_eq!(plan.skipped, ["e2e/a.spec.ts"]);
    assert!(plan.targets.is_empty());
}

#[test]
fn an_unowned_selected_test_escalates() {
    let root = repo("tp-unowned", &["other/src.py", "other/test_b.py"]);
    let env = env("version = 1\n[targets.x]\nabout=\"x\"\nrun=\"x\"\ntests=[\"backend/tests/**\"]\n");
    let answer = code(r#"{"seeds": {"other/src.py": {"indexed": true, "tests": ["other/test_b.py"], "conftests": []}}}"#);
    let plan = taskplan::plan(&root, Some(&env), &changed(&["other/src.py"]), &answer);
    assert_eq!(plan.reason, "unowned_test:other/test_b.py");
}

#[test]
fn a_test_outside_its_targets_cwd_escalates() {
    let root = repo("tp-outside", &["frontend/src/a.py", "frontend/test_a.py"]);
    let env = env("version = 1\n[targets.backend]\nabout=\"x\"\nrun=\"x\"\ncwd=\"backend\"\ntests=[\"**/test_*.py\"]\n");
    let answer = code(r#"{"seeds": {"frontend/src/a.py": {"indexed": true, "tests": ["frontend/test_a.py"], "conftests": []}}}"#);
    let plan = taskplan::plan(&root, Some(&env), &changed(&["frontend/src/a.py"]), &answer);
    assert_eq!(plan.reason, "path_outside_cwd:frontend/test_a.py");
}

#[test]
fn the_first_declared_target_owns_a_test() {
    let root = repo("tp-first", &["backend/app/a.py", "backend/tests/test_a.py"]);
    // `second` is declared first, so file order, not name order, decides.
    let env = env(
        "version = 1\n\
         [targets.second]\nabout=\"s\"\nrun=\"s\"\ncwd=\"backend\"\ntests=[\"backend/tests/**\"]\n\
         [targets.first]\nabout=\"f\"\nrun=\"f\"\ncwd=\"backend\"\ntests=[\"backend/tests/**\"]\n",
    );
    let answer = code(r#"{"seeds": {"backend/app/a.py": {"indexed": true, "tests": ["backend/tests/test_a.py"], "conftests": []}}}"#);
    let plan = taskplan::plan(&root, Some(&env), &changed(&["backend/app/a.py"]), &answer);
    assert_eq!(plan.targets.len(), 1);
    assert_eq!(plan.targets[0].name, "second");
    assert_eq!(plan.targets[0].args, ["tests/test_a.py"]);
    assert_eq!(
        plan.targets[0].why["backend/tests/test_a.py"],
        ["backend/app/a.py"]
    );
}

#[test]
fn more_than_two_hundred_tests_runs_the_target_whole() {
    let files: Vec<String> = (0..201).map(|i| format!("backend/tests/test_{i}.py")).collect();
    let refs: Vec<&str> = files.iter().map(String::as_str).collect();
    let mut files_with_seed = refs.clone();
    files_with_seed.push("backend/app/big.py");
    let root = repo("tp-201", &files_with_seed);
    let env = env("version = 1\n[targets.backend]\nabout=\"x\"\nrun=\"x\"\ncwd=\"backend\"\ntests=[\"backend/tests/**\"]\n");
    let answer = serde_json::json!({
        "seeds": {"backend/app/big.py": {"indexed": true, "tests": files, "conftests": []}}
    });
    let plan = taskplan::plan(
        &root,
        Some(&env),
        &changed(&["backend/app/big.py"]),
        &code(&answer.to_string()),
    );
    assert_eq!(plan.targets.len(), 1);
    assert!(plan.targets[0].whole);
    assert!(plan.targets[0].args.is_empty());
}

#[test]
fn a_watch_target_runs_whole_on_a_matching_change() {
    let root = repo("tp-watch", &["src-tauri/src/lib.rs"]);
    let env = env("version = 1\n[targets.rust]\nabout=\"x\"\nrun=\"x\"\nwatch=[\"src-tauri/**\"]\n");
    let plan = taskplan::plan(
        &root,
        Some(&env),
        &changed(&["src-tauri/src/lib.rs"]),
        &code(r#"{"seeds": {}}"#),
    );
    assert_eq!(plan.targets.len(), 1);
    assert_eq!(plan.targets[0].name, "rust");
    assert!(plan.targets[0].whole);
}

#[test]
fn a_check_with_files_true_gets_only_existing_paths() {
    let root = repo("tp-check", &["src/foo.rs"]);
    let env = env("version = 1\n[checks.fmt]\nrun=\"fmt\"\nwatch=[\"src/**\"]\nfiles=true\n");
    let answer = code(r#"{"seeds": {"src/foo_test.rs": {"indexed": true, "tests": ["src/foo_test.rs"], "conftests": []}}}"#);
    let plan = taskplan::plan(
        &root,
        Some(&env),
        &changed(&["src/foo.rs", "src/foo_test.rs"]),
        &answer,
    );
    assert_eq!(plan.mode, "scoped", "{}", plan.reason);
    assert_eq!(plan.checks.len(), 1);
    assert_eq!(plan.checks[0].name, "fmt");
    assert_eq!(plan.checks[0].args, ["src/foo.rs"]);
}

#[test]
fn a_deleted_file_can_leave_a_files_check_with_nothing_to_run() {
    let root = repo("tp-check-empty", &[]);
    let env = env("version = 1\n[checks.fmt]\nrun=\"fmt\"\nwatch=[\"src/**\"]\nfiles=true\n");
    let answer = code(r#"{"seeds": {"src/gone_test.rs": {"indexed": true, "tests": ["src/gone_test.rs"], "conftests": []}}}"#);
    let plan = taskplan::plan(
        &root,
        Some(&env),
        &changed(&["src/gone_test.rs"]),
        &answer,
    );
    assert_eq!(plan.mode, "scoped", "{}", plan.reason);
    assert!(plan.checks.is_empty(), "no existing file, no check");
}

#[test]
fn untested_and_unchecked_are_recorded_not_escalated() {
    let root = repo("tp-untested", &["backend/app/lonely.py", "docs/readme.md"]);
    let env = env("version = 1\n[targets.backend]\nabout=\"x\"\nrun=\"x\"\ntests=[\"backend/tests/**\"]\n");
    let answer = code(r#"{"seeds": {"backend/app/lonely.py": {"indexed": true, "tests": [], "conftests": []}}}"#);
    let plan = taskplan::plan(
        &root,
        Some(&env),
        &changed(&["backend/app/lonely.py", "docs/readme.md"]),
        &answer,
    );
    assert_eq!(plan.mode, "scoped");
    assert_eq!(plan.untested, ["backend/app/lonely.py"]);
    assert_eq!(plan.unchecked, ["backend/app/lonely.py", "docs/readme.md"]);
}

#[test]
fn the_conftest_rule_adds_every_test_under_it_with_the_seed_in_why() {
    let root = repo("tp-conftest", &["backend/app/core/db.py", "backend/tests/test_a.py", "backend/tests/test_b.py"]);
    let env = env("version = 1\n[targets.backend]\nabout=\"x\"\nrun=\"x\"\ncwd=\"backend\"\ntests=[\"backend/tests/**\"]\n");
    let answer = code(
        r#"{
          "seeds": {"backend/app/core/db.py": {"indexed": true, "tests": [], "conftests": ["backend/tests/conftest.py"]}},
          "conftest_tests": {"backend/tests/conftest.py": ["backend/tests/test_a.py", "backend/tests/test_b.py"]}
        }"#,
    );
    let plan = taskplan::plan(&root, Some(&env), &changed(&["backend/app/core/db.py"]), &answer);
    assert_eq!(plan.targets.len(), 1);
    assert_eq!(plan.targets[0].args, ["tests/test_a.py", "tests/test_b.py"]);
    assert_eq!(
        plan.targets[0].why["backend/tests/test_a.py"],
        ["backend/app/core/db.py via backend/tests/conftest.py"]
    );
}

#[test]
fn the_womens_fantasy_shaped_fixture_matches_the_expected_plan() {
    let root = repo(
        "tp-wfs",
        &["backend/app/api/routes/quiz.py", "backend/tests/api/routes/test_quiz.py"],
    );
    let env = env(
        r#"
version = 1

[task]
full = ["backend/app/alembic/**", "backend/app/seed/**"]
skip = ["frontend/tests/**", "frontend/tests-deployed/**"]

[targets.backend]
about = "backend pytest"
cwd = "backend"
run = "uv run pytest -n 1 -q"
tests = ["backend/tests/**"]

[targets.frontend]
about = "frontend vitest"
cwd = "frontend"
run = "bun run test:unit --"
tests = ["frontend/src/**"]

[checks.prek]
about = "pre-commit hooks"
run = "uv run prek run --from-ref \"$FABRO_TASK_BASE\" --to-ref HEAD"
watch = ["**"]
files = false
timeout = 300
"#,
    );
    let answer = code(
        r#"{"seeds": {"backend/app/api/routes/quiz.py": {"indexed": true, "tests": ["backend/tests/api/routes/test_quiz.py"], "conftests": []}}}"#,
    );
    let plan = taskplan::plan(
        &root,
        Some(&env),
        &changed(&["backend/app/api/routes/quiz.py"]),
        &answer,
    );
    let expected: serde_json::Value = serde_json::from_str(
        r#"{
          "version": 1,
          "mode": "scoped",
          "reason": "",
          "base": "",
          "changed": ["backend/app/api/routes/quiz.py"],
          "checks": [{"name": "prek", "args": []}],
          "targets": [{
            "name": "backend",
            "args": ["tests/api/routes/test_quiz.py"],
            "whole": false,
            "why": {"backend/tests/api/routes/test_quiz.py": ["backend/app/api/routes/quiz.py"]}
          }],
          "untested": [],
          "unchecked": [],
          "skipped": []
        }"#,
    )
    .unwrap();
    assert_eq!(serde_json::to_value(&plan).unwrap(), expected);
}

// --- changed_set over a real repository (C1) -----------------------------------------

fn git(root: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .expect("git runs");
    assert!(
        out.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

#[test]
fn changed_set_includes_edits_untracked_files_and_a_renames_old_path() {
    let root = common::temp_root("tp-git");
    git(&root, &["init", "-q"]);
    git(&root, &["config", "user.email", "t@example.com"]);
    git(&root, &["config", "user.name", "t"]);
    write(&root, "a.txt");
    write(&root, "b.txt");
    git(&root, &["add", "."]);
    git(&root, &["commit", "-qm", "base"]);
    let base = git(&root, &["rev-parse", "HEAD"]);

    std::fs::write(root.join("a.txt"), "edited").unwrap();
    write(&root, "untracked.txt");
    git(&root, &["mv", "b.txt", "c.txt"]);

    let set = taskplan::changed_set(&root, &base).unwrap();
    assert_eq!(set, ["a.txt", "b.txt", "c.txt", "untracked.txt"]);
}


// --- the CLI: no_config and index_unavailable ----------------------------------------

use std::os::unix::fs::PermissionsExt;

#[test]
fn the_cli_reports_no_config_without_a_declaration() {
    let root = common::temp_root("tp-cli-noconfig");
    let out = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["task-plan", "--root"])
        .arg(&root)
        .output()
        .unwrap();
    assert!(out.status.success());
    let plan: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(plan["mode"], "full");
    assert_eq!(plan["reason"], "no_config");
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn the_cli_reports_index_unavailable_as_a_full_plan() {
    let _g = common::LOCK.lock().unwrap();
    let root = common::temp_root("tp-cli-index");
    std::fs::create_dir_all(root.join(".fabro")).unwrap();
    std::fs::write(
        root.join(".fabro/test.toml"),
        "version = 1\n[targets.x]\nabout=\"x\"\nrun=\"x\"\ntests=[\"a/**\"]\n",
    )
    .unwrap();
    git(&root, &["init", "-q"]);
    git(&root, &["config", "user.email", "t@example.com"]);
    git(&root, &["config", "user.name", "t"]);
    write(&root, "a.py");
    git(&root, &["add", "."]);
    git(&root, &["commit", "-qm", "base"]);
    std::fs::write(root.join("a.py"), "edited").unwrap();

    let bin = common::temp_root("tp-cli-index-bin");
    let stub = bin.join("fabro-code");
    std::fs::write(&stub, "#!/bin/sh\necho 'no code index; use grep' >&2\nexit 2\n").unwrap();
    std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).unwrap();
    unsafe { std::env::set_var("FABRO_CODE_BIN", &stub) };
    let out = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["task-plan", "--root"])
        .arg(&root)
        .args(["--base", "HEAD"])
        .output()
        .unwrap();
    unsafe { std::env::remove_var("FABRO_CODE_BIN") };

    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let plan: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(plan["mode"], "full");
    assert_eq!(plan["reason"], "index_unavailable");
    assert_eq!(plan["changed"], serde_json::json!(["a.py"]));

    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&bin);
}

#[test]
fn a_watch_target_owns_the_tests_it_watches_and_runs_whole() {
    // writers-app: `src-tauri/tests/*.rs` are tests by TEST_RE, and the Rust target is a
    // `watch` target. A selected integration test is that target's, not unowned.
    let root = repo("tp-watch-own", &["shared/types.ts", "src-tauri/tests/bible.rs"]);
    let env = env("version = 1\n[targets.rust]\nabout=\"x\"\nrun=\"x\"\nwatch=[\"src-tauri/**\"]\n");
    let answer = code(r#"{"seeds": {"shared/types.ts": {"indexed": true, "tests": ["src-tauri/tests/bible.rs"], "conftests": []}}}"#);
    let plan = taskplan::plan(&root, Some(&env), &changed(&["shared/types.ts"]), &answer);
    assert_eq!(plan.mode, "scoped", "{}", plan.reason);
    assert_eq!(plan.targets.len(), 1);
    assert_eq!(plan.targets[0].name, "rust");
    assert!(plan.targets[0].whole);
    assert!(plan.targets[0].args.is_empty());
}

#[test]
fn a_changed_fixture_under_a_tests_glob_that_selects_nothing_is_untested() {
    // A JSON fixture or snapshot is not in the index and imports nothing, so it selects no
    // test. It lives under the target's tests glob, so it is the target's untested change.
    let root = repo("tp-fixture", &["backend/tests/fixtures/data.json", "docs/readme.md"]);
    let env = env("version = 1\n[targets.backend]\nabout=\"x\"\nrun=\"x\"\ncwd=\"backend\"\ntests=[\"backend/tests/**\"]\n");
    let answer = code(
        r#"{"seeds": {
            "backend/tests/fixtures/data.json": {"indexed": false, "tests": [], "conftests": []},
            "docs/readme.md": {"indexed": false, "tests": [], "conftests": []}
        }}"#,
    );
    let plan = taskplan::plan(
        &root,
        Some(&env),
        &changed(&["backend/tests/fixtures/data.json", "docs/readme.md"]),
        &answer,
    );
    assert_eq!(plan.mode, "scoped", "{}", plan.reason);
    assert_eq!(plan.untested, ["backend/tests/fixtures/data.json"]);
}

#[test]
fn the_cli_reports_a_base_it_cannot_diff_as_no_base() {
    let root = common::temp_root("tp-cli-nobase");
    std::fs::create_dir_all(root.join(".fabro")).unwrap();
    std::fs::write(
        root.join(".fabro/test.toml"),
        "version = 1\n[targets.x]\nabout=\"x\"\nrun=\"x\"\ntests=[\"a/**\"]\n",
    )
    .unwrap();
    git(&root, &["init", "-q"]);
    git(&root, &["config", "user.email", "t@example.com"]);
    git(&root, &["config", "user.name", "t"]);
    write(&root, "a.py");
    git(&root, &["add", "."]);
    git(&root, &["commit", "-qm", "base"]);

    let out = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["task-plan", "--root"])
        .arg(&root)
        .args(["--base", "0000000000000000000000000000000000000000"])
        .output()
        .unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let plan: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(plan["mode"], "full");
    assert_eq!(plan["reason"], "no_base");

    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn a_selection_over_a_targets_max_tests_falls_back_to_the_full_suite() {
    // womens-fantasy-sports, measured 2026-10-14: a `core/db.py` edit reaches the backend
    // conftest and selects all 168 backend test files. At `-n 1` they took 154 s, while the
    // whole `ci.sh` (8 xdist workers) took 104 s, so the full suite is the faster gate.
    let files: Vec<String> = (0..3).map(|i| format!("backend/tests/test_{i}.py")).collect();
    let mut present: Vec<&str> = files.iter().map(String::as_str).collect();
    present.push("backend/app/core/db.py");
    let root = repo("tp-max", &present);
    let decl = "version = 1\n[targets.backend]\nabout=\"x\"\nrun=\"x\"\ncwd=\"backend\"\ntests=[\"backend/tests/**\"]\nmax_tests = 2\n";
    let answer = serde_json::json!({
        "seeds": {"backend/app/core/db.py": {"indexed": true, "tests": files, "conftests": []}}
    });
    let plan = taskplan::plan(
        &root,
        Some(&env(decl)),
        &changed(&["backend/app/core/db.py"]),
        &code(&answer.to_string()),
    );
    assert_eq!(reasons(&plan), ("full".into(), "too_many_tests:backend".into()));

    let at_limit = env(&decl.replace("max_tests = 2", "max_tests = 3"));
    let plan = taskplan::plan(
        &root,
        Some(&at_limit),
        &changed(&["backend/app/core/db.py"]),
        &code(&answer.to_string()),
    );
    assert_eq!(plan.mode, "scoped", "{}", plan.reason);
    assert_eq!(plan.targets[0].args.len(), 3);
}
