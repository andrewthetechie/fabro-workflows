//! The git-safe tools: `restore_file` and `baseline_check` (docs/coder-tweaks C4).
//!
//! Agents ran `git stash` and `git checkout --` for two honest jobs: undoing their own
//! change to one file, and finding out whether a failure predates it. Both mutate
//! state the checkpoint commit later records. These tools do the same jobs without
//! touching the agent's index, branch or stash. They run git themselves, which the
//! `git-guard` hook allows: it watches the agent's `shell`, not our tools.
//!
//! The two tools use different base commits: [`restore_base`] is where the agent's own
//! work started, [`baseline_base`] is what that work is compared with.
//!
//! `baseline_check` runs a command in a separate worktree at its base. The shared
//! dependency directories are the delicate part, and the spike in
//! `docs/coder-tweaks/04-git-safe-tools.md` records why they are set up as they are:
//! a plain `uv run` in the worktree re-points the shared virtualenv's editable install
//! at the worktree, so every later import in the agent's own checkout reads the base.

// Rust guideline compliant 2026-07-21

use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use crate::common;

/// The MCP name of the restore tool.
pub const RESTORE_FILE: &str = "restore_file";
/// The MCP name of the baseline tool.
pub const BASELINE_CHECK: &str = "baseline_check";

/// Default `baseline_check` time limit, seconds.
const DEFAULT_TIMEOUT_S: u64 = 120;
/// Largest `baseline_check` time limit, seconds.
///
/// Below the 660 s `tool_timeout` of `[run.agent.mcps.io]` in each `workflow.toml`, so
/// a runaway command returns this module's answer instead of fabro's silent timeout.
const MAX_TIMEOUT_S: u64 = 600;
/// Lines of output `baseline_check` returns, counted from the end.
const TAIL_LINES: usize = 200;
/// How deep below the checkout the dependency directories are searched for.
///
/// Four covers `frontend/node_modules` and `backend/.venv` in the four target
/// repositories, with a level to spare, and keeps the walk off deep source trees.
const SEARCH_DEPTH: usize = 4;

/// The tool names this module serves.
pub fn is_tool(name: &str) -> bool {
    name == RESTORE_FILE || name == BASELINE_CHECK
}

/// `(name, description, input schema)` for each tool this module serves.
pub fn tools() -> Vec<(&'static str, String, serde_json::Value)> {
    vec![
        (
            RESTORE_FILE,
            "Undo your change to one file: restore it to its content before your work \
             started (the task's base commit, or the start of this stage), or delete it \
             if it did not exist then. Use it instead of git checkout or git stash. It \
             never touches the index or other files."
                .to_string(),
            serde_json::json!({
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "A repository-relative file path."}
                },
                "required": ["path"],
                "additionalProperties": false
            }),
        ),
        (
            BASELINE_CHECK,
            "Run a command on the base commit your work is compared with, in a separate \
             worktree, to see whether a failure predates your change. Use it instead of git stash. It never touches \
             your checkout. Returns the exit code and the last 200 lines of output."
                .to_string(),
            serde_json::json!({
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "A shell command, run from the worktree root, for example `uv run pytest tests/test_x.py` or `npx vitest run`."},
                    "timeout_s": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT_S,
                                  "description": "Seconds before the command is killed. Default 120."}
                },
                "required": ["command"],
                "additionalProperties": false
            }),
        ),
    ]
}

/// Runs one of this module's tools.
///
/// # Errors
///
/// Returns a sentence for the model when the arguments are invalid, no checkout or
/// base commit is found, or git fails.
pub async fn call(
    name: &str,
    args: &serde_json::Map<String, serde_json::Value>,
) -> Result<String, String> {
    let checkout = crate::code::checkout()?;
    let root = common::io_root();
    match name {
        RESTORE_FILE => {
            let path = args.get("path").and_then(|v| v.as_str()).unwrap_or("").trim();
            let base = restore_base(&root, &checkout)?;
            restore_file(&checkout, &base, path)
        }
        BASELINE_CHECK => {
            let command = args.get("command").and_then(|v| v.as_str()).unwrap_or("").trim();
            let timeout = args.get("timeout_s").and_then(|v| v.as_u64()).unwrap_or(DEFAULT_TIMEOUT_S);
            let base = baseline_base(&root, &checkout)?;
            baseline_check(&root, &checkout, &base, command, timeout).await
        }
        other => Err(format!("unknown tool '{other}'")),
    }
}

