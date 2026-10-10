//! The Test environment (ADR 0018): `.fabro/test.toml`, its fingerprint and stamp, and the
//! preparation that `ci.sh` (through `fabro-io test-env`) and `run_tests` share.
//!
//! Contracts: docs/test-env/00-overview-and-contracts.md C1 to C3. The file belongs to the
//! target repository; nothing here reads the Stage manifest.

// Rust guideline compliant 2026-07-21

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::io::{ErrorKind, Read, Write};
use std::os::unix::process::CommandExt;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::time::{Duration, Instant};

use globset::{GlobBuilder, GlobSetBuilder};
use serde::Deserialize;

use crate::common::{atomic_write, io_root, sha256_hex};

/// The declaration, relative to the checkout root (C1).
pub const TEST_TOML: &str = ".fabro/test.toml";
/// The stamp file under the io root (C3).
pub const STAMP_FILE: &str = "test-env.stamp";
/// The preparation log under the io root. Every preparation truncates it (C2, C4).
pub const LOG_FILE: &str = "test-env.log";

/// The only accepted `version` (C1).
const VERSION: u32 = 1;
/// Largest `timeout` any table may set, in seconds (C1). It is also the total budget one
/// `run_tests` call may spend (C4), so no single phase can outlast the tool's own cap.
const TIMEOUT_MAX_S: u64 = 600;
/// `prepare.timeout` when the key is absent, in seconds (C1).
const PREPARE_TIMEOUT_DEFAULT_S: u64 = 300;
/// `targets.<name>.timeout` when the key is absent, in seconds (C1).
const TARGET_TIMEOUT_DEFAULT_S: u64 = 120;
/// How often the supervisor polls a running preparation step. Short enough that a budget
/// overrun is seen within a fraction of a second; each poll is one `try_wait`.
const POLL_INTERVAL: Duration = Duration::from_millis(50);
/// How long to wait for a step's output reader once the step has exited. A step that starts
/// a daemon holding the pipe must not block the caller forever, so the reader is then detached.
const DRAIN_GRACE: Duration = Duration::from_secs(2);

/// A parsed, validated `.fabro/test.toml` (C1).
#[derive(Debug)]
pub struct TestEnv {
    /// The `[env]` table, applied to every preparation step and target. Sorted by name.
    pub env: BTreeMap<String, String>,
    /// The top-level `path` entries, relative to the checkout root, in order.
    pub path: Vec<PathBuf>,
    /// The `[prepare]` table, or its defaults when the table is absent.
    pub prepare: Prepare,
    /// The `[targets.*]` tables, keyed by name.
    pub targets: BTreeMap<String, Target>,
    /// The exact bytes the file was parsed from; the fingerprint hashes them (C3).
    source: Vec<u8>,
}

/// The `[prepare]` table (C1).
#[derive(Debug)]
pub struct Prepare {
    /// Globs, relative to the checkout root, whose matched files are hashed into the fingerprint.
    pub inputs: Vec<String>,
    /// Budget for the whole preparation, in seconds.
    pub timeout: u64,
    /// Steps, each run as its own `sh -c` from the checkout root, stopping at the first failure.
    pub run: Vec<String>,
}

/// One `[targets.<name>]` table (C1).
#[derive(Debug)]
pub struct Target {
    /// One line naming what the target runs, shown to agents.
    pub about: String,
    /// Directory the command runs in, relative to the checkout root.
    pub cwd: PathBuf,
    /// The command. `run_tests` appends the checked arguments to it.
    pub run: String,
    /// Flags accepted alone, such as `-x`.
    pub flags: Vec<String>,
    /// Flags that take the next argument as their value, such as `-k`.
    pub value_flags: Vec<String>,
    /// Budget for the run, in seconds.
    pub timeout: u64,
}

