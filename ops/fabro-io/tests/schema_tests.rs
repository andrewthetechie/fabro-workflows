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
