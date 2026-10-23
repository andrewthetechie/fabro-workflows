//! `scout plan` and `scout summary`: the C5 to C8 rules over hand-built inputs.

mod common;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::de::DeserializeOwned;

use fabro_io::scout::config::ConfigOut;
use fabro_io::scout::flaky::{FlakyJob, FlakyOut, Occurrence};
use fabro_io::scout::flakeradar::{Failure, FlakeradarOut, FlakyTest, JobLink};
use fabro_io::scout::plan::{self, Action, Issue, PlanInput, PlanOut, Radar, RadarLink, Sources};
use fabro_io::scout::ratchet::{EntryOut, Finding, RatchetOut};
use fabro_io::scout::text::{self, Filed};
use fabro_io::scout::{flaky_fp, ratchet_fp};

/// The base fixture directory (C2 to C5 output shapes).
fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/scout/plan")
}

/// One base fixture, which each test edits.
fn load<T: DeserializeOwned>(name: &str) -> T {
    serde_json::from_str(&std::fs::read_to_string(fixtures().join(name)).unwrap()).unwrap()
}

/// A config with one Ratchet rule declared, so the source is neither absent nor invalid.
fn config(filing: bool) -> ConfigOut {
    let mut config: ConfigOut = load("config.json");
    config.filing = filing;
    config
}

/// A Ratchet source entry with `count` findings over `files` distinct paths.
fn entry_with(tool: &str, rule: &str, count: usize, files: usize) -> EntryOut {
    let findings: Vec<Finding> = (0..count)
        .map(|i| Finding {
            path: format!("src/f{}.py", if files == 0 { 0 } else { i % files }),
            line: (i as u64) + 1,
            column: 1,
            message: format!("finding {i}"),
        })
        .collect();
    EntryOut {
        index: 0,
        tool: tool.to_string(),
        rule: rule.to_string(),
        fp: ratchet_fp(tool, rule),
        status: "ok".to_string(),
        error: None,
        count,
        files,
        findings,
        command: "cmd".to_string(),
        enforce_in: "pyproject.toml".to_string(),
        enforce_how: "how".to_string(),
    }
}

/// A Ratchet source output with these entries.
fn ratchet(entries: Vec<EntryOut>) -> RatchetOut {
    let mut ratchet: RatchetOut = load("ratchet.json");
    ratchet.entries = entries;
    ratchet
}

/// An occurrence created at `created_at`, newest-first order is the caller's.
fn occ(run_id: u64, created_at: &str) -> Occurrence {
    Occurrence {
        run_id,
        run_url: format!("https://example.test/{run_id}"),
        attempt: 1,
        failed_job_id: run_id,
        failed_job_url: format!("https://example.test/{run_id}/job"),
        failed_steps: vec!["Run pytest".to_string()],
        created_at: created_at.to_string(),
        head_branch: "main".to_string(),
        head_sha: "abc".to_string(),
    }
}

/// A Flaky job with the given occurrences.
fn flaky_job(path: &str, job: &str, occurrences: Vec<Occurrence>) -> FlakyJob {
    let count = occurrences.len();
    FlakyJob { fp: flaky_fp(path, job), workflow: path.to_string(), job: job.to_string(), count, occurrences }
}

/// A Flaky source output with these jobs.
fn flaky(jobs: Vec<FlakyJob>) -> FlakyOut {
    let mut flaky: FlakyOut = load("flaky.json");
    flaky.jobs = jobs;
    flaky
}

/// A scout issue carrying one marker.
fn issue(number: u64, state: &str, reason: Option<&str>, closed_at: Option<&str>, fp: &str) -> Issue {
    Issue {
        number,
        state: state.to_string(),
        state_reason: reason.map(ToString::to_string),
        closed_at: closed_at.map(ToString::to_string),
        body: format!("<!-- fabro:scout fp={fp} -->\n"),
    }
}

/// Runs the plan with the fixture repo and commit.
fn run(config: &ConfigOut, ratchet: &RatchetOut, flaky: &FlakyOut, issues: &[Issue]) -> PlanOut {
    plan::plan(&PlanInput {
        config,
        ratchet,
        flaky,
        issues,
        logs_dir: Path::new("/nonexistent"),
        repo: "andrewthetechie/jelly-swipe",
        sha: "abcdef1234567890",
        flakeradar: Radar::Off,
    })
}

/// The kinds of `out`'s actions, in order.
fn kinds(out: &PlanOut) -> Vec<&str> {
    out.actions.iter().map(|action| action.kind.as_str()).collect()
}

/// A plan with the fixture repo and no actions by default.
fn plan_out(filing: bool, actions: Vec<Action>) -> PlanOut {
    PlanOut {
        filing,
        repo: "andrewthetechie/jelly-swipe".to_string(),
        sources: Sources { ratchet: "ok".to_string(), flaky: "ok".to_string() },
        actions,
    }
}

/// One action, for the summary tests.
fn act(kind: &str, source: &str, fp: &str, issue: Option<u64>, subject: &str, detail: &str) -> Action {
    Action {
        kind: kind.to_string(),
        source: source.to_string(),
        fp: fp.to_string(),
        issue,
        title: None,
        body: None,
        labels: None,
        detail: detail.to_string(),
        subject: Some(subject.to_string()),
        flakeradar: None,
    }
}

#[test]
fn the_base_fixtures_plan_two_files() {
    let config: ConfigOut = load("config.json");
    let ratchet: RatchetOut = load("ratchet.json");
    let flaky: FlakyOut = load("flaky.json");
    let issues: Vec<Issue> = load("issues.json");
    let out = run(&config, &ratchet, &flaky, &issues);
    assert_eq!(kinds(&out), ["file", "file"]);
    assert_eq!(out.actions[0].source, "ratchet");
    assert_eq!(out.actions[0].detail, "2 findings in 1 file");
    assert_eq!(out.actions[1].source, "flaky");
    assert_eq!(out.actions[1].detail, "3 flakes in 14 days");
}

#[test]
fn moved_finding_stays_known() {
    let config = config(false);
    let ratchet = ratchet(vec![entry_with("ruff", "B006", 2, 1)]);
    let flaky = flaky(vec![]);
    let issues = vec![issue(7, "open", None, None, "ratchet:ruff:b006")];
    let out = run(&config, &ratchet, &flaky, &issues);
    assert_eq!(kinds(&out), ["known"]);
    assert_eq!(out.actions[0].issue, Some(7));
}