/// The raw shape of the file. `deny_unknown_fields` on every table makes an unknown key an error.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    version: u32,
    #[serde(default)]
    env: BTreeMap<String, String>,
    #[serde(default)]
    path: Vec<String>,
    prepare: Option<RawPrepare>,
    #[serde(default)]
    targets: BTreeMap<String, RawTarget>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawPrepare {
    #[serde(default)]
    inputs: Vec<String>,
    timeout: Option<u64>,
    #[serde(default)]
    run: Vec<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawTarget {
    about: String,
    cwd: Option<String>,
    run: String,
    #[serde(default)]
    flags: Vec<String>,
    #[serde(default)]
    value_flags: Vec<String>,
    timeout: Option<u64>,
}

impl TestEnv {
    /// Reads and validates `root/.fabro/test.toml`.
    ///
    /// Returns `Ok(None)` when the file does not exist: a repository that declares nothing
    /// needs nothing.
    ///
    /// # Errors
    ///
    /// Returns a sentence naming the key when the file cannot be read or breaks a C1 rule.
    pub fn load(root: &Path) -> Result<Option<TestEnv>, String> {
        let path = root.join(TEST_TOML);
        match std::fs::read(&path) {
            Ok(bytes) => {
                let text = String::from_utf8(bytes)
                    .map_err(|_| format!("{TEST_TOML} is not valid UTF-8"))?;
                parse(&text).map(Some)
            }
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
            Err(e) => Err(format!("cannot read {}: {e}", path.display())),
        }
    }
}

/// Parses and validates the text of a `.fabro/test.toml` (C1).
///
/// # Errors
///
/// Returns a sentence naming the first key that breaks a C1 rule, or the TOML error.
pub fn parse(text: &str) -> Result<TestEnv, String> {
    let raw: Raw = toml::from_str(text).map_err(|e| format!("{TEST_TOML}: {e}"))?;
    validate(raw, text.as_bytes().to_vec())
}

fn validate(raw: Raw, source: Vec<u8>) -> Result<TestEnv, String> {
    if raw.version != VERSION {
        return Err(format!("{TEST_TOML}: version must be {VERSION}, found {}", raw.version));
    }
    for key in raw.env.keys() {
        if key == "PATH" {
            return Err("[env] key `PATH` is not allowed; set the top-level `path` key instead".to_string());
        }
        if !valid_env_key(key) {
            return Err(format!("[env] key `{key}` must match ^[A-Z_][A-Z0-9_]*$"));
        }
    }
    for entry in &raw.path {
        check_relative(entry, "path")?;
    }
    let prepare = match raw.prepare {
        None => Prepare {
            inputs: Vec::new(),
            timeout: PREPARE_TIMEOUT_DEFAULT_S,
            run: Vec::new(),
        },
        Some(p) => {
            for (i, glob) in p.inputs.iter().enumerate() {
                glob_builder(glob)
                    .build()
                    .map_err(|e| format!("prepare.inputs[{i}] `{glob}` is not a valid glob: {e}"))?;
            }
            for (i, step) in p.run.iter().enumerate() {
                if step.trim().is_empty() {
                    return Err(format!("prepare.run[{i}] must not be empty"));
                }
            }
            let timeout = p.timeout.unwrap_or(PREPARE_TIMEOUT_DEFAULT_S);
            check_timeout("prepare.timeout", timeout)?;
            Prepare {
                inputs: p.inputs,
                timeout,
                run: p.run,
            }
        }
    };
    let mut targets = BTreeMap::new();
    for (name, t) in raw.targets {
        if !valid_target_name(&name) {
            return Err(format!("target name `{name}` must match ^[a-z0-9-]+$"));
        }
        if t.about.trim().is_empty() || t.about.contains(['\n', '\r']) {
            return Err(format!("targets.{name}.about must be one non-empty line"));
        }
        if t.run.trim().is_empty() {
            return Err(format!("targets.{name}.run must not be empty"));
        }
        let cwd = t.cwd.unwrap_or_else(|| ".".to_string());
        check_relative(&cwd, &format!("targets.{name}.cwd"))?;
        let timeout = t.timeout.unwrap_or(TARGET_TIMEOUT_DEFAULT_S);
        check_timeout(&format!("targets.{name}.timeout"), timeout)?;
        targets.insert(
            name,
            Target {
                about: t.about,
                cwd: PathBuf::from(cwd),
                run: t.run,
                flags: t.flags,
                value_flags: t.value_flags,
                timeout,
            },
        );
    }
    Ok(TestEnv {
        env: raw.env,
        path: raw.path.into_iter().map(PathBuf::from).collect(),
        prepare,
        targets,
        source,
    })
}

/// A glob with `*` stopping at `/`, so `backend/*.py` means one directory level.
fn glob_builder(pattern: &str) -> GlobBuilder<'_> {
    let mut builder = GlobBuilder::new(pattern);
    builder.literal_separator(true);
    builder
}

