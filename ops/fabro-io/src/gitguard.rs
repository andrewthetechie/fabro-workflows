//! The `git-guard` hook (docs/coder-tweaks C2): a guard against mistakes, not a boundary.
//!
//! Reads `FABRO_HOOK_CONTEXT`. When the tool is `shell` and the stage is not exempt, it
//! scans every `git` invocation in `tool_input.command` and blocks (exit 2,
//! `{"decision":"block","reason":...}`) one that changes files, the index, branches or
//! remotes. The reason names the safe alternative: `restore_file`, `baseline_check`, or
//! "leave it to the checkpoint". It proceeds, with a warning, on any error of its own.
//!
//! The parser is quote aware, so `grep -n 'git stash' f` and `echo "git commit"` pass.
//! It does not look inside `sh -c`, `bash -c` or `python`: those bypass it, as they
//! bypass `io-guard` (ADR 0016 D8). Closing them is not a goal.

// Rust guideline compliant 2026-07-21

use std::process::ExitCode;

use crate::{common, manifest};

/// Read-only subcommands, allowed everywhere (C2).
const READ_ONLY: &[&str] = &[
    "status", "diff", "log", "show", "blame", "grep", "ls-files", "ls-tree", "rev-parse",
    "merge-base", "cat-file", "range-diff", "describe", "shortlog", "rev-list", "diff-tree",
    "name-rev", "for-each-ref", "check-ignore", "version", "help",
];

/// `git branch` flags that only list.
const BRANCH_LISTING: &[&str] = &[
    "-l", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--show-current",
    "--no-color", "--color", "-i", "--ignore-case",
];

/// `git branch` flags that list and take a value.
const BRANCH_VALUE_FLAGS: &[&str] =
    &["--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--abbrev"];

/// Global git options that take their value as the next word.
const GLOBAL_VALUE_OPTS: &[&str] = &["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"];

/// Words that run the next word as a command and are skipped to find it.
const WRAPPERS: &[&str] = &["time", "env", "nice", "nohup", "sudo", "command", "exec"];

/// The `manifest.json` value of `"git"` that exempts a stage.
const WRITE: &str = "write";

/// One blocked `git` invocation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Violation {
    /// The mutating subcommand, for example `stash`.
    pub subcommand: String,
    /// The sentence returned to the agent.
    pub reason: String,
}

/// The `git-guard` subcommand entry point.
pub fn run() -> ExitCode {
    let Ok(raw) = std::env::var("FABRO_HOOK_CONTEXT") else {
        eprintln!("git-guard: warning: FABRO_HOOK_CONTEXT not set; proceeding");
        return ExitCode::SUCCESS;
    };
    let ctx: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("git-guard: warning: could not parse FABRO_HOOK_CONTEXT ({e}); proceeding");
            return ExitCode::SUCCESS;
        }
    };
    let Some(command) = shell_command(&ctx) else {
        return ExitCode::SUCCESS;
    };
    let Some((node, _)) = common::read_stage() else {
        eprintln!("git-guard: warning: no stage.json; proceeding");
        return ExitCode::SUCCESS;
    };
    let m = match manifest::load() {
        Ok(m) => m,
        Err(e) => {
            eprintln!("git-guard: warning: cannot read manifest ({e}); proceeding");
            return ExitCode::SUCCESS;
        }
    };
    if m.stage(&node).is_some_and(|s| s.git.as_deref() == Some(WRITE)) {
        return ExitCode::SUCCESS;
    }
    match violations(&command).into_iter().next() {
        Some(v) => {
            println!("{}", serde_json::json!({"decision": "block", "reason": v.reason}));
            ExitCode::from(2)
        }
        None => ExitCode::SUCCESS,
    }
}

/// The shell command of a `shell` tool call, or `None` for any other tool.
///
/// A context with no tool name but a string `tool_input.command` counts as `shell`, so
/// a renamed field cannot turn the guard off silently.
fn shell_command(ctx: &serde_json::Value) -> Option<String> {
    let name = ctx
        .get("tool_name")
        .or_else(|| ctx.get("tool"))
        .and_then(|v| v.as_str());
    if name.is_some_and(|n| n != "shell") {
        return None;
    }
    ctx.get("tool_input")?.get("command")?.as_str().map(str::to_string)
}