#[test]
fn a_closed_not_planned_or_duplicate_is_suppressed() {
    for reason in ["not_planned", "duplicate"] {
        let config = config(false);
        let ratchet = ratchet(vec![entry_with("ruff", "B006", 2, 1)]);
        let flaky = flaky(vec![]);
        let issues =
            vec![issue(9, "closed", Some(reason), Some("2026-10-01T00:00:00Z"), "ratchet:ruff:b006")];
        let out = run(&config, &ratchet, &flaky, &issues);
        assert_eq!(kinds(&out), ["suppressed"], "{reason}");
        assert_eq!(out.actions[0].issue, Some(9));
    }
}

#[test]
fn a_closed_completed_is_a_regression() {
    let config = config(false);
    let ratchet = ratchet(vec![entry_with("ruff", "B006", 2, 1)]);
    let flaky = flaky(vec![]);
    let issues =
        vec![issue(9, "closed", Some("completed"), Some("2026-10-01T00:00:00Z"), "ratchet:ruff:b006")];
    let out = run(&config, &ratchet, &flaky, &issues);
    assert_eq!(kinds(&out), ["regression"]);
    assert_eq!(out.actions[0].issue, Some(9));
    assert!(out.actions[0].title.as_deref().unwrap().starts_with("scout (regression of #"));
}

#[test]
fn a_flaky_regression_counts_only_occurrences_after_the_close() {
    let config = config(false);
    let ratchet = ratchet(vec![]);
    let issues = vec![issue(
        20,
        "closed",
        Some("completed"),
        Some("2026-10-10T00:00:00Z"),
        "flaky:.github/workflows/ci.yml:test",
    )];
    let two_after = flaky(vec![flaky_job(
        ".github/workflows/ci.yml",
        "test",
        vec![
            occ(1, "2026-10-12T00:00:00Z"),
            occ(2, "2026-10-11T00:00:00Z"),
            occ(3, "2026-10-08T00:00:00Z"),
            occ(4, "2026-10-05T00:00:00Z"),
        ],
    )]);
    let out = run(&config, &ratchet, &two_after, &issues);
    assert!(out.actions.is_empty(), "two after the close is below min_flakes");

    let three_after = flaky(vec![flaky_job(
        ".github/workflows/ci.yml",
        "test",
        vec![
            occ(1, "2026-10-13T00:00:00Z"),
            occ(2, "2026-10-12T00:00:00Z"),
            occ(3, "2026-10-11T00:00:00Z"),
            occ(4, "2026-10-08T00:00:00Z"),
            occ(5, "2026-10-05T00:00:00Z"),
        ],
    )]);
    let out = run(&config, &ratchet, &three_after, &issues);
    assert_eq!(kinds(&out), ["regression"]);
    assert_eq!(out.actions[0].issue, Some(20));
    let body = out.actions[0].body.as_deref().unwrap();
    assert_eq!(body.matches("failed at step(s)").count(), 3);
    assert!(!body.contains("[run 4, attempt"), "an older occurrence is listed");
    assert!(!body.contains("[run 5, attempt"), "an older occurrence is listed");
}

#[test]
fn too_big_over_fifteen_findings_or_eight_files() {
    let config = config(false);
    let flaky = flaky(vec![]);
    let issues = Vec::new();
    let over_findings = run(&config, &ratchet(vec![entry_with("ruff", "B006", 16, 8)]), &flaky, &issues);
    assert_eq!(kinds(&over_findings), ["too_big"]);
    let over_files = run(&config, &ratchet(vec![entry_with("ruff", "B006", 9, 9)]), &flaky, &issues);
    assert_eq!(kinds(&over_files), ["too_big"]);
    let at_limit = run(&config, &ratchet(vec![entry_with("ruff", "B006", 15, 8)]), &flaky, &issues);
    assert_eq!(kinds(&at_limit), ["file"]);
}

#[test]
fn the_per_run_cap_files_three_and_caps_the_rest() {
    let config = config(false);
    let flaky = flaky(vec![]);
    let issues = Vec::new();
    let entries = (1..=5).map(|i| entry_with("ruff", &format!("B00{i}"), 1, 1)).collect();
    let out = run(&config, &ratchet(entries), &flaky, &issues);
    assert_eq!(kinds(&out), ["file", "file", "file", "capped", "capped"]);
}

#[test]
fn two_open_issues_give_one_file_and_one_backpressure() {
    let config = config(false);
    let flaky = flaky(vec![]);
    let issues = vec![
        issue(1, "open", None, None, "ratchet:ruff:b001"),
        issue(2, "open", None, None, "ratchet:ruff:b002"),
        issue(3, "open", None, None, "flaky:.github/workflows/ci.yml:test"),
    ];
    let entries = vec![entry_with("ruff", "B003", 1, 1), entry_with("ruff", "B004", 1, 1)];
    let out = run(&config, &ratchet(entries), &flaky, &issues);
    assert_eq!(kinds(&out), ["file", "backpressure"], "the open flaky issue is not a ratchet issue");
}

#[test]
fn a_known_fingerprint_after_the_cap_is_still_known() {
    let config = config(false);
    let flaky = flaky(vec![]);
    let issues = vec![
        issue(1, "open", None, None, "ratchet:ruff:b001"),
        issue(2, "open", None, None, "ratchet:ruff:b002"),
        issue(3, "open", None, None, "ratchet:ruff:b004"),
    ];
    let entries = vec![entry_with("ruff", "B003", 1, 1), entry_with("ruff", "B004", 1, 1)];
    let out = run(&config, &ratchet(entries), &flaky, &issues);
    assert_eq!(kinds(&out), ["backpressure", "known"]);
    assert_eq!(out.actions[1].issue, Some(3));
}

#[test]
fn a_skipped_or_invalid_ratchet_source_is_named() {
    let base = config(false);
    let flaky = flaky(vec![]);
    let issues = Vec::new();
    let skipped = RatchetOut {
        status: "skipped".to_string(),
        reason: Some("setup.sh failed (exit 1)".to_string()),
        entries: Vec::new(),
    };
    let out = run(&base, &skipped, &flaky, &issues);
    assert_eq!(out.sources.ratchet, "skipped: setup.sh failed (exit 1)");
    assert!(out.actions.iter().all(|action| action.source != "ratchet"));

    let mut invalid = config(false);
    invalid.valid = false;
    invalid.error = Some("version must be 1, found 2".to_string());
    invalid.ratchet = Vec::new();
    let out = run(&invalid, &skipped, &flaky, &issues);
    assert_eq!(
        out.sources.ratchet,
        "skipped: scout.toml is invalid: version must be 1, found 2"
    );

    let mut absent = config(false);
    absent.ratchet = Vec::new();
    let out = run(&absent, &ratchet(vec![]), &flaky, &issues);
    assert_eq!(out.sources.ratchet, "absent");
}

