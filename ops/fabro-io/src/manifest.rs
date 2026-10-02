//! Parse and validate the Stage manifest (C2).
//!
//! The binary holds no stage facts; everything comes from the manifest. The runtime
//! reads the generated (flat) form: `FABRO_IO_MANIFEST` env var, or (the fallback
//! carrier D4) `/tmp/fabro/.io/manifest.json`. Unknown keys are rejected, so a
//! misspelled contract name fails loudly at first touch instead of silently.

use std::collections::HashMap;

use serde::Deserialize;

use crate::common;

/// The flat generated manifest, as it appears in each `workflow.toml` (C2).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub version: u32,
    #[serde(rename = "min_binary")]
    pub min_binary: String,
    #[serde(default)]
    pub stages: HashMap<String, Stage>,
}

/// One stage: its ordered inputs, optional output contract and sealed paths.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Stage {
    #[serde(default)]
    pub inputs: Vec<Input>,
    #[serde(default)]
    pub output: Option<Output>,
    #[serde(default)]
    pub sealed: Vec<String>,
    /// `"write"` exempts the stage from `git-guard` (docs/coder-tweaks C2); anything
    /// else, or nothing, means the stage may only read with git.
    #[serde(default)]
    pub git: Option<String>,
}

/// One ordered input. `absent` is the sentence shown when an optional input is absent.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub name: String,
    pub path: String,
    pub required: bool,
    #[serde(default)]
    pub about: String,
    #[serde(default)]
    pub absent: Option<String>,
}

/// The output contract. `schema` is the inline JSON Schema (draft 2020-12).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Output {
    pub path: String,
    pub schema: serde_json::Value,
}

/// Where a manifest was read from, for error messages.
#[derive(Debug)]
pub enum LoadError {
    InvalidJson(String),
    Missing,
}

impl std::fmt::Display for LoadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LoadError::InvalidJson(e) => write!(f, "{e}"),
            LoadError::Missing => write!(f, "no manifest found"),
        }
    }
}

/// Load and parse the manifest from `FABRO_IO_MANIFEST`, or the `.io/manifest.json`
/// fallback carrier, in that order.
pub fn load() -> Result<Manifest, LoadError> {
    if let Ok(raw) = std::env::var("FABRO_IO_MANIFEST") {
        return parse(&raw);
    }
    let file = common::io_dir().join("manifest.json");
    match std::fs::read_to_string(&file) {
        Ok(raw) => parse(&raw),
        Err(_) => Err(LoadError::Missing),
    }
}

fn parse(raw: &str) -> Result<Manifest, LoadError> {
    serde_json::from_str(raw).map_err(|e| LoadError::InvalidJson(e.to_string()))
}

impl Manifest {
    /// The stage entry for a node id, if the manifest lists it.
    pub fn stage(&self, node: &str) -> Option<&Stage> {
        self.stages.get(node)
    }

    /// Is the manifest's `min_binary` newer than the running binary? Returns the
    /// required version when the running binary is too old.
    pub fn min_binary_above(&self) -> Option<String> {
        if version_gt(&self.min_binary, env!("CARGO_PKG_VERSION")) {
            Some(self.min_binary.clone())
        } else {
            None
        }
    }

    /// The output schema for a stage, if it writes a contract.
    pub fn output_schema(&self, node: &str) -> Option<&serde_json::Value> {
        self.stage(node).and_then(|s| s.output.as_ref()).map(|o| &o.schema)
    }
}

/// Compare two dotted numeric versions (e.g. `0.2.0` > `0.1.9`). Truncates an
/// unreleased suffix (anything after the digits) for comparison.
pub fn version_gt(a: &str, b: &str) -> bool {
    fn nums(v: &str) -> Vec<u64> {
        let core = v
            .split(['-', '+'])
            .next()
            .unwrap_or(v);
        core.split('.')
            .filter_map(|p| p.parse::<u64>().ok())
            .collect()
    }
    let a = nums(a);
    let b = nums(b);
    let n = a.len().max(b.len());
    for i in 0..n {
        let x = a.get(i).copied().unwrap_or(0);
        let y = b.get(i).copied().unwrap_or(0);
        if x != y {
            return x > y;
        }
    }
    false
}
