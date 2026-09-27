//! Shared helpers: the sandbox io root, atomic writes and the spike log.
//!
//! Contracts: docs/stage-io/00-overview-and-contracts.md C1. The io root is
//! `/tmp/fabro` on the host; tests override it with `FABRO_IO_ROOT` (task 03).

use std::path::{Path, PathBuf};

/// The sandbox root holding stage contracts. Overridden by `FABRO_IO_ROOT` in tests.
pub fn io_root() -> PathBuf {
    std::env::var("FABRO_IO_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/tmp/fabro"))
}

/// The `.io/` directory under the root. `stage` creates it itself so it can be the
/// first thing written under the root (the `claim` lesson in AGENTS.md).
pub fn io_dir() -> PathBuf {
    io_root().join(".io")
}

/// Write `bytes` to `path` atomically: a temp file beside it, then a rename.
pub fn atomic_write(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let dir = path.parent().unwrap_or_else(|| Path::new("."));
    std::fs::create_dir_all(dir)?;
    let tmp = dir.join(format!(".fabro-io-{}-{}", std::process::id(), new_visit()));
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

/// A 128-bit random visit id as 32 lowercase hex characters (C1).
pub fn new_visit() -> String {
    let mut buf = [0u8; 16];
    getrandom::fill(&mut buf).expect("getrandom failed: system has no entropy");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// Append one line to `.io/spike.log` (task 02 spike only).
///
/// The line carries the time, the node id and the manifest-env state, so the seven
/// spike checks can read them off the log without a sandbox shell after the run.
pub fn spike_log(node: &str, note: &str) {
    let t = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true);
    let mv = std::env::var("FABRO_IO_MANIFEST");
    let manifest = match &mv {
        Ok(v) => format!("yes len={}", v.len()),
        Err(_) => "no".to_string(),
    };
    let line = format!("{t} node={node} manifest={manifest} {note}\n");
    let path = io_dir().join("spike.log");
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = f.write_all(line.as_bytes());
    }
}