fn valid_env_key(key: &str) -> bool {
    let mut chars = key.chars();
    matches!(chars.next(), Some('A'..='Z' | '_'))
        && chars.all(|c| matches!(c, 'A'..='Z' | '0'..='9' | '_'))
}

fn valid_target_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(|c| matches!(c, 'a'..='z' | '0'..='9' | '-'))
}

/// Rejects an absolute path or one that climbs out of the checkout.
fn check_relative(value: &str, key: &str) -> Result<(), String> {
    let path = Path::new(value);
    let climbs = path.components().any(|c| matches!(c, Component::ParentDir));
    if value.is_empty() || path.is_absolute() || climbs {
        return Err(format!(
            "{key} entry `{value}` must be relative to the checkout root and stay inside it"
        ));
    }
    Ok(())
}

fn check_timeout(key: &str, seconds: u64) -> Result<(), String> {
    if (1..=TIMEOUT_MAX_S).contains(&seconds) {
        Ok(())
    } else {
        Err(format!("{key} must be 1 to {TIMEOUT_MAX_S} seconds, found {seconds}"))
    }
}

/// The fingerprint of `env` for the checkout at `root` (C3).
///
/// It covers the absolute root path, the declaration's bytes, and the path and content hash
/// of every file the `prepare.inputs` globs match. Symlinks are neither followed nor hashed,
/// and `.git` is skipped, so no input is read from outside the checkout.
pub fn fingerprint(root: &Path, env: &TestEnv) -> String {
    let root = std::path::absolute(root).unwrap_or_else(|_| root.to_path_buf());
    let mut manifest = String::new();
    let _ = writeln!(manifest, "root {}", root.display());
    let _ = writeln!(manifest, "toml {}", sha256_hex(&env.source));
    for (rel, hash) in input_hashes(&root, &env.prepare.inputs) {
        let _ = writeln!(manifest, "input {rel} {hash}");
    }
    sha256_hex(manifest.as_bytes())
}

/// The sorted map from relative path to content hash of every file the globs match.
fn input_hashes(root: &Path, globs: &[String]) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    if globs.is_empty() {
        return out;
    }
    let mut builder = GlobSetBuilder::new();
    for glob in globs {
        // `parse` compiled every glob before a TestEnv exists, so this cannot fail.
        builder.add(
            glob_builder(glob)
                .build()
                .expect("prepare.inputs were validated by parse"),
        );
    }
    let set = builder.build().expect("prepare.inputs were validated by parse");
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            if entry.file_name() == ".git" {
                continue;
            }
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            if kind.is_symlink() {
                continue;
            }
            let path = entry.path();
            if kind.is_dir() {
                stack.push(path);
                continue;
            }
            let Ok(rel) = path.strip_prefix(root) else {
                continue;
            };
            let rel = rel.to_string_lossy().into_owned();
            if set.is_match(&rel) {
                let hash = match std::fs::read(&path) {
                    Ok(bytes) => sha256_hex(&bytes),
                    Err(e) => format!("unreadable {e}"),
                };
                out.insert(rel, hash);
            }
        }
    }
    out
}

/// What a successful preparation produced.
#[derive(Debug)]
pub struct Prepared {
    /// The fingerprint the stamp now records.
    pub fingerprint: String,
    /// Whole seconds the preparation took.
    pub seconds: u64,
}

/// Runs every `prepare.run` step in order within `budget` in total, then writes the stamp.
///
/// Output goes to stderr and to [`LOG_FILE`], which every call truncates. The first failing
/// step stops the run. The stamp is removed before the first step and again on failure, so
/// a stamp on disk always records a preparation that completed (C3, D4).
///
/// # Errors
///
/// Returns a sentence naming the failing step by index and text, the budget overrun, or an
/// io failure on the log or the stamp.
pub fn prepare(root: &Path, env: &TestEnv, budget: Duration) -> Result<Prepared, String> {
    prepare_with(root, env, budget, &[])
}

