//! `box-claim` and `box-guard`: the two box-precedence hooks (more-local-inference C3, C10).
//!
//! `box-claim` runs in a coder loop before a box stage. It cancels the background run
//! on its box through fabro's run API, then waits for strix-infer to report the box
//! idle (C3). It exits 0 on every path, because fabro treats any other hook exit as a
//! block: the claim may delay a box stage by seconds, never stop one (D5, D6).
//!
//! `box-guard` runs in a background package before each of its own agent stages. It
//! cancels its own run when the box's coder loop is in a box stage or the box is busy
//! (C10). Its only non-zero path is C10 step 5's fallback — an unknown run id or a
//! failed self-cancel — because going on would put two sessions on one box.
//!
//! Neither ever prints to stdout: fabro reads a hook's stdout as its decision. The
//! decisions live in pure functions below; [`run_claim`] and [`run_guard`] are the I/O
//! shells around them.

// Rust guideline compliant 2026-07-21

use std::collections::BTreeMap;
use std::process::ExitCode;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::manifest::{BoxMode, Manifest};
use crate::{common, manifest};

/// The claim's whole-command deadline (C3). The hook's timeout is 60 s (C4), so the
/// worst case — one 5 s call starting just before the deadline — still fits.
const CLAIM_DEADLINE: Duration = Duration::from_secs(45);

/// The guard's whole-command deadline (C10), inside the hook's 30 s timeout.
const GUARD_DEADLINE: Duration = Duration::from_secs(20);

/// Every HTTP call's timeout (C3, C10).
const CALL_TIMEOUT: Duration = Duration::from_secs(5);

/// How often the claim re-reads `/health` while waiting (C3 step 4).
const POLL_INTERVAL: Duration = Duration::from_secs(2);

/// The lane label a background run carries (C6).
const BACKGROUND_LANE: &str = "background";

/// The lane label a coder loop carries (C6).
const CODER_LANE: &str = "coder";

/// The box file the `claim` node writes (C2).
const BOX_FILE: &str = "box";

/// The issue contract `claim` writes; the claim reads `.number` from it (C3).
const ISSUE_FILE: &str = "issue.json";

/// The active run statuses (fabro `ACTIVE_BOARD_COLUMNS`), as the query string C3
/// step 3 gives them. The brackets are percent-encoded because that is what works on
/// the live API: a bare `page[limit]` is honoured, and the router decodes `%5B`/`%5D`.
const ACTIVE_RUNS_QUERY: &str = "?status=pending&status=runnable&status=initializing&status=running&status=blocked&page%5Blimit%5D=100";

/// The exit code that blocks a stage (C10 step 5).
const BLOCK: u8 = 2;

/// `box-claim`'s entry point. Exits 0 on every path (C3).
pub fn run_claim() -> ExitCode {
    let deadline = Instant::now() + CLAIM_DEADLINE;
    match claim(deadline) {
        Ok(Some(line)) => eprintln!("{line}"),
        Ok(None) => {}
        Err(message) => eprintln!("box-claim: {message}"),
    }
    ExitCode::SUCCESS
}

/// `box-guard`'s entry point. Exits 2 only where C10 step 5 says to.
pub fn run_guard() -> ExitCode {
    let deadline = Instant::now() + GUARD_DEADLINE;
    match guard(deadline) {
        Ok(report) => {
            eprintln!("{}", report.line);
            if report.blocked {
                ExitCode::from(BLOCK)
            } else {
                ExitCode::SUCCESS
            }
        }
        Err(message) => {
            eprintln!("box-guard: {message}");
            ExitCode::SUCCESS
        }
    }
}

// --- pure decisions -----------------------------------------------------------------

/// The box-plan trial's arm: `(issue_number // 2) % 2 == 1` (`docs/plan-box-trial/`).
pub fn plan_arm_true(issue: u64) -> bool {
    (issue / 2) % 2 == 1
}

