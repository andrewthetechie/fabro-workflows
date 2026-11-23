//! `stage-end`: a stage that ended `succeeded` without a submitted result runs again (#93).

mod common;

use std::path::Path;

use fabro_io::stageend::{self, Decision};

const VISIT: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const OLD_VISIT: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

fn set_ctx(ctx: &serde_json::Value) {
    // SAFETY: callers run inside `common::with_manifest`, which holds the lock every test
    // that touches the environment takes.
    unsafe {
        std::env::set_var("FABRO_HOOK_CONTEXT", ctx.to_string());
    }
}

/// The `stage_complete` hook, as fabro fires it for a node that did not fail.
fn complete(node: &str, status: &str) -> Decision {
    set_ctx(&serde_json::json!({"event": "stage_complete", "node_id": node, "status": status}));
    stageend::decide(&["mystage".into(), "phase.work".into()]).unwrap()
}

/// The `edge_selected` hook for the edge `from -> to`.
fn edge(from: &str, to: &str) -> Decision {
    set_ctx(&serde_json::json!({"event": "edge_selected", "edge_from": from, "edge_to": to}));
    stageend::decide(&["mystage".into(), "phase.work".into()]).unwrap()
}

fn rerun(node: &str) -> Decision {
    Decision::Rerun(node.into())
}

fn write_result(root: &Path, stage: &str, visit: &str) {
    let body = serde_json::json!({"summary": "s", "_io": {"stage": stage, "visit": visit}});
    std::fs::write(root.join("out.json"), body.to_string()).unwrap();
}

/// One visit of the stage that ended `succeeded`, up to the edge out of it.
fn visit(root: &Path) -> Decision {
    common::write_stage(root, "mystage", VISIT);
    complete("mystage", "succeeded");
    edge("mystage", "gate")
}

fn with_stage(tag: &str, f: impl FnOnce(&Path)) {
    let root = common::temp_root(tag);
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "mystage", || f(&root));
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn a_submitted_result_proceeds() {
    with_stage("se-ok", |root| {
        write_result(root, "mystage", VISIT);
        assert_eq!(visit(root), Decision::Proceed);
    });
}

#[test]
fn a_missing_result_reruns_twice_and_then_proceeds() {
    with_stage("se-missing", |root| {
        assert_eq!(visit(root), rerun("mystage"));
        assert_eq!(visit(root), rerun("mystage"));
        assert_eq!(visit(root), Decision::Proceed, "the gate decides after the last re-run");
        assert_eq!(visit(root), rerun("mystage"), "giving up resets the count for the next entry");
    });
}

#[test]
fn a_rerun_that_submits_proceeds_and_resets_the_count() {
    with_stage("se-recovered", |root| {
        assert_eq!(visit(root), rerun("mystage"));
        write_result(root, "mystage", VISIT);
        assert_eq!(visit(root), Decision::Proceed);
        std::fs::remove_file(root.join("out.json")).unwrap();
        assert_eq!(visit(root), rerun("mystage"));
        assert_eq!(visit(root), rerun("mystage"), "a later entry has both re-runs");
    });
}