#[test]
fn a_ratchet_error_entry_becomes_an_error_action() {
    let config = config(false);
    let flaky = flaky(vec![]);
    let issues = Vec::new();
    let mut bad = entry_with("ruff", "B006", 0, 0);
    bad.status = "error".to_string();
    bad.error = Some("timed out after 1 s".to_string());
    let out = run(&config, &ratchet(vec![bad]), &flaky, &issues);
    assert_eq!(kinds(&out), ["error"]);
    assert_eq!(out.actions[0].detail, "timed out after 1 s");
    assert_eq!(out.actions[0].subject.as_deref(), Some("ruff B006"));
}

#[test]
fn ratchet_comes_before_flaky_in_each_one_s_own_order() {
    let config = config(false);
    let issues = Vec::new();
    let entries = vec![entry_with("ruff", "B002", 1, 1), entry_with("ruff", "B001", 1, 1)];
    let jobs = vec![
        flaky_job(".github/workflows/ci.yml", "lint", vec![
            occ(4, "2026-10-12T00:00:00Z"),
            occ(5, "2026-10-11T00:00:00Z"),
            occ(6, "2026-10-10T00:00:00Z"),
        ]),
        flaky_job(
            ".github/workflows/ci.yml",
            "test",
            vec![
                occ(1, "2026-10-13T00:00:00Z"),
                occ(2, "2026-10-12T00:00:00Z"),
                occ(3, "2026-10-11T00:00:00Z"),
                occ(7, "2026-10-10T00:00:00Z"),
            ],
        ),
    ];
    let out = run(&config, &ratchet(entries), &flaky(jobs), &issues);
    let subjects: Vec<&str> =
        out.actions.iter().map(|action| action.subject.as_deref().unwrap()).collect();
    assert_eq!(subjects, ["ruff B002", "ruff B001", "test", "lint"]);
}

#[test]
fn labels_follow_d10() {
    let config = config(false);
    let issues = Vec::new();
    let ratchet = ratchet(vec![entry_with("ruff", "B006", 1, 1)]);
    let jobs = vec![flaky_job(
        ".github/workflows/ci.yml",
        "test",
        vec![
            occ(1, "2026-10-12T00:00:00Z"),
            occ(2, "2026-10-11T00:00:00Z"),
            occ(3, "2026-10-10T00:00:00Z"),
        ],
    )];
    let out = run(&config, &ratchet, &flaky(jobs), &issues);
    let ratchet_labels =
        out.actions.iter().find(|a| a.source == "ratchet").unwrap().labels.clone().unwrap();
    assert_eq!(ratchet_labels, ["agent", "scout", "ai-generated", "bug", "fast-path"]);
    let flaky_labels =
        out.actions.iter().find(|a| a.source == "flaky").unwrap().labels.clone().unwrap();
    assert_eq!(flaky_labels, ["agent", "scout", "ai-generated", "flaky-test"]);
}

#[test]
fn bodies_start_with_the_marker_and_hold_the_details() {
    let config = config(false);
    let issues = Vec::new();
    let ratchet = ratchet(vec![entry_with("ruff", "B006", 2, 1)]);
    let jobs = vec![flaky_job(
        ".github/workflows/ci.yml",
        "test",
        vec![
            occ(1, "2026-10-12T00:00:00Z"),
            occ(2, "2026-10-11T00:00:00Z"),
            occ(3, "2026-10-10T00:00:00Z"),
        ],
    )];
    let out = run(&config, &ratchet, &flaky(jobs), &issues);
    let ratchet_body =
        out.actions.iter().find(|a| a.source == "ratchet").unwrap().body.clone().unwrap();
    assert!(ratchet_body.starts_with("<!-- fabro:scout fp=ratchet:ruff:b006 -->\n"));
    assert!(ratchet_body.contains("`src/f0.py:1:1` finding 0"), "{ratchet_body}");
    assert!(ratchet_body.contains("`src/f0.py:2:1` finding 1"), "{ratchet_body}");
    assert!(ratchet_body.contains("pyproject.toml"));
    assert!(ratchet_body.contains("how"));
    let flaky_body = out.actions.iter().find(|a| a.source == "flaky").unwrap().body.clone().unwrap();
    assert!(flaky_body.starts_with("<!-- fabro:scout fp=flaky:.github/workflows/ci.yml:test -->\n"));
    assert!(flaky_body.contains("No log was available."));
    // Every criterion is checkable in the diff: no run writes a PR description (lawncare-saas#2970).
    assert!(!flaky_body.contains("PR description"), "{flaky_body}");
    assert!(flaky_body.contains("- [ ] A comment in the changed test, code or CI step names the cause"), "{flaky_body}");
    assert!(flaky_body.contains("- Do not add a skip marker or skip condition to any test"), "{flaky_body}");
}

#[test]
fn clean_log_strips_timestamps_ansi_and_mentions() {
    let raw = "2026-10-14T12:00:00.1234567Z hello \u{1b}[31m@user\n````\n2026-10-14T12:00:01.0000000Z second\n";
    let cleaned = text::clean_log(raw);
    assert!(!cleaned.contains("2026-10-14T12:00:00"), "{cleaned}");
    assert!(!cleaned.contains('\u{1b}'), "{cleaned}");
    assert!(cleaned.contains("'''"), "{cleaned}");
    assert!(cleaned.contains("@\u{200b}user"), "{cleaned}");
    assert!(!cleaned.contains("````"), "{cleaned}");
}

#[test]
fn clean_log_keeps_sixty_lines_and_three_thousand_characters() {
    let raw: Vec<String> =
        (0..500).map(|i| format!("line {i:04} {}", "x".repeat(97))).collect();
    let cleaned = text::clean_log(&raw.join("\n"));
    assert!(cleaned.lines().count() <= 60, "{} lines", cleaned.lines().count());
    assert!(cleaned.chars().count() <= 3000, "{} chars", cleaned.chars().count());
}

