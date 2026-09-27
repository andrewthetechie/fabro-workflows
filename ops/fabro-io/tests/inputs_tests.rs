//! Tests 4–6: `inputs` paging, ordering, absent/missing handling and exit codes.

mod common;

use fabro_io::{inputs, pages, stage};

fn write_big(root: &std::path::Path) -> Vec<u8> {
    // 1200 lines of 99 'A' + newline = 120,000 bytes -> 3 pages (each ~49100 bytes).
    let mut content = String::new();
    for _ in 0..1200 {
        content.push_str(&"A".repeat(99));
        content.push('\n');
    }
    std::fs::write(root.join("big.txt"), &content).unwrap();
    content.into_bytes()
}

#[test]
fn test_4_inputs_batch_order_absent_and_paging() {
    let root = common::temp_root("in4");
    std::fs::write(root.join("issues.json"), r#"{"n":1}"#).unwrap();
    let big = write_big(&root);
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0)); // sets stage.json/visit
        let res = inputs::build(None, None);
        assert!(!res.is_error, "all present inputs served: {}", res.text);
        let t = &res.text;

        let i_issues = t.find("issues.json").expect("issues header");
        let i_big = t.find("big.txt").expect("big header");
        let i_absent = t
            .find("(absent: improve did not write opt)")
            .expect("absent sentence");
        assert!(
            i_issues < i_absent && i_absent < i_big,
            "issues, then the absent sentence, then big, in manifest order"
        );

        assert!(
            t.contains("=== opt: ") && t.contains("(absent: improve did not write opt)"),
            "absent optional input is named, then its sentence"
        );
        assert!(t.contains("part 1 of 3"), "large input paged as part 1 of 3");

        // Part 1 of `big` (~49100 bytes) does not fit whole after `issues`, so it is not
        // shown at all (never cut) and every one of its pages gets a MORE line.
        assert!(t.contains("(not shown: no room left"), "big is deferred, not cut: {t}");
        assert!(!t.contains(&"A".repeat(99)), "no byte of big is shown by the batch");
        assert!(t.contains("MORE: inputs(name=\"big\", part=1)"));
        assert!(t.contains("MORE: inputs(name=\"big\", part=2)"));
        assert!(t.contains("MORE: inputs(name=\"big\", part=3)"));
        assert!(!t.contains("MORE: inputs(name=\"big\", part=4)"));

        // Alone, `big` is the first block of its result, so part 1 is shown whole.
        let alone = inputs::build(Some("big"), None);
        assert!(!alone.is_error);
        assert!(alone.text.contains("part 1 of 3"));
        assert!(!alone.text.contains("MORE: inputs(name=\"big\", part=1)"));
        assert!(alone.text.contains("MORE: inputs(name=\"big\", part=2)"));

        // Page determinism: parts requested out of order equal a straight split.
        let parts = pages::split(&big, pages::BUDGET);
        assert_eq!(parts.len(), 3, "120 KB at 49152 B/page is 3 pages");
        let p3 = inputs::build(Some("big"), Some(3));
        assert!(!p3.is_error);
        let p2 = inputs::build(Some("big"), Some(2));
        assert!(!p2.is_error);
        assert_eq!(
            p3.text,
            String::from_utf8_lossy(&parts[2].bytes).into_owned(),
            "part 3 requested first returns the same bytes as the straight split"
        );
        assert_eq!(
            p2.text,
            String::from_utf8_lossy(&parts[1].bytes).into_owned(),
            "part 2 after part 3 returns the same bytes"
        );

        // Concatenation is byte-identical to the file (test 5 folded here).
        let joined: Vec<u8> = parts.iter().flat_map(|p| p.bytes.clone()).collect();
        assert_eq!(joined, big, "all pages concatenate to the file");
    });
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn test_6_missing_required_input_is_error_and_cli_exits_3() {
    let root = common::temp_root("in6");
    // Do NOT create issues.json; opt absent; big absent too.
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || {
        assert_eq!(stage::run(), std::process::ExitCode::from(0));
        let res = inputs::build(None, None);
        assert!(res.is_error, "a missing required input must flag is_error");
        assert!(res.text.contains("MISSING: issues"), "MISSING names the input: {}", res.text);

        // served.json records `missing`.
        let served: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(root.join(".io/served.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(served["inputs"]["issues"]["status"], "missing");

        // CLI exit code 3.
        let code = inputs::run_cli(&[]);
        assert_eq!(code, std::process::ExitCode::from(3));
    });
    let _ = std::fs::remove_dir_all(&root);
}
