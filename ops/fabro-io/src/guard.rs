//! The `guard` hook (C5): a guard against mistakes, not a boundary.
//!
//! Reads `FABRO_HOOK_CONTEXT`, collects every string value in `tool_input`, and blocks
//! (exit 2, `{"decision":"block",...}`) when one contains a Sealed path or a path a
//! sealed glob matches. It proceeds on its own errors with a warning, so a broken guard
//! never stops a stage (ADR 0016 D8).

use std::process::ExitCode;

use globset::{GlobBuilder, GlobMatcher};

use crate::{common, manifest};

/// The `guard` subcommand entry point.
pub fn run() -> ExitCode {
    let Ok(raw) = std::env::var("FABRO_HOOK_CONTEXT") else {
        eprintln!("guard: warning: FABRO_HOOK_CONTEXT not set; proceeding");
        return ExitCode::SUCCESS;
    };
    let ctx: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("guard: warning: could not parse FABRO_HOOK_CONTEXT ({e}); proceeding");
            return ExitCode::SUCCESS;
        }
    };

    // The sealed list for the current stage.
    let Some((node, _)) = common::read_stage() else {
        eprintln!("guard: warning: no stage.json; proceeding");
        return ExitCode::SUCCESS;
    };
    let m = match manifest::load() {
        Ok(m) => m,
        Err(e) => {
            eprintln!("guard: warning: cannot read manifest ({e}); proceeding");
            return ExitCode::SUCCESS;
        }
    };
    let Some(stage) = m.stage(&node) else {
        return ExitCode::SUCCESS;
    };
    if stage.sealed.is_empty() {
        return ExitCode::SUCCESS;
    }
    let matchers: Vec<(String, Option<GlobMatcher>)> = stage
        .sealed
        .iter()
        .map(|entry| {
            let matcher = if entry.contains(['*', '?', '[']) {
                GlobBuilder::new(entry)
                    .literal_separator(true)
                    .build()
                    .ok()
                    .map(|g| g.compile_matcher())
            } else {
                None
            };
            (entry.clone(), matcher)
        })
        .collect();

    if let Some(matched) = blocked_by(&ctx, &matchers) {
        println!(
            "{{\"decision\":\"block\",\"reason\":\"{} is sealed for this stage\"}}",
            matched
        );
        return ExitCode::from(2);
    }
    ExitCode::SUCCESS
}

/// The matched sealed entry, or None if nothing is sealed.
fn blocked_by(ctx: &serde_json::Value, matchers: &[(String, Option<GlobMatcher>)]) -> Option<String> {
    let tool_input = ctx.get("tool_input").unwrap_or(&serde_json::Value::Null);
    let mut strings = Vec::new();
    collect_strings(tool_input, &mut strings);
    for s in &strings {
        for (entry, matcher) in matchers {
            match matcher {
                Some(m) => {
                    for token in tokenize(s) {
                        if m.is_match(token) {
                            return Some(entry.clone());
                        }
                    }
                }
                None => {
                    if s.contains(entry.as_str()) {
                        return Some(entry.clone());
                    }
                }
            }
        }
    }
    None
}

/// Collect every string leaf of a JSON value, recursively (including nested shapes).
fn collect_strings(v: &serde_json::Value, out: &mut Vec<String>) {
    match v {
        serde_json::Value::String(s) => out.push(s.clone()),
        serde_json::Value::Array(a) => {
            for x in a {
                collect_strings(x, out);
            }
        }
        serde_json::Value::Object(o) => {
            for (_, x) in o {
                collect_strings(x, out);
            }
        }
        _ => {}
    }
}

/// Split a string into candidate path tokens on non-path punctuation and whitespace.
fn tokenize(s: &str) -> Vec<&str> {
    s.split(|c: char| {
        c.is_whitespace()
            || matches!(c, '\'' | '"' | '`' | ';' | '|' | '&' | '(' | ')' | '[' | ']' | '{' | '}' | ',' | '>' | '<' | '!')
    })
    .filter(|t| !t.is_empty())
    .collect()
}
