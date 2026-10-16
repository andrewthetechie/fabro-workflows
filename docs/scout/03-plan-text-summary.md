# Task 03: `fabro-io scout plan` and `fabro-io scout summary`

Read `00-overview-and-contracts.md` first (D6, D8-D10, D17, D18; contracts C5-C8). Invoke
`/ms-rust` before you write Rust.

## Goal

The plan decides, for every candidate, what the run would do, and writes each issue's title
and body. The summary turns the plan, plus the graph's write log, into the one Discord line.
Both are pure.

## Steps

1. Create `ops/fabro-io/src/scout/plan.rs` and `text.rs`, add both to `scout/mod.rs`, and add
   `plan` and `summary` to `run_cli`. The constants live in `plan.rs`:
   `MAX_FINDINGS = 15`, `MAX_FILES = 8`, `PER_RUN_CAP = 3`, `MAX_OPEN = 3`.
2. **Markers** (`plan.rs`): `pub fn markers(body: &str) -> Vec<String>` finds every
   `<!-- fabro:scout fp=` and reads up to the next ` -->`, keeping only results that pass
   `is_fingerprint`. Then
   `pub fn known(issues: &[Issue]) -> BTreeMap<String, Vec<&Issue>>`, each list highest
   number first.
3. **`pub fn plan(input: &PlanInput) -> PlanOut`**, following C5's numbered steps exactly and
   in D18's order. Keep a per-source counter of planned `file` + `regression`, and check
   backpressure (step 5) before the cap (step 6). Note what steps 1-2 mean: a `known` or
   `suppressed` fingerprint is reported even when the source is already over the cap.
4. **`text.rs`**: `ratchet_title`, `ratchet_body`, `flaky_title`, `flaky_body`, and
   `clean_log(raw: &str) -> String` (C7's four cleaning steps, in order). Follow C6 and C7's
   templates line for line, blank lines included. `SHA8` is the first 8 characters of
   `--sha`. `<basename of workflow>` is the part after the last `/`. Pluralise `finding`,
   `file` and `flake` only by count (`1 finding`, `2 findings`). Enforce the 60000-character
   body limit by dropping log tails oldest first.
5. **`summary`** (C8) in `text.rs`: `pub fn summary(plan: &PlanOut, filed: Option<&[Filed]>) -> String`.
   Follow C8's parts order and its empty rule. Strip `"` and `\` last.
6. CLI:
   - `scout plan --config F --ratchet F --flaky F --issues F --logs-dir D --repo O/N --sha S`.
     Any missing or invalid input file is exit 1. A missing log file is not an error.
     `--repo` must contain exactly one `/`, and `--sha` must be at least 8 hex characters,
     or exit 2. Prints `PlanOut`.
   - `scout summary --plan F [--filed F]` prints the line with no trailing newline, and
     prints nothing for an empty line.
7. Bump `ops/fabro-io/Cargo.toml` to `version = "0.6.0"` and run `cargo build` so that
   `Cargo.lock` follows. Do **not** change `min_binary` in `.fabro/workflows/_io/manifest.json`:
   scout has no agent stage, and raising it would stop every agent stage until the images are
   rebuilt (`invariants-graph.md`, Stage I/O).

## Fixtures: `ops/fabro-io/tests/fixtures/scout/plan/`

Small hand-written JSON in the C2-C4 output shapes, and `issues.json` in the `--issues`
shape. Build each test's input in the test itself, by loading a base fixture and editing it,
rather than keeping one file per case.

## Tests: `ops/fabro-io/tests/scout_plan_tests.rs`

One `#[test]` per line:

- **Moved finding stays known (#32):** an open issue whose body has
  `<!-- fabro:scout fp=ratchet:ruff:b006 -->`, plus a ratchet entry for B006 whose findings
  sit at new lines, gives `known` and no `file`.
- Closed `not_planned`, and closed `duplicate`, give `suppressed`.
- Closed `completed` gives `regression`, `issue` set, and a title starting
  `scout (regression of #`.
- Flaky regression: an issue closed `completed` at `2026-10-10T00:00:00Z`, with occurrences
  on 10-05, 10-08, 10-11 and 10-12 and `min_flakes = 3`, gives **nothing**, because only 2
  occurrences come after the close. With a third one on 10-13, it gives `regression`, and the
  body lists only the 3 later occurrences.
- `count = 16` gives `too_big`, and so does `files = 9`. `count = 15, files = 8` gives `file`.
- Five new ratchet rules and no open issues: three `file`, then two `capped`.
- Two open `ratchet:` issues and two new rules: one `file`, then one `backpressure`. An open
  `flaky:` issue does not count against ratchet.
- A `known` fingerprint after the cap is still `known`, not `capped`.
- Ratchet `status: skipped` gives `sources.ratchet == "skipped: setup.sh failed (exit 1)"`
  and no ratchet actions. An invalid config gives `skipped: scout.toml is invalid: ...`.
- A ratchet `error` entry gives an `error` action whose `detail` is the error.
- Order: ratchet actions come before flaky ones, ratchet in declaration order, flaky by count.
- Labels: a ratchet `file` has the five D10 labels, including `fast-path`. A flaky `file` has
  four, without `fast-path`.
- Body: the first line is the marker. A ratchet body lists every finding and contains
  `ENFORCE_IN` and `ENFORCE_HOW`. A flaky body with no log files says `No log was available.`
- `clean_log`: the timestamp prefix is removed, ANSI codes are removed, ```` ```` ```` becomes
  `'''`, `@user` becomes `@` U+200B `user`, and 500 lines become at most 60 lines and 3000
  characters.
- A body over 60000 characters drops log tails until it fits.
- A marker with an invalid fingerprint (`fp=Bad Key`) is ignored.
- `summary`: dry with one `file` gives `scout jelly-swipe DRY RUN: would file 1 | would file ratchet ruff B006 (2 findings in 1 file)`.
  Live with `filed` gives `filed #12 ...`. Only `known` and `suppressed` actions, live, give
  the empty string. A dry `too_big` alone gives a non-empty line, and the same plan live gives
  an empty one. `raced` and `failed` entries appear. A 3000-character input ends with
  `...and N more` and is at most 1500 characters. No `"` and no backslash survive.

## Done when

- `cargo test` passes, and `make check` with `CHECK_CARGO=always` passes.
- One commit: `feat(fabro-io): scout plan, issue text and summary; 0.6.0 (scout 03, #32)`.
