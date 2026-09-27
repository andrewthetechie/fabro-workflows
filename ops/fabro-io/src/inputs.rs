//! The `inputs` tool (C3): return the stage's inputs, paged, and record what was served.
//!
//! The same code backs the MCP tool and the CLI. A page-mode call (`name` and `part`)
//! returns the raw bytes of that page. A batch call walks the ordered inputs, shows each
//! one whose first page fits whole in the rest of the page budget, prints only the header
//! of each one that does not, and ends with one `MORE:` line for every page not yet shown.
//! A page is never cut to fit: what the batch shows of an input is always exactly one of
//! its fixed parts, so what `served.json` records as served is what the model saw.

use std::path::Path;
use std::process::ExitCode;

use crate::{common, manifest, pages, served};

/// The page budget (bytes).
pub const BUDGET: usize = pages::BUDGET;

/// The result of an `inputs` call: the text and whether it must be flagged as an error
/// (`is_error`). Fabro turns an `is_error` tool result into a visible message, so the
/// model can read it and act.
pub struct InputsResult {
    pub text: String,
    pub is_error: bool,
}

/// Build the inputs text for a call. `name`/`part` mirror the MCP `inputs` parameters.
///
/// The read-modify-write of `served.json` runs under an exclusive lock, because fabro
/// runs a turn's tool calls in parallel: without it, two `inputs` calls in one turn each
/// save their own record and the later save drops the earlier call's parts.
pub fn build(name: Option<&str>, part: Option<u32>) -> InputsResult {
    let Some((node, visit)) = common::read_stage() else {
        return err("no stage.json: call fabro-io stage first");
    };
    let m = match manifest::load() {
        Ok(m) => m,
        Err(e) => return err(&format!("cannot read manifest: {e}")),
    };
    let Some(stage) = m.stage(&node) else {
        return err(&format!("no stage '{node}' in the manifest"));
    };
    if let Some(n) = name
        && !stage.inputs.iter().any(|i| i.name == n)
    {
        return err(&format!(
            "no input named '{n}'. This stage's inputs are: {}",
            stage.inputs.iter().map(|i| i.name.as_str()).collect::<Vec<_>>().join(", ")
        ));
    }
    if part.is_some() && name.is_none() {
        return err("part requires a name");
    }

    let _lock = served::lock();
    let mut served_rec = served::load(&visit);
    let result = match (name, part) {
        (Some(n), Some(p)) => page(stage, n, p, &mut served_rec),
        _ => batch(stage, name, &mut served_rec),
    };
    let _ = served::save(&served_rec);
    result
}

/// Batch: walk inputs in manifest order within the page budget (C3).
fn batch(
    stage: &manifest::Stage,
    only: Option<&str>,
    served_rec: &mut served::Served,
) -> InputsResult {
    let mut text = String::new();
    let mut remaining = BUDGET;
    let mut is_error = false;
    let mut more: Vec<String> = Vec::new();

    for input in &stage.inputs {
        if only.is_some_and(|n| n != input.name) {
            continue;
        }
        let block = match std::fs::read(Path::new(&input.path)) {
            Err(_) if input.required => {
                is_error = true;
                mark(served_rec, input, None, &[], "missing");
                format!("MISSING: {} ({})\n", input.name, input.path)
            }
            Err(_) => {
                mark(served_rec, input, None, &[], "absent");
                format!(
                    "=== {}: {} (absent) ===\n(absent: {})\n",
                    input.name,
                    input.path,
                    input.absent.as_deref().unwrap_or("not written")
                )
            }
            Ok(bytes) => {
                let parts = pages::split(&bytes, BUDGET);
                let n = parts.len();
                let head = header(input, &bytes, n);
                let first = format!("{head}{}\n", lossy(&parts[0].bytes));
                // The first block of a result is always shown whole, even when its header
                // pushes it past the budget: otherwise an input whose first page is
                // exactly the budget could never be shown by a batch call at all.
                if first.len() <= remaining || text.is_empty() {
                    mark(served_rec, input, Some(&bytes), &[1], "ok");
                    more.extend((2..=n).map(|k| more_line(&input.name, k)));
                    first
                } else {
                    more.extend((1..=n).map(|k| more_line(&input.name, k)));
                    format!("{head}(not shown: no room left in this result; see MORE below)\n")
                }
            }
        };
        remaining = remaining.saturating_sub(block.len());
        text.push_str(&block);
    }
    if !more.is_empty() {
        text.push_str("\nNot yet shown. Request each of these before you continue:\n");
        for line in more {
            text.push_str(&line);
        }
    }
    InputsResult { text, is_error }
}