/// Every mutating `git` invocation in `command`, in order.
pub fn violations(command: &str) -> Vec<Violation> {
    let mut found = Vec::new();
    for words in command_words(command) {
        let words = strip_wrappers(words);
        let Some(first) = words.first() else { continue };
        if first.rsplit('/').next() != Some("git") {
            continue;
        }
        if let Some(sub) = mutating_subcommand(&words) {
            found.push(Violation { reason: reason(&sub), subcommand: sub });
        }
    }
    found
}

/// The command words of each segment of `command`.
///
/// Splits at `&&`, `||`, `;`, `|`, `&`, newlines, parentheses, braces, backticks and
/// `$(`, outside quotes. A backslash escapes the next character, which keeps the `\|`
/// of a grep alternation in one word. The body of a here-document is skipped.
fn command_words(command: &str) -> Vec<Vec<String>> {
    let chars: Vec<char> = command.chars().collect();
    let mut segments: Vec<Vec<String>> = vec![Vec::new()];
    let mut word = String::new();
    let mut has_word = false;
    let mut quote: Option<char> = None;
    let mut heredocs: Vec<(String, bool)> = Vec::new();
    let mut i = 0;

    fn end_word(segments: &mut [Vec<String>], word: &mut String, has_word: &mut bool) {
        if *has_word {
            if let Some(seg) = segments.last_mut() {
                seg.push(std::mem::take(word));
            }
        }
        word.clear();
        *has_word = false;
    }
    fn end_segment(segments: &mut Vec<Vec<String>>, word: &mut String, has_word: &mut bool) {
        end_word(segments, word, has_word);
        if segments.last().is_some_and(|s| !s.is_empty()) {
            segments.push(Vec::new());
        }
    }

    while i < chars.len() {
        let c = chars[i];
        if let Some(q) = quote {
            if c == q {
                quote = None;
            } else if c == '\\' && q == '"' && i + 1 < chars.len() {
                word.push(chars[i + 1]);
                i += 1;
            } else {
                word.push(c);
            }
        } else if c == '\'' || c == '"' {
            quote = Some(c);
            has_word = true;
        } else if c == '\\' && i + 1 < chars.len() {
            word.push(chars[i + 1]);
            has_word = true;
            i += 1;
        } else if c == '<' && chars.get(i + 1) == Some(&'<') && chars.get(i + 2) != Some(&'<') {
            end_word(&mut segments, &mut word, &mut has_word);
            let (delimiter, strip_tabs, next) = heredoc_delimiter(&chars, i + 2);
            if !delimiter.is_empty() {
                heredocs.push((delimiter, strip_tabs));
            }
            i = next;
            continue;
        } else if c == '\n' {
            end_segment(&mut segments, &mut word, &mut has_word);
            i = skip_heredocs(&chars, i + 1, &mut heredocs);
            continue;
        } else if matches!(c, ';' | '(' | ')' | '{' | '}' | '`') {
            end_segment(&mut segments, &mut word, &mut has_word);
        } else if c == '$' && chars.get(i + 1) == Some(&'(') {
            end_segment(&mut segments, &mut word, &mut has_word);
            i += 1;
        } else if c == '&' || c == '|' {
            end_segment(&mut segments, &mut word, &mut has_word);
            if chars.get(i + 1) == Some(&c) {
                i += 1;
            }
        } else if c.is_whitespace() {
            end_word(&mut segments, &mut word, &mut has_word);
        } else {
            word.push(c);
            has_word = true;
        }
        i += 1;
    }
    end_word(&mut segments, &mut word, &mut has_word);
    segments.into_iter().filter(|s| !s.is_empty()).collect()
}

/// The here-document delimiter that starts at `from` (just after `<<`), whether the
/// operator was `<<-`, and the index after the delimiter word.
fn heredoc_delimiter(chars: &[char], from: usize) -> (String, bool, usize) {
    let mut i = from;
    let strip_tabs = chars.get(i) == Some(&'-');
    if strip_tabs {
        i += 1;
    }
    while chars.get(i).is_some_and(|c| *c == ' ' || *c == '\t') {
        i += 1;
    }
    let mut delimiter = String::new();
    while let Some(&c) = chars.get(i) {
        if c.is_whitespace() || matches!(c, ';' | '&' | '|' | '(' | ')') {
            break;
        }
        if !matches!(c, '\'' | '"' | '\\') {
            delimiter.push(c);
        }
        i += 1;
    }
    (delimiter, strip_tabs, i)
}

