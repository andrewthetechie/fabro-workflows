//! `flakeradar classify`: is every failing check of a PR a known flaky test (ADR 0028 F8,
//! docs/flakeradar C10)? The fixtures are live CI-run lookups from 2026-10-17 with names and
//! messages replaced: lawncare-saas 38881352466 (one flaky frontend test, its job explained),
//! womens-fantasy-sports 38836594409 (pre-commit, no tests) and 38779657986 (Playwright shards
//! red with no test report).

mod common;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

use fabro_io::flakeradar::{self, Check, RunLookup};

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/flakeradar")
}

fn lookups() -> BTreeMap<u64, RunLookup> {
    let mut map = BTreeMap::new();
    for run in [38_881_352_466_u64, 38_836_594_409, 38_779_657_986] {
        let text = std::fs::read_to_string(fixtures().join(format!("run_{run}.json"))).unwrap();
        map.insert(run, serde_json::from_str(&text).unwrap());
    }
    map
}

fn check(name: &str, bucket: &str, run: u64, job: u64) -> Check {
    Check {
        name: name.to_string(),
        bucket: bucket.to_string(),
        link: format!("https://github.com/o/r/actions/runs/{run}/job/{job}"),
    }
}

/// The lawncare-saas frontend `test` job, red on its one flaky test.
fn flaky_check() -> Check {
    check("test", "fail", 38_881_352_466, 116_909_021_192)
}

#[test]
fn a_check_red_only_on_a_flaky_test_is_known_flaky() {
    let out = flakeradar::classify(&[flaky_check()], &lookups(), &[]);
    assert!(out.known_flaky);
    let one = &out.checks[0];
    assert!(one.known_flaky, "{:?}", one.reason);
    assert_eq!((one.run, one.job), (Some(38_881_352_466), Some(116_909_021_192)));
    assert_eq!(one.tests.len(), 1);
    assert_eq!(one.tests[0].name, "test 1869302");
    assert_eq!((one.tests[0].tier.as_str(), one.tests[0].proven), ("flaky", 2));
    assert_eq!(one.tests[0].message, "expect(element).not.toBeInTheDocument()");
}

#[test]
fn passing_and_skipped_checks_are_not_judged() {
    let checks = vec![
        flaky_check(),
        Check { name: "lint".into(), bucket: "pass".into(), link: "https://github.com/o/r/actions/runs/1/job/2".into() },
        Check { name: "deploy".into(), bucket: "skipping".into(), link: String::new() },
    ];
    let out = flakeradar::classify(&checks, &lookups(), &[]);
    assert!(out.known_flaky);
    assert_eq!(out.checks.len(), 1);
}

#[test]
fn a_job_that_failed_outside_its_tests_is_not_known_flaky() {
    let pre_commit = check("pre-commit", "fail", 38_836_594_409, 116_767_008_074);
    let out = flakeradar::classify(&[flaky_check(), pre_commit], &lookups(), &[]);
    assert!(!out.known_flaky);
    assert_eq!(out.checks[1].reason.as_deref(), Some("the job failed outside its tests"));
}

#[test]
fn a_test_that_is_not_known_flaky_names_its_tier() {
    let mut lookups = lookups();
    let run = lookups.get_mut(&38_881_352_466).unwrap();
    run.failures[0].tier = "suspect".to_string();
    run.failures[0].confirmed_flake_count = 0;
    let out = flakeradar::classify(&[flaky_check()], &lookups, &[]);
    assert!(!out.known_flaky);
    assert_eq!(out.checks[0].reason.as_deref(), Some("test 1869302 is suspect with no proven flake"));

    // A suspect with a proven flake is known flaky: the lawncare-saas#2905 migration tests.
    let run = lookups.get_mut(&38_881_352_466).unwrap();
    run.failures[0].confirmed_flake_count = 1;
    assert!(flakeradar::classify(&[flaky_check()], &lookups, &[]).known_flaky);
}

#[test]
fn a_test_that_passed_on_a_runner_retry_does_not_count() {
    let mut lookups = lookups();
    let run = lookups.get_mut(&38_881_352_466).unwrap();
    run.failures[0].status = "passed".to_string();
    let out = flakeradar::classify(&[flaky_check()], &lookups, &[]);
    assert!(!out.known_flaky);
    assert_eq!(out.checks[0].reason.as_deref(), Some("no failing test is reported for the job"));
}

#[test]
fn a_missing_pending_failed_or_truncated_lookup_is_not_known_flaky() {
    let out = flakeradar::classify(&[flaky_check()], &BTreeMap::new(), &[]);
    assert_eq!(out.checks[0].reason.as_deref(), Some("FlakeRadar has no report of the run"));
    for (edit, reason) in [
        ("pending", "FlakeRadar is still processing the run's reports"),
        ("failed", "some of the run's reports failed to process"),
        ("truncated", "the run has too many failures to judge"),
    ] {
        let mut lookups = lookups();
        let run = lookups.get_mut(&38_881_352_466).unwrap();
        match edit {
            "pending" => run.reports.pending = 1,
            "failed" => run.reports.failed = 1,
            _ => run.truncated = true,
        }
        let out = flakeradar::classify(&[flaky_check()], &lookups, &[]);
        assert!(!out.known_flaky, "{edit}");
        assert_eq!(out.checks[0].reason.as_deref(), Some(reason), "{edit}");
    }
}

