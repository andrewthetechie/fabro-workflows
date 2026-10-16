//! `scout plan` and `scout summary`: the C5 to C8 rules over hand-built inputs.

mod common;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::de::DeserializeOwned;

use fabro_io::scout::config::ConfigOut;
use fabro_io::scout::flaky::{FlakyJob, FlakyOut, Occurrence};
use fabro_io::scout::plan::{self, Action, Issue, PlanInput, PlanOut, Sources};
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
        Filed { fp: "ratchet:ruff:b001".to_string(), outcome: "raced".to_string(), number: None, error: None },
        Filed {
            fp: "ratchet:ruff:b002".to_string(),
            outcome: "failed".to_string(),
            number: None,
            error: Some("label create failed".to_string()),
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