/// Does a stage with this `box` value act on `arm`? (C3 step 1, C10 step 2.)
pub fn acts(mode: BoxMode, arm: bool) -> bool {
    match mode {
        BoxMode::Never => false,
        BoxMode::Always => true,
        BoxMode::PlanArm => arm,
    }
}

/// Two consecutive idle readings mean the box is free (C3 step 4).
pub fn confirmed_idle(recent: &[bool]) -> bool {
    recent.len() >= 2 && recent[recent.len() - 1] && recent[recent.len() - 2]
}

/// Parse `FABRO_BOX_HEALTH`: comma-separated `pool=url` pairs (C3).
///
/// Whitespace around a pair, around either side of the `=`, and around a trailing
/// comma are all tolerated; an entry without an `=` or with an empty side is skipped
/// rather than treated as the box. An unknown pool is simply absent from the map.
pub fn parse_box_health(raw: &str) -> BTreeMap<String, String> {
    let mut pairs = BTreeMap::new();
    for entry in raw.split(',') {
        let Some((pool, url)) = entry.split_once('=') else {
            continue;
        };
        let (pool, url) = (pool.trim(), url.trim());
        if pool.is_empty() || url.is_empty() {
            continue;
        }
        pairs.insert(pool.to_string(), url.to_string());
    }
    pairs
}

/// One strix-infer `/health` reading (C3 step 4, C10 step 3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Health {
    /// A request is in flight.
    pub busy: bool,
    /// Requests waiting behind it.
    pub queued: u64,
}

impl Health {
    /// The two fields C3 and C10 name: `busy == false` and `queued == 0`.
    pub fn is_idle(self) -> bool {
        !self.busy && self.queued == 0
    }

    /// Parse a `/health` body. A body without both fields reads as `None`, which both
    /// callers treat as "not idle".
    pub fn parse(raw: &str) -> Option<Self> {
        let body: Value = serde_json::from_str(raw).ok()?;
        Some(Self {
            busy: body.get("busy")?.as_bool()?,
            queued: body.get("queued")?.as_u64()?,
        })
    }
}

/// The runs to cancel for `box_name`: background runs on that box, never the run's
/// own (C3 step 3, C10 step 2).
pub fn lane_runs_on(runs: &[Value], lane: &str, box_name: &str, own: Option<&str>) -> Vec<String> {
    runs.iter()
        .filter(|run| label(run, "lane") == Some(lane))
        .filter(|run| label(run, "box") == Some(box_name))
        .filter_map(|run| run.get("id").and_then(Value::as_str))
        .filter(|id| Some(*id) != own)
        .map(str::to_string)
        .collect()
}

/// A coder loop on the box, reduced to what the guard judges (C10 step 2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CoderRun {
    /// The workflow the run is a version of, from its `workflow` label.
    pub workflow: String,
    /// `labels.issue`, when the run carries one.
    pub issue: Option<u64>,
    /// The stage fabro reports as `running`, or `None` between stages.
    pub running_stage: Option<String>,
}

/// Is this coder run's current stage a box stage (C10 step 2)?
pub fn is_box_stage(manifest: &Manifest, run: &CoderRun) -> bool {
    let Some(stage) = run.running_stage.as_deref() else {
        return false;
    };
    let mode = manifest.coder_stage_mode(&run.workflow, stage);
    acts(mode, run.issue.is_some_and(plan_arm_true))
}

/// C10 step 4: the box is clear when no coder-loop box stage is running and
/// strix-infer reports it idle.
pub fn guard_clear(manifest: &Manifest, coders: &[CoderRun], health: Health) -> bool {
    health.is_idle() && !coders.iter().any(|run| is_box_stage(manifest, run))
}

/// The stage fabro reports as `running`, if any.
pub fn running_stage(stages: &[Value]) -> Option<&str> {
    stages
        .iter()
        .find(|stage| stage.get("status").and_then(Value::as_str) == Some("running"))
        .and_then(|stage| stage.get("node_id"))
        .and_then(Value::as_str)
}