#[test]
fn a_body_over_sixty_thousand_characters_drops_log_tails() {
    let config = config(false);
    let issues = Vec::new();
    let ratchet = ratchet(vec![]);
    let many: Vec<Occurrence> = (1..=400).map(|i| occ(i, "2026-10-12T00:00:00Z")).collect();
    let without = run(&config, &ratchet, &flaky(vec![flaky_job(".github/workflows/ci.yml", "test", many.clone())]), &issues);
    let base = without.actions[0].body.clone().unwrap();
    assert!(base.chars().count() < 60_000, "the base body is {} chars", base.chars().count());

    let logs = common::temp_root("scout-plan-logs");
    for id in 1..=3 {
        std::fs::write(logs.join(format!("{id}.txt")), "y".repeat(5000)).unwrap();
    }
    let out = plan::plan(&PlanInput {
        config: &config,
        ratchet: &ratchet,
        flaky: &flaky(vec![flaky_job(".github/workflows/ci.yml", "test", many)]),
        issues: &issues,
        logs_dir: &logs,
        repo: "andrewthetechie/jelly-swipe",
        sha: "abcdef1234567890",
        flakeradar: Radar::Off,
    });
    let body = out.actions[0].body.clone().unwrap();
    assert!(body.chars().count() <= 60_000, "the body is {} chars", body.chars().count());
    assert!(body.matches("### Run").count() < 3, "at least one log tail was dropped");
    std::fs::remove_dir_all(&logs).ok();
}

#[test]
fn a_marker_with_an_invalid_fingerprint_is_ignored() {
    let config = config(false);
    let flaky = flaky(vec![]);
    let ratchet = ratchet(vec![entry_with("ruff", "B006", 1, 1)]);
    let issues = vec![Issue {
        number: 1,
        state: "open".to_string(),
        state_reason: None,
        closed_at: None,
        body: "<!-- fabro:scout fp=Bad Key -->\n".to_string(),
    }];
    assert!(plan::markers("<!-- fabro:scout fp=Bad Key -->").is_empty());
    let out = run(&config, &ratchet, &flaky, &issues);
    assert_eq!(kinds(&out), ["file"]);
}

#[test]
fn summary_dry_with_one_file() {
    let plan = plan_out(
        false,
        vec![act("file", "ratchet", "ratchet:ruff:b006", None, "ruff B006", "2 findings in 1 file")],
    );
    assert_eq!(
        text::summary(&plan, None),
        "scout jelly-swipe DRY RUN: would file 1 | would file ratchet ruff B006 (2 findings in 1 file)"
    );
}

#[test]
fn summary_live_with_filed() {
    let plan = plan_out(
        true,
        vec![act("file", "ratchet", "ratchet:ruff:b006", None, "ruff B006", "2 findings in 1 file")],
    );
    let filed = vec![Filed {
        fp: "ratchet:ruff:b006".to_string(),
        outcome: "filed".to_string(),
        number: Some(12),
        error: None,
        link: None,
        link_detail: None,
    }];
    let line = text::summary(&plan, Some(&filed));
    assert!(line.contains("filed #12 ratchet ruff B006 (2 findings in 1 file)"), "{line}");
}

#[test]
fn summary_only_known_and_suppressed_is_empty() {
    let plan = plan_out(
        true,
        vec![
            act("known", "ratchet", "ratchet:ruff:b001", Some(1), "ruff B001", "1 finding in 1 file"),
            act("suppressed", "ratchet", "ratchet:ruff:b002", Some(2), "ruff B002", "1 finding in 1 file"),
        ],
    );
    assert_eq!(text::summary(&plan, None), "");
}

#[test]
fn summary_dry_too_big_posts_and_live_does_not() {
    let dry = plan_out(
        false,
        vec![act("too_big", "ratchet", "ratchet:ruff:b006", None, "ruff B006", "16 findings in 1 file")],
    );
    let line = text::summary(&dry, None);
    assert!(!line.is_empty());
    assert!(line.contains("too big ruff B006 (16 findings in 1 file)"), "{line}");
    let live = plan_out(
        true,
        vec![act("too_big", "ratchet", "ratchet:ruff:b006", None, "ruff B006", "16 findings in 1 file")],
    );
    assert_eq!(text::summary(&live, None), "");
}

#[test]
fn summary_shows_raced_and_write_failed() {
    let plan = plan_out(
        true,
        vec![
            act("file", "ratchet", "ratchet:ruff:b001", None, "ruff B001", "1 finding in 1 file"),
            act("file", "ratchet", "ratchet:ruff:b002", None, "ruff B002", "1 finding in 1 file"),
        ],
    );
    let filed = vec![
        Filed { fp: "ratchet:ruff:b001".to_string(), outcome: "raced".to_string(), number: None, error: None, link: None, link_detail: None },
        Filed {
            fp: "ratchet:ruff:b002".to_string(),
            outcome: "failed".to_string(),
            number: None,
            error: Some("label create failed".to_string()),
            link: None,
            link_detail: None,
        },
    ];
    let line = text::summary(&plan, Some(&filed));
    assert!(line.contains("raced ruff B001 (1 finding in 1 file)"), "{line}");
    assert!(line.contains("write failed ruff B002 (1 finding in 1 file): label create failed"), "{line}");
}

#[test]
fn summary_names_a_skipped_source() {
    let mut plan = plan_out(false, Vec::new());
    plan.sources.ratchet = "skipped: setup.sh failed (exit 1)".to_string();
    let line = text::summary(&plan, None);
    assert!(line.contains("ratchet skipped: setup.sh failed (exit 1)"), "{line}");
}

#[test]
fn summary_truncates_to_fifteen_hundred_characters() {
    let actions: Vec<Action> = (0..40)
        .map(|i| {
            act(
                "file",
                "flaky",
                &format!("flaky:.github/workflows/ci.yml:job-{i}"),
                None,
                &format!("job {i}"),
                "3 flakes in 14 days",
            )
        })
        .collect();
    let plan = plan_out(false, actions);
    let line = text::summary(&plan, None);
    assert!(line.chars().count() <= 1500, "{} chars", line.chars().count());
    assert!(line.contains("...and "), "{line}");
    assert!(line.ends_with(" more"), "{line}");
}

#[test]
fn summary_strips_quotes_and_backslashes() {
    let plan = plan_out(
        false,
        vec![act("file", "flaky", "flaky:.github/workflows/ci.yml:test", None, "job \"a\\b\"", "3 flakes in 14 days")],
    );
    let line = text::summary(&plan, None);
    assert!(!line.contains('"'), "{line}");
    assert!(!line.contains('\\'), "{line}");
}

