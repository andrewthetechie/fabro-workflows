//! Regression tests for the batch `inputs` call, unknown names, concurrent calls, and the
//! `stage` block decision (the 2026-09-27 review of docs/stage-io).

mod common;

use std::path::Path;
use std::process::Command;

use fabro_io::{inputs, pages, stage, submit};

/// Inputs in order: `a` (small), `diff` (93 KB, 2 parts), `c` (small, required),
/// `d` (small, optional, present), `e` (optional, absent). One output.
fn manifest(root: &Path) -> String {
    format!(
        r#"{{"version":1,"min_binary":"0.1.0","stages":{{
          "st": {{
            "inputs": [
              {{"name":"a","path":"{0}/a.txt","required":true}},
              {{"name":"diff","path":"{0}/diff.patch","required":true}},
              {{"name":"c","path":"{0}/c.txt","required":true}},
              {{"name":"d","path":"{0}/d.txt","required":false,"absent":"no d"}},
              {{"name":"e","path":"{0}/e.txt","required":false,"absent":"no e"}}
            ],
            "output": {{"path":"{0}/out.json","schema":{{"type":"object"}}}}
          }}
        }}}}"#,
        root.display()
    )
}

/// 1169 numbered lines of 80 bytes: 93,520 bytes, two pages.
fn write_inputs(root: &Path) -> Vec<u8> {
    std::fs::write(root.join("a.txt"), "x".repeat(4000) + "\n").unwrap();
    let mut diff = String::new();
    for i in 1..1170 {
        diff.push_str(&format!("L{i:05} {}\n", "d".repeat(72)));
    }
    std::fs::write(root.join("diff.patch"), &diff).unwrap();
    std::fs::write(root.join("c.txt"), "c-content\n").unwrap();
    std::fs::write(root.join("d.txt"), "d-content\n").unwrap();
    diff.into_bytes()
}

fn served(root: &Path) -> serde_json::Value {
    serde_json::from_str(&std::fs::read_to_string(root.join(".io/served.json")).unwrap()).unwrap()
}

/// A page is never cut: the batch shows none of a page that does not fit whole, and
/// `submit` accepts only once every page was really returned.
#[test]
fn batch_never_records_a_page_it_did_not_show_whole() {
    let root = common::temp_root("b-cut");
    let diff = write_inputs(&root);
    let parts = pages::split(&diff, pages::BUDGET);
    assert_eq!(parts.len(), 2);
    common::with_manifest(&root, &manifest(&root), "st", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0));
        let res = inputs::build(None, None);
        assert!(!res.is_error, "{}", res.text);
        assert!(!res.text.contains("L00001 "), "no line of the diff is shown by the batch");
        assert_eq!(served(&root)["inputs"].get("diff"), None, "diff is not recorded");

        // Part 2 alone does not complete the input: part 1 was never shown.
        assert!(!inputs::build(Some("diff"), Some(2)).is_error);
        let out = serde_json::json!({});
        match submit::run(&out) {
            submit::Outcome::Incomplete(m) => assert!(m.contains("part=1"), "{m}"),
            o => panic!("submit must refuse until part 1 is read: {o:?}"),
        }
        // Every page returned by page mode is exactly the fixed part, with no line lost.
        let p1 = inputs::build(Some("diff"), Some(1));
        assert_eq!(p1.text, String::from_utf8_lossy(&parts[0].bytes));
        assert!(matches!(submit::run(&out), submit::Outcome::Written(_)));
    });
    let _ = std::fs::remove_dir_all(&root);
}

/// Every input after one that does not fit is still shown if it fits, or named with a
/// MORE line, and the MORE lines close the result.
#[test]
fn batch_names_every_input_after_an_overflow() {
    let root = common::temp_root("b-after");
    write_inputs(&root);
    common::with_manifest(&root, &manifest(&root), "st", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0));
        let t = inputs::build(None, None).text;
        for h in ["=== a: ", "=== diff: ", "=== c: ", "=== d: ", "=== e: "] {
            assert!(t.contains(h), "header {h} missing from:\n{t}");
        }
        assert!(t.contains("c-content"), "a small input after the overflow is shown");
        assert!(t.contains("d-content"), "an optional input after the overflow is shown");
        assert!(t.contains("(absent: no e)"));
        let first_more = t.find("MORE: ").expect("MORE lines");
        assert!(first_more > t.find("d-content").unwrap(), "MORE lines close the result");
        assert!(t.contains("MORE: inputs(name=\"diff\", part=1)"));
        assert!(t.contains("MORE: inputs(name=\"diff\", part=2)"));

        let s = served(&root);
        assert_eq!(s["inputs"]["c"]["served"], serde_json::json!([1]));
        assert_eq!(s["inputs"]["d"]["served"], serde_json::json!([1]));
        assert_eq!(s["inputs"]["e"]["status"], "absent");
    });
    let _ = std::fs::remove_dir_all(&root);
}