/// A `labels.<key>` value from a run projection.
pub fn label<'a>(run: &'a Value, key: &str) -> Option<&'a str> {
    run.get("labels")?.get(key)?.as_str()
}

// --- the claim ----------------------------------------------------------------------

/// One claim run. `Ok(None)` is every path that returns at once, printing nothing.
fn claim(deadline: Instant) -> Result<Option<String>, String> {
    let Some(node) = nonempty_env("FABRO_NODE_ID") else {
        return Err("FABRO_NODE_ID is not set; nothing to claim".to_string());
    };
    let manifest = manifest::load().map_err(|e| format!("cannot read the manifest ({e})"))?;
    let Some(stage) = manifest.stage(&node) else {
        return Ok(None);
    };
    if !acts(stage.box_mode, read_issue_number().is_some_and(plan_arm_true)) {
        return Ok(None);
    }
    let started = Instant::now();
    let box_name = read_box().ok_or("no /tmp/fabro/box; nothing to claim")?;
    let health_url = health_url(&box_name)?;
    let (own, source) = resolve_run_id();
    let api = Api::from_env(deadline)?;

    let runs = api.get_json(&format!("/runs{ACTIVE_RUNS_QUERY}"))?;
    let active = runs.get("data").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
    let mut cancelled = 0_usize;
    for id in lane_runs_on(active, BACKGROUND_LANE, &box_name, own.as_deref()) {
        api.cancel(&id)?;
        cancelled += 1;
    }

    let idle = wait_for_idle(&health_url, &api, deadline);
    Ok(Some(format!(
        "box-claim: {}/{node} box={box_name} cancelled={cancelled} waited={}s idle={idle} run={}",
        workflow(),
        started.elapsed().as_secs(),
        source.as_str()
    )))
}

/// Poll `/health` every [`POLL_INTERVAL`] until two consecutive idle reads or the
/// deadline (C3 step 4).
fn wait_for_idle(health_url: &str, api: &Api, deadline: Instant) -> bool {
    let mut recent: Vec<bool> = Vec::new();
    loop {
        recent.push(read_health(health_url, api).is_some_and(Health::is_idle));
        if confirmed_idle(&recent) {
            return true;
        }
        // Give up when the next poll would start after the deadline; the command must
        // stay inside its budget rather than overshoot by a whole interval.
        if Instant::now() + POLL_INTERVAL >= deadline {
            return false;
        }
        std::thread::sleep(POLL_INTERVAL);
    }
}

// --- the guard ----------------------------------------------------------------------

/// What the guard did this stage, for its one stderr line (C10).
struct GuardReport {
    line: String,
    blocked: bool,
}

fn guard(deadline: Instant) -> Result<GuardReport, String> {
    let Some(node) = nonempty_env("FABRO_NODE_ID") else {
        return Err("FABRO_NODE_ID is not set; nothing to guard".to_string());
    };
    let manifest = manifest::load().map_err(|e| format!("cannot read the manifest ({e})"))?;
    let box_name = read_box().ok_or("no /tmp/fabro/box; nothing to guard")?;
    let health_url = health_url(&box_name)?;
    let (own, source) = resolve_run_id();
    let api = Api::from_env(deadline)?;

    let runs = api.get_json(&format!("/runs{ACTIVE_RUNS_QUERY}"))?;
    let active = runs.get("data").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
    let mut coders = Vec::new();
    for id in lane_runs_on(active, CODER_LANE, &box_name, own.as_deref()) {
        let stages = match api.get_json(&format!("/runs/{id}/stages")) {
            Ok(body) => body.get("data").and_then(Value::as_array).cloned().unwrap_or_default(),
            Err(e) => {
                // A run that ends between the listing and this read has no stages left
                // to report; that is between stages, not a box stage.
                eprintln!("box-guard: no stages for coder run {id} ({e}); treating it as between stages");
                Vec::new()
            }
        };
        let run = active.iter().find(|run| run.get("id").and_then(Value::as_str) == Some(id.as_str()));
        coders.push(CoderRun {
            workflow: run.and_then(|r| label(r, "workflow")).unwrap_or_default().to_string(),
            issue: run
                .and_then(|r| label(r, "issue"))
                .and_then(|issue| issue.parse::<u64>().ok()),
            running_stage: running_stage(&stages).map(str::to_string),
        });
    }

    let health = read_health(&health_url, &api).ok_or("cannot read the box's /health")?;
    let clear = guard_clear(&manifest, &coders, health);
    let (action, blocked) = if clear {
        ("none", false)
    } else {
        self_cancel(&api, own.as_deref())?
    };
    Ok(GuardReport {
        line: format!(
            "box-guard: {}/{node} box={box_name} clear={clear} action={action} run={}",
            workflow(),
            source.as_str()
        ),
        blocked,
    })
}