/// [`prepare`] with extra variables for every step (docs/test-env C5). The base worktree of
/// `baseline_check` needs the same shared-dependency guard its command runs under, so a
/// plain `uv run` cannot re-point the agent's virtualenv at the base.
///
/// # Errors
///
/// As [`prepare`].
pub fn prepare_with(
    root: &Path,
    env: &TestEnv,
    budget: Duration,
    extra: &[(String, String)],
) -> Result<Prepared, String> {
    let io = io_root();
    std::fs::create_dir_all(&io).map_err(|e| format!("cannot create {}: {e}", io.display()))?;
    let stamp = io.join(STAMP_FILE);
    remove_stamp(&stamp)?;
    let log_path = io.join(LOG_FILE);
    let log = std::fs::File::create(&log_path)
        .map_err(|e| format!("cannot create {}: {e}", log_path.display()))?;
    let fp = fingerprint(root, env);
    let vars = command_env(root, env, extra);
    let started = Instant::now();
    let deadline = started + budget;
    let result = env.prepare.run.iter().enumerate().try_for_each(|(i, step)| {
        run_step(root, &vars, i, step, deadline, &log)
    });
    if let Err(message) = result {
        return Err(match remove_stamp(&stamp) {
            Ok(()) => message,
            Err(stale) => format!("{message}; {stale}"),
        });
    }
    let seconds = started.elapsed().as_secs();
    let body = serde_json::json!({
        "fingerprint": fp,
        "root": root.display().to_string(),
        "prepared_at": chrono::Utc::now().to_rfc3339(),
        "seconds": seconds,
    });
    atomic_write(&stamp, body.to_string().as_bytes())
        .map_err(|e| format!("cannot write {}: {e}", stamp.display()))?;
    Ok(Prepared {
        fingerprint: fp,
        seconds,
    })
}

fn remove_stamp(path: &Path) -> Result<(), String> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("cannot remove {}: {e}", path.display())),
    }
}

/// Runs one `prepare.run` step as `sh -c` in its own process group, teeing its output.
fn run_step(
    root: &Path,
    vars: &[(String, String)],
    index: usize,
    step: &str,
    deadline: Instant,
    log: &std::fs::File,
) -> Result<(), String> {
    let label = format!("prepare.run[{index}] `{step}`");
    let log_path = io_root().join(LOG_FILE);
    if Instant::now() >= deadline {
        return Err(format!("{label} was not started: the preparation budget is spent"));
    }
    let (mut reader, writer) =
        std::io::pipe().map_err(|e| format!("cannot make a pipe for {label}: {e}"))?;
    let mut child = {
        let mut cmd = Command::new("sh");
        cmd.args(["-c", step])
            .current_dir(root)
            .envs(vars.iter().map(|(k, v)| (k.as_str(), v.as_str())))
            .stdin(Stdio::null())
            .stdout(writer.try_clone().map_err(|e| format!("cannot clone pipe: {e}"))?)
            .stderr(writer)
            .process_group(0);
        cmd.spawn()
            .map_err(|e| format!("{label} could not start: {e}"))?
    };
    let mut log = log
        .try_clone()
        .map_err(|e| format!("cannot share {}: {e}", log_path.display()))?;
    let (done_tx, done_rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut buf = [0u8; 8192];
        let mut stderr = std::io::stderr();
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let _ = log.write_all(&buf[..n]);
                    let _ = stderr.write_all(&buf[..n]);
                }
                Err(e) if e.kind() == ErrorKind::Interrupted => {}
                Err(_) => break,
            }
        }
        let _ = done_tx.send(());
    });
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {}
            Err(e) => return Err(format!("{label} could not be waited on: {e}")),
        }
        if Instant::now() >= deadline {
            kill_group(child.id());
            let _ = child.wait();
            return Err(format!(
                "{label} ran past the preparation budget and was killed; see {}",
                log_path.display()
            ));
        }
        std::thread::sleep(POLL_INTERVAL);
    };
    let _ = done_rx.recv_timeout(DRAIN_GRACE);
    if !status.success() {
        let how = status
            .code()
            .map_or_else(|| "a signal".to_string(), |c| format!("exit code {c}"));
        return Err(format!("{label} failed with {how}; see {}", log_path.display()));
    }
    Ok(())
}

/// Kills the process group `pid` leads. The step runs in its own group, so this takes the
/// children it started (uv, pytest, node) with it.
fn kill_group(pid: u32) {
    let _ = Command::new("kill")
        .args(["-KILL", "--", &format!("-{pid}")])
        .output();
}