#[test]
fn plan_cli_rejects_a_bad_repo_and_sha() {
    let args = |repo: &str, sha: &str| {
        let mut args: Vec<String> = [
            "--config",
            "--ratchet",
            "--flaky",
            "--issues",
        ]
        .iter()
        .flat_map(|flag| [(*flag).to_string(), "/nonexistent".to_string()])
        .collect();
        args.extend([
            "--logs-dir".to_string(),
            "/tmp".to_string(),
            "--repo".to_string(),
            repo.to_string(),
            "--sha".to_string(),
            sha.to_string(),
        ]);
        args
    };
    assert_eq!(plan::run_cli(&args("jelly-swipe", "abcdef12")), ExitCode::from(2));
    assert_eq!(plan::run_cli(&args("owner/name/extra", "abcdef12")), ExitCode::from(2));
    assert_eq!(plan::run_cli(&args("owner/name", "abc")), ExitCode::from(2));
    assert_eq!(plan::run_cli(&args("owner/name", "zzzzzzzz")), ExitCode::from(2));
}

#[test]
fn plan_cli_reports_a_missing_input_file() {
    let args: Vec<String> = [
        "--config",
        "--ratchet",
        "--flaky",
        "--issues",
    ]
    .iter()
    .flat_map(|flag| [(*flag).to_string(), "/nonexistent".to_string()])
    .chain([
        "--logs-dir".to_string(),
        "/tmp".to_string(),
        "--repo".to_string(),
        "owner/name".to_string(),
        "--sha".to_string(),
        "abcdef12".to_string(),
    ])
    .collect();
    assert_eq!(plan::run_cli(&args), ExitCode::FAILURE);
}

#[test]
fn a_null_issue_body_reads_as_empty() {
    // GitHub sends `body: null` for an issue with no text, and a human may label any issue `scout`.
    let issues: Vec<Issue> = serde_json::from_str(
        r#"[{"number":4,"state":"open","state_reason":null,"closed_at":null,"body":null}]"#,
    )
    .unwrap();
    assert_eq!(issues[0].body, "");
    assert!(plan::markers(&issues[0].body).is_empty());
}

#[test]
fn summary_live_header_counts_only_filed_outcomes() {
    let plan = plan_out(
        true,
        vec![
            act("file", "ratchet", "ratchet:ruff:b001", None, "ruff B001", "1 finding in 1 file"),
            act("file", "ratchet", "ratchet:ruff:b002", None, "ruff B002", "1 finding in 1 file"),
        ],
    );
    let filed = vec![
        Filed { fp: "ratchet:ruff:b001".to_string(), outcome: "filed".to_string(), number: Some(7), error: None, link: None, link_detail: None },
        Filed {
            fp: "ratchet:ruff:b002".to_string(),
            outcome: "failed".to_string(),
            number: None,
            error: Some("boom".to_string()),
            link: None,
            link_detail: None,
        },
    ];
    let line = text::summary(&plan, Some(&filed));
    assert!(line.starts_with("scout jelly-swipe: filed 1 | "), "{line}");
}

#[test]
fn clean_log_survives_a_multibyte_character_before_the_first_space() {
    // A line that starts with a digit and holds a multibyte char before its first space must
    // not panic on a byte slice, and a real timestamp is still stripped.
    let raw = "1é ok\n2026-10-14T12:00:00.1234567Z — probe";
    assert_eq!(text::clean_log(raw), "1é ok\n— probe");
}

#[test]
fn clean_log_drops_stray_control_bytes() {
    let raw = "2026-10-14T12:00:00.1234567Z \u{1b}]0;title\u{7}ok\r\n2026-10-14T12:00:01.0000000Z \u{1b}[31mred\u{1b}[0m\tend";
    assert_eq!(text::clean_log(raw), "]0;titleok\nred\tend");
}

#[test]
fn clean_log_ends_at_the_first_error_line() {
    let raw = "2026-10-14T12:00:00.0000000Z test failed: x\n2026-10-14T12:00:00.0000000Z ##[error]Process completed with exit code 1.\n2026-10-14T12:00:01.0000000Z Post job cleanup.\n2026-10-14T12:00:01.0000000Z ##[error]second";
    assert_eq!(text::clean_log(raw), "test failed: x\n##[error]Process completed with exit code 1.");
}

#[test]
fn an_ignored_job_is_never_planned() {
    let mut config = config(false);
    config.flaky.ignore_jobs = vec!["alls-green".to_string()];
    let three = || vec![occ(1, "2026-10-10T00:00:00Z"), occ(2, "2026-10-11T00:00:00Z"), occ(3, "2026-10-12T00:00:00Z")];
    let flaky = flaky(vec![
        flaky_job(".github/workflows/ci.yml", "alls-green", three()),
        flaky_job(".github/workflows/ci.yml", "test", three()),
    ]);
    let out = run(&config, &ratchet(vec![]), &flaky, &[]);
    assert_eq!(kinds(&out), vec!["file"]);
    assert_eq!(out.actions[0].fp, flaky_fp(".github/workflows/ci.yml", "test"));
}

// ---- ADR 0028: FlakeRadar in the flaky source (docs/flakeradar C4b, C5 amendment, C7b) ----

/// A config whose repository FlakeRadar covers.
fn covered(filing: bool) -> ConfigOut {
    let mut config = config(filing);
    config.flakeradar.covered = true;
    config
}

/// One FlakeRadar candidate with one failure at `failed_at`.
fn radar_test(fingerprint: &str, name: &str, score: f64, proven: u32, failed_at: &str) -> FlakyTest {
    FlakyTest {
        fp: format!("flaky:test-pilot:{fingerprint}"),
        id: 11850,
        url: "https://flakes.example/?repo=o/n&project=pilot&test=11850".to_string(),
        project: "pilot".to_string(),
        suite: Some("ui".to_string()),
        classname: Some("welcome".to_string()),
        name: name.to_string(),
        file: Some("tests/ui/welcome.spec.ts".to_string()),
        line: Some(12),
        score,
        proven,
        category: Some("timing".to_string()),
        last_seen_at: "2026-10-17T01:32:23Z".to_string(),
        last_failing_sha: Some("2621219947475f39b1c30e3d08d4f353fbf37172".to_string()),
        last_failing_branch: Some("chore/scout-toml".to_string()),
        failures: vec![Failure {
            created_at: failed_at.to_string(),
            message: "RPC error: eval timed out after 10s".to_string(),
            details: "Error: timed out\n```\n@someone".to_string(),
            branch: Some("chore/scout-toml".to_string()),
            ci_run_id: Some("38965640313".to_string()),
        }],
    }
}

