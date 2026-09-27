//! The `submit` tool (C4): validate a stage's output, stamp the Input receipt and write
//! it atomically with sorted keys and two-space indentation.

use std::path::Path;

use jsonschema::{Draft, Validator};

use crate::{common, manifest, served};

/// The outcome of a submit call, mapped to exit codes in the CLI.
#[derive(Debug)]
pub enum Outcome {
    /// 0
    Written(String),
    /// 3 — refusal 1: no valid served record for the current visit.
    NeedInputs(String),
    /// 3 — refusal 2: a required input was not read in full.
    Incomplete(String),
    /// 4 — refusal 3: schema violations.
    SchemaViolation(String),
    /// 1 — an internal error (the guard is not a security boundary).
    Error(String),
}

/// Submit an output object for the current stage. Shared by the MCP tool and the CLI.
pub fn run(output: &serde_json::Value) -> Outcome {
    let Some((node, visit)) = common::read_stage() else {
        return Outcome::NeedInputs("call inputs first".into());
    };
    let m = match manifest::load() {
        Ok(m) => m,
        Err(e) => return Outcome::Error(format!("cannot read manifest: {e}")),
    };
    let Some(stage) = m.stage(&node) else {
        return Outcome::Error(format!("no stage '{node}' in the manifest"));
    };
    let Some(out) = &stage.output else {
        return Outcome::Error(format!("stage '{node}' has no output contract"));
    };

    // Refusal 1: served.json must hold the current visit.
    match served::stored_visit() {
        Some(v) if v == visit => {}
        _ => return Outcome::NeedInputs("call inputs first".into()),
    }
    let served_rec = served::load(&visit);

    // Refusal 2: every required input complete.
    for input in &stage.inputs {
        if !input.required {
            continue;
        }
        if let Some(msg) = incomplete(&served_rec, input) {
            return Outcome::Incomplete(msg);
        }
    }

    // Refusal 3: schema.
    let compiled: Validator = match jsonschema::options()
        .with_draft(Draft::Draft202012)
        .build(&out.schema)
    {
        Ok(c) => c,
        Err(e) => return Outcome::Error(format!("invalid schema: {e}")),
    };
    let mut violations: Vec<String> = Vec::new();
    for err in compiled.iter_errors(output) {
        let ptr = err.instance_path.to_string();
        violations.push(format!("{ptr}: {err}"));
    }
    if !violations.is_empty() {
        return Outcome::SchemaViolation(violations.join("\n"));
    }

    // Stamp `_io` and write with sorted keys, two-space indent, atomically.
    let mut io = serde_json::Map::new();
    io.insert("stage".into(), serde_json::Value::String(node.clone()));
    io.insert("visit".into(), serde_json::Value::String(visit.clone()));
    io.insert("binary".into(), serde_json::Value::String(env!("CARGO_PKG_VERSION").into()));
    let mut io_inputs = serde_json::Map::new();
    for input in &stage.inputs {
        if let Ok(b) = std::fs::read(Path::new(&input.path)) {
            io_inputs.insert(input.name.clone(), serde_json::Value::String(common::sha256_hex(&b)));
        }
    }
    io.insert("inputs".into(), serde_json::Value::Object(io_inputs));

    let Some(mut obj) = output.as_object().cloned() else {
        return Outcome::Error("output must be a JSON object".into());
    };
    obj.insert("_io".into(), serde_json::Value::Object(io));
    let serialized = match serde_json::to_string_pretty(&serde_json::Value::Object(obj)) {
        Ok(s) => s,
        Err(e) => return Outcome::Error(format!("could not serialize: {e}")),
    };
    if let Err(e) = common::atomic_write(Path::new(&out.path), serialized.as_bytes()) {
        return Outcome::Error(format!("could not write {}: {e}", out.path));
    }
    Outcome::Written(format!("written: {}", out.path))
}

/// The refusal-2 message for a required input that is not complete, or None when complete.
fn incomplete(served_rec: &served::Served, input: &manifest::Input) -> Option<String> {
    let name = &input.name;
    let entry = served_rec.inputs.get(name);
    let entry = match entry {
        Some(e) => e,
        None => {
            return Some(format!(
                "input {name} was not read in full: call inputs(name=\"{name}\", part=1)"
            ))
        }
    };
    match entry.status.as_str() {
        "absent" => None,
        "ok" => entry.next_part().map(|p| {
            format!("input {name} was not read in full: call inputs(name=\"{name}\", part={p})")
        }),
        "missing" => Some(format!("input {name} is missing: call inputs(name=\"{name}\")")),
        _ => Some(format!("input {name} was not read in full: call inputs(name=\"{name}\")")),
    }
}
