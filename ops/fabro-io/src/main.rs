//! fabro-io — the Stage I/O MCP server (ADR 0016, docs/stage-io).
//!
//! Task-02 spike: `stage` writes `/tmp/fabro/.io/stage.json` and appends the spike
//! log; `serve` runs the streamable-HTTP MCP server with a `whoami` tool. Task 03
//! fills in `manifest`, `pages`, `inputs`, `submit` and `guard`.

mod common;
mod serve;
mod stage;

use std::process::ExitCode;

const USAGE: &str = "usage: fabro-io <stage|serve|version> ...";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("stage") => stage::run(),
        Some("serve") => serve::run(&args[2..]),
        Some("version") => {
            println!("fabro-io {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(64)
        }
    }
}
