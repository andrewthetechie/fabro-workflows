//! `scout config` and `scout ratchet`: the C1 to C3 rules over fixtures, plus one run of a
//! real `sh` per outcome (C3).

mod common;

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use fabro_io::scout::config::{self, ConfigOut, FlakyCfg, RatchetEntry};
use fabro_io::scout::ratchet::{self, Finding};
use fabro_io::scout::{flaky_fp, is_fingerprint, ratchet_fp, slug};

/// The fixture directory.
fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/scout")
}

/// The root the fixtures' absolute paths sit under (C3): the parse tests never read it.
fn root() -> PathBuf {
    PathBuf::from("/tmp/fabro-scout-test")
}

/// One fixture, as bytes.
fn read(name: &str) -> Vec<u8> {
    std::fs::read(fixtures().join(name)).unwrap()
}

/// The number of distinct paths a finding list names.
fn files(findings: &[Finding]) -> usize {
    findings.iter().map(|f| f.path.as_str()).collect::<BTreeSet<_>>().len()
}

/// A one-entry config for the `run` tests.
fn one_entry(command: &str, timeout_secs: u64) -> ConfigOut {
    ConfigOut {
        present: true,
        valid: true,
        error: None,
        filing: false,
        ratchet: vec![RatchetEntry {
            index: 0,
            tool: "ruff".to_string(),
            rule: "B006".to_string(),
            command: command.to_string(),
            enforce_in: "pyproject.toml".to_string(),
            enforce_how: "add B006 to extend-select".to_string(),
            timeout_secs,
        }],
        flaky: FlakyCfg::default(),
    }
}

#[test]
fn slug_follows_c3() {
    assert_eq!(slug("B006"), "b006");
    assert_eq!(
        slug("@typescript-eslint/no-floating-promises"),
        "typescript-eslint/no-floating-promises"
    );
    assert_eq!(slug("clippy::needless_collect"), "clippy-needless_collect");
    assert_eq!(slug("UI end-to-end (tauri-pilot)"), "ui-end-to-end-tauri-pilot");
    assert_eq!(slug("--"), "unknown");
    assert_eq!(slug(""), "unknown");
}

#[test]
fn fingerprints_follow_c3() {
    assert_eq!(ratchet_fp("ruff", "B006"), "ratchet:ruff:b006");
    assert_eq!(
        ratchet_fp("eslint", "@typescript-eslint/no-floating-promises"),
        "ratchet:eslint:typescript-eslint/no-floating-promises"
    );
    assert_eq!(
        ratchet_fp("clippy", "clippy::needless_collect"),
        "ratchet:clippy:clippy-needless_collect"
    );
    assert_eq!(
        flaky_fp(".github/workflows/ci.yml", "UI end-to-end (tauri-pilot)"),
        "flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot"
    );
    assert!(is_fingerprint(&ratchet_fp("ruff", "B006")));
    assert!(is_fingerprint(&flaky_fp(".github/workflows/ci.yml", "lint")));
}

#[test]
fn is_fingerprint_rejects_bad_shapes() {
    assert!(is_fingerprint("ratchet:ruff:b006"));
    assert!(is_fingerprint("ratchet:eslint:typescript-eslint/no-floating-promises"));
    assert!(is_fingerprint("flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot"));
    assert!(!is_fingerprint("ratchet:ruff"));
    assert!(!is_fingerprint("retro:x:y"));
    assert!(!is_fingerprint("ratchet:ruff:B006"));
    assert!(!is_fingerprint("ratchet:ruff:b006:extra"));
    assert!(!is_fingerprint("ratchet:ruff:"));
    assert!(!is_fingerprint("ratchet::b006"));
}

#[test]
fn a_moved_finding_is_not_new() {
    let at = ratchet::parse("ruff", "B006", &read("ruff.json"), &root()).unwrap();
    let moved = ratchet::parse("ruff", "B006", &read("ruff_moved.json"), &root()).unwrap();
    assert_eq!(at.len(), moved.len());
    assert_eq!(files(&at), files(&moved));
    assert_eq!(ratchet_fp("ruff", "B006"), "ratchet:ruff:b006");
    for (before, after) in at.iter().zip(&moved) {
        assert_eq!(before.path, after.path);
        assert_eq!(before.column, after.column);
        assert_eq!(before.message, after.message);
        assert_ne!(before.line, after.line, "the moved fixture must move every line");
    }
}

#[test]
fn ruff_keeps_only_the_rule_and_normalises_paths() {
    let findings = ratchet::parse("ruff", "B006", &read("ruff.json"), &root()).unwrap();
    assert_eq!(findings.len(), 3);
    assert_eq!(files(&findings), 2);
    assert_eq!(findings[0].path, "src/a.py");
    assert_eq!(findings[2].path, "src/b.py", "the absolute path is made relative");
    assert!(!findings.iter().any(|f| f.message.contains("Line too long")));
}

#[test]
fn eslint_keeps_only_the_rule() {
    let findings = ratchet::parse(
        "eslint",
        "@typescript-eslint/no-floating-promises",
        &read("eslint.json"),
        &root(),
    )
    .unwrap();
    assert_eq!(findings.len(), 3);
    assert_eq!(findings[0].path, "src/app.ts");
    assert!(!findings.iter().any(|f| f.message.contains("console")));
}

