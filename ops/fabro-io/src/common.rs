//! Shared helpers: the sandbox io root and atomic writes.
//!
//! Contracts: docs/stage-io/00-overview-and-contracts.md C1. The io root is
//! `/tmp/fabro` on the host; tests override it with `FABRO_IO_ROOT`.

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

/// The sha256 of `bytes`, lowercase hex (C1, C4).
pub fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::Digest;
    let digest = sha2::Sha256::digest(bytes);
    hex::encode(digest)
}

/// The current stage identity from `.io/stage.json`: `(node, visit)`. None if the
/// file is missing or malformed.
pub fn read_stage() -> Option<(String, String)> {
    let raw = std::fs::read_to_string(io_dir().join("stage.json")).ok()?;
    let v: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let node = v.get("node")?.as_str()?.to_string();
    let visit = v.get("visit")?.as_str()?.to_string();
    Some((node, visit))
}