/// C10 step 5: cancel the run's own id, or fall back to blocking the stage.
fn self_cancel(api: &Api, own: Option<&str>) -> Result<(&'static str, bool), String> {
    let Some(id) = own else {
        eprintln!("box-guard: not clear and the run's own id is unknown; blocking the stage");
        return Ok(("block", true));
    };
    match api.cancel(id) {
        Ok(()) => Ok(("self-cancel", false)),
        Err(e) => {
            eprintln!("box-guard: not clear and self-cancel failed ({e}); blocking the stage");
            Ok(("block", true))
        }
    }
}

// --- I/O ----------------------------------------------------------------------------

/// The `pool=url` health URL for `box_name`, or a sentence naming what is missing.
fn health_url(box_name: &str) -> Result<String, String> {
    let raw = nonempty_env("FABRO_BOX_HEALTH").ok_or("FABRO_BOX_HEALTH is not set")?;
    parse_box_health(&raw)
        .remove(box_name)
        .ok_or_else(|| format!("FABRO_BOX_HEALTH has no entry for {box_name}"))
}

/// The box from `/tmp/fabro/box` (C2). Empty or missing reads as no box.
fn read_box() -> Option<String> {
    let raw = std::fs::read_to_string(common::io_root().join(BOX_FILE)).ok()?;
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

/// `.number` from `/tmp/fabro/issue.json`, for the `"plan-arm"` rule (C3).
fn read_issue_number() -> Option<u64> {
    let raw = std::fs::read_to_string(common::io_root().join(ISSUE_FILE)).ok()?;
    let body: Value = serde_json::from_str(&raw).ok()?;
    body.get("number")?.as_u64()
}

/// An environment variable's value, or `None` when unset or empty.
pub fn nonempty_env(key: &str) -> Option<String> {
    std::env::var(key)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

/// `FABRO_WORKFLOW`, or an empty string, for the summary lines.
fn workflow() -> String {
    nonempty_env("FABRO_WORKFLOW").unwrap_or_default()
}

/// Where the run's own id came from (C3 inputs).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RunIdSource {
    /// `FABRO_RUN_ID` was set.
    Env,
    /// The `run_id` field of the `FABRO_HOOK_CONTEXT` file.
    HookContext,
    /// Neither was available.
    Unknown,
}

impl RunIdSource {
    /// The token the summary line carries.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Env => "env",
            Self::HookContext => "hook-context",
            Self::Unknown => "unknown",
        }
    }
}

/// The run's own id, and where it was read from: `FABRO_RUN_ID` first, then the
/// `FABRO_HOOK_CONTEXT` file's `run_id`, else unknown (C3). Task 12 reads the source
/// off the hook's stderr to see which of the two a sandbox `stage_start` hook gets.
pub fn resolve_run_id() -> (Option<String>, RunIdSource) {
    if let Some(id) = nonempty_env("FABRO_RUN_ID") {
        return (Some(id), RunIdSource::Env);
    }
    if let Ok(context) = common::hook_context()
        && let Some(id) = context.get("run_id").and_then(Value::as_str)
        && !id.is_empty()
    {
        return (Some(id.to_string()), RunIdSource::HookContext);
    }
    (None, RunIdSource::Unknown)
}

