//! The `inputs` tool (C3): return the stage's inputs, paged, and record what was served.
//!
//! The same code backs the MCP tool and the CLI. A page-mode call (`name` and `part`)
//! returns the raw bytes of that page; a batch call walks the ordered inputs until the
//! page budget is used and emits `MORE:` lines for any input that does not fit.

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
    let mut served_rec = served::load(&visit);

    let result = if part.is_some() && name.is_none() {
        err("part requires a name")
    } else if part.is_some() {
        page(&stage, name.unwrap(), part.unwrap(), &mut served_rec)
    } else {
        batch(&stage, name, &mut served_rec)
    };

    let _ = served::save(&served_rec);
    result
}

/// Batch: walk inputs in manifest order until the budget is used (C3).
fn batch(
    stage: &manifest::Stage,
    only: Option<&str>,
    served_rec: &mut served::Served,
) -> InputsResult {
    let mut text = String::new();
    let mut remaining = BUDGET;
    let mut is_error = false;

    for input in &stage.inputs {
        if let Some(n) = only {
            if n != input.name {
                continue;
            }
        }
        if remaining <= 0 {
            break;
        }
        match serve_one_batch(input, &mut text, &mut remaining, served_rec, &mut is_error) {
            Serve::Continue => {}
            Serve::Stop => break,
        }
    }
    InputsResult { text, is_error }
}

enum Serve {
    Continue,
    Stop,
}

/// Serve one input in batch mode; `Stop` signals the budget is exhausted.
fn serve_one_batch(
    input: &manifest::Input,
    text: &mut String,
    remaining: &mut usize,
    served_rec: &mut served::Served,
    is_error: &mut bool,
) -> Serve {
    let data = std::fs::read(Path::new(&input.path));
    match data {
        Err(_) if input.required => {
            *is_error = true;
            let line = format!("MISSING: {} ({})\n", input.name, input.path);
            text.push_str(&line);
            *remaining = remaining.saturating_sub(line.len());
            mark(served_rec, input, None, &[], "missing");
            Serve::Continue
        }
        Err(_) => {
            let absent = input.absent.clone().unwrap_or_else(|| "not written".to_string());
            let line = format!("(absent: {absent})\n");
            text.push_str(&line);
            *remaining = remaining.saturating_sub(line.len());
            mark(served_rec, input, None, &[], "absent");
            Serve::Continue
        }
        Ok(bytes) => {
            let nbytes = bytes.len();
            let nlines = line_count(&bytes);
            let header = format!("=== {}: {} ({nbytes} bytes, {nlines} lines) ===\n", input.name, input.path);
            let about = if input.about.is_empty() {
                String::new()
            } else {
                format!("{}\n", input.about)
            };
            let parts = pages::split(&bytes, BUDGET);
            if parts.len() == 1 {
                let block = format!("{header}{about}{}\n", lossy(&bytes));
                if block.len() <= *remaining {
                    text.push_str(&block);
                    *remaining = remaining.saturating_sub(block.len());
                    mark(served_rec, input, Some(&bytes), &[1], "ok");
                    Serve::Continue
                } else {
                    // One small file but no room left: header + advice.
                    text.push_str(&format!("{header}{about}MORE: inputs(name=\"{}\", part=1)\n", input.name));
                    *remaining = 0;
                    Serve::Stop
                }
            } else {
                let n = parts.len();
                let prefix = format!("{header}{about}part 1 of {n}\n");
                let available = remaining.saturating_sub(prefix.len()).min(parts[0].bytes.len());
                let show_len = line_min_prefix(&parts[0].bytes, available);
                text.push_str(&prefix);
                text.push_str(&lossy(&parts[0].bytes[..show_len]));
                text.push('\n');
                mark(served_rec, input, Some(&bytes), &[1], "ok");
                for k in 2..=n {
                    text.push_str(&format!("MORE: inputs(name=\"{}\", part={k})\n", input.name));
                }
                *remaining = 0;
                Serve::Stop
            }
        }
    }
}

// (kept intentionally small: no additional state needed beyond `Serve`)

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

/// The longest prefix of `bytes` ending at a line boundary within `max` bytes; if none,
/// a UTF-8 character boundary (a single line longer than the budget).
fn line_min_prefix(bytes: &[u8], max: usize) -> usize {
    if max >= bytes.len() {
        return bytes.len();
    }
    let mut cut = 0;
    for (i, &b) in bytes.iter().take(max).enumerate() {
        if b == b'\n' {
            cut = i + 1;
        }
    }
    if cut == 0 {
        let mut end = max;
        while end > 0 && !crate::pages::char_boundary(bytes, end) {
            end -= 1;
        }
        end
    } else {
        cut
    }
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
