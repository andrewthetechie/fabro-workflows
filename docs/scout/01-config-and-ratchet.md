# Task 01: `fabro-io scout config` and `fabro-io scout ratchet`

Read `00-overview-and-contracts.md` first (D2-D4, D7, D14, D15; contracts C1-C3). Invoke
`/ms-rust` before you write Rust.

## Goal

Two subcommands. `config` turns a repository's `.fabro/scout.toml` into normalised JSON and
never fails on a bad file. `ratchet` runs each declared command and counts one rule's findings
from ruff, eslint or clippy JSON. The fingerprint helpers live here too, because task 02 and
task 03 use them.

## Steps

1. **Module layout.** Create `ops/fabro-io/src/scout/mod.rs` with `pub mod config; pub mod ratchet;`
   (tasks 02 and 03 add `flaky`, `plan`, `text`) and `pub fn run_cli(args: &[String]) -> ExitCode`,
   which dispatches on the first argument: `config`, `ratchet` (task 02 adds `flaky`, task 03
   `plan` and `summary`). An unknown or missing sub-subcommand prints usage to stderr and
   returns exit code 2. Add `pub mod scout;` to `src/lib.rs` in alphabetical order. In
   `src/cli.rs`, add `"scout" => scout::run_cli(rest),` next to `"task-plan"`, and add `scout` to
   the `help` text.
2. **Fingerprints** (C3), in `mod.rs`:
   - `pub fn slug(text: &str) -> String`
   - `pub fn ratchet_fp(tool: &str, rule: &str) -> String`
   - `pub fn flaky_fp(workflow_path: &str, job_name: &str) -> String`
   - `pub fn is_fingerprint(fp: &str) -> bool`, the C3 pattern written by hand (no regex
     crate): the prefix is `ratchet` or `flaky`, then exactly two `:`-separated parts, each
     non-empty and made only of `[a-z0-9_./-]`.
3. **`config.rs`** (C1, C2). `serde` structs with `#[serde(deny_unknown_fields)]` for the
   file, and a separate `Serialize` struct for C2's output. `pub fn load(path: &Path) -> ConfigOut`
   never returns an error: a missing file and an invalid file both become C2's shapes. The
   validation rules, each with its own one-line `error` text:
   - `version` is not 1;
   - `tool` is not one of `ruff`, `eslint`, `clippy`;
   - an empty `rule`, `command`, `enforce_in` or `enforce_how`;
   - `timeout_secs` outside `1..=3000`;
   - a duplicate `(tool, rule)`;
   - more than 10 `[[ratchet]]` entries;
   - `min_flakes` outside `1..=20`, `window_days` outside `1..=30`.

   `filing` is `true` only when the file is valid and says `filing = true`. Defaults:
   `timeout_secs` 1200, `min_flakes` 3, `window_days` 14. `index` is the position in the file.
   CLI: `scout config [--file PATH]`, default `.fabro/scout.toml`, prints the JSON, exits 0.
4. **`ratchet.rs`** (C3).
   - `pub fn parse(tool: &str, rule: &str, stdout: &[u8], root: &Path) -> Result<Vec<Finding>, String>`
     is pure, with one private function per tool. Paths are made relative as C3 says. Use
     `Path::strip_prefix` against `root`, and against `root` canonicalised when that works.
   - `pub fn run(config: &ConfigOut, root: &Path) -> RatchetOut` runs the entries in order.
     Spawn with `std::process::Command::new("sh").arg("-c").arg(cmd)`, `.current_dir(root)`,
     `.process_group(0)` (from `std::os::unix::process::CommandExt`), stdout piped, stdin null,
     stderr inherited (so it lands in the stage log). Read stdout on a thread into a buffer
     capped at 64 MiB. Poll `try_wait` every 100 ms. On the deadline, call
     `nix::sys::signal::killpg(Pid::from_raw(child_pid), Signal::SIGKILL)` and then `wait`.
     Apply C3's budget.
   - CLI: `scout ratchet --config FILE [--root DIR]`. An unreadable or invalid config JSON is
     exit 1. Otherwise prints `RatchetOut` and exits 0, even when every entry errored.
5. **Fixtures**, under `ops/fabro-io/tests/fixtures/scout/`. Write them by hand in each tool's
   real output shape:
   - `ruff.json`: three B006 findings in two files, one at an absolute path under the test
     root, plus one E501 finding (filtered out).
   - `ruff_moved.json`: the same B006 findings, each moved 7 lines down.
   - `eslint.json`: two file results with three `@typescript-eslint/no-floating-promises`
     messages, one `no-console` message, and a result with `errorCount: 0`.
   - `eslint_fatal.json`: one result whose only message has `fatal: true` and `ruleId: null`.
   - `clippy.jsonl`: one `compiler-artifact` line, the same `clippy::needless_collect` finding
     twice (from the `lib` and `test` targets), one finding at `/root/.cargo/registry/...`,
     one `clippy::redundant_clone` finding, a non-JSON line, and a `build-finished` line.
   - `scout_valid.toml`, `scout_minimal.toml` (only `version = 1`), and one invalid file per
     validation rule above (`scout_bad_*.toml`).

## Tests: `ops/fabro-io/tests/scout_ratchet_tests.rs`

Each is one `#[test]`:

- `slug` on `B006`, `@typescript-eslint/no-floating-promises`, `clippy::needless_collect`,
  `UI end-to-end (tauri-pilot)`, `--`, `""` gives C3's examples, and `unknown` for the last two.
- `is_fingerprint` accepts C3's three examples and rejects `ratchet:ruff`, `retro:x:y`,
  `ratchet:ruff:B006` (capital letter) and `ratchet:ruff:b006:extra`.
- **Moved lines are not new (#32):** `ruff.json` and `ruff_moved.json` give the same
  `ratchet_fp`, the same `count` and `files`, and findings that differ only in `line`.
- ruff: `count == 3`, `files == 2`, E501 dropped, the absolute path made relative.
- eslint: three findings, `no-console` dropped. `eslint_fatal.json` gives
  `Err("eslint could not parse ...")`.
- clippy: the duplicate is collapsed, the registry path dropped, `redundant_clone`, the
  non-JSON line and the artifact lines ignored: one finding.
- Garbage stdout (`not json`) for ruff gives the C3 error text.
- `config::load`: missing file, valid, minimal (all defaults), and each `scout_bad_*.toml`
  gives `valid: false`, `filing: false`, `ratchet: []`, and a non-empty `error`.
- `run`, with real `sh`: an entry whose command is `cat <fixture>` gives `status: ok`. An
  entry `exit 1` with no stdout gives the not-valid-JSON error. An entry `sleep 30` with
  `timeout_secs = 1` gives `timed out after 1 s` in under 5 seconds, and the entry after it
  still runs.

## Done when

- `cd ops/fabro-io && cargo test` passes, and `make check` with `CHECK_CARGO=always` passes.
- `cargo run -q -- scout config --file tests/fixtures/scout/scout_valid.toml` prints C2's shape.
- One commit: `feat(fabro-io): scout config and ratchet subcommands (scout 01, #32)`.
