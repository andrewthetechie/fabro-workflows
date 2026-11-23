//! The `stage-end` hook: run a stage again when it ended without a submitted result (#93).
//!
//! A model sometimes prints a malformed tool call as plain text. Fabro takes a reply with no
//! tool call as the answer, so the stage ends `succeeded` with nothing written, and node
//! retries never apply because nothing failed. The Input receipt is the proof of a finished
//! stage instead, whatever the closing message says.
//!
//! One command serves two hooks, told apart by the hook context's `event`:
//!
//! - `stage_complete` marks the visit that ended `succeeded`. Timeouts and failures fire
//!   another event, and `allow_partial` reports `partially_succeeded`, so neither is marked.
//! - `edge_selected` reads the mark. If the marked visit's stage wrote no result for that
//!   visit, it overrides the edge with one back to the same node, [`MAX_RERUNS`] times.
//!
//! The hook line names the stages that must submit. The manifest cannot say it, because some
//! stages with an output contract submit only when blocked.

// Rust guideline compliant 2026-07-21

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde_json::Value;

use crate::{common, manifest};

/// How many times one entry into a stage may be run again.
///
/// The box model lost about 3% of sessions (47 of 1,705 visits, 2026-11-12 to 2026-11-23), so
/// two re-runs leave about 1 in 48,000 entries to the gate if losses are independent. Each re-run is a fresh session of
/// the stage's full length, and the node's timeout applies to each one separately.
pub const MAX_RERUNS: u64 = 2;

/// What the hook tells fabro.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Decision {
    /// Follow the edge fabro selected.
    Proceed,
    /// Go to this node instead, which is the one that just ended.
    Rerun(String),
}

/// Runs the `stage-end` subcommand.
///
/// It exits 0 on every path: fabro reads any other code from a blocking `edge_selected` hook
/// as a block, which ends the run. An error therefore proceeds, and the stage's own gate
/// reads the missing result as it always has.
pub fn run(stages: &[String]) -> ExitCode {
    match decide(stages) {
        Ok(Decision::Proceed) => {}
        Ok(Decision::Rerun(node)) => {
            println!("{}", serde_json::json!({"decision": "override", "edge_to": node}));
        }
        Err(e) => eprintln!("stage-end: warning: {e}; proceeding"),
    }
    ExitCode::SUCCESS
}

/// Judges one hook firing; `stages` are the node ids that must submit a result.
///
/// # Errors
///
/// Returns a sentence when the hook context, the manifest or the files under `.io/` cannot
/// be read or written. A re-run that cannot be counted is an error too, because an uncounted
/// re-run is an endless one.
pub fn decide(stages: &[String]) -> Result<Decision, String> {
    let ctx = common::hook_context()?;
    match field(&ctx, "event") {
        Some("stage_complete") => mark(&ctx, stages).map(|()| Decision::Proceed),
        Some("edge_selected") => judge(&ctx, stages),
        _ => Ok(Decision::Proceed),
    }
}

/// Records that the current visit of a listed stage ended `succeeded`.
fn mark(ctx: &Value, stages: &[String]) -> Result<(), String> {
    let Some(node) = field(ctx, "node_id").filter(|n| listed(n, stages)) else {
        return Ok(());
    };
    if field(ctx, "status") != Some("succeeded") {
        return Ok(());
    }
    match common::read_stage() {
        Some((current, visit)) if current == node => write(&state_path(node, "succeeded"), &visit),
        _ => Ok(()),
    }
}

/// Decides the edge out of a listed stage.
///
/// The hook's matcher also sees `edge_to`, so it fires on edges into a listed stage as well;
/// only `edge_from` is judged.
fn judge(ctx: &Value, stages: &[String]) -> Result<Decision, String> {
    let Some(node) = field(ctx, "edge_from").filter(|n| listed(n, stages)) else {
        return Ok(Decision::Proceed);
    };
    let Some(visit) = common::read_stage().filter(|(n, _)| n == node).map(|(_, v)| v) else {
        return Ok(Decision::Proceed);
    };
    let tries_path = state_path(node, "tries");
    let marked = std::fs::read_to_string(state_path(node, "succeeded")).unwrap_or_default();
    if marked != visit || has_result(node, &visit)? {
        return reset(&tries_path);
    }
    let tries = std::fs::read_to_string(&tries_path)
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or(0);
    if tries >= MAX_RERUNS {
        return reset(&tries_path);
    }
    write(&tries_path, &(tries + 1).to_string())?;
    Ok(Decision::Rerun(node.to_string()))
}

/// Whether the stage's output file carries the receipt of this visit.
///
/// A stage with no output contract has nothing to miss, so it counts as having one.
fn has_result(node: &str, visit: &str) -> Result<bool, String> {
    let m = manifest::load().map_err(|e| format!("cannot read manifest: {e}"))?;
    let Some(out) = m.stage(node).and_then(|s| s.output.as_ref()) else {
        return Ok(true);
    };
    let result: Value = std::fs::read_to_string(&out.path)
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default();
    let io = &result["_io"];
    Ok(field(io, "stage") == Some(node) && field(io, "visit") == Some(visit))
}

/// Proceeds, and gives the stage's next entry all its re-runs.
fn reset(tries: &Path) -> Result<Decision, String> {
    match std::fs::remove_file(tries) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(format!("cannot remove {}: {e}", tries.display())),
        _ => Ok(Decision::Proceed),
    }
}

fn listed(node: &str, stages: &[String]) -> bool {
    stages.iter().any(|s| s == node)
}

fn field<'a>(v: &'a Value, key: &str) -> Option<&'a str> {
    v.get(key).and_then(Value::as_str)
}

/// `.io/stage-end/<node>.<kind>`.
fn state_path(node: &str, kind: &str) -> PathBuf {
    common::io_dir().join("stage-end").join(format!("{node}.{kind}"))
}

fn write(path: &Path, text: &str) -> Result<(), String> {
    common::atomic_write(path, text.as_bytes()).map_err(|e| format!("cannot write {}: {e}", path.display()))
}