#[test]
fn a_result_that_is_not_this_visits_is_missing() {
    with_stage("se-stale", |root| {
        write_result(root, "mystage", OLD_VISIT);
        assert_eq!(visit(root), rerun("mystage"), "a result of an earlier visit");
        write_result(root, "other", VISIT);
        assert_eq!(visit(root), rerun("mystage"), "a result of another stage");
    });
    with_stage("se-garbage", |root| {
        std::fs::write(root.join("out.json"), "{not json").unwrap();
        assert_eq!(visit(root), rerun("mystage"));
        std::fs::write(root.join("out.json"), r#"{"summary":"hand written"}"#).unwrap();
        assert_eq!(visit(root), rerun("mystage"), "a result with no receipt");
    });
}

#[test]
fn a_stage_that_did_not_succeed_keeps_its_routing() {
    with_stage("se-failed", |root| {
        // A failed stage fires `stage_failed`, so no marker exists for its visit.
        common::write_stage(root, "mystage", VISIT);
        assert_eq!(edge("mystage", "gate"), Decision::Proceed);
        // `allow_partial` turns an exhausted timeout into `partially_succeeded`.
        common::write_stage(root, "mystage", VISIT);
        complete("mystage", "partially_succeeded");
        assert_eq!(edge("mystage", "gate"), Decision::Proceed);
    });
    with_stage("se-old-marker", |root| {
        common::write_stage(root, "mystage", OLD_VISIT);
        complete("mystage", "succeeded");
        common::write_stage(root, "mystage", VISIT);
        assert_eq!(edge("mystage", "gate"), Decision::Proceed, "an earlier visit's marker");
    });
}

#[test]
fn a_failure_between_reruns_resets_the_count() {
    with_stage("se-reset", |root| {
        assert_eq!(visit(root), rerun("mystage"));
        common::write_stage(root, "mystage", OLD_VISIT);
        assert_eq!(edge("mystage", "gate"), Decision::Proceed);
        assert_eq!(visit(root), rerun("mystage"));
        assert_eq!(visit(root), rerun("mystage"));
    });
}

#[test]
fn only_an_edge_out_of_a_listed_stage_is_judged() {
    with_stage("se-edges", |root| {
        common::write_stage(root, "mystage", VISIT);
        complete("mystage", "succeeded");
        // The matcher also matches `edge_to`, so the hook fires on the way in.
        assert_eq!(edge("prep", "mystage"), Decision::Proceed);
        assert_eq!(edge("unlisted", "gate"), Decision::Proceed);
        // A listed node that is not the stage on record is not judged by its record.
        assert_eq!(edge("phase.work", "gate"), Decision::Proceed);
        assert_eq!(edge("mystage", "gate"), rerun("mystage"));
    });
}

#[test]
fn a_listed_stage_with_no_output_contract_proceeds() {
    let root = common::temp_root("se-noout");
    let m = common::manifest_readonly(&root);
    common::with_manifest(&root, &m, "reader", || {
        common::write_stage(&root, "reader", VISIT);
        set_ctx(&serde_json::json!({"event": "stage_complete", "node_id": "reader", "status": "succeeded"}));
        stageend::decide(&["reader".into()]).unwrap();
        set_ctx(&serde_json::json!({"event": "edge_selected", "edge_from": "reader", "edge_to": "x"}));
        assert_eq!(stageend::decide(&["reader".into()]).unwrap(), Decision::Proceed);
    });
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn its_own_errors_never_rerun() {
    with_stage("se-errors", |root| {
        // SAFETY: as in `set_ctx`.
        unsafe {
            std::env::remove_var("FABRO_HOOK_CONTEXT");
        }
        assert!(stageend::decide(&["mystage".into()]).is_err(), "no hook context");
        common::write_stage(root, "mystage", VISIT);
        complete("mystage", "succeeded");
        // A tries file that cannot be written would otherwise re-run the stage for ever.
        let tries = root.join(".io/stage-end");
        let _ = std::fs::remove_dir_all(&tries);
        std::fs::write(&tries, "a file, not a directory").unwrap();
        set_ctx(&serde_json::json!({"event": "edge_selected", "edge_from": "mystage", "edge_to": "gate"}));
        assert!(stageend::decide(&["mystage".into()]).is_err(), "an uncountable re-run is an error");
    });
}

/// The binary itself: exactly one decision on stdout, exit 0 on every path.
#[test]
fn the_command_prints_fabros_override_and_always_exits_0() {
    let root = common::temp_root("se-bin");
    let manifest = common::manifest(&root);
    common::write_stage(&root, "mystage", VISIT);
    let run = |ctx: Option<&str>| {
        let mut cmd = std::process::Command::new(env!("CARGO_BIN_EXE_fabro-io"));
        cmd.args(["stage-end", "mystage"])
            .env("FABRO_IO_ROOT", &root)
            .env("FABRO_IO_MANIFEST", &manifest)
            .env_remove("FABRO_HOOK_CONTEXT");
        if let Some(ctx) = ctx {
            cmd.env("FABRO_HOOK_CONTEXT", ctx);
        }
        let out = cmd.output().unwrap();
        (out.status.code(), String::from_utf8(out.stdout).unwrap())
    };
    let done = r#"{"event":"stage_complete","node_id":"mystage","status":"succeeded"}"#;
    assert_eq!(run(Some(done)), (Some(0), String::new()));
    let (code, out) = run(Some(r#"{"event":"edge_selected","edge_from":"mystage","edge_to":"gate"}"#));
    assert_eq!(code, Some(0));
    let decision: serde_json::Value = serde_json::from_str(out.trim()).unwrap();
    assert_eq!(decision, serde_json::json!({"decision": "override", "edge_to": "mystage"}));
    assert_eq!(run(None), (Some(0), String::new()), "an error proceeds in silence");
    assert_eq!(run(Some("not json")), (Some(0), String::new()));
    let _ = std::fs::remove_dir_all(&root);
}
