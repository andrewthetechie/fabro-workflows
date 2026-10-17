//! `scout flakeradar`: FlakeRadar's tests and CI-run lookups into scout candidates (ADR 0028
//! C4b). The fixtures are live writers-app responses from 2026-10-17, trimmed, with every
//! name, branch and failure text replaced: that repository is private and this one is not.

mod common;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

use fabro_io::scout::config::ConfigOut;
use fabro_io::scout::flakeradar::{self, History, Inputs, RunFailures, TestItem};
use fabro_io::scout::flaky::{FlakyJob, FlakyOut, Occurrence};

const REPO: &str = "andrewthetechie/writers-app";
const BASE: &str = "https://flakes.herrington.services";
/// 14 days before the fixtures were taken: 11739, last seen 2026-09-26, is outside it.
const SINCE: &str = "2026-10-03T00:00:00Z";

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/scout/flakeradar")
}

fn tests() -> Vec<TestItem> {
    serde_json::from_str(&std::fs::read_to_string(fixtures().join("tests.json")).unwrap()).unwrap()
}

fn histories() -> BTreeMap<u64, History> {
    let mut map = BTreeMap::new();
    for id in [11739, 11850, 11853, 11903] {
        let path = fixtures().join(format!("history/test_{id}.json"));
        map.insert(id, serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap());
    }
    map
}

fn runs() -> BTreeMap<(u64, u64), RunFailures> {
    let path = fixtures().join("runs/run_38965640313_1.json");
    let one: RunFailures = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    BTreeMap::from([((38_965_640_313, 1), one)])
}

fn config(min_proven: u32) -> ConfigOut {
    let mut config: ConfigOut = serde_json::from_str(
        r#"{"present":true,"valid":true,"error":null,"filing":false,"ratchet":[],"flaky":{"min_flakes":3,"window_days":14}}"#,
    )
    .unwrap();
    config.flakeradar.covered = true;
    config.flakeradar.min_proven = min_proven;
    config
}

fn occ(run_id: u64, attempt: u64, failed_job_id: u64) -> Occurrence {
    Occurrence {
        run_id,
        run_url: String::new(),
        attempt,
        failed_job_id,
        failed_job_url: String::new(),
        failed_steps: vec![],
        created_at: "2026-10-16T21:10:00Z".to_string(),
        head_branch: "chore/scout-toml".to_string(),
        head_sha: "2621219947475f39".to_string(),
    }
}

/// The tauri-pilot job with the writers-app#1083 flake (explained) and one FlakeRadar never saw.
fn flaky() -> FlakyOut {
    let occurrences = vec![occ(38_965_640_313, 1, 117_191_778_924), occ(38_000_000_001, 1, 116_000_000_001)];
    FlakyOut {
        since: SINCE.to_string(),
        jobs: vec![FlakyJob {
            fp: "flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot".to_string(),
            workflow: ".github/workflows/ci.yml".to_string(),
            job: "UI end-to-end (tauri-pilot)".to_string(),
            count: occurrences.len(),
            occurrences,
        }],
    }
}

fn digest(
    tests: &[TestItem],
    histories: &BTreeMap<u64, History>,
    config: &ConfigOut,
) -> flakeradar::FlakeradarOut {
    let runs = runs();
    let flaky = flaky();
    flakeradar::digest(&Inputs {
        tests,
        histories,
        runs: &runs,
        flaky: &flaky,
        config,
        since: SINCE,
        base: BASE,
        repo: REPO,
    })
    .unwrap()
}

#[test]
fn candidates_are_fresh_proven_flaky_tests_in_score_order() {
    let out = digest(&tests(), &histories(), &config(1));
    let ids: Vec<u64> = out.tests.iter().map(|t| t.id).collect();
    // 11739 is flaky with score 0.7 but was last seen 2026-09-26; the suspects never qualify.
    // 11853 and 11903 tie on score and proven count, so the fingerprint orders them.
    assert_eq!(ids, vec![11850, 11853, 11903]);
    let first = &out.tests[0];
    assert_eq!(first.fp, "flaky:test-pilot:de539bc6672291b6ffd4e5744d6d2bc087fa2b61");
    assert_eq!(first.url, "https://flakes.herrington.services/?repo=andrewthetechie/writers-app&project=pilot&test=11850");
    assert_eq!(first.name, "test 11850");
    assert_eq!((first.score, first.proven), (0.7, 2));
    assert_eq!(first.category.as_deref(), Some("timing"));
    assert_eq!(first.last_failing_sha.as_deref(), Some("2621219947475f39b1c30e3d08d4f353fbf37172"));
}

#[test]
fn min_proven_drops_tests_with_fewer_proven_flakes() {
    let out = digest(&tests(), &histories(), &config(2));
    let ids: Vec<u64> = out.tests.iter().map(|t| t.id).collect();
    assert_eq!(ids, vec![11850]);
}

#[test]
fn a_quarantined_test_is_never_a_candidate() {
    let mut tests = tests();
    tests.iter_mut().find(|t| t.id == 11850).unwrap().quarantined = true;
    let out = digest(&tests, &histories(), &config(1));
    assert!(out.tests.iter().all(|t| t.id != 11850));
}

#[test]
fn a_candidate_without_a_history_file_is_dropped() {
    let mut histories = histories();
    histories.remove(&11853);
    let out = digest(&tests(), &histories, &config(1));
    let ids: Vec<u64> = out.tests.iter().map(|t| t.id).collect();
    assert_eq!(ids, vec![11850, 11903]);
}

