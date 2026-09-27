//! The `stage` subcommand (C5): write `stage.json` with a fresh 128-bit visit.
//!
//! This is the `io-stage` stage_start hook. It creates `/tmp/fabro/.io/` itself, checks
//! the binary is not older than the manifest's `min_binary`, and writes a new random
//! visit on every stage invocation so a retried stage gets a fresh identity (spike
//! check 6).

use std::process::ExitCode;

use crate::{common, manifest};

/// The `stage` subcommand entry point.
///
/// A block decision is printed on stdout, because fabro parses a hook's decision from
/// stdout and replaces a missing one with "hook exited with code 2".
pub fn run() -> ExitCode {
    match manifest::load() {
        Err(manifest::LoadError::InvalidJson(e)) => {
            return block(&format!("FABRO_IO_MANIFEST is not valid JSON: {e}"));
        }
        Err(manifest::LoadError::Missing) => {
            // No manifest at all: nothing to compare, still write stage.json.
        }
        Ok(m) => {
            if let Some(required) = m.min_binary_above() {
                return block(&format!(
                    "fabro-io {} is older than the manifest's min_binary {required}; rebuild the profile images",
                    env!("CARGO_PKG_VERSION")
                ));
            }
        }
    }

    let started = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true);
    let workflow = std::env::var("FABRO_WORKFLOW").unwrap_or_default();
    let node = std::env::var("FABRO_NODE_ID").unwrap_or_default();
    let visit = common::new_visit();
    let stage = serde_json::json!({
        "workflow": workflow,
        "node": node,
        "visit": visit,
        "started": started,
    });
    let path = common::io_dir().join("stage.json");
    let bytes = serde_json::to_vec_pretty(&stage).unwrap_or_else(|_| b"{}".to_vec());
    if let Err(e) = common::atomic_write(&path, &bytes) {
        // C5: exit 0 in every case but the two blocks. A blocking hook that exits 1 would
        // stop the stage. Remove the previous stage's identity instead, so that neither the
        // server nor a gate can mistake it for this stage's: every receipt check then fails
        // closed on the empty visit.
        let _ = std::fs::remove_file(&path);
        eprintln!("stage: warning: could not write {}: {e}; proceeding without a stage identity", path.display());
    }
    ExitCode::SUCCESS
}

/// Print a C5 block decision on stdout and return exit code 2.
fn block(reason: &str) -> ExitCode {
    println!("{}", serde_json::json!({"decision": "block", "reason": reason}));
    ExitCode::from(2)
}
