//! The `stage` subcommand (C5) and the task-02 spike's log line.
//!
//! Writes `/tmp/fabro/.io/stage.json` with the workflow, node and a fresh visit so
//! the MCP server can report the current stage, then appends the spike line.

use serde_json::json;
use std::process::ExitCode;

use crate::common;

pub fn run() -> ExitCode {
    let started = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true);
    let workflow = std::env::var("FABRO_WORKFLOW").unwrap_or_default();
    let node = std::env::var("FABRO_NODE_ID").unwrap_or_default();
    let visit = common::new_visit();
    let stage = json!({
        "workflow": workflow,
        "node": node,
        "visit": visit,
        "started": started,
    });
    let path = common::io_dir().join("stage.json");
    let bytes = serde_json::to_vec_pretty(&stage).unwrap_or_else(|_| b"{}".to_vec());
    if let Err(e) = common::atomic_write(&path, &bytes) {
        eprintln!("stage: could not write {}: {e}", path.display());
        return ExitCode::FAILURE;
    }
    common::spike_log(&node, "stage_start");
    ExitCode::SUCCESS
}
