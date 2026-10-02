//! The `code_*` tools: the Code index served as MCP tools (ADR 0014, docs/code-context).
//!
//! Each tool runs one verb of the `fabro-code` wrapper in the repository checkout and
//! returns what it prints. The wrapper stays the only reader of the index (ADR 0014
//! D1); this module only puts it in the tool list next to `read_file`. A local model
//! picks a listed tool over a shell command it was told about: on 2026-10-01 the
//! `improve` and `coder` stages of two backlog runs made zero `fabro-code` calls
//! between them and read about 120 KB of whole files instead.
//!
//! The server runs with cwd `/workspace`, while the checkout is `/workspace/<repo>`,
//! and the wrapper reads `.codegraph/` relative to its cwd, so the checkout is found
//! here (see [`find_checkout`]).

// Rust guideline compliant 2026-07-21

use std::path::{Path, PathBuf};
use std::time::Duration;

/// How long one wrapper call may run.
///
/// Below the 30 s `tool_timeout` of `[run.agent.mcps.io]` in each `workflow.toml`, so
/// a slow call returns this module's own error instead of fabro's silent tool
/// timeout (ADR 0016: the server's absence is silent in fabro). A healthy call takes
/// about 0.3 s, including the wrapper's `codegraph sync`.
const CALL_TIMEOUT: Duration = Duration::from_secs(25);

/// One `code_*` tool and the wrapper verb it runs.
#[derive(Debug)]
pub struct Verb {
    /// The MCP tool name.
    pub tool: &'static str,
    /// The `fabro-code` verb.
    pub verb: &'static str,
    /// The tool description shown to the model.
    pub description: &'static str,
    /// True when the verb takes a list of paths (`tests`), false for one `name`.
    pub paths: bool,
}

/// Said in every description: what the index cannot see, from ADR 0014 D1.
const LIMITS: &str = "Answers include your own edits. The index knows calls and imports \
    only, matched by name, so confirm with grep -rnw before you conclude a name is unused.";

/// Every `code_*` tool, in the order they are listed.
pub const VERBS: &[Verb] = &[
    Verb {
        tool: "code_def",
        verb: "def",
        description: "Where a symbol is defined: path:line, kind and signature. Takes an \
            exact name. Use it before grep or glob to find a definition.",
        paths: false,
    },
    Verb {
        tool: "code_show",
        verb: "show",
        description: "A symbol's source, what it calls and what calls it. Takes an exact \
            name, or path:line for the narrowest symbol that holds the line. Use it instead \
            of read_file on a large file.",
        paths: false,
    },
    Verb {
        tool: "code_callers",
        verb: "callers",
        description: "Every call site of a symbol (path:line and the enclosing symbol). \
            Check it before you change or remove a signature.",
        paths: false,
    },
    Verb {
        tool: "code_callees",
        verb: "callees",
        description: "What a symbol calls (path:line of each callee's definition).",
        paths: false,
    },
    Verb {
        tool: "code_impact",
        verb: "impact",
        description: "Every symbol that reaches this one within two call or import edges: \
            the blast radius of a change.",
        paths: false,
    },
    Verb {
        tool: "code_tests",
        verb: "tests",
        description: "Test files that import the given files, directly or through up to \
            four more imports. Takes repository-relative paths.",
        paths: true,
    },
];

/// The verb served under `tool`, if it is a `code_*` tool.
pub fn verb(tool: &str) -> Option<&'static Verb> {
    VERBS.iter().find(|v| v.tool == tool)
}

/// The full description of `verb`: its own text, then [`LIMITS`].
pub fn description(verb: &Verb) -> String {
    format!("{} {LIMITS}", verb.description)
}

/// The JSON input schema of `verb`.
pub fn schema(verb: &Verb) -> serde_json::Value {
    if verb.paths {
        serde_json::json!({
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                          "description": "Repository-relative file paths."}
            },
            "required": ["paths"],
            "additionalProperties": false
        })
    } else {
        serde_json::json!({
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "An exact symbol name. code_show also takes path:line."}
            },
            "required": ["name"],
            "additionalProperties": false
        })
    }
}

