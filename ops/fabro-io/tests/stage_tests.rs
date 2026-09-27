//! Tests 1–3: `stage` writes stage.json with a fresh visit; version gates.

mod common;

use fabro_io::stage;

#[test]
fn stage_writes_valid_json_and_changes_visit() {
    let root = common::temp_root("stage");
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || {
        let c1 = stage::run();
        assert_eq!(c1, std::process::ExitCode::from(0));
        let v1: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(root.join(".io/stage.json")).unwrap())
                .unwrap();
        let visit1 = v1["visit"].as_str().unwrap();
        assert_eq!(visit1.len(), 32);
        assert!(visit1.chars().all(|c| c.is_ascii_hexdigit()));
        assert_eq!(v1["node"], "mystage");
        assert_eq!(v1["workflow"], "wf");

        let c2 = stage::run();
        assert_eq!(c2, std::process::ExitCode::from(0));
        let v2: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(root.join(".io/stage.json")).unwrap())
                .unwrap();
        assert_ne!(v2["visit"], v1["visit"], "a second stage call must change the visit");
    });
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn stage_min_binary_above_version_exits_2() {
    let root = common::temp_root("stage-old");
    let m = common::manifest_min_binary(&root, "99.0.0");
    common::with_manifest(&root, &m, "mystage", || {
        let c = stage::run();
        assert_eq!(c, std::process::ExitCode::from(2), "older binary must block at stage_start");
    });
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn stage_node_not_in_manifest_exits_0() {
    let root = common::temp_root("stage-ghost");
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "ghost", || {
        let c = stage::run();
        assert_eq!(c, std::process::ExitCode::from(0), "a node the manifest does not list must not block");
        let v: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(root.join(".io/stage.json")).unwrap())
                .unwrap();
        assert_eq!(v["node"], "ghost");
    });
    let _ = std::fs::remove_dir_all(&root);
}