/// Runs the plan with FlakeRadar's state.
fn run_radar(config: &ConfigOut, flaky: &FlakyOut, issues: &[Issue], radar: Radar<'_>) -> PlanOut {
    let ratchet = ratchet(vec![]);
    plan::plan(&PlanInput {
        config,
        ratchet: &ratchet,
        flaky,
        issues,
        logs_dir: Path::new("/nonexistent"),
        repo: "andrewthetechie/writers-app",
        sha: "abcdef1234567890",
        flakeradar: radar,
    })
}

/// Three occurrences of one job, with failed job ids 1 to 3.
fn three_flakes() -> FlakyOut {
    let occurrences = (1..=3).map(|i| occ(i, "2026-10-12T00:00:00Z")).collect();
    flaky(vec![flaky_job(".github/workflows/ci.yml", "UI end-to-end (tauri-pilot)", occurrences)])
}

#[test]
fn flaky_tests_are_filed_before_jobs_and_share_their_cap() {
    let radar = FlakeradarOut {
        tests: vec![
            radar_test("aaaa", "welcome mat shows", 0.7, 2, "2026-10-16T21:18:39Z"),
            radar_test("bbbb", "drawer shows step 1", 0.6, 1, "2026-10-16T21:18:39Z"),
            radar_test("cccc", "folder picker", 0.6, 1, "2026-10-16T21:18:39Z"),
        ],
        explained: vec![],
        jobs: vec![],
    };
    let out = run_radar(&covered(false), &three_flakes(), &[], Radar::Read(&radar));
    assert_eq!(kinds(&out), ["file", "file", "file", "capped"]);
    assert_eq!(out.actions[0].fp, "flaky:test-pilot:aaaa");
    assert_eq!(out.actions[0].source, "flaky");
    assert_eq!(out.actions[0].detail, "score 0.70, 2 proven flakes");
    assert_eq!(
        out.actions[0].title.as_deref(),
        Some("scout: flaky test \"welcome mat shows\" in pilot (score 0.70, 2 proven flakes)")
    );
    assert_eq!(out.actions[3].fp, "flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot");
    assert_eq!(out.sources.flaky, "ok");
}

#[test]
fn a_flaky_test_issue_names_the_test_and_its_failures() {
    let radar = FlakeradarOut {
        tests: vec![radar_test("aaaa", "welcome mat shows", 0.7, 2, "2026-10-16T21:18:39Z")],
        explained: vec![],
        jobs: vec![],
    };
    let out = run_radar(&covered(false), &flaky(vec![]), &[], Radar::Read(&radar));
    let body = out.actions[0].body.clone().unwrap();
    assert!(body.starts_with("<!-- fabro:scout fp=flaky:test-pilot:aaaa -->\n"), "{body}");
    assert!(body.contains("`tests/ui/welcome.spec.ts:12`"), "{body}");
    assert!(body.contains("https://flakes.example/?repo=o/n&project=pilot&test=11850"), "{body}");
    assert!(body.contains("## Recent failures"), "{body}");
    assert!(body.contains("2026-10-16 on `chore/scout-toml`"), "{body}");
    assert!(body.contains("RPC error: eval timed out after 10s"), "{body}");
    // The failure text is untrusted: no fence can be closed from inside it, and no one is pinged.
    assert!(!body.contains("\n```\n@someone"), "{body}");
    assert!(body.contains("@\u{200b}someone"), "{body}");
    assert!(body.contains("## Last failing commit"), "{body}");
    let at = radar_test("aaaa", "test_upgrade@migration", 0.7, 2, "2026-10-16T21:18:39Z");
    assert!(text::test_body(&at, None).contains("`test_upgrade@migration`"), "a code span keeps its @");
    assert!(body.contains("`26212199`"), "{body}");
    assert!(body.contains("Do not quarantine the test in FlakeRadar."), "{body}");
    assert!(!body.contains("PR description"), "{body}");
    assert!(body.contains("- [ ] A comment or docstring in the changed code names the cause"), "{body}");
    assert!(body.contains("- Do not add a skip marker or skip condition to any test"), "{body}");
    assert_eq!(
        out.actions[0].labels.clone().unwrap(),
        ["agent", "scout", "ai-generated", "flaky-test"]
    );
}

#[test]
fn a_job_whose_flakes_flakeradar_explains_is_not_filed() {
    let radar = FlakeradarOut { tests: vec![], explained: vec![1], jobs: vec![] };
    let out = run_radar(&covered(false), &three_flakes(), &[], Radar::Read(&radar));
    // Two unexplained flakes are below min_flakes (3), so the job's flake is its test's.
    assert_eq!(kinds(&out), ["explained"]);
    assert_eq!(out.actions[0].issue, None);
    assert_eq!(out.actions[0].title, None);
    assert_eq!(out.actions[0].detail, "1 of 3 flakes explained by FlakeRadar tests");
}

#[test]
fn a_job_with_enough_unexplained_flakes_is_filed_on_those_alone() {
    let occurrences = (1..=4).map(|i| occ(i, "2026-10-12T00:00:00Z")).collect();
    let flaky = flaky(vec![flaky_job(".github/workflows/ci.yml", "pre-commit", occurrences)]);
    let radar = FlakeradarOut { tests: vec![], explained: vec![2], jobs: vec![] };
    let out = run_radar(&covered(false), &flaky, &[], Radar::Read(&radar));
    assert_eq!(kinds(&out), ["file"]);
    assert_eq!(out.actions[0].detail, "3 flakes in 14 days");
    let body = out.actions[0].body.clone().unwrap();
    assert!(!body.contains("[run 2,"), "an explained occurrence is not evidence: {body}");
}

#[test]
fn a_failed_flakeradar_read_plans_no_flaky_action() {
    let out = run_radar(&covered(false), &three_flakes(), &[], Radar::Skipped("tests page 1 returned 502"));
    assert!(out.actions.is_empty(), "{:?}", kinds(&out));
    assert_eq!(out.sources.flaky, "skipped: flakeradar tests page 1 returned 502");
    let line = text::summary(&out, None);
    assert_eq!(line, "scout writers-app DRY RUN | flaky skipped: flakeradar tests page 1 returned 502");
}