/// The `PATH` for preparation and targets: the `path` entries under `root`, in order, then
/// the caller's `PATH` (C1).
pub fn path_value(root: &Path, env: &TestEnv) -> String {
    path_over(root, env, &std::env::var("PATH").unwrap_or_default())
}

/// The `path` entries under `root`, in order, then `base`. An empty `base` adds nothing.
fn path_over(root: &Path, env: &TestEnv, base: &str) -> String {
    let mut entries: Vec<String> = env
        .path
        .iter()
        .map(|p| root.join(p).display().to_string())
        .collect();
    if !base.is_empty() {
        entries.push(base.to_string());
    }
    entries.join(":")
}

/// The variables every preparation step and target runs with (C1, C2): `[env]`, then `extra`,
/// then `PATH`. An `extra` `PATH` replaces the caller's `PATH` as the base the `path` entries
/// are prepended to. Sorted by name.
pub fn command_env(root: &Path, env: &TestEnv, extra: &[(String, String)]) -> Vec<(String, String)> {
    let mut vars: BTreeMap<String, String> = env.env.clone();
    let mut base = std::env::var("PATH").unwrap_or_default();
    for (key, value) in extra {
        if key == "PATH" {
            base = value.clone();
        } else {
            vars.insert(key.clone(), value.clone());
        }
    }
    vars.insert("PATH".to_string(), path_over(root, env, &base));
    vars.into_iter().collect()
}

/// The fingerprint recorded in the stamp, or `None` when there is no stamp (C3).
pub fn stamp_fingerprint() -> Option<String> {
    let raw = std::fs::read_to_string(io_root().join(STAMP_FILE)).ok()?;
    let v: serde_json::Value = serde_json::from_str(&raw).ok()?;
    v.get("fingerprint")?.as_str().map(str::to_string)
}

/// The preparation log's path under the io root (C2).
pub fn log_path() -> PathBuf {
    io_root().join(LOG_FILE)
}

/// The `export` lines `ci.sh` evaluates (C2): each `[env]` key in name order, then `PATH`.
pub fn exports(root: &Path, env: &TestEnv) -> String {
    let mut out = String::new();
    for (key, value) in &env.env {
        let _ = writeln!(out, "export {key}={}", shell_quote(value));
    }
    let _ = writeln!(out, "export PATH={}", shell_quote(&path_value(root, env)));
    out
}

/// Single-quotes `value` for `sh`, escaping each embedded quote as `'\''`.
pub fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', r"'\''"))
}

/// The `fabro-io test-env` subcommand (C2): `test-env [--check] [--root DIR]`.
pub fn run_cli(args: &[String]) -> ExitCode {
    let mut check = false;
    let mut root: Option<PathBuf> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--check" => check = true,
            "--root" => {
                i += 1;
                let Some(dir) = args.get(i) else {
                    eprintln!("test-env: --root needs a directory");
                    return ExitCode::from(64);
                };
                root = Some(PathBuf::from(dir));
            }
            other => {
                eprintln!("test-env: unexpected argument '{other}'");
                return ExitCode::from(64);
            }
        }
        i += 1;
    }
    let root = match root.map_or_else(checkout_root, Ok) {
        Ok(root) => root,
        Err(message) => {
            eprintln!("test-env: {message}");
            return ExitCode::from(64);
        }
    };
    let env = match TestEnv::load(&root) {
        Ok(Some(env)) => env,
        Ok(None) => return ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("test-env: {message}");
            return ExitCode::FAILURE;
        }
    };
    if check {
        return ExitCode::SUCCESS;
    }
    match prepare(&root, &env, Duration::from_secs(env.prepare.timeout)) {
        Ok(_) => {
            print!("{}", exports(&root, &env));
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("test-env: {message}");
            ExitCode::FAILURE
        }
    }
}

/// The top level of the git checkout the current directory is in.
pub fn checkout_root() -> Result<PathBuf, String> {
    let out = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .map_err(|e| format!("cannot run git: {e}"))?;
    if !out.status.success() {
        return Err("not inside a git checkout; pass --root DIR".to_string());
    }
    Ok(PathBuf::from(String::from_utf8_lossy(&out.stdout).trim()))
}