/// An unknown name is an error that lists the stage's input names, in both call forms.
#[test]
fn unknown_name_is_an_error_listing_the_names() {
    let root = common::temp_root("b-name");
    write_inputs(&root);
    common::with_manifest(&root, &manifest(&root), "st", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0));
        for res in [inputs::build(Some("issue"), None), inputs::build(Some("issue"), Some(1))] {
            assert!(res.is_error, "unknown name must be an error");
            assert!(res.text.contains("no input named 'issue'"), "{}", res.text);
            assert!(res.text.contains("a, diff, c, d, e"), "{}", res.text);
        }
    });
    let _ = std::fs::remove_dir_all(&root);
}

/// A blank name, with or without a part, is the batch walk and not an unknown name: the
/// first call GLM made in every production `refute` session of 2026-09-27.
#[test]
fn blank_name_is_the_batch_walk() {
    let root = common::temp_root("b-blank");
    write_inputs(&root);
    common::with_manifest(&root, &manifest(&root), "st", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0));
        let batch = inputs::build(None, None);
        assert!(!batch.is_error, "{}", batch.text);
        let calls = [(Some(""), Some(1)), (Some(""), None), (Some("  "), Some(2)), (None, Some(1))];
        for (name, part) in calls {
            let res = inputs::build(name, part);
            assert!(!res.is_error, "{name:?}/{part:?}: {}", res.text);
            assert_eq!(res.text, batch.text, "{name:?}/{part:?} must be the batch walk");
        }
    });
    let _ = std::fs::remove_dir_all(&root);
}

/// Parallel `inputs` calls in one turn each keep their record: none is lost to a later save.
#[test]
fn parallel_calls_do_not_lose_served_parts() {
    let root = common::temp_root("b-par");
    write_inputs(&root);
    common::with_manifest(&root, &manifest(&root), "st", || {
        for _ in 0..25 {
            assert_eq!(stage::run(), std::process::ExitCode::from(0));
            std::thread::scope(|s| {
                for (name, part) in [("a", 1), ("diff", 1), ("diff", 2), ("c", 1), ("d", 1)] {
                    s.spawn(move || inputs::build(Some(name), Some(part)));
                }
            });
            let s = served(&root);
            assert_eq!(s["inputs"]["a"]["served"], serde_json::json!([1]));
            assert_eq!(s["inputs"]["c"]["served"], serde_json::json!([1]));
            assert_eq!(s["inputs"]["d"]["served"], serde_json::json!([1]));
            let mut diff: Vec<u64> = s["inputs"]["diff"]["served"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_u64().unwrap())
                .collect();
            diff.sort_unstable();
            assert_eq!(diff, vec![1, 2], "both diff parts recorded");
        }
    });
    let _ = std::fs::remove_dir_all(&root);
}

/// The block decision reaches fabro: it is on stdout, and it is valid JSON even when the
/// reason carries quotes.
#[test]
fn stage_block_decision_is_json_on_stdout() {
    let root = common::temp_root("b-stage");
    let run = |manifest: &str| {
        Command::new(env!("CARGO_BIN_EXE_fabro-io"))
            .arg("stage")
            .env("FABRO_IO_ROOT", &root)
            .env("FABRO_IO_MANIFEST", manifest)
            .env("FABRO_NODE_ID", "st")
            .output()
            .unwrap()
    };
    let old = run(&common::manifest_min_binary(&root, "99.0.0"));
    assert_eq!(old.status.code(), Some(2));
    let d: serde_json::Value = serde_json::from_slice(&old.stdout).expect("JSON on stdout");
    assert_eq!(d["decision"], "block");
    assert!(d["reason"].as_str().unwrap().contains("rebuild the profile images"));

    let bad = run(r#"{"version":"one"}"#);
    assert_eq!(bad.status.code(), Some(2));
    let d: serde_json::Value = serde_json::from_slice(&bad.stdout).expect("escaped JSON");
    assert!(d["reason"].as_str().unwrap().contains("not valid JSON"));
    let _ = std::fs::remove_dir_all(&root);
}
