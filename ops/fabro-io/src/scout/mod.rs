//! `scout`: the seventh package's pure logic (ADR 0027).
//!
//! `config` turns a repository's `.fabro/scout.toml` into normalised JSON and `ratchet`
//! runs its declared Ratchet rules and counts one rule's findings. Tasks 02 and 03 add
//! `flaky`, `plan` and `text`. Everything here is pure except `ratchet`, which runs the
//! declared commands: no subcommand calls GitHub or reads the clock.
//!
//! Contracts: docs/scout/00-overview-and-contracts.md C1 to C8.

// Rust guideline compliant 2026-07-21

pub mod config;
pub mod flaky;
pub mod ratchet;

use std::process::ExitCode;

const USAGE: &str = concat!(
    "usage: fabro-io scout <command> [args]\n",
    "commands: config [--file PATH] | ratchet --config FILE [--root DIR] | flaky --runs FILE --jobs-dir DIR --since TIMESTAMP\n"
);

/// Dispatches `fabro-io scout <command>` (C2, C3).
///
/// An unknown or missing sub-subcommand prints usage to stderr and returns exit code 2.
#[must_use]
pub fn run_cli(args: &[String]) -> ExitCode {
    let Some(sub) = args.first().map(String::as_str) else {
        eprint!("{USAGE}");
        return ExitCode::from(2);
    };
    match sub {
        "config" => config::run_cli(&args[1..]),
        "ratchet" => ratchet::run_cli(&args[1..]),
        "flaky" => flaky::run_cli(&args[1..]),
        other => {
            eprintln!("scout: unknown subcommand '{other}'");
            eprint!("{USAGE}");
            ExitCode::from(2)
        }
    }
}

/// Lowercases `text` and collapses every run outside `[a-z0-9_./-]` into one `-` (C3).
///
/// Leading and trailing `-` are removed, and an empty result becomes `unknown`.
#[must_use]
pub fn slug(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut pending_dash = false;
    for c in text.chars().flat_map(char::to_lowercase) {
        if c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '_' | '.' | '/' | '-') {
            if pending_dash && !out.is_empty() {
                out.push('-');
            }
            pending_dash = false;
            out.push(c);
        } else {
            pending_dash = true;
        }
    }
    let trimmed = out.trim_matches('-');
    if trimmed.is_empty() { "unknown".to_string() } else { trimmed.to_string() }
}

/// The C3 fingerprint of one Ratchet rule: `ratchet:<tool>:<rule>`.
#[must_use]
pub fn ratchet_fp(tool: &str, rule: &str) -> String {
    format!("ratchet:{}:{}", slug(tool), slug(rule))
}

/// The C3 fingerprint of one Flaky job: `flaky:<workflow path>:<job name>`.
#[must_use]
pub fn flaky_fp(workflow_path: &str, job_name: &str) -> String {
    format!("flaky:{}:{}", slug(workflow_path), slug(job_name))
}

/// True when `fp` matches C3's `^(ratchet|flaky)(:[a-z0-9_./-]+){2}$`.
///
/// The pattern is written by hand because `fabro-io` carries no regex crate.
#[must_use]
pub fn is_fingerprint(fp: &str) -> bool {
    let mut parts = fp.split(':');
    match parts.next() {
        Some("ratchet" | "flaky") => {}
        _ => return false,
    }
    let mut count = 0;
    for part in parts {
        let allowed = part.bytes().all(|b| {
            b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b, b'_' | b'.' | b'/' | b'-')
        });
        if part.is_empty() || !allowed {
            return false;
        }
        count += 1;
    }
    count == 2
}

/// True when `s` is GitHub's UTC timestamp form `YYYY-MM-DDTHH:MM:SSZ` (C4).
///
/// Exactly 20 characters, digits where digits belong, and `T` and `Z` in place. The fields
/// are not range-checked: every timestamp in this series is compared as a string.
#[must_use]
pub fn is_github_ts(s: &str) -> bool {
    let bytes = s.as_bytes();
    if bytes.len() != 20 {
        return false;
    }
    let digits = [0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18];
    if !digits.iter().all(|i| bytes[*i].is_ascii_digit()) {
        return false;
    }
    bytes[4] == b'-'
        && bytes[7] == b'-'
        && bytes[10] == b'T'
        && bytes[13] == b':'
        && bytes[16] == b':'
        && bytes[19] == b'Z'
}