/// The header of one present input, its `about` line, and its part count when paged.
fn header(input: &manifest::Input, bytes: &[u8], parts: usize) -> String {
    let mut h = format!(
        "=== {}: {} ({} bytes, {} lines) ===\n",
        input.name,
        input.path,
        bytes.len(),
        line_count(bytes)
    );
    if !input.about.is_empty() {
        h.push_str(&input.about);
        h.push('\n');
    }
    if parts > 1 {
        h.push_str(&format!("part 1 of {parts}\n"));
    }
    h
}

fn more_line(name: &str, part: usize) -> String {
    format!("MORE: inputs(name=\"{name}\", part={part})\n")
}

/// Page mode: return exactly one page's raw bytes (C3, test 5: concatenation is exact).
fn page(
    stage: &manifest::Stage,
    name: &str,
    part: u32,
    served_rec: &mut served::Served,
) -> InputsResult {
    let Some(input) = stage.inputs.iter().find(|i| i.name == name) else {
        return err(&format!("no input named '{name}'"));
    };
    let data = std::fs::read(Path::new(&input.path));
    match data {
        Err(_) if input.required => {
            let line = format!("MISSING: {} ({})\n", input.name, input.path);
            mark(served_rec, input, None, &[], "missing");
            InputsResult { text: line, is_error: true }
        }
        Err(_) => {
            let absent = input.absent.clone().unwrap_or_else(|| "not written".to_string());
            mark(served_rec, input, None, &[], "absent");
            InputsResult { text: format!("(absent: {absent})\n"), is_error: false }
        }
        Ok(bytes) => {
            let parts = pages::split(&bytes, BUDGET);
            let n = parts.len() as u32;
            if part == 0 || part > n {
                return err(&format!("part {part} of {n} is out of range"));
            }
            mark(served_rec, input, Some(&bytes), &[part], "ok");
            InputsResult { text: lossy(&parts[(part - 1) as usize].bytes), is_error: false }
        }
    }
}

fn mark(
    served_rec: &mut served::Served,
    input: &manifest::Input,
    bytes: Option<&[u8]>,
    served_parts: &[u32],
    status: &str,
) {
    let entry = served_rec.inputs.entry(input.name.clone()).or_default();
    entry.path = input.path.clone();
    entry.status = status.to_string();
    if let Some(b) = bytes {
        entry.sha256 = common::sha256_hex(b);
        entry.parts = pages::split(b, BUDGET).len() as u32;
        for p in served_parts {
            if !entry.served.contains(p) {
                entry.served.push(*p);
            }
        }
    } else {
        entry.parts = 0;
        entry.served.clear();
    }
}

fn line_count(bytes: &[u8]) -> usize {
    if bytes.is_empty() {
        return 0;
    }
    let nl = bytes.iter().filter(|&&b| b == b'\n').count();
    if bytes.ends_with(b"\n") {
        nl
    } else {
        nl + 1
    }
}

fn lossy(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

fn err(msg: &str) -> InputsResult {
    InputsResult { text: format!("{msg}\n"), is_error: true }
}

/// CLI entry: `fabro-io inputs [<name> [--part N]]`. Prints the text; exits 3 when a
/// required input is missing (C3).
pub fn run_cli(args: &[String]) -> ExitCode {
    let (mut name, mut part) = (None, None);
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--part" => {
                if i + 1 >= args.len() {
                    eprintln!("inputs: --part needs a number");
                    return ExitCode::from(64);
                }
                part = args[i + 1].parse::<u32>().ok();
                if part.is_none() {
                    eprintln!("inputs: invalid --part '{}'", args[i + 1]);
                    return ExitCode::from(64);
                }
                i += 2;
            }
            a if name.is_none() => {
                name = Some(a.to_string());
                i += 1;
            }
            a => {
                eprintln!("inputs: unexpected argument '{}'", a);
                return ExitCode::from(64);
            }
        }
    }
    let res = build(name.as_deref(), part);
    print!("{}", res.text);
    if res.is_error {
        ExitCode::from(3)
    } else {
        ExitCode::SUCCESS
    }
}