/// The trimmed commit id in `root/name`, when the file holds one.
fn sha_file(root: &Path, name: &str) -> Option<String> {
    let raw = std::fs::read_to_string(root.join(name)).ok()?;
    let sha = raw.trim();
    (!sha.is_empty() && sha.chars().all(|c| c.is_ascii_hexdigit())).then(|| sha.to_string())
}

/// The commit `restore_file` restores to: the start of the agent's own unit of work.
///
/// In backlog's task loop that is `task_base_sha`, so a rework stage can still undo
/// the coder's out-of-scope edit to a file. Once `run_base_sha` exists (backlog's
/// merge phase, which keeps the last task's stale `task_base_sha`, and every pr-review
/// stage) and in workflows with neither file, it is `HEAD`. Agents cannot commit and
/// every stage ends in a checkpoint commit, so `HEAD` is the tree the stage started
/// from. A merge base with `origin/<base_ref>` is never used here: in pr-review it
/// would put the file back to `main` and erase the PR author's change to it.
///
/// # Errors
///
/// Returns a sentence for the model when `HEAD` cannot be read.
pub fn restore_base(root: &Path, checkout: &Path) -> Result<String, String> {
    if !root.join("run_base_sha").exists()
        && let Some(sha) = sha_file(root, "task_base_sha")
    {
        return Ok(sha);
    }
    Ok(git(checkout, &["rev-parse", "HEAD"])?.trim().to_string())
}

/// The commit `baseline_check` runs on: what the agent's work is compared against.
///
/// `run_base_sha` first: the branch point in backlog's merge phase, and the PR head
/// the run claimed in pr-review. Then `task_base_sha` (backlog's task loop). Then the
/// merge base of `HEAD` and `origin/<base_ref>`.
///
/// # Errors
///
/// Returns a sentence for the model when no file is usable.
pub fn baseline_base(root: &Path, checkout: &Path) -> Result<String, String> {
    if let Some(sha) = sha_file(root, "run_base_sha").or_else(|| sha_file(root, "task_base_sha")) {
        return Ok(sha);
    }
    if let Ok(raw) = std::fs::read_to_string(root.join("base_ref")) {
        let base_ref = raw.trim();
        if !base_ref.is_empty() {
            // No run or task base: compare with the branch point on the base branch.
            let out = git(checkout, &["merge-base", "HEAD", &format!("origin/{base_ref}")])?;
            return Ok(out.trim().to_string());
        }
    }
    Err("no base commit is known for this stage (run_base_sha, task_base_sha and base_ref \
         are all missing); note the failure and move on"
        .to_string())
}

/// Runs `git` in `dir` and returns its stdout.
fn git(dir: &Path, args: &[&str]) -> Result<String, String> {
    let out = Command::new("git")
        .args(args)
        .current_dir(dir)
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("cannot run git: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        Err(format!(
            "git {} failed: {}",
            args.first().copied().unwrap_or(""),
            String::from_utf8_lossy(&out.stderr).trim()
        ))
    }
}

/// The checkout-relative form of `path`, or the reason it is refused.
fn relative_path(checkout: &Path, path: &str) -> Result<PathBuf, String> {
    if path.is_empty() {
        return Err("restore_file needs a non-empty `path`".to_string());
    }
    let given = Path::new(path);
    let rel = if given.is_absolute() {
        given
            .strip_prefix(checkout)
            .map_err(|_| format!("{path} is outside the checkout"))?
            .to_path_buf()
    } else {
        given.to_path_buf()
    };
    let mut clean = PathBuf::new();
    for part in rel.components() {
        match part {
            Component::Normal(p) => clean.push(p),
            Component::CurDir => {}
            _ => return Err(format!("{path} is outside the checkout")),
        }
    }
    if clean.as_os_str().is_empty() {
        return Err("restore_file takes one file, not the repository root".to_string());
    }
    if clean.starts_with(".github/workflows") {
        return Err(
            ".github/workflows/ cannot be edited, so it cannot be restored here either".to_string()
        );
    }
    if clean.starts_with(".git") {
        return Err(format!("{path} is inside .git"));
    }
    Ok(clean)
}