/// Skips the bodies of the pending here-documents, starting at the line at `from`.
/// Returns the index of the first character after the last terminator.
fn skip_heredocs(chars: &[char], from: usize, pending: &mut Vec<(String, bool)>) -> usize {
    let mut i = from;
    for (delimiter, strip_tabs) in pending.drain(..) {
        while i < chars.len() {
            let end = chars[i..].iter().position(|c| *c == '\n').map_or(chars.len(), |p| i + p);
            let line: String = chars[i..end].iter().collect();
            i = (end + 1).min(chars.len());
            let line = if strip_tabs { line.trim_start_matches('\t') } else { line.as_str() };
            if line == delimiter {
                break;
            }
        }
    }
    i
}

/// Drops wrapper words (`env X=1`, `time`, `timeout N`, `xargs -n1`) in front of the command.
fn strip_wrappers(mut words: Vec<String>) -> Vec<String> {
    loop {
        let Some(first) = words.first() else { return words };
        let base = first.rsplit('/').next().unwrap_or(first).to_string();
        let is_assignment = first.split_once('=').is_some_and(|(k, _)| {
            !k.is_empty() && !k.starts_with(|c: char| c.is_ascii_digit()) && k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
        });
        if is_assignment || WRAPPERS.contains(&base.as_str()) {
            words.remove(0);
        } else if base == "timeout" {
            words.remove(0);
            while words.first().is_some_and(|w| w.starts_with('-')) {
                words.remove(0);
            }
            if !words.is_empty() {
                words.remove(0);
            }
        } else if base == "xargs" {
            words.remove(0);
            while words.first().is_some_and(|w| w.starts_with('-')) {
                words.remove(0);
            }
        } else {
            return words;
        }
    }
}

/// The mutating subcommand of `git ...` (the first word is `git`), or `None` when the
/// invocation only reads.
fn mutating_subcommand(words: &[String]) -> Option<String> {
    let mut i = 1;
    while i < words.len() && words[i].starts_with('-') {
        i += if GLOBAL_VALUE_OPTS.contains(&words[i].as_str()) { 2 } else { 1 };
    }
    let sub = words.get(i)?.as_str();
    let args = &words[(i + 1).min(words.len())..];
    let first_arg = args.first().map(String::as_str);
    let read_only = match sub {
        s if READ_ONLY.contains(&s) => true,
        "stash" => matches!(first_arg, Some("list" | "show")),
        "worktree" => first_arg == Some("list"),
        "config" => first_arg.is_some_and(|a| a.starts_with("--get")),
        "branch" => branch_lists(args),
        _ => false,
    };
    if read_only { None } else { Some(sub.to_string()) }
}

/// True when every `git branch` argument only lists.
fn branch_lists(args: &[String]) -> bool {
    let mut i = 0;
    while i < args.len() {
        let a = args[i].as_str();
        if BRANCH_VALUE_FLAGS.contains(&a) {
            i += 2;
            continue;
        }
        if !(BRANCH_LISTING.contains(&a) || a.starts_with("--format") || a.starts_with("--sort") || a.starts_with("--abbrev")) {
            return false;
        }
        i += 1;
    }
    true
}

/// The block reason for `sub`, naming the alternative (C2).
fn reason(sub: &str) -> String {
    match sub {
        "stash" | "checkout" | "restore" | "reset" | "clean" | "switch" => format!(
            "git {sub} is blocked: agents do not change files, the index or branches with git. \
             Use `restore_file` to undo your change to a file; to see whether a failure \
             predates your change, use `baseline_check`."
        ),
        "commit" | "add" | "push" | "rm" | "mv" | "tag" | "cherry-pick" | "revert" | "apply" | "am" => format!(
            "git {sub} is blocked: the checkpoint commits your working tree after this stage. \
             Leave your changes uncommitted."
        ),
        "fetch" | "pull" | "merge" | "rebase" | "remote" | "clone" | "submodule" => format!(
            "git {sub} is blocked: the graph's command nodes own branch and remote state."
        ),
        _ => format!(
            "git {sub} is blocked: it changes repository state, which the checkpoint and the \
             graph's command nodes own. Use `restore_file` to undo your change to a file, or \
             `baseline_check` to test the base commit."
        ),
    }
}
