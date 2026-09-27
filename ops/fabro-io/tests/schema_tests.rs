//! Compile every Stage-manifest output schema in `.fabro/workflows/_io/schemas/`
//! with the `jsonschema` crate (ADR 0016, task 05). Schema validity is checked in
//! Rust, where the validator lives. Anything that does not compile under draft
//! 2020-12 fails this test, so a bad schema is caught offline before a stage goes
//! live.

use std::fs;
use std::path::Path;

/// The schemas directory: `FABRO_IO_SCHEMAS_DIR` when set, else the repository layout.
///
/// In a checkout the crate root is `ops/fabro-io` and the schemas live two levels up under
/// `.fabro/workflows`. On the host the crate is synced alone to `~/fabro-io`, so
/// `build-images.sh` mounts the synced schemas and names them with the variable.
fn schemas_dir() -> std::path::PathBuf {
    match std::env::var_os("FABRO_IO_SCHEMAS_DIR") {
        Some(dir) => dir.into(),
        None => Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.fabro/workflows/_io/schemas"),
    }
}

#[test]
fn every_schema_compiles() {
    let dir = schemas_dir();
    let mut files: Vec<std::path::PathBuf> = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("schemas dir {dir:?} (set FABRO_IO_SCHEMAS_DIR outside a checkout): {e}"))
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|x| x == "json").unwrap_or(false))
        .collect();
    files.sort();
    assert!(!files.is_empty(), "at least one schema file must exist");
    for f in &files {
        let raw = fs::read_to_string(f)
            .unwrap_or_else(|e| panic!("{f:?} unreadable: {e}"));
        let schema: serde_json::Value = serde_json::from_str(&raw)
            .unwrap_or_else(|e| panic!("{f:?}: {e}"));
        jsonschema::options()
            .with_draft(jsonschema::Draft::Draft202012)
            .build(&schema)
            .unwrap_or_else(|e| panic!("{f:?} does not compile under draft 2020-12: {e}"));
    }
}

fn validator(name: &str) -> jsonschema::Validator {
    let f = schemas_dir().join(format!("{name}.schema.json"));
    let raw = fs::read_to_string(&f).unwrap_or_else(|e| panic!("{f:?} unreadable: {e}"));
    let schema: serde_json::Value = serde_json::from_str(&raw).unwrap_or_else(|e| panic!("{f:?}: {e}"));
    jsonschema::options()
        .with_draft(jsonschema::Draft::Draft202012)
        .build(&schema)
        .unwrap_or_else(|e| panic!("{f:?}: {e}"))
}

/// A schema that refuses a field its gate requires makes every such result fail the
/// gate: `submit` refuses the field, the model drops it, and the gate rejects the file.
/// triage_gate requires `basis` on every decision of a non-architecture issue, and the
/// schema shipped without it (runs 01M3J2CY0QSWP15WE8H3P3VDRE, 01M3J9W4K9Q3JT2YK7FPRYEZN4).
#[test]
fn triage_accepts_a_decision_basis() {
    let v = validator("triage");
    let ok = serde_json::json!({
        "readiness": "ready", "classification": "bug", "confidence": "high",
        "title": "fix(api): x", "labels": [], "questions": [], "summary": "s",
        "decisions": [{"question": "Where?", "decision": "Here.", "basis": "CONTEXT.md"}]
    });
    let errs: Vec<String> = v.iter_errors(&ok).map(|e| e.to_string()).collect();
    assert!(errs.is_empty(), "{errs:?}");
}

/// improve_gate rejects a `ready` result without `task.title` and `task.body`, and a
/// `split` without `tasks`. The schema refuses them too, so `submit` answers in the
/// same session instead of the gate costing a full improve visit
/// (run 01M3HRXSYA5RAYP078D3NWCYSV).
#[test]
fn improve_result_requires_the_task_its_disposition_needs() {
    let v = validator("improve_result");
    let ready_no_body = serde_json::json!({
        "disposition": "ready", "reason": "r", "task": {"id": "a", "title": "t"}
    });
    assert!(!v.is_valid(&ready_no_body), "ready without task.body must be refused");
    let ready_no_task = serde_json::json!({"disposition": "ready", "reason": "r"});
    assert!(!v.is_valid(&ready_no_task), "ready without task must be refused");
    let split_no_tasks = serde_json::json!({"disposition": "split", "reason": "r"});
    assert!(!v.is_valid(&split_no_tasks), "split without tasks must be refused");
    let ready = serde_json::json!({
        "disposition": "ready", "reason": "r", "task": {"id": "a", "title": "t", "body": "b"}
    });
    assert!(v.is_valid(&ready));
    let redundant = serde_json::json!({"disposition": "redundant", "reason": "r"});
    assert!(v.is_valid(&redundant));
}