#[test]
fn an_uncovered_repository_plans_exactly_what_it_did_before() {
    let radar = FlakeradarOut {
        tests: vec![radar_test("aaaa", "welcome mat shows", 0.7, 2, "2026-10-16T21:18:39Z")],
        explained: vec![1, 2, 3],
        jobs: vec![JobLink { fp: flaky_fp(".github/workflows/ci.yml", "UI end-to-end (tauri-pilot)"), id: 12 }],
    };
    let config = config(false);
    let before = serde_json::to_string(&run_radar(&config, &three_flakes(), &[], Radar::Off)).unwrap();
    let read = serde_json::to_string(&run_radar(&config, &three_flakes(), &[], Radar::Read(&radar))).unwrap();
    let skipped = serde_json::to_string(&run_radar(&config, &three_flakes(), &[], Radar::Skipped("x"))).unwrap();
    assert_eq!(read, before);
    assert_eq!(skipped, before);
    assert!(before.contains("\"kind\":\"file\""), "{before}");
}

#[test]
fn a_test_regresses_only_on_a_failure_after_its_issue_closed() {
    let fp = "flaky:test-pilot:aaaa";
    let closed = vec![issue(40, "closed", Some("completed"), Some("2026-10-14T00:00:00Z"), fp)];
    let after = FlakeradarOut {
        tests: vec![radar_test("aaaa", "welcome mat shows", 0.7, 2, "2026-10-16T21:18:39Z")],
        explained: vec![],
        jobs: vec![],
    };
    let out = run_radar(&covered(false), &flaky(vec![]), &closed, Radar::Read(&after));
    assert_eq!(kinds(&out), ["regression"]);
    assert_eq!(out.actions[0].issue, Some(40));
    assert!(out.actions[0].title.as_deref().unwrap().starts_with("scout (regression of #40): flaky test"));

    let before = FlakeradarOut {
        tests: vec![radar_test("aaaa", "welcome mat shows", 0.7, 2, "2026-10-13T09:00:00Z")],
        explained: vec![],
        jobs: vec![],
    };
    let out = run_radar(&covered(false), &flaky(vec![]), &closed, Radar::Read(&before));
    assert!(out.actions.is_empty(), "{:?}", kinds(&out));
}

#[test]
fn a_flaky_test_with_an_open_or_not_planned_issue_is_known_or_suppressed() {
    let radar = FlakeradarOut {
        tests: vec![
            radar_test("aaaa", "one", 0.7, 2, "2026-10-16T21:18:39Z"),
            radar_test("bbbb", "two", 0.6, 1, "2026-10-16T21:18:39Z"),
        ],
        explained: vec![],
        jobs: vec![],
    };
    let issues = vec![
        issue(41, "open", None, None, "flaky:test-pilot:aaaa"),
        issue(42, "closed", Some("not_planned"), Some("2026-10-14T00:00:00Z"), "flaky:test-pilot:bbbb"),
    ];
    let out = run_radar(&covered(false), &flaky(vec![]), &issues, Radar::Read(&radar));
    assert_eq!(kinds(&out), ["known", "suppressed"]);
    assert_eq!(out.actions[0].issue, Some(41));
    assert_eq!(out.actions[1].issue, Some(42));
}

#[test]
fn the_summary_counts_explained_jobs() {
    let radar = FlakeradarOut {
        tests: vec![radar_test("aaaa", "welcome mat shows", 0.7, 2, "2026-10-16T21:18:39Z")],
        explained: vec![1],
        jobs: vec![],
    };
    let out = run_radar(&covered(false), &three_flakes(), &[], Radar::Read(&radar));
    assert_eq!(kinds(&out), ["file", "explained"]);
    assert_eq!(
        text::summary(&out, None),
        "scout writers-app DRY RUN: would file 1, explained 1 | would file flaky welcome mat shows (score 0.70, 2 proven flakes)"
    );
}

// ---- ADR 0028 C11: the action carries its FlakeRadar target, only for flaky file/regression ----

/// The JSON of one action, for the presence of the C11 key.
fn json_of(action: &Action) -> serde_json::Value {
    serde_json::to_value(action).unwrap()
}

#[test]
fn a_test_file_action_carries_its_flakeradar_link() {
    let radar = FlakeradarOut {
        tests: vec![radar_test("aaaa", "welcome mat shows", 0.7, 2, "2026-10-16T21:18:39Z")],
        explained: vec![],
        jobs: vec![],
    };
    let out = run_radar(&covered(false), &flaky(vec![]), &[], Radar::Read(&radar));
    assert_eq!(kinds(&out), ["file"]);
    assert_eq!(out.actions[0].flakeradar, Some(RadarLink { entity: "tests".to_string(), id: 11850 }));
    assert_eq!(json_of(&out.actions[0])["flakeradar"], serde_json::json!({"entity": "tests", "id": 11850}));
}

#[test]
fn a_test_regression_carries_its_flakeradar_link() {
    let closed = vec![issue(
        40,
        "closed",
        Some("completed"),
        Some("2026-10-14T00:00:00Z"),
        "flaky:test-pilot:aaaa",
    )];
    let radar = FlakeradarOut {
        tests: vec![radar_test("aaaa", "welcome mat shows", 0.7, 2, "2026-10-16T21:18:39Z")],
        explained: vec![],
        jobs: vec![],
    };
    let out = run_radar(&covered(false), &flaky(vec![]), &closed, Radar::Read(&radar));
    assert_eq!(kinds(&out), ["regression"]);
    assert_eq!(out.actions[0].flakeradar, Some(RadarLink { entity: "tests".to_string(), id: 11850 }));
}

#[test]
fn a_job_file_action_carries_the_job_id_when_flakeradar_lists_its_fingerprint() {
    let fp = flaky_fp(".github/workflows/ci.yml", "UI end-to-end (tauri-pilot)");
    let radar = FlakeradarOut {
        tests: vec![],
        explained: vec![],
        jobs: vec![JobLink { fp: fp.clone(), id: 12 }],
    };
    let out = run_radar(&covered(false), &three_flakes(), &[], Radar::Read(&radar));
    assert_eq!(kinds(&out), ["file"]);
    assert_eq!(out.actions[0].flakeradar, Some(RadarLink { entity: "jobs".to_string(), id: 12 }));
    assert_eq!(json_of(&out.actions[0])["flakeradar"], serde_json::json!({"entity": "jobs", "id": 12}));
}

