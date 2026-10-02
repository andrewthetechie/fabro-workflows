//! The `code_*` tools: checkout discovery, argument checks and the wrapper exit codes.
//!
//! A stub `fabro-code` stands in for the real wrapper, which needs codegraph and an
//! index. What is under test is the glue: which directory the wrapper runs in, what it
//! is passed, and which exit codes reach the model as an answer or as an error.

mod common;

use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use fabro_io::code;

const STUB: &str = r#"#!/bin/sh
echo "cwd=$(basename "$(pwd)") args=$*"
case "$2" in
  missing) echo 'no symbol named missing in the index'; exit 1 ;;
  noindex) echo 'no code index; use grep' >&2; exit 2 ;;
esac
exit 0
"#;

fn mkrepo(dir: &Path, codegraph: bool) {
    std::fs::create_dir_all(dir.join(".git")).unwrap();
    if codegraph {
        std::fs::create_dir_all(dir.join(".codegraph")).unwrap();
    }
}

fn args(v: serde_json::Value) -> serde_json::Map<String, serde_json::Value> {
    v.as_object().cloned().unwrap()
}

fn set(key: &str, value: &str) {
    unsafe {
        std::env::set_var(key, value);
    }
}

fn unset(key: &str) {
    unsafe {
        std::env::remove_var(key);
    }
}

#[test]
fn find_checkout_picks_the_one_repository() {
    let root = common::temp_root("code-find");

    // The directory itself is a checkout.
    let own = root.join("own");
    mkrepo(&own, false);
    assert_eq!(code::find_checkout(&own).unwrap(), own);

    // /workspace with one repository below it: the server's real layout.
    let ws = root.join("ws");
    mkrepo(&ws.join("lawncare-saas"), false);
    std::fs::create_dir_all(ws.join("not-a-repo")).unwrap();
    assert_eq!(code::find_checkout(&ws).unwrap(), ws.join("lawncare-saas"));

    // Two repositories: the indexed one wins.
    let two = root.join("two");
    mkrepo(&two.join("a"), false);
    mkrepo(&two.join("b"), true);
    assert_eq!(code::find_checkout(&two).unwrap(), two.join("b"));

    // Two indexed repositories, or none at all: an error that points at the shell.
    let amb = root.join("amb");
    mkrepo(&amb.join("a"), true);
    mkrepo(&amb.join("b"), true);
    let e = code::find_checkout(&amb).unwrap_err();
    assert!(e.contains("more than one"), "{e}");
    let empty = root.join("empty");
    std::fs::create_dir_all(&empty).unwrap();
    let e = code::find_checkout(&empty).unwrap_err();
    assert!(e.contains("no repository checkout") && e.contains("fabro-code"), "{e}");

    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn arguments_are_checked() {
    let def = code::verb("code_def").unwrap();
    let tests = code::verb("code_tests").unwrap();
    assert!(code::verb("read_file").is_none());

    assert_eq!(code::arguments(def, &args(serde_json::json!({"name": " foo "}))).unwrap(), ["foo"]);
    assert!(code::arguments(def, &args(serde_json::json!({}))).is_err());
    assert!(code::arguments(def, &args(serde_json::json!({"name": ""}))).is_err());
    assert!(code::arguments(def, &args(serde_json::json!({"name": "-rf"}))).is_err());
    assert_eq!(
        code::arguments(tests, &args(serde_json::json!({"paths": ["a.py", "b.py"]}))).unwrap(),
        ["a.py", "b.py"]
    );
    assert!(code::arguments(tests, &args(serde_json::json!({"paths": []}))).is_err());
}

#[test]
fn run_maps_wrapper_exit_codes() {
    let root = common::temp_root("code-run");
    let repo = root.join("workspace").join("repo");
    mkrepo(&repo, true);
    let stub = root.join("fabro-code");
    std::fs::write(&stub, STUB).unwrap();
    std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).unwrap();

    let _g = common::LOCK.lock().unwrap();
    set("FABRO_CODE_BIN", &stub.display().to_string());
    set("FABRO_CODE_ROOT", &repo.display().to_string());
    let rt = tokio::runtime::Runtime::new().unwrap();

    let show = code::verb("code_show").unwrap();
    let out = rt.block_on(code::run(show, &args(serde_json::json!({"name": "src/a.py:12"}))));
    assert_eq!(out.unwrap(), "cwd=repo args=show src/a.py:12", "runs in the checkout");

    let tests = code::verb("code_tests").unwrap();
    let out = rt.block_on(code::run(tests, &args(serde_json::json!({"paths": ["a.py", "b.py"]}))));
    assert_eq!(out.unwrap(), "cwd=repo args=tests a.py b.py");

    // Exit 1 is an answer ("nothing found"), not an error.
    let def = code::verb("code_def").unwrap();
    let out = rt.block_on(code::run(def, &args(serde_json::json!({"name": "missing"}))));
    assert!(out.unwrap().contains("no symbol named missing"));

    // Exit 2 (no index) is an error carrying the wrapper's own advice.
    let out = rt.block_on(code::run(def, &args(serde_json::json!({"name": "noindex"}))));
    let e = out.unwrap_err();
    assert!(e.contains("exited 2") && e.contains("use grep"), "{e}");

    // A missing wrapper is an error that says to use grep.
    set("FABRO_CODE_BIN", &root.join("absent").display().to_string());
    let e = rt.block_on(code::run(def, &args(serde_json::json!({"name": "x"})))).unwrap_err();
    assert!(e.contains("cannot run") && e.contains("use grep"), "{e}");

    unset("FABRO_CODE_BIN");
    unset("FABRO_CODE_ROOT");
    let _ = std::fs::remove_dir_all(&root);
}