#[test]
fn failures_are_newest_first_and_normalised() {
    let out = digest(&tests(), &histories(), &config(1));
    let failures = &out.tests[0].failures;
    let at: Vec<&str> = failures.iter().map(|f| f.created_at.as_str()).collect();
    assert_eq!(at, vec!["2026-10-16T21:18:39Z", "2026-10-12T22:54:06Z"]);
    // This test's runner writes no traceback, only a message.
    assert_eq!(failures[0].message, "RPC error (-32603): Eval error: eval timed out after 10s");
    assert_eq!(failures[0].details, "");
    assert_eq!(failures[0].ci_run_id.as_deref(), Some("38965640313"));
    assert_eq!(out.tests[0].last_seen_at, "2026-10-17T01:32:23Z");
}

#[test]
fn a_long_traceback_keeps_both_ends() {
    let mut histories = histories();
    let history = histories.get_mut(&11850).unwrap();
    let failed = history.executions.iter_mut().find(|e| e.status == "failed").unwrap();
    let lines: Vec<String> = (1..=60).map(|i| format!("line {i}")).collect();
    failed.details = Some(lines.join("\n"));
    let out = digest(&tests(), &histories, &config(1));
    let details = &out.tests[0].failures[0].details;
    assert!(details.starts_with("line 1\nline 2\n"), "{details}");
    assert!(details.contains("line 25\n... (10 lines cut) ...\nline 36\n"), "{details}");
    assert!(details.ends_with("line 60"), "{details}");

    let failed = histories.get_mut(&11850).unwrap().executions.iter_mut().find(|e| e.status == "failed").unwrap();
    failed.details = Some("Error: timed out\n  at step one\n".to_string());
    let out = digest(&tests(), &histories, &config(1));
    assert_eq!(out.tests[0].failures[0].details, "Error: timed out\n  at step one");
}

#[test]
fn sgr_remnants_are_removed_from_the_failure_text() {
    let mut histories = histories();
    let history = histories.get_mut(&11850).unwrap();
    let failed = history.executions.iter_mut().find(|e| e.status == "failed").unwrap();
    failed.details = Some("\u{1b}[31mError\u{1b}[39m: [2mexpect([22m".to_string());
    failed.message = Some("[2mexpect([22m[31melement[39m[2m).not.toBeInTheDocument()[22m".to_string());
    let out = digest(&tests(), &histories, &config(1));
    assert_eq!(out.tests[0].failures[0].message, "expect(element).not.toBeInTheDocument()");
    assert_eq!(out.tests[0].failures[0].details, "Error: expect(");
}

#[test]
fn an_occurrence_is_explained_only_when_its_failed_job_is_explained() {
    let out = digest(&tests(), &histories(), &config(1));
    // 38965640313's tauri-pilot job is `explained` there; FlakeRadar has no record of the other.
    assert_eq!(out.explained, vec![117_191_778_924]);
}

#[test]
fn a_bad_timestamp_is_an_error() {
    let mut tests = tests();
    tests[0].last_seen_at = "yesterday".to_string();
    let runs = runs();
    let flaky = flaky();
    let config = config(1);
    let err = flakeradar::digest(&Inputs {
        tests: &tests,
        histories: &histories(),
        runs: &runs,
        flaky: &flaky,
        config: &config,
        since: SINCE,
        base: BASE,
        repo: REPO,
    })
    .unwrap_err();
    assert!(err.contains("yesterday"), "{err}");
}

#[test]
fn normalise_keeps_the_seconds_and_rejects_other_shapes() {
    assert_eq!(flakeradar::normalise_ts("2026-10-16T20:58:11.683359Z").as_deref(), Some("2026-10-16T20:58:11Z"));
    assert_eq!(flakeradar::normalise_ts("2026-10-16T20:58:11Z").as_deref(), Some("2026-10-16T20:58:11Z"));
    assert_eq!(flakeradar::normalise_ts("2026-10-16T20:58:11+00:00").as_deref(), Some("2026-10-16T20:58:11Z"));
    assert_eq!(flakeradar::normalise_ts("2026-10-16T20:58:11.5+02:00"), None);
    assert_eq!(flakeradar::normalise_ts("2026-10-16 20:58:11Z"), None);
}

#[test]
fn the_cli_reads_the_graphs_files() {
    let dir = common::temp_root("scout-flakeradar-cli");
    std::fs::write(dir.join("config.json"), serde_json::to_string(&config(1)).unwrap()).unwrap();
    std::fs::write(dir.join("flaky.json"), serde_json::to_string(&flaky()).unwrap()).unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["scout", "flakeradar", "--tests"])
        .arg(fixtures().join("tests.json"))
        .arg("--history-dir")
        .arg(fixtures().join("history"))
        .arg("--runs-dir")
        .arg(fixtures().join("runs"))
        .arg("--flaky")
        .arg(dir.join("flaky.json"))
        .arg("--config")
        .arg(dir.join("config.json"))
        .args(["--since", SINCE, "--base", BASE, "--repo", REPO])
        .output()
        .unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let json: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(json["tests"].as_array().unwrap().len(), 3);
    assert_eq!(json["explained"], serde_json::json!([117_191_778_924_u64]));
}