/// One strix-infer `/health` read; `None` on any failure, which reads as not idle.
fn read_health(url: &str, api: &Api) -> Option<Health> {
    let body = api.get_text(url).ok()?;
    Health::parse(&body)
}

/// fabro's API and the boxes' `/health`, over plain `http://` (D23).
struct Api {
    base: String,
    token: Option<String>,
    agent: ureq::Agent,
    deadline: Instant,
}

impl Api {
    /// The API named by `FABRO_API_URL`, with the optional `FABRO_API_TOKEN`.
    fn from_env(deadline: Instant) -> Result<Self, String> {
        let base = nonempty_env("FABRO_API_URL").ok_or("FABRO_API_URL is not set")?;
        let config = ureq::Agent::config_builder()
            .timeout_global(Some(CALL_TIMEOUT))
            .http_status_as_error(false)
            .build();
        Ok(Self {
            base: base.trim_end_matches('/').to_string(),
            token: nonempty_env("FABRO_API_TOKEN"),
            agent: config.into(),
            deadline,
        })
    }

    /// `GET` a path on the API and parse the JSON body. A non-2xx is an error.
    fn get_json(&self, path: &str) -> Result<Value, String> {
        let body = self.get_text(&format!("{}{path}", self.base))?;
        serde_json::from_str(&body).map_err(|e| format!("GET {path} answered invalid JSON ({e})"))
    }

    /// `GET` an absolute URL and return the body. A non-2xx is an error.
    fn get_text(&self, url: &str) -> Result<String, String> {
        self.check_deadline("GET", url)?;
        let request = self.authorize(self.agent.get(url));
        let (status, body) = self.finish("GET", url, request.call())?;
        if !(200..300).contains(&status) {
            return Err(format!("GET {url} answered {status}"));
        }
        Ok(body)
    }

    /// `POST /runs/{id}/cancel`. 200, 202 and 409 all count as done (C3 step 3).
    fn cancel(&self, run_id: &str) -> Result<(), String> {
        let url = format!("{}/runs/{run_id}/cancel", self.base);
        self.check_deadline("POST", &url)?;
        let request = self.authorize(self.agent.post(&url));
        let (status, _) = self.finish("POST", &url, request.send_empty())?;
        match status {
            200 | 202 | 409 => Ok(()),
            other => Err(format!("POST /runs/{run_id}/cancel answered {other}")),
        }
    }

    /// Refuse to start a call past the command's deadline (C3, C10).
    fn check_deadline(&self, method: &str, url: &str) -> Result<(), String> {
        if Instant::now() >= self.deadline {
            return Err(format!("the deadline passed before {method} {url}"));
        }
        Ok(())
    }

    /// Add the bearer token when `FABRO_API_TOKEN` is set.
    fn authorize<B>(&self, request: ureq::RequestBuilder<B>) -> ureq::RequestBuilder<B> {
        match &self.token {
            Some(token) => request.header("Authorization", &format!("Bearer {token}")),
            None => request,
        }
    }

    /// Read a response into `(status, body)`.
    fn finish(
        &self,
        method: &str,
        url: &str,
        response: Result<ureq::http::Response<ureq::Body>, ureq::Error>,
    ) -> Result<(u16, String), String> {
        let response = response.map_err(|e| format!("{method} {url} failed ({e})"))?;
        let status = response.status().as_u16();
        let body = response
            .into_body()
            .read_to_string()
            .map_err(|e| format!("{method} {url} body unreadable ({e})"))?;
        Ok((status, body))
    }
}