/// Restores `path` to its content at `base`, or deletes it if `base` had none.
///
/// # Errors
///
/// Returns a sentence for the model when the path is refused, names a directory, or
/// git or the filesystem fails.
pub fn restore_file(checkout: &Path, base: &str, path: &str) -> Result<String, String> {
    let rel = relative_path(checkout, path)?;
    let rel_str = rel.to_string_lossy().into_owned();
    let target = checkout.join(&rel);
    // A symlinked parent could point outside the checkout.
    if let (Some(parent), Ok(root)) = (target.parent(), checkout.canonicalize()) {
        if let Ok(real) = parent.canonicalize() {
            if !real.starts_with(&root) {
                return Err(format!("{path} is outside the checkout"));
            }
        }
    }
    let short = &base[..base.len().min(7)];
    let spec = format!("{base}:{rel_str}");
    if git(checkout, &["cat-file", "-e", &spec]).is_err() {
        return match std::fs::symlink_metadata(&target) {
            Ok(m) if m.is_file() || m.file_type().is_symlink() => {
                std::fs::remove_file(&target).map_err(|e| format!("cannot delete {rel_str}: {e}"))?;
                Ok(format!("deleted {rel_str}: it did not exist at {short}"))
            }
            Ok(_) => Err(format!("{rel_str} is a directory; restore_file takes one file")),
            Err(_) => Ok(format!("{rel_str} did not exist at {short} and is already absent")),
        };
    }
    if git(checkout, &["cat-file", "-t", &spec])?.trim() != "blob" {
        return Err(format!("{rel_str} is a directory at {short}; restore_file takes one file"));
    }
    let content = Command::new("git")
        .args(["show", &spec])
        .current_dir(checkout)
        .output()
        .map_err(|e| format!("cannot run git: {e}"))?;
    if !content.status.success() {
        return Err(format!("git show failed: {}", String::from_utf8_lossy(&content.stderr).trim()));
    }
    let before = std::fs::read(&target).ok();
    if before.as_deref() == Some(content.stdout.as_slice()) {
        return Ok(format!("{rel_str} already matches {short}; nothing changed"));
    }
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
    }
    std::fs::write(&target, &content.stdout).map_err(|e| format!("cannot write {rel_str}: {e}"))?;
    let tree = git(checkout, &["ls-tree", base, "--", &rel_str])?;
    if tree.starts_with("100755") {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode(0o755));
    }
    Ok(match before {
        Some(_) => format!("restored {rel_str} to its content at {short}"),
        None => format!("restored {rel_str} (it was missing) to its content at {short}"),
    })
}

/// Directories under `dir` (up to [`SEARCH_DEPTH`] deep) that are named `name`.
///
/// Does not descend into a match, `.git`, `node_modules`, `.venv` or `target`.
fn find_dirs(dir: &Path, name: &str) -> Vec<PathBuf> {
    fn walk(dir: &Path, name: &str, depth: usize, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        let mut entries: Vec<_> = entries.filter_map(Result::ok).collect();
        entries.sort_by_key(|e| e.file_name());
        for e in entries {
            let Ok(kind) = e.file_type() else { continue };
            if !kind.is_dir() {
                continue;
            }
            let file_name = e.file_name();
            let fname = file_name.to_string_lossy();
            if fname == name {
                out.push(e.path());
            } else if depth > 0 && !matches!(&*fname, ".git" | "node_modules" | ".venv" | "target") {
                walk(&e.path(), name, depth - 1, out);
            }
        }
    }
    let mut out = Vec::new();
    walk(dir, name, SEARCH_DEPTH, &mut out);
    out
}

/// The environment for a command run in `worktree`, or `None` when this repository has
/// no shared dependencies the worktree could use.
///
/// Links every `node_modules` of the checkout into the same place in the worktree.
/// Points `uv` at the checkout's `.venv` without letting it sync, and puts the
/// worktree's own sources first on `PYTHONPATH`: the editable install in the shared
/// virtualenv names the checkout, and an unsynced `uv run` would otherwise import the
/// agent's code instead of the base. `CARGO_TARGET_DIR` is a separate directory, so the
/// agent's `target/` is never written.
fn environment(root: &Path, checkout: &Path, worktree: &Path) -> Option<Vec<(String, String)>> {
    let mut env: Vec<(String, String)> = Vec::new();
    let mut found = false;

    for nm in find_dirs(checkout, "node_modules") {
        let Ok(rel) = nm.strip_prefix(checkout) else { continue };
        let link = worktree.join(rel);
        if link.symlink_metadata().is_ok() {
            found = true;
            continue;
        }
        if link.parent().is_some_and(Path::exists) && std::os::unix::fs::symlink(&nm, &link).is_ok() {
            found = true;
        }
    }

    if let Some(venv) = find_dirs(checkout, ".venv").into_iter().find(|v| {
        v.parent().is_some_and(|p| p.join("uv.lock").exists() || p.join("pyproject.toml").exists())
    }) {
        found = true;
        let project = venv.parent().unwrap_or(checkout);
        let rel = project.strip_prefix(checkout).unwrap_or(Path::new(""));
        let wt_project = worktree.join(rel);
        let mut paths: Vec<String> = Vec::new();
        for candidate in [wt_project.join("src"), wt_project.clone()] {
            if candidate.is_dir() {
                paths.push(candidate.display().to_string());
            }
        }
        env.push(("UV_PROJECT_ENVIRONMENT".into(), venv.display().to_string()));
        env.push(("UV_NO_SYNC".into(), "1".into()));
        env.push(("UV_FROZEN".into(), "1".into()));
        env.push(("VIRTUAL_ENV".into(), venv.display().to_string()));
        env.push(("PYTHONPATH".into(), paths.join(":")));
        let old_path = std::env::var("PATH").unwrap_or_default();
        env.push(("PATH".into(), format!("{}:{old_path}", venv.join("bin").display())));
    }

    if !found {
        return None;
    }
    if worktree.join("Cargo.toml").exists() {
        env.push(("CARGO_TARGET_DIR".into(), root.join("base-target").display().to_string()));
    }
    Some(env)
}

