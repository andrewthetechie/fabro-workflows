//! `scout config`: a repository's `.fabro/scout.toml` as normalised JSON (ADR 0027 C1, C2).
//!
//! This never fails on a bad file. A missing file and an invalid file both become C2's
//! shapes, because D15 makes the run continue dry rather than stop: the flaky source still
//! runs with the defaults. The whole file is closed: an unknown key, an unknown tool or a
//! value outside its range is an error, and the first one is named.

// Rust guideline compliant 2026-07-21

use std::collections::BTreeSet;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::{Deserialize, Serialize};

/// The only accepted `version` (C1).
const VERSION: u32 = 1;
/// `ratchet[].timeout_secs` when the key is absent, in seconds (C1).
const TIMEOUT_DEFAULT_S: u64 = 1200;
/// The most `[[ratchet]]` entries one file may declare (C1).
const MAX_RATCHET: usize = 10;
/// `timeout_secs` may be at least this (C1).
const TIMEOUT_MIN_S: u64 = 1;
/// `timeout_secs` may be at most this (C1).
const TIMEOUT_MAX_S: u64 = 3000;
/// `flaky.min_flakes` when the key is absent (C1).
const MIN_FLAKES_DEFAULT: u32 = 3;
/// `min_flakes` may be at least this (C1).
const MIN_FLAKES_MIN: u32 = 1;
/// `min_flakes` may be at most this (C1).
const MIN_FLAKES_MAX: u32 = 20;
/// `flaky.window_days` when the key is absent (C1).
const WINDOW_DAYS_DEFAULT: u32 = 14;
/// `window_days` may be at least this (C1).
const WINDOW_DAYS_MIN: u32 = 1;
/// `window_days` may be at most this (C1).
const WINDOW_DAYS_MAX: u32 = 30;
/// The tools a Ratchet rule may name (C1, D4).
const TOOLS: [&str; 3] = ["ruff", "eslint", "clippy"];
/// The `--file` default, relative to the checkout root (C2).
pub const SCOUT_TOML: &str = ".fabro/scout.toml";

/// The raw `.fabro/scout.toml`. `deny_unknown_fields` on every table makes an unknown key
/// an error (C1).
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    version: u32,
    #[serde(default)]
    filing: bool,
    #[serde(default)]
    ratchet: Vec<RawRatchet>,
    flaky: Option<RawFlaky>,
}

/// One `[[ratchet]]` entry (C1).
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawRatchet {
    tool: String,
    rule: String,
    command: String,
    enforce_in: String,
    enforce_how: String,
    timeout_secs: Option<u64>,
}

/// The optional `[flaky]` table (C1).
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFlaky {
    min_flakes: Option<u32>,
    window_days: Option<u32>,
}

/// C2's output, and the config JSON `ratchet` and `plan` read back.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ConfigOut {
    /// Whether the file exists.
    pub present: bool,
    /// Whether the file parsed and passed every C1 rule.
    pub valid: bool,
    /// A one-line reason when it did not; `null` when it did.
    pub error: Option<String>,
    /// True only when the file is valid and says `filing = true` (D14).
    pub filing: bool,
    /// The Ratchet rules, in declaration order.
    pub ratchet: Vec<RatchetEntry>,
    /// The flaky thresholds.
    pub flaky: FlakyCfg,
}

/// One Ratchet rule as C2 prints it.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RatchetEntry {
    /// The position in the file, from 0.
    pub index: usize,
    /// `ruff`, `eslint` or `clippy`.
    pub tool: String,
    /// The rule as the tool reports it.
    pub rule: String,
    /// The command that prints the tool's JSON on stdout.
    pub command: String,
    /// The config file the fix adds the rule to.
    pub enforce_in: String,
    /// How the fix enforces it there.
    pub enforce_how: String,
    /// The entry's own deadline, in seconds.
    pub timeout_secs: u64,
}

/// The `[flaky]` thresholds, with C1's defaults filled in.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FlakyCfg {
    /// Occurrences in the window that file a Flaky job (D5).
    pub min_flakes: u32,
    /// How many days back the Flaky source looks (D5).
    pub window_days: u32,
}

impl Default for FlakyCfg {
    fn default() -> Self {
        FlakyCfg { min_flakes: MIN_FLAKES_DEFAULT, window_days: WINDOW_DAYS_DEFAULT }
    }
}

/// Reads and normalises `path` (C2). Never returns an error.
///
/// A missing file is `present: false`; an unreadable or invalid one is `present: true,
/// valid: false` with a one-line `error`, `filing: false` and no Ratchet rules (D15).
#[must_use]
pub fn load(path: &Path) -> ConfigOut {
    match std::fs::read_to_string(path) {
        Ok(text) => parse(&text),
        Err(e) if e.kind() == ErrorKind::NotFound => ConfigOut {
            present: false,
            valid: true,
            error: None,
            filing: false,
            ratchet: Vec::new(),
            flaky: FlakyCfg::default(),
        },
        Err(e) => invalid(format!("cannot read {}: {e}", path.display())),
    }
}