#[test]
fn a_fatal_eslint_message_is_an_error() {
    let error = ratchet::parse("eslint", "no-console", &read("eslint_fatal.json"), &root())
        .unwrap_err();
    assert!(error.starts_with("eslint could not parse "), "{error}");
    assert!(error.contains("broken.ts"), "{error}");
}

#[test]
fn clippy_collapses_targets_and_drops_registry_paths() {
    let findings =
        ratchet::parse("clippy", "clippy::needless_collect", &read("clippy.jsonl"), &root())
            .unwrap();
    assert_eq!(findings.len(), 1);
    assert_eq!(findings[0].path, "src/lib.rs");
    assert_eq!(findings[0].line, 10);
    assert_eq!(findings[0].column, 5);
}

#[test]
fn garbage_stdout_is_the_c3_error() {
    assert_eq!(
        ratchet::parse("ruff", "B006", b"not json", &root()).unwrap_err(),
        "ruff output is not valid JSON"
    );
    assert_eq!(
        ratchet::parse("eslint", "no-console", b"not json", &root()).unwrap_err(),
        "eslint output is not valid JSON"
    );
}

#[test]
fn config_missing_file_is_absent_and_dry() {
    let out = config::load(Path::new("/nonexistent/scout.toml"));
    assert!(!out.present);
    assert!(out.valid);
    assert_eq!(out.error, None);
    assert!(!out.filing);
    assert!(out.ratchet.is_empty());
    assert_eq!((out.flaky.min_flakes, out.flaky.window_days), (3, 14));
}

#[test]
fn config_valid_file_keeps_every_key() {
    let out = config::load(&fixtures().join("scout_valid.toml"));
    assert!(out.present);
    assert!(out.valid);
    assert_eq!(out.error, None);
    assert!(out.filing);
    assert_eq!(out.ratchet.len(), 1);
    let entry = &out.ratchet[0];
    assert_eq!(entry.index, 0);
    assert_eq!(entry.tool, "ruff");
    assert_eq!(entry.rule, "B006");
    assert_eq!(entry.timeout_secs, 900);
    assert_eq!(entry.enforce_in, "pyproject.toml");
    assert_eq!((out.flaky.min_flakes, out.flaky.window_days), (4, 7));
}

#[test]
fn config_minimal_file_uses_the_defaults() {
    let out = config::load(&fixtures().join("scout_minimal.toml"));
    assert!(out.valid);
    assert_eq!(out.error, None);
    assert!(!out.filing);
    assert!(out.ratchet.is_empty());
    assert_eq!((out.flaky.min_flakes, out.flaky.window_days), (3, 14));
}

#[test]
fn every_bad_config_is_invalid_and_dry() {
    let mut seen = 0;
    for entry in std::fs::read_dir(fixtures()).unwrap() {
        let path = entry.unwrap().path();
        let name = path.file_name().unwrap().to_string_lossy().into_owned();
        if !name.starts_with("scout_bad_") {
            continue;
        }
        seen += 1;
        let out = config::load(&path);
        assert!(out.present, "{name}");
        assert!(!out.valid, "{name}");
        assert!(out.error.as_deref().is_some_and(|e| !e.is_empty()), "{name}");
        assert!(!out.filing, "{name}");
        assert!(out.ratchet.is_empty(), "{name}");
    }
    assert!(seen >= 12, "expected a scout_bad_*.toml per C1 rule, found {seen}");
}

#[test]
fn run_a_command_that_prints_the_tools_json() {
    let dir = common::temp_root("scout-ratchet-ok");
    let command = format!("cat {}", fixtures().join("ruff.json").display());
    let out = ratchet::run(&one_entry(&command, 30), &dir);
    assert_eq!(out.status, "ok");
    assert_eq!(out.reason, None);
    assert_eq!(out.entries.len(), 1);
    let entry = &out.entries[0];
    assert_eq!(entry.status, "ok");
    assert_eq!(entry.error, None);
    assert_eq!(entry.fp, "ratchet:ruff:b006");
    assert_eq!(entry.count, 2, "the fixture's absolute path is outside this root");
    assert_eq!(entry.files, 1);
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn run_ignores_the_exit_code_but_needs_json() {
    let dir = common::temp_root("scout-ratchet-exit1");
    let out = ratchet::run(&one_entry("exit 1", 10), &dir);
    assert_eq!(out.entries[0].status, "error");
    assert_eq!(
        out.entries[0].error.as_deref(),
        Some("ruff output is not valid JSON")
    );
    assert_eq!(out.entries[0].count, 0);
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn run_kills_a_command_at_its_deadline_and_goes_on() {
    let dir = common::temp_root("scout-ratchet-timeout");
    let mut config = one_entry("sleep 30", 1);
    config.ratchet.push(RatchetEntry {
        index: 1,
        tool: "ruff".to_string(),
        rule: "B006".to_string(),
        command: format!("cat {}", fixtures().join("ruff.json").display()),
        enforce_in: "pyproject.toml".to_string(),
        enforce_how: "add B006 to extend-select".to_string(),
        timeout_secs: 10,
    });
    let started = Instant::now();
    let out = ratchet::run(&config, &dir);
    let elapsed = started.elapsed();
    assert!(elapsed < Duration::from_secs(5), "the deadline took {elapsed:?}");
    assert_eq!(out.entries[0].status, "error");
    assert_eq!(out.entries[0].error.as_deref(), Some("timed out after 1 s"));
    assert_eq!(out.entries[1].status, "ok", "the entry after a timeout still runs");
    assert_eq!(out.entries[1].count, 2);
    std::fs::remove_dir_all(&dir).ok();
}
