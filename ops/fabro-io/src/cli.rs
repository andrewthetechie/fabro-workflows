//! Subcommand dispatch and exit-code mapping (C3–C5).
//!
//! `stage` 0/2 · `input` 0/3 · `submit` 0/3/4 · `guard` 0/2 · `git-guard` 0/2 · `version` 0 · usage 64.

use std::process::ExitCode;

use crate::{gitguard, guard, inputs, runtests, serve, stage, submit, testenv};

const USAGE: &str = concat!(
    "usage: fabro-io <command> [args]\n",
    "commands: stage | serve [--port N] | guard | git-guard | inputs [<name> [--part N]] | submit --file <path> | test-env [--check] [--root DIR] | run-tests TARGET [args...] [--fresh] | version\n"
);

pub fn run(args: &[String]) -> ExitCode {
    let Some(sub) = args.get(1).map(String::as_str) else {
        eprint!("{USAGE}");
        return ExitCode::from(64);
    };
    let rest = &args[2..];
    match sub {
        "stage" => stage::run(),
        "serve" => serve::run(rest),
        "guard" => guard::run(),
        "git-guard" => gitguard::run(),
        "inputs" => inputs::run_cli(rest),
        "submit" => submit_cli(rest),
        "test-env" => testenv::run_cli(rest),
        "run-tests" => runtests::run_cli(rest),
        "version" => {
            println!("fabro-io {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        "help" | "-h" | "--help" => {
            eprint!("{USAGE}");
            ExitCode::SUCCESS
        }
        other => {
            eprintln!("fabro-io: unknown command '{other}'");
            eprint!("{USAGE}");
            ExitCode::from(64)
        }
    }
}

fn submit_cli(args: &[String]) -> ExitCode {
    let mut file: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--file" => {
                if i + 1 >= args.len() {
                    eprintln!("submit: --file needs a path");
                    return ExitCode::from(64);
                }
                file = Some(args[i + 1].clone());
                i += 2;
            }
            a => {
                eprintln!("submit: unexpected argument '{a}'");
                return ExitCode::from(64);
            }
        }
    }
    let Some(file) = file else {
        eprintln!("submit: --file <path> is required");
        return ExitCode::from(64);
    };
    let raw = match std::fs::read_to_string(&file) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("submit: could not read {file}: {e}");
            return ExitCode::FAILURE;
        }
    };
    let output: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("submit: {file} is not valid JSON: {e}");
            return ExitCode::FAILURE;
        }
    };
    match submit::run(&output) {
        submit::Outcome::Written(msg) => {
            println!("{msg}");
            ExitCode::SUCCESS
        }
        submit::Outcome::NeedInputs(m) | submit::Outcome::Incomplete(m) => {
            eprintln!("{m}");
            ExitCode::from(3)
        }
        submit::Outcome::SchemaViolation(m) => {
            eprint!("{m}");
            ExitCode::from(4)
        }
        submit::Outcome::Error(m) => {
            eprintln!("submit: {m}");
            ExitCode::FAILURE
        }
    }
}
