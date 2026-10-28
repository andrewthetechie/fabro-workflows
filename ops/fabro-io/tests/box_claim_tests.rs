//! more-local-inference task 02: `box-claim` and `box-guard` (C3, C10).
//!
//! The decisions are pure and are tested directly; each I/O path runs against
//! [`stub::Stub`], a canned-response server on a real socket.

#[path = "common/stub.rs"]
mod stub;

mod common;

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::{MutexGuard, PoisonError};
use std::time::Instant;

use fabro_io::boxclaim::{
    self, CoderRun, Health, RunIdSource, acts, confirmed_idle, guard_clear, is_box_stage, label,
    lane_runs_on, nonempty_env, parse_box_health, plan_arm_true, resolve_run_id, running_stage,
};
use fabro_io::manifest::{BoxMode, Manifest};

use stub::{Reply, Route, Stub};

/// The variables a test may set. Everything else the hooks read is cleared.
const KEYS: [&str; 9] = [
    "FABRO_IO_ROOT",
    "FABRO_IO_MANIFEST",
    "FABRO_NODE_ID",
    "FABRO_WORKFLOW",
    "FABRO_BOX_HEALTH",
    "FABRO_API_URL",
    "FABRO_API_TOKEN",
    "FABRO_RUN_ID",
    "FABRO_HOOK_CONTEXT",
];

/// An isolated sandbox: a temp io root, a cleared and curated environment, and the
/// process-wide lock the other tests in this binary take.
struct Sandbox {
    _guard: MutexGuard<'static, ()>,
    root: PathBuf,
    saved: Vec<(&'static str, Option<OsString>)>,
}

impl Sandbox {
    fn new(tag: &str) -> Self {
        let guard = common::LOCK.lock().unwrap_or_else(PoisonError::into_inner);
        let saved = KEYS
            .iter()
            .map(|key| (*key, std::env::var_os(key)))
            .collect();
        for key in KEYS {
            // SAFETY: the environment is process-global; this struct holds `LOCK`,
            // which every test that reads or writes it takes, and restores it on drop.
            unsafe { std::env::remove_var(key) };
        }
        let root = common::temp_root(tag);
        // SAFETY: as above.
        unsafe { std::env::set_var("FABRO_IO_ROOT", &root) };
        Self { _guard: guard, root, saved }
    }

    fn set(&self, key: &str, value: &str) -> &Self {
        // SAFETY: as in `new`.
        unsafe { std::env::set_var(key, value) };
        self
    }

    fn manifest(&self, raw: &str) -> &Self {
        self.set("FABRO_IO_MANIFEST", raw)
    }

    fn box_file(&self, pool: &str) -> &Self {
        std::fs::write(self.root.join("box"), format!("{pool}\n")).expect("write the box file");
        self
    }