/// The repository checkout under `dir`.
///
/// Returns `dir` itself when it holds `.git`. Otherwise returns its one child that
/// holds `.git`. When several children do, it returns the one child that also holds
/// `.codegraph`.
///
/// # Errors
///
/// Returns a sentence for the model when no checkout, or more than one, is found.
pub fn find_checkout(dir: &Path) -> Result<PathBuf, String> {
    if dir.join(".git").exists() {
        return Ok(dir.to_path_buf());
    }
    let mut repos: Vec<PathBuf> = std::fs::read_dir(dir)
        .map_err(|e| format!("cannot list {}: {e}; use fabro-code in the shell", dir.display()))?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.join(".git").exists())
        .collect();
    if repos.len() > 1 {
        repos.retain(|p| p.join(".codegraph").exists());
    }
    match repos.len() {
        1 => Ok(repos.remove(0)),
        0 => Err(format!(
            "no repository checkout under {}; use fabro-code in the shell",
            dir.display()
        )),
        _ => Err(format!(
            "more than one repository checkout under {}; use fabro-code in the shell",
            dir.display()
        )),
    }
}

/// The checkout the tools run in: `FABRO_CODE_ROOT` if set, else found from the cwd.
fn checkout() -> Result<PathBuf, String> {
    if let Ok(root) = std::env::var("FABRO_CODE_ROOT") {
        return Ok(PathBuf::from(root));
    }
    let cwd = std::env::current_dir().map_err(|e| format!("no working directory: {e}"))?;
    find_checkout(&cwd)
}

/// The wrapper arguments for `verb` from the tool call's `args`.
///
/// # Errors
///
/// Returns a sentence for the model when a required argument is missing, empty, or
/// starts with `-`.
pub fn arguments(
    verb: &Verb,
    args: &serde_json::Map<String, serde_json::Value>,
) -> Result<Vec<String>, String> {
    let values: Vec<String> = if verb.paths {
        args.get("paths")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|p| p.as_str()).map(str::to_string).collect())
            .unwrap_or_default()
    } else {
        args.get("name")
            .and_then(|v| v.as_str())
            .map(|n| vec![n.trim().to_string()])
            .unwrap_or_default()
    };
    let what = if verb.paths { "paths" } else { "name" };
    if values.is_empty() || values.iter().any(String::is_empty) {
        return Err(format!("{} needs a non-empty `{what}`", verb.tool));
    }
    if values.iter().any(|v| v.starts_with('-')) {
        return Err(format!("{}: `{what}` cannot start with '-'", verb.tool));
    }
    Ok(values)
}

/// Runs `verb` through the wrapper with `args` and returns its answer.
///
/// The wrapper binary is `fabro-code`, or `FABRO_CODE_BIN` when set (tests). Its exit
/// 1 ("nothing found", "ambiguous") is an answer, so it is returned as `Ok`. Exit 2
/// (no index) and every other failure are `Err`, worded so the model falls back to
/// grep.
///
/// # Errors
///
/// Returns a sentence for the model when the arguments are invalid, no checkout is
/// found, the wrapper cannot start or exceeds [`CALL_TIMEOUT`], or it exits with a
/// status other than 0 or 1.
pub async fn run(
    verb: &Verb,
    args: &serde_json::Map<String, serde_json::Value>,
) -> Result<String, String> {
    let values = arguments(verb, args)?;
    let root = checkout()?;
    let bin = std::env::var("FABRO_CODE_BIN").unwrap_or_else(|_| "fabro-code".to_string());
    let child = tokio::process::Command::new(&bin)
        .arg(verb.verb)
        .args(&values)
        .current_dir(&root)
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true)
        .output();
    let out = match tokio::time::timeout(CALL_TIMEOUT, child).await {
        Err(_) => {
            return Err(format!(
                "{} timed out after {}s; use grep",
                verb.tool,
                CALL_TIMEOUT.as_secs()
            ));
        }
        Ok(Err(e)) => return Err(format!("cannot run {bin}: {e}; use grep")),
        Ok(Ok(out)) => out,
    };
    let mut text = String::from_utf8_lossy(&out.stdout).trim_end().to_string();
    let stderr = String::from_utf8_lossy(&out.stderr);
    if !stderr.trim().is_empty() {
        if !text.is_empty() {
            text.push('\n');
        }
        text.push_str(stderr.trim_end());
    }
    match out.status.code() {
        Some(0 | 1) => Ok(text),
        Some(code) => Err(format!("{} exited {code}: {text}", verb.tool)),
        None => Err(format!("{} was killed: {text}", verb.tool)),
    }
}