/// Parses and validates the text of a `.fabro/scout.toml` (C1, C2).
#[must_use]
pub fn parse(text: &str) -> ConfigOut {
    let raw: Raw = match toml::from_str(text) {
        Ok(raw) => raw,
        Err(e) => return invalid(e.to_string()),
    };
    if let Err(error) = validate(&raw) {
        return invalid(error);
    }
    let ratchet = raw
        .ratchet
        .iter()
        .enumerate()
        .map(|(index, entry)| RatchetEntry {
            index,
            tool: entry.tool.clone(),
            rule: entry.rule.clone(),
            command: entry.command.clone(),
            enforce_in: entry.enforce_in.clone(),
            enforce_how: entry.enforce_how.clone(),
            timeout_secs: entry.timeout_secs.unwrap_or(TIMEOUT_DEFAULT_S),
        })
        .collect();
    let flaky = raw.flaky.as_ref().map_or_else(FlakyCfg::default, |flaky| FlakyCfg {
        min_flakes: flaky.min_flakes.unwrap_or(MIN_FLAKES_DEFAULT),
        window_days: flaky.window_days.unwrap_or(WINDOW_DAYS_DEFAULT),
    });
    ConfigOut {
        present: true,
        valid: true,
        error: None,
        filing: raw.filing,
        ratchet,
        flaky,
    }
}

/// The C2 shape for an invalid file (D15).
fn invalid(error: String) -> ConfigOut {
    ConfigOut {
        present: true,
        valid: false,
        error: Some(error),
        filing: false,
        ratchet: Vec::new(),
        flaky: FlakyCfg::default(),
    }
}

/// Applies every C1 rule, returning the first one broken as a one-line reason.
fn validate(raw: &Raw) -> Result<(), String> {
    if raw.version != VERSION {
        return Err(format!("version must be {VERSION}, found {}", raw.version));
    }
    if raw.ratchet.len() > MAX_RATCHET {
        return Err(format!(
            "at most {MAX_RATCHET} [[ratchet]] entries are allowed, found {}",
            raw.ratchet.len()
        ));
    }
    let mut seen: BTreeSet<(&str, &str)> = BTreeSet::new();
    for (index, entry) in raw.ratchet.iter().enumerate() {
        if !TOOLS.contains(&entry.tool.as_str()) {
            return Err(format!(
                "ratchet[{index}]: tool must be one of {}, found \"{}\"",
                TOOLS.join(", "),
                entry.tool
            ));
        }
        for (name, value) in [
            ("rule", &entry.rule),
            ("command", &entry.command),
            ("enforce_in", &entry.enforce_in),
            ("enforce_how", &entry.enforce_how),
        ] {
            if value.is_empty() {
                return Err(format!("ratchet[{index}]: {name} must not be empty"));
            }
        }
        if let Some(timeout) = entry.timeout_secs
            && !(TIMEOUT_MIN_S..=TIMEOUT_MAX_S).contains(&timeout)
        {
            return Err(format!(
                "ratchet[{index}]: timeout_secs must be between {TIMEOUT_MIN_S} and {TIMEOUT_MAX_S}, found {timeout}"
            ));
        }
        if !seen.insert((&entry.tool, &entry.rule)) {
            return Err(format!(
                "ratchet[{index}]: duplicate rule {} {}",
                entry.tool, entry.rule
            ));
        }
    }
    if let Some(flaky) = &raw.flaky {
        if let Some(min_flakes) = flaky.min_flakes
            && !(MIN_FLAKES_MIN..=MIN_FLAKES_MAX).contains(&min_flakes)
        {
            return Err(format!(
                "flaky.min_flakes must be between {MIN_FLAKES_MIN} and {MIN_FLAKES_MAX}, found {min_flakes}"
            ));
        }
        if let Some(window_days) = flaky.window_days
            && !(WINDOW_DAYS_MIN..=WINDOW_DAYS_MAX).contains(&window_days)
        {
            return Err(format!(
                "flaky.window_days must be between {WINDOW_DAYS_MIN} and {WINDOW_DAYS_MAX}, found {window_days}"
            ));
        }
    }
    Ok(())
}

/// Runs `scout config [--file PATH]` (C2). Always exits 0 except for bad arguments.
#[must_use]
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut file = PathBuf::from(SCOUT_TOML);
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--file" => {
                i += 1;
                let Some(value) = args.get(i) else {
                    eprintln!("scout config: --file needs a path");
                    return ExitCode::from(2);
                };
                file = PathBuf::from(value);
            }
            other => {
                eprintln!("scout config: unexpected argument '{other}'");
                return ExitCode::from(2);
            }
        }
        i += 1;
    }
    let out = load(&file);
    let json = serde_json::to_string(&out).unwrap_or_else(|e| {
        eprintln!("scout config: cannot serialize the result: {e}");
        String::new()
    });
    println!("{json}");
    ExitCode::SUCCESS
}
