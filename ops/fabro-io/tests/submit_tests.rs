//! Tests 7–11: `submit` refusal paths, success, visit invalidation and the fixture.

mod common;

use std::io::Write;

use fabro_io::{inputs, stage, submit};

const FIXED_VISIT: &str = "fac3fac3fac3fac3fac3fac3fac3fac3";

fn valid_output() -> serde_json::Value {
    serde_json::json!({"summary":"fixed","criteria":[{"criterion":"the bug","status":"met"}]})
}

fn serve_all(root: &std::path::Path) {
    // issues + opt (absent) + big page 1 via batch, then big pages 2..3.
    let _ = inputs::build(None, None);
    let served: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(root.join(".io/served.json")).unwrap(),
    )
    .unwrap();
    if let Some(big) = served["inputs"]["big"]["parts"].as_u64() {
        for p in 2..=big {
            let _ = inputs::build(Some("big"), Some(p as u32));
        }
    }
}

fn out_path(root: &std::path::Path) -> std::path::PathBuf {
    root.join("out.json")
}

#[test]
fn test_7_submit_before_inputs_refuses() {
    let root = common::temp_root("s7");
    std::fs::write(root.join("issues.json"), r#"{"n":1}"#).unwrap();
    std::fs::write(root.join("big.txt"), "x\n").unwrap();
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0));
        match submit::run(&valid_output()) {
            submit::Outcome::NeedInputs(msg) => assert!(msg.contains("call inputs first"), "{msg}"),
            other => panic!("expected NeedInputs, got {other:?}"),
        }
        assert!(!out_path(&root).exists(), "must write nothing");
    });
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn test_8_submit_after_incomplete_large_input_refuses_naming_next_part() {
    let root = common::temp_root("s8");
    std::fs::write(root.join("issues.json"), r#"{"n":1}"#).unwrap();
    // 120 KB big file (3 pages).
    for _ in 0..1200 {
        let mut line = "A".repeat(99);
        line.push('\n');
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(root.join("big.txt"))
            .unwrap()
            .write_all(line.as_bytes())
            .unwrap();
    }
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0));
        let _ = inputs::build(None, None); // `big` does not fit after `issues`: deferred
        let _ = inputs::build(Some("big"), Some(1)); // part 1 only
        match submit::run(&valid_output()) {
            submit::Outcome::Incomplete(msg) => {
                assert!(msg.contains("big"), "names the input: {msg}");
                assert!(msg.contains("part=2"), "names the next part: {msg}");
            }
            other => panic!("expected Incomplete, got {other:?}"),
        }
        assert!(!out_path(&root).exists(), "must write nothing");
    });
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn test_9_submit_schema_violation_lists_pointer_and_writes_nothing() {
    let root = common::temp_root("s9");
    std::fs::write(root.join("issues.json"), r#"{"n":1}"#).unwrap();
    std::fs::write(root.join("big.txt"), "small\n").unwrap();
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0));
        serve_all(&root);
        // Missing `summary` and a criteria item missing `status`.
        let bad = serde_json::json!({"criteria":[{"criterion":"x"}]});
        match submit::run(&bad) {
            submit::Outcome::SchemaViolation(msgs) => {
                assert!(
                    msgs.contains(':'),
                    "violations carry a pointer and message: {msgs}"
                );
            }
            other => panic!("expected SchemaViolation, got {other:?}"),
        }
        assert!(!out_path(&root).exists(), "must write nothing");
    });
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn test_10_submit_success_stamps_receipt_and_matches_fixture() {
    let root = common::temp_root("s10");
    std::fs::write(root.join("issues.json"), r#"{"issue":1205,"title":"x"}"#).unwrap();
    std::fs::write(root.join("big.txt"), "diff content\nsecond line\n").unwrap();
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || {
        common::write_stage(&root, "mystage", FIXED_VISIT);
        serve_all(&root);
        match submit::run(&valid_output()) {
            submit::Outcome::Written(msg) => assert!(msg.starts_with("written:")),
            other => panic!("expected Written, got {other:?}"),
        }
        let raw = std::fs::read_to_string(out_path(&root)).unwrap();
        let v: serde_json::Value = serde_json::from_str(&raw).unwrap();
        let io = &v["_io"];
        assert_eq!(io["stage"], "mystage");
        assert_eq!(io["visit"], FIXED_VISIT);
        assert_eq!(io["binary"], env!("CARGO_PKG_VERSION"));
        let inputs_map = io["inputs"].as_object().unwrap();
        assert!(inputs_map.contains_key("issues"), "receipt hashes each input");
        assert!(inputs_map.contains_key("big"));

        let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/refute.json");
        if std::env::var("GEN_FIXTURE").is_ok() {
            std::fs::create_dir_all(fixture.parent().unwrap()).unwrap();
            std::fs::write(&fixture, raw.as_bytes()).unwrap();
        } else {
            let expected = std::fs::read_to_string(&fixture)
                .expect("fixture refute.json; run with GEN_FIXTURE=1 to generate it");
            assert_eq!(
                raw, expected,
                "submit output must be byte-identical to the committed fixture"
            );
        }
    });
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn test_11_new_visit_invalidates_older_served() {
    let root = common::temp_root("s11");
    std::fs::write(root.join("issues.json"), r#"{"n":1}"#).unwrap();
    std::fs::write(root.join("big.txt"), "small\n").unwrap();
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || {
        common::write_stage(&root, "mystage", FIXED_VISIT);
        serve_all(&root);
        // A new stage visit (as a retried stage would produce).
        common::write_stage(&root, "mystage", &"b".repeat(32));
        match submit::run(&valid_output()) {
            submit::Outcome::NeedInputs(msg) => assert!(msg.contains("call inputs first"), "{msg}"),
            other => panic!("expected NeedInputs after a new visit, got {other:?}"),
        }
    });
    let _ = std::fs::remove_dir_all(&root);
}
