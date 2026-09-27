//! `served.json` (C1): per-visit record of which input parts each `inputs` call served.
//!
//! A new stage visit invalidates the previous record. `submit` reads this to refuse an
//! incomplete read, so the values here are the contract between `inputs` and `submit`.

use std::collections::BTreeMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::common;

/// The served record. `visit` anchors it to one stage visit.
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct Served {
    pub visit: String,
    #[serde(default)]
    pub inputs: BTreeMap<String, ServedInput>,
}

/// One input's served state.
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct ServedInput {
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub sha256: String,
    #[serde(default)]
    pub parts: u32,
    #[serde(default)]
    pub served: Vec<u32>,
    /// `ok` | `absent` | `missing`
    #[serde(default)]
    pub status: String,
}

impl ServedInput {
    /// An input is complete when it is optional-and-absent, or when every part has been
    /// served (C1).
    pub fn is_complete(&self) -> bool {
        match self.status.as_str() {
            "absent" => true,
            "ok" => {
                if self.parts == 0 {
                    true
                } else {
                    (1..=self.parts).all(|p| self.served.contains(&p))
                }
            }
            _ => false,
        }
    }

    /// The first part not yet served, for a refuse message (C4). None when complete.
    pub fn next_part(&self) -> Option<u32> {
        if self.status != "ok" {
            return None;
        }
        (1..=self.parts).find(|p| !self.served.contains(p))
    }
}

pub fn path() -> std::path::PathBuf {
    common::io_dir().join("served.json")
}

/// Take the exclusive lock that serializes read-modify-write of `served.json`.
///
/// The lock is `flock` on `.io/served.lock` and is released when the returned file is
/// dropped. A lock that cannot be taken returns `None` and the caller proceeds unlocked:
/// a lost update only costs the model a `submit` refusal and another `inputs` call, while
/// a failed tool call would cost the whole read.
pub fn lock() -> Option<std::fs::File> {
    let dir = common::io_dir();
    std::fs::create_dir_all(&dir).ok()?;
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(dir.join("served.lock"))
        .ok()?;
    file.lock().ok()?;
    Some(file)
}

/// Load the served record for the current visit, or a fresh one.
pub fn load(visit: &str) -> Served {
    let raw = std::fs::read_to_string(path()).ok();
    match raw.and_then(|r| serde_json::from_str::<Served>(&r).ok()) {
        Some(s) if s.visit == visit => s,
        _ => Served { visit: visit.to_string(), inputs: BTreeMap::new() },
    }
}

/// Save atomically.
pub fn save(served: &Served) -> std::io::Result<()> {
    let bytes = serde_json::to_vec_pretty(served).unwrap_or_else(|_| b"{}".to_vec());
    common::atomic_write(&path(), &bytes)
}

/// The 2020-12-invalid JSON shapes are rejected by construction; this only guards the
/// path join for sealed matching elsewhere.
pub fn exists() -> bool {
    Path::new(&path()).exists()
}

/// The visit recorded on disk, if a served.json exists for a prior stage. None otherwise.
pub fn stored_visit() -> Option<String> {
    let raw = std::fs::read_to_string(path()).ok()?;
    serde_json::from_str::<serde_json::Value>(&raw).ok()?
        .get("visit")?
        .as_str()
        .map(|s| s.to_string())
}