#[test]
fn a_job_flakeradar_never_saw_has_no_link_key() {
    let radar = FlakeradarOut { tests: vec![], explained: vec![], jobs: vec![] };
    let out = run_radar(&covered(false), &three_flakes(), &[], Radar::Read(&radar));
    assert_eq!(kinds(&out), ["file"]);
    assert_eq!(out.actions[0].flakeradar, None);
    assert!(json_of(&out.actions[0]).get("flakeradar").is_none(), "{}", json_of(&out.actions[0]));
}

#[test]
fn known_suppressed_capped_backpressure_and_explained_actions_have_no_link_key() {
    // known and suppressed: an open and a not-planned issue for two test candidates.
    let radar = FlakeradarOut {
        tests: vec![
            radar_test("aaaa", "one", 0.7, 2, "2026-10-16T21:18:39Z"),
            radar_test("bbbb", "two", 0.6, 1, "2026-10-16T21:18:39Z"),
        ],
        explained: vec![],
        jobs: vec![],
    };
    let issues = vec![
        issue(41, "open", None, None, "flaky:test-pilot:aaaa"),
        issue(42, "closed", Some("not_planned"), Some("2026-10-14T00:00:00Z"), "flaky:test-pilot:bbbb"),
    ];
    let out = run_radar(&covered(false), &flaky(vec![]), &issues, Radar::Read(&radar));
    assert_eq!(kinds(&out), ["known", "suppressed"]);
    for action in &out.actions {
        assert_eq!(action.flakeradar, None, "{:?}", action.kind);
    }

    // capped: three tests fill the cap, so the job candidate is capped.
    let radar = FlakeradarOut {
        tests: vec![
            radar_test("aaaa", "one", 0.7, 2, "2026-10-16T21:18:39Z"),
            radar_test("bbbb", "two", 0.6, 1, "2026-10-16T21:18:39Z"),
            radar_test("cccc", "three", 0.6, 1, "2026-10-16T21:18:39Z"),
        ],
        explained: vec![],
        jobs: vec![],
    };
    let out = run_radar(&covered(false), &three_flakes(), &[], Radar::Read(&radar));
    assert_eq!(kinds(&out), ["file", "file", "file", "capped"]);
    assert_eq!(out.actions[3].flakeradar, None);

    // backpressure: three open flaky issues of another fingerprint hold the source back.
    let issues = vec![
        issue(51, "open", None, None, "flaky:other:a"),
        issue(52, "open", None, None, "flaky:other:b"),
        issue(53, "open", None, None, "flaky:other:c"),
    ];
    let radar = FlakeradarOut { tests: vec![], explained: vec![], jobs: vec![] };
    let out = run_radar(&covered(false), &three_flakes(), &issues, Radar::Read(&radar));
    assert_eq!(kinds(&out), ["backpressure"]);
    assert_eq!(out.actions[0].flakeradar, None);

    // explained: the job's flakes are its tests'.
    let radar = FlakeradarOut { tests: vec![], explained: vec![1], jobs: vec![] };
    let out = run_radar(&covered(false), &three_flakes(), &[], Radar::Read(&radar));
    assert_eq!(kinds(&out), ["explained"]);
    assert_eq!(out.actions[0].flakeradar, None);
}

#[test]
fn a_ratchet_file_action_has_no_link_key() {
    let config = config(false);
    let ratchet = ratchet(vec![entry_with("ruff", "B006", 2, 1)]);
    let flaky = flaky(vec![]);
    let out = run(&config, &ratchet, &flaky, &[]);
    assert_eq!(kinds(&out), ["file"]);
    assert_eq!(out.actions[0].source, "ratchet");
    assert_eq!(out.actions[0].flakeradar, None);
    assert!(json_of(&out.actions[0]).get("flakeradar").is_none());
}

// ---- ADR 0028 C11: the C8 suffix for a FlakeRadar link that conflicted or failed ----

/// A live plan with one filed flaky action, for the write-back suffix tests.
fn one_filed_plan() -> PlanOut {
    plan_out(
        true,
        vec![act(
            "file",
            "flaky",
            "flaky:test-pilot:aaaa",
            None,
            "welcome mat shows",
            "score 0.70, 2 proven flakes",
        )],
    )
}

/// A `filed` write-log entry for that action, with the given link outcome.
fn filed_with(link: Option<&str>, detail: Option<&str>) -> Filed {
    Filed {
        fp: "flaky:test-pilot:aaaa".to_string(),
        outcome: "filed".to_string(),
        number: Some(101),
        error: None,
        link: link.map(ToString::to_string),
        link_detail: detail.map(ToString::to_string),
    }
}

/// The line a filed flaky action with no link suffix prints.
const PLAIN_FILED: &str =
    "scout jelly-swipe: filed 1 | filed #101 flaky welcome mat shows (score 0.70, 2 proven flakes)";

#[test]
fn summary_reports_a_flakeradar_conflict() {
    let plan = one_filed_plan();
    let filed = vec![filed_with(Some("conflict"), Some("55"))];
    assert_eq!(
        text::summary(&plan, Some(&filed)),
        "scout jelly-swipe: filed 1 | filed #101 flaky welcome mat shows (score 0.70, 2 proven flakes) (FlakeRadar already links #55)"
    );
}

#[test]
fn summary_reports_a_failed_flakeradar_link() {
    let plan = one_filed_plan();
    let filed = vec![filed_with(Some("failed"), Some("HTTP 502"))];
    assert_eq!(
        text::summary(&plan, Some(&filed)),
        "scout jelly-swipe: filed 1 | filed #101 flaky welcome mat shows (score 0.70, 2 proven flakes) (FlakeRadar link failed: HTTP 502)"
    );
}

#[test]
fn summary_says_nothing_about_a_linked_or_replaced_write() {
    let plan = one_filed_plan();
    for link in ["linked", "replaced"] {
        let filed = vec![filed_with(Some(link), None)];
        assert_eq!(text::summary(&plan, Some(&filed)), PLAIN_FILED, "{link}");
    }
}

#[test]
fn a_write_log_entry_without_a_link_key_reads_as_before() {
    let filed: Vec<Filed> = serde_json::from_str(
        r#"[{"fp":"flaky:test-pilot:aaaa","outcome":"filed","number":101,"error":null}]"#,
    )
    .unwrap();
    assert_eq!(filed[0].link, None);
    assert_eq!(filed[0].link_detail, None);
    assert_eq!(text::summary(&one_filed_plan(), Some(&filed)), PLAIN_FILED);
}