/// The base worktree, created on first use and reused while it is at `base`.
fn ensure_worktree(root: &Path, checkout: &Path, base: &str) -> Result<PathBuf, String> {
    let wt = root.join("base-tree");
    if wt.exists() {
        let at = git(&wt, &["rev-parse", "HEAD"]).map(|s| s.trim().to_string()).unwrap_or_default();
        if !at.is_empty() && (at.starts_with(base) || base.starts_with(&at)) {
            return Ok(wt);
        }
        let _ = git(checkout, &["worktree", "remove", "--force", &wt.display().to_string()]);
        let _ = std::fs::remove_dir_all(&wt);
        let _ = git(checkout, &["worktree", "prune"]);
    }
    std::fs::create_dir_all(root).map_err(|e| format!("cannot create {}: {e}", root.display()))?;
    git(checkout, &["worktree", "add", "--detach", &wt.display().to_string(), base])?;
    Ok(wt)
}

/// Runs `command` on the task base, in a worktree, and returns the exit code and the
/// last [`TAIL_LINES`] lines of output.
///
/// # Errors
///
/// Returns a sentence for the model when the command is empty, no worktree can be made,
/// or this repository has no shared dependencies to run the command with. A command
/// that fails or exceeds `timeout_s` is an answer, not an error.
pub async fn baseline_check(
    root: &Path,
    checkout: &Path,
    base: &str,
    command: &str,
    timeout_s: u64,
) -> Result<String, String> {
    if command.is_empty() {
        return Err("baseline_check needs a non-empty `command`".to_string());
    }
    let wt = ensure_worktree(root, checkout, base)?;
    let Some(env) = environment(root, checkout, &wt) else {
        return Err("baseline_check is not available in this repository; note the failure and \
                    move on"
            .to_string());
    };
    let limit = Duration::from_secs(timeout_s.clamp(1, MAX_TIMEOUT_S));
    let mut cmd = tokio::process::Command::new("bash");
    cmd.arg("-c")
        .arg(format!("exec 2>&1; {command}"))
        .current_dir(&wt)
        .envs(env)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .kill_on_drop(true)
        .process_group(0);
    let child = cmd.spawn().map_err(|e| format!("cannot run bash: {e}"))?;
    let pid = child.id();
    let (code, bytes, timed_out) = match tokio::time::timeout(limit, child.wait_with_output()).await {
        Ok(Ok(out)) => (
            out.status.code().map_or("killed".to_string(), |c| c.to_string()),
            out.stdout,
            false,
        ),
        Ok(Err(e)) => return Err(format!("baseline_check failed: {e}")),
        Err(_) => {
            // The whole group: the command usually runs children (uv, pytest, node).
            if let Some(pid) = pid {
                let _ = Command::new("kill").args(["-KILL", "--", &format!("-{pid}")]).output();
            }
            (format!("timed out after {}s and was killed", limit.as_secs()), Vec::new(), true)
        }
    };
    let text = String::from_utf8_lossy(&bytes);
    let lines: Vec<&str> = text.lines().collect();
    let cut = lines.len().saturating_sub(TAIL_LINES);
    let mut out = format!("exit={code}\n");
    if cut > 0 {
        out.push_str(&format!("({cut} earlier lines cut)\n"));
    }
    out.push_str(&lines[cut..].join("\n"));
    if timed_out {
        out.push_str("\n(output of a killed command is not kept)");
    }
    Ok(out.trim_end().to_string())
}