    fn issue(&self, number: u64) -> &Self {
        std::fs::write(self.root.join("issue.json"), format!("{{\"number\":{number}}}"))
            .expect("write issue.json");
        self
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        for (key, value) in &self.saved {
            // SAFETY: as in `new`.
            unsafe {
                match value {
                    Some(value) => std::env::set_var(key, value),
                    None => std::env::remove_var(key),
                }
            }
        }
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

/// A one-stage manifest whose stage carries `body`, and optional `coder_box_stages`.
fn manifest_with(stage: &str, body: &str, coder_box_stages: &str) -> String {
    let coder = if coder_box_stages.is_empty() {
        String::new()
    } else {
        format!(r#","coder_box_stages":{coder_box_stages}"#)
    };
    format!(
        r#"{{"version":1,"min_binary":"0.1.0","stages":{{"{stage}":{body}}}{coder}}}"#
    )
}

fn mode(value: &str) -> String {
    manifest_with("coder", &format!(r#"{{"box":{value}}}"#), "")
}

/// A response body of `n` active runs, as `GET /runs` returns them.
fn runs_body(runs: &str) -> Reply {
    Reply::Json(200, format!(r#"{{"data":[{runs}],"meta":{{"has_more":false}}}}"#))
}

const IDLE: &str = r#"{"busy":false,"queued":0,"idle_seconds":42.0,"live_tokens":0}"#;

// --- pure decisions -----------------------------------------------------------------

#[test]
fn plan_arm_is_issue_halved_then_odd() {
    assert!(!plan_arm_true(0));
    assert!(!plan_arm_true(1));
    assert!(plan_arm_true(2));
    assert!(plan_arm_true(3));
    assert!(!plan_arm_true(4));
    assert!(!plan_arm_true(5));
    assert!(plan_arm_true(6));
    assert!(plan_arm_true(7));
}

#[test]
fn acts_maps_every_box_mode() {
    assert!(!acts(BoxMode::Never, false));
    assert!(!acts(BoxMode::Never, true));
    assert!(acts(BoxMode::Always, false));
    assert!(acts(BoxMode::Always, true));
    assert!(!acts(BoxMode::PlanArm, false));
    assert!(acts(BoxMode::PlanArm, true));
}

#[test]
fn box_health_parse_tolerates_spaces_and_a_trailing_comma() {
    let parsed = parse_box_health(
        " coders-a = http://10.10.0.29:5300/health , coders-b=http://10.10.0.56:5300/health , ",
    );
    assert_eq!(parsed.len(), 2);
    assert_eq!(
        parsed.get("coders-a").map(String::as_str),
        Some("http://10.10.0.29:5300/health")
    );
    assert_eq!(
        parsed.get("coders-b").map(String::as_str),
        Some("http://10.10.0.56:5300/health")
    );
}

#[test]
fn box_health_parse_skips_a_pair_without_a_url_and_carries_no_unknown_pool() {
    let parsed = parse_box_health("coders-a=http://a/health,nonsense,coders-b=");
    assert_eq!(parsed.len(), 1);
    assert!(parsed.contains_key("coders-a"));
    assert!(!parsed.contains_key("coders-z"), "an unknown pool is simply absent");
}

#[test]
fn confirmed_idle_needs_the_last_two_reads() {
    assert!(!confirmed_idle(&[]));
    assert!(!confirmed_idle(&[true]), "one read is not enough");
    assert!(confirmed_idle(&[true, true]));
    assert!(!confirmed_idle(&[true, false, true]));
    assert!(confirmed_idle(&[false, true, true]));
    assert!(!confirmed_idle(&[true, true, false]));
}

#[test]
fn health_needs_both_fields_and_reads_them_as_the_contract_says() {
    assert_eq!(Health::parse(IDLE), Some(Health { busy: false, queued: 0 }));
    assert_eq!(Health::parse(IDLE).map(Health::is_idle), Some(true));
    assert_eq!(
        Health::parse(r#"{"busy":true,"queued":0}"#).map(Health::is_idle),
        Some(false)
    );
    assert_eq!(
        Health::parse(r#"{"busy":false,"queued":1}"#).map(Health::is_idle),
        Some(false)
    );
    assert_eq!(Health::parse(r#"{"queued":0}"#), None, "no `busy` reads as not idle");
    assert_eq!(Health::parse("not json"), None);
}

#[test]
fn claim_filter_keeps_background_runs_on_the_box_and_never_its_own() {
    let runs: Vec<serde_json::Value> = serde_json::from_str(
        r#"[
          {"id":"bg-a","labels":{"lane":"background","box":"coders-a"}},
          {"id":"coder-a","labels":{"lane":"coder","box":"coders-a"}},
          {"id":"bg-b","labels":{"lane":"background","box":"coders-b"}},
          {"id":"bg-other","labels":{"lane":"background","box":"coders-a"}}
        ]"#,
    )
    .expect("runs json");

    assert_eq!(
        lane_runs_on(&runs, "background", "coders-a", None),
        vec!["bg-a".to_string(), "bg-other".to_string()]
    );
    assert_eq!(
        lane_runs_on(&runs, "background", "coders-a", Some("bg-a")),
        vec!["bg-other".to_string()],
        "the run's own id is never cancelled"
    );
    assert_eq!(
        lane_runs_on(&runs, "coder", "coders-a", None),
        vec!["coder-a".to_string()]
    );
}

#[test]
fn running_stage_is_the_one_marked_running() {
    let stages: Vec<serde_json::Value> = serde_json::from_str(
        r#"[{"node_id":"claim","status":"succeeded"},{"node_id":"coder","status":"running"}]"#,
    )
    .expect("stages json");
    assert_eq!(running_stage(&stages), Some("coder"));
    let finished: Vec<serde_json::Value> =
        serde_json::from_str(r#"[{"node_id":"coder","status":"succeeded"}]"#).expect("stages json");
    assert_eq!(running_stage(&finished), None, "between stages");
}

#[test]
fn label_reads_only_string_labels() {
    let run: serde_json::Value =
        serde_json::from_str(r#"{"labels":{"lane":"coder","issue":"42","n":3}}"#).expect("run json");
    assert_eq!(label(&run, "lane"), Some("coder"));
    assert_eq!(label(&run, "n"), None);
    assert_eq!(label(&run, "absent"), None);
}

/// The manifest a background package carries: `backlog.coder` and `backlog.plan` are box
/// stages, `review` is not.
fn guard_manifest() -> Manifest {
    serde_json::from_str(&manifest_with(
        "work",
        r#"{"box":true}"#,
        r#"{"backlog":{"coder":true,"plan":"plan-arm"}}"#,
    ))
    .expect("guard manifest")
}

fn coder(workflow: &str, issue: Option<u64>, stage: Option<&str>) -> CoderRun {
    CoderRun {
        workflow: workflow.to_string(),
        issue,
        running_stage: stage.map(str::to_string),
    }
}

#[test]
fn guard_is_clear_with_no_coder_run_on_an_idle_box() {
    let idle = Health { busy: false, queued: 0 };
    assert!(guard_clear(&guard_manifest(), &[], idle));
}

#[test]
fn guard_clears_a_coder_run_in_a_hosted_stage() {
    let idle = Health { busy: false, queued: 0 };
    assert!(guard_clear(&guard_manifest(), &[coder("backlog", Some(2), Some("review"))], idle));
}

#[test]
fn guard_blocks_a_coder_run_in_a_box_stage() {
    let idle = Health { busy: false, queued: 0 };
    let run = coder("backlog", Some(2), Some("coder"));
    assert!(is_box_stage(&guard_manifest(), &run));
    assert!(!guard_clear(&guard_manifest(), &[run], idle));
}

#[test]
fn guard_judges_a_plan_arm_stage_by_the_issue() {
    let manifest = guard_manifest();
    let idle = Health { busy: false, queued: 0 };
    // (1 // 2) % 2 == 0: the arm is off, so the stage is not a box stage.
    let off_arm = coder("backlog", Some(1), Some("plan"));
    assert!(!is_box_stage(&manifest, &off_arm));
    assert!(guard_clear(&manifest, &[off_arm], idle));
    // (2 // 2) % 2 == 1: the arm is on.
    let on_arm = coder("backlog", Some(2), Some("plan"));
    assert!(is_box_stage(&manifest, &on_arm));
    assert!(!guard_clear(&manifest, &[on_arm], idle));
    // No issue label at all cannot be the arm, so the stage is not a box stage.
    assert!(!is_box_stage(&manifest, &coder("backlog", None, Some("plan"))));
}

#[test]
fn guard_treats_between_stages_on_an_idle_box_as_clear() {
    let idle = Health { busy: false, queued: 0 };
    let between = coder("backlog", Some(2), None);
    assert!(!is_box_stage(&guard_manifest(), &between));
    assert!(guard_clear(&guard_manifest(), &[between], idle));
}

#[test]
fn guard_blocks_any_box_whose_health_is_not_idle() {
    let busy = Health { busy: true, queued: 0 };
    assert!(!guard_clear(&guard_manifest(), &[], busy));
    let queued = Health { busy: false, queued: 3 };
    assert!(!guard_clear(&guard_manifest(), &[], queued));
}

#[test]
fn guard_cannot_attribute_an_unknown_workflow_and_says_not_a_box_stage() {
    let run = coder("", Some(2), Some("coder"));
    assert!(!is_box_stage(&guard_manifest(), &run));
}

// --- the claim shell ----------------------------------------------------------------

#[test]
fn claim_returns_at_once_for_a_non_box_stage() {
    let stub = Stub::start(vec![]);
    let sandbox = Sandbox::new("claim-never");
    sandbox
        .manifest(&mode("false"))
        .set("FABRO_NODE_ID", "coder")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_RUN_ID", "own")
        .box_file("coders-a");

    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    assert_eq!(stub.count(), 0, "a non-box stage does no I/O at all");
}

#[test]
fn claim_returns_at_once_for_a_stage_the_manifest_does_not_list() {
    let stub = Stub::start(vec![]);
    let sandbox = Sandbox::new("claim-absent");
    sandbox
        .manifest(&manifest_with("other", r#"{"box":true}"#, ""))
        .set("FABRO_NODE_ID", "coder")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_RUN_ID", "own")
        .box_file("coders-a");

    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    assert_eq!(stub.count(), 0);
}

#[test]
fn claim_does_nothing_on_the_even_arm_of_a_plan_arm_stage() {
    let stub = Stub::start(vec![]);
    let sandbox = Sandbox::new("claim-plan-off");
    sandbox
        .manifest(&manifest_with("plan", r#"{"box":"plan-arm"}"#, ""))
        .set("FABRO_NODE_ID", "plan")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_RUN_ID", "own")
        .box_file("coders-a")
        .issue(1);

    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    assert_eq!(stub.count(), 0);
}

#[test]
fn claim_acts_on_the_odd_arm_of_a_plan_arm_stage() {
    let stub = Stub::start(vec![
        Route::get("/health", Reply::Json(200, IDLE.to_string())),
        Route::get("/runs?", runs_body("")),
    ]);
    let sandbox = Sandbox::new("claim-plan-on");
    sandbox
        .manifest(&manifest_with("plan", r#"{"box":"plan-arm"}"#, ""))
        .set("FABRO_NODE_ID", "plan")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_RUN_ID", "own")
        .box_file("coders-a")
        .issue(2);

    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    assert!(
        stub.requests()
            .iter()
            .any(|(method, path)| method == "GET" && path.starts_with("/runs?")),
        "the odd arm lists the runs: {:?}",
        stub.requests()
    );
}

#[test]
fn claim_does_nothing_without_a_box_file() {
    let stub = Stub::start(vec![]);
    let sandbox = Sandbox::new("claim-no-box");
    sandbox
        .manifest(&mode("true"))
        .set("FABRO_NODE_ID", "coder")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_RUN_ID", "own");

    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    assert_eq!(stub.count(), 0);
}

#[test]
fn claim_does_nothing_for_a_pool_the_health_variable_does_not_name() {
    let stub = Stub::start(vec![]);
    let sandbox = Sandbox::new("claim-unknown-pool");
    sandbox
        .manifest(&mode("true"))
        .set("FABRO_NODE_ID", "coder")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_RUN_ID", "own")
        .box_file("coders-z");

    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    assert_eq!(stub.count(), 0);
}

#[test]
fn claim_cancels_a_background_run_and_accepts_202() {
    let stub = Stub::start(vec![
        Route::get("/health", Reply::Json(200, IDLE.to_string())),
        Route::get(
            "/runs?",
            runs_body(r#"{"id":"bg1","labels":{"lane":"background","box":"coders-a"}}"#),
        ),
        Route::post("/runs/bg1/cancel", Reply::Status(202)),
    ]);
    let sandbox = Sandbox::new("claim-202");
    sandbox
        .manifest(&mode("true"))
        .set("FABRO_NODE_ID", "coder")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_API_TOKEN", "secret")
        .set("FABRO_RUN_ID", "own")
        .box_file("coders-a");

    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    let requests = stub.requests();
    assert!(
        requests.contains(&("POST".to_string(), "/runs/bg1/cancel".to_string())),
        "the background run is cancelled: {requests:?}"
    );
    assert_eq!(
        requests.iter().filter(|(_, path)| path == "/health").count(),
        2,
        "the idle rule needs two consecutive reads: {requests:?}"
    );
}

#[test]
fn claim_accepts_409_on_a_cancel() {
    let stub = Stub::start(vec![
        Route::get("/health", Reply::Json(200, IDLE.to_string())),
        Route::get(
            "/runs?",
            runs_body(r#"{"id":"bg1","labels":{"lane":"background","box":"coders-a"}}"#),
        ),
        Route::post("/runs/bg1/cancel", Reply::Status(409)),
    ]);
    let sandbox = Sandbox::new("claim-409");
    sandbox
        .manifest(&mode("true"))
        .set("FABRO_NODE_ID", "coder")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_RUN_ID", "own")
        .box_file("coders-a");

    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    assert!(
        stub.requests()
            .contains(&("POST".to_string(), "/runs/bg1/cancel".to_string()))
    );
}

#[test]
fn claim_exits_zero_after_one_call_times_out_and_stops_there() {
    let stub = Stub::start(vec![Route::get("/runs?", Reply::Hang)]);
    let sandbox = Sandbox::new("claim-hang");
    sandbox
        .manifest(&mode("true"))
        .set("FABRO_NODE_ID", "coder")
        .set("FABRO_WORKFLOW", "backlog")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .set("FABRO_RUN_ID", "own")
        .box_file("coders-a");

    let started = Instant::now();
    assert_eq!(boxclaim::run_claim(), ExitCode::SUCCESS);
    let elapsed = started.elapsed();
    assert!(
        elapsed.as_secs() >= 4,
        "the call's own 5 s timeout bounded it, not the 45 s deadline: {elapsed:?}"
    );
    assert!(
        elapsed.as_secs() < 20,
        "the failure exits instead of waiting out the deadline: {elapsed:?}"
    );
    assert_eq!(
        stub.requests().iter().filter(|(_, path)| path == "/health").count(),
        0,
        "a failed cancel step does not go on to the wait"
    );
}

// --- the guard shell ----------------------------------------------------------------

/// The routes a box with one coder loop in its `coder` stage answers.
fn guard_routes(cancel_status: u16) -> Vec<Route> {
    vec![
        Route::get("/health", Reply::Json(200, IDLE.to_string())),
        Route::get(
            "/runs?",
            runs_body(
                r#"{"id":"c1","labels":{"lane":"coder","box":"coders-a","workflow":"backlog","issue":"2"}}"#,
            ),
        ),
        Route::get("/runs/c1/stages", runs_body(r#"{"node_id":"coder","status":"running"}"#)),
        Route::post("/runs/own/cancel", Reply::Status(cancel_status)),
    ]
}

fn guard_sandbox<'a>(stub: &Stub, sandbox: &'a Sandbox) -> &'a Sandbox {
    sandbox
        .manifest(&manifest_with(
            "work",
            r#"{"box":true}"#,
            r#"{"backlog":{"coder":true}}"#,
        ))
        .set("FABRO_NODE_ID", "work")
        .set("FABRO_WORKFLOW", "box-noop")
        .set("FABRO_BOX_HEALTH", &format!("coders-a={}/health", stub.url))
        .set("FABRO_API_URL", &stub.url)
        .box_file("coders-a")
}

#[test]
fn guard_cancels_its_own_run_when_the_loop_is_in_a_box_stage() {
    let stub = Stub::start(guard_routes(202));
    let sandbox = Sandbox::new("guard-self-cancel");
    guard_sandbox(&stub, &sandbox).set("FABRO_RUN_ID", "own");

    assert_eq!(boxclaim::run_guard(), ExitCode::SUCCESS);
    assert!(
        stub.requests()
            .contains(&("POST".to_string(), "/runs/own/cancel".to_string())),
        "the guard cancels its own run: {:?}",
        stub.requests()
    );
}

#[test]
fn guard_blocks_the_stage_when_its_own_run_id_is_unknown() {
    let stub = Stub::start(guard_routes(202));
    let sandbox = Sandbox::new("guard-block");
    guard_sandbox(&stub, &sandbox);

    assert_eq!(boxclaim::run_guard(), ExitCode::from(2));
    assert!(
        !stub
            .requests()
            .iter()
            .any(|(method, _)| method == "POST"),
        "nothing is cancelled when the id is unknown"
    );
}

#[test]
fn guard_leaves_a_clear_box_alone() {
    let stub = Stub::start(vec![
        Route::get("/health", Reply::Json(200, IDLE.to_string())),
        Route::get("/runs?", runs_body("")),
    ]);
    let sandbox = Sandbox::new("guard-clear");
    guard_sandbox(&stub, &sandbox).set("FABRO_RUN_ID", "own");

    assert_eq!(boxclaim::run_guard(), ExitCode::SUCCESS);
    assert!(
        !stub
            .requests()
            .iter()
            .any(|(method, _)| method == "POST")
    );
}

// --- run identity -------------------------------------------------------------------

#[test]
fn run_id_comes_from_the_environment_first() {
    let sandbox = Sandbox::new("run-id-env");
    sandbox.set("FABRO_RUN_ID", "from-env");
    assert_eq!(resolve_run_id(), (Some("from-env".to_string()), RunIdSource::Env));
}

#[test]
fn run_id_falls_back_to_the_hook_context_file() {
    let sandbox = Sandbox::new("run-id-ctx");
    let context = sandbox.root.join("hook-context.json");
    std::fs::write(&context, r#"{"run_id":"from-context"}"#).expect("write the hook context");
    sandbox.set(
        "FABRO_HOOK_CONTEXT",
        context.to_str().expect("utf-8 path"),
    );
    assert_eq!(
        resolve_run_id(),
        (Some("from-context".to_string()), RunIdSource::HookContext)
    );
}

#[test]
fn run_id_is_unknown_with_neither_source() {
    let _sandbox = Sandbox::new("run-id-none");
    assert_eq!(resolve_run_id(), (None, RunIdSource::Unknown));
}

// --- the shipped binary -------------------------------------------------------------

fn binary() -> std::process::Command {
    let mut command = std::process::Command::new(env!("CARGO_BIN_EXE_fabro-io"));
    command.env_clear();
    command
}

#[test]
fn binary_exits_zero_with_one_stderr_line_and_no_stdout_when_the_environment_is_empty() {
    for (subcommand, prefix) in [("box-claim", "box-claim:"), ("box-guard", "box-guard:")] {
        let output = binary()
            .arg(subcommand)
            .output()
            .expect("run the fabro-io binary");
        assert_eq!(
            output.status.code(),
            Some(0),
            "{subcommand} with no environment must exit 0"
        );
        assert!(
            output.stdout.is_empty(),
            "{subcommand} must never print to stdout: {:?}",
            String::from_utf8_lossy(&output.stdout)
        );
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert_eq!(
            stderr.lines().count(),
            1,
            "{subcommand} must log exactly one stderr line, got {stderr:?}"
        );
        assert!(
            stderr.starts_with(prefix),
            "{subcommand} must prefix its line with {prefix:?}, got {stderr:?}"
        );
    }
}

#[test]
fn nonempty_env_treats_an_empty_value_as_unset() {
    let sandbox = Sandbox::new("nonempty-env");
    sandbox.set("FABRO_API_URL", "   ");
    assert_eq!(nonempty_env("FABRO_API_URL"), None);
    sandbox.set("FABRO_API_URL", " http://x ");
    assert_eq!(nonempty_env("FABRO_API_URL").as_deref(), Some("http://x"));
}

#[test]
fn manifest_missing_box_reads_as_never() {
    let parsed: Manifest =
        serde_json::from_str(&manifest_with("coder", "{}", "")).expect("manifest");
    let stage = parsed.stage("coder").expect("the stage");
    assert_eq!(stage.box_mode, BoxMode::Never);
    assert_eq!(parsed.coder_stage_mode("backlog", "coder"), BoxMode::Never);
}

#[test]
fn manifest_carries_each_box_value() {
    for (written, expected) in [
        ("false", BoxMode::Never),
        ("true", BoxMode::Always),
        (r#""plan-arm""#, BoxMode::PlanArm),
    ] {
        let parsed: Manifest =
            serde_json::from_str(&manifest_with("coder", &format!(r#"{{"box":{written}}}"#), ""))
                .expect("every contract value loads");
        assert_eq!(parsed.stage("coder").expect("the stage").box_mode, expected);
    }
}

#[test]
fn manifest_reads_coder_box_stages_per_workflow() {
    let parsed: Manifest = serde_json::from_str(&manifest_with(
        "work",
        r#"{"box":true}"#,
        r#"{"backlog":{"coder":true,"plan":"plan-arm"},"pr-review":{"rebase_agent_t1":true}}"#,
    ))
    .expect("manifest");
    assert_eq!(parsed.coder_stage_mode("backlog", "coder"), BoxMode::Always);
    assert_eq!(parsed.coder_stage_mode("backlog", "plan"), BoxMode::PlanArm);
    assert_eq!(
        parsed.coder_stage_mode("pr-review", "rebase_agent_t1"),
        BoxMode::Always
    );
    assert_eq!(parsed.coder_stage_mode("ralph", "coder"), BoxMode::Never);
    assert_eq!(parsed.coder_stage_mode("backlog", "review"), BoxMode::Never);
}

#[test]
fn manifest_rejects_a_box_value_that_is_not_the_contract() {
    assert!(serde_json::from_str::<Manifest>(&mode("\"maybe\"")).is_err());
    assert!(serde_json::from_str::<Manifest>(&mode("1")).is_err());
}