#[test]
fn a_check_that_is_not_an_actions_job_is_not_known_flaky() {
    let codeql = Check { name: "codeql".into(), bucket: "fail".into(), link: "https://github.com/o/r/runs/5".into() };
    let out = flakeradar::classify(&[codeql], &lookups(), &[]);
    assert!(!out.known_flaky);
    assert_eq!(out.checks[0].reason.as_deref(), Some("not a GitHub Actions job"));
}

#[test]
fn no_failing_check_is_not_known_flaky() {
    assert!(!flakeradar::classify(&[], &lookups(), &[]).known_flaky);
}

#[test]
fn an_aggregator_is_judged_by_the_checks_it_waits_on() {
    let aggregator = check("alls-green-playwright", "fail", 38_779_657_986, 116_582_572_835);
    let ignore = vec!["alls-green-playwright".to_string()];
    let out = flakeradar::classify(&[flaky_check(), aggregator.clone()], &lookups(), &ignore);
    assert!(out.known_flaky, "{:?}", out.checks);
    assert_eq!(out.checks[1].reason.as_deref(), Some("an aggregator, judged by the checks it waits on"));
    // Alone, an aggregator proves nothing.
    assert!(!flakeradar::classify(&[aggregator.clone()], &lookups(), &ignore).known_flaky);
    // Without the declaration it is a job that failed outside its tests.
    assert!(!flakeradar::classify(&[flaky_check(), aggregator], &lookups(), &[]).known_flaky);
}

#[test]
fn the_section_names_each_known_flaky_test() {
    let out = flakeradar::classify(&[flaky_check()], &lookups(), &[]);
    let section = flakeradar::section(&out);
    assert!(section.starts_with("## Known flaky (FlakeRadar)\n"), "{section}");
    assert!(section.contains("- `test 1869302` in `frontend`: flaky, 2 proven flakes"), "{section}");
    assert!(section.contains("These failures are not caused by this PR: do not change, skip or retry these tests."), "{section}");
    let none = flakeradar::classify(&[check("pre-commit", "fail", 38_836_594_409, 116_767_008_074)], &lookups(), &[]);
    assert_eq!(flakeradar::section(&none), "");
}

#[test]
fn the_cli_reads_the_graphs_files() {
    let dir = common::temp_root("flakeradar-classify-cli");
    let checks = serde_json::json!([
        {"name": "test", "state": "FAILURE", "bucket": "fail", "link": "https://github.com/o/r/actions/runs/38881352466/job/116909021192"},
        {"name": "alls-green", "state": "FAILURE", "bucket": "fail", "link": "https://github.com/o/r/actions/runs/38779657986/job/116582572835"}
    ]);
    std::fs::write(dir.join("checks.json"), checks.to_string()).unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["flakeradar", "classify", "--checks"])
        .arg(dir.join("checks.json"))
        .arg("--failures-dir")
        .arg(fixtures())
        .args(["--ignore-jobs", r#"["alls-green"]"#, "--section"])
        .arg(dir.join("section.md"))
        .output()
        .unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let json: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(json["known_flaky"], true);
    let section = std::fs::read_to_string(dir.join("section.md")).unwrap();
    assert!(section.contains("`test 1869302`"), "{section}");

    let bad = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["flakeradar", "classify", "--checks"])
        .arg(dir.join("missing.json"))
        .arg("--failures-dir")
        .arg(fixtures())
        .output()
        .unwrap();
    assert_eq!(bad.status.code(), Some(1));
}

#[test]
fn unknown_means_no_failing_check_is_shown_to_be_a_real_failure() {
    // No lookup at all (FlakeRadar down, or the rerun attempt not reported yet): unknown.
    let out = flakeradar::classify(&[flaky_check()], &BTreeMap::new(), &[]);
    assert!(!out.known_flaky);
    assert!(out.unknown);
    // Reports still pending: unknown.
    let mut pending = lookups();
    pending.get_mut(&38_881_352_466).unwrap().reports.pending = 1;
    assert!(flakeradar::classify(&[flaky_check()], &pending, &[]).unknown);
    // A job that failed outside its tests is evidence of a real failure: not unknown.
    let pre_commit = check("pre-commit", "fail", 38_836_594_409, 116_767_008_074);
    let out = flakeradar::classify(&[flaky_check(), pre_commit], &lookups(), &[]);
    assert!(!out.unknown);
    // So is a stable test, and a check that is not an Actions job.
    let mut stable = lookups();
    stable.get_mut(&38_881_352_466).unwrap().failures[0].tier = "stable".to_string();
    assert!(!flakeradar::classify(&[flaky_check()], &stable, &[]).unknown);
    let codeql = Check { name: "codeql".into(), bucket: "fail".into(), link: "https://github.com/o/r/runs/5".into() };
    assert!(!flakeradar::classify(&[codeql], &lookups(), &[]).unknown);
    // Known flaky is not unknown.
    assert!(!flakeradar::classify(&[flaky_check()], &lookups(), &[]).unknown);
}
