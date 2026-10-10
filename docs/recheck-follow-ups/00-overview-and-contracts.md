# Recheck follow-ups: overview and canonical contracts

**Status:** designed 2026-10-10. Tasks 01-08 are applied, and task 09's deploys ran on
2026-10-10 at `466848b`. Task 09's live checks (except `autofix`, verified) and its recheck,
and task 10, wait for runs. ADRs 0019, 0020 and 0021 are proposed.
The measurements taken while planning are in `measure-2026-10-10.txt`.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. **Before you edit a `.fabro` file, a prompt or a `workflow.toml`, invoke the
`/fabro-workflow` skill** and read `docs/agents/invariants-graph.md` and the area file for
the part you change.

## What this is

Five open `enhancement` issues came out of the coder-tweaks rechecks of 2026-10-08 and
2026-10-09 (`docs/coder-tweaks/result-2026-10-08.txt`, `result-2026-10-09.txt`) and out of
issue #7. This series implements all five:

| Issue | What | ADR | Tasks |
|---|---|---|---|
| #3 | `fabro-agent-tools.py`: finish the M1 pipe-filter and M3 byte forms, add a per-repo line, record the new-form baseline | none | 01 |
| #14 | `fabro-export-runs.sh` and `--outcomes`, so a recheck needs no scratch script | 0020 | 02, 03 |
| #13 | Block reason, `autofix` and the graph SHA in a run's events and labels | 0020 | 04, 05 |
| #12 | `check-graph-invariants.py` for the mechanical invariants, in `make check` | 0019 | 06, 07 |
| #5 | Decompose a new shared abstraction skeleton first | 0021 | 08, 10 |

Task 09 deploys the `ops/` changes and runs the first recheck with the new tools.

## Operator decisions

These were drafted on 2026-10-10 from the issues, the code and the measurements, and the
operator confirmed all of them the same day.

| # | Decision |
|---|---|
| D1 | **One series, three ADRs.** #12 is ADR 0019. #13 and #14 are one decision, ADR 0020: a run records its own outcome, and rechecks read only that record. #5 is ADR 0021. #3 corrects a metric and needs no ADR. |
| D2 | **The checker reads the graphs with its own stdlib DOT reader** and runs offline in `make check`. `make check-host` compares the reader with `fabro parse` on all six `.fabro` files (ADR 0019). |
| D3 | **Rule exceptions live in the checker,** each with its reason and source: agent and human nodes may exceed `stall_timeout`, and `pr-review` hooks only `report_merged` (operator decision 13 in its `workflow.toml`). No exception is written in a graph. |
| D4 | **The breaker floor is 20, and it applies to every root graph that imports a phase.** `issue-triage` gets `loop_restart_signature_limit=20`. Its triage phase has gate-to-agent retry loops, and the breaker counts run-wide. |
| D5 | **Today's violations are fixed, not allowed.** The `#` comment lines in five scripts move to `//` comments above their nodes. The line continuation in `watch_checks` becomes one line. The `\n` in a triage comment is reworded. Backlog's `discord-rescue` matcher becomes `^human_rescue$`. |
| D6 | **The routing-schema rule moves into the offline checker.** `ops/check-routing-schemas.py` stays in `make check-host` until the parity gate has passed once, and is then deleted. |
| D7 | **One key, `merge_block_reason`, for every stop without a merge:** `report_blocked`, `mark_needs_human` and `pr-review`'s `entry_failed`. `review_merge_outcome` already says which kind of stop it was. |
| D8 | **`autofix` publishes `autofix_ran` and `autofix_rc`.** The issue asks for the first. The exit code costs one more key and shows a `fix.sh` that fails every time. |
| D9 | **`workflow_sha` is the commit SHA** of the `fabro-workflows` tree a client registered, not a `.fabro/` tree hash. It is set by the scheduler, `fabro-fire-backlog.sh` and `fire-pr-review.sh`. |
| D10 | **The export runs on the Mac over ssh** and writes event files only. `fabro dump` is not part of a recheck (35.5 MB for one run). It is recorded in `docs/coder-tweaks/handoff.md` as the way to read one run's command output. |
| D11 | **Skeleton first covers the ports too** (ADR 0021). A port uses the core's interface unchanged. The triage `plan` counts skeleton and bodies as one Task map entry, so the Child-issue threshold does not move. |
| D12 | **The old metric forms stay until the first recheck that uses this series' tools is recorded.** Task 09 removes them after that result file is committed. |
| D13 | **Out of scope:** the Discord message carrying `merge_block_reason` (a later change to `discord-notify.sh`), the PATH wrapper around `grep`/`rg` (coder-tweaks D6), and the M1 target. |

## Verified facts (sources)

Each fact was checked on 2026-10-10 against HEAD `23c3f03`, the live API or the host.

- **Most of #3 is already in the script.** `b229455` added the pipe-filter M1 form, M3 by bytes
  and whole reads of 12 KB or more (`ops/fabro-agent-tools.py`, `commands_piped`,
  `split_segments_piped`), with unit tests in `test_stdin_filters`. Missing: a per-repo line
  for M1-M3, event fixtures for the four cases #3 names, the new-form baseline note, and the
  C7 table in `docs/coder-tweaks/00-overview-and-contracts.md`.
- **The script reproduces the old baseline.** On the 20 baseline runs, the old forms match
  `baseline-2026-10-02.txt` except shell grep 796 (793 recorded), which
  `result-2026-10-08.txt` already explains. New forms on the same runs: M1 0.5% without pipe
  filters (213 segments read a pipe), M3 71.8% by bytes, whole reads of 12 KB or more 230 =
  1.25 per visit and 25,384 bytes per visit. (`result-2026-10-08.txt`'s 1.32 and 26.7 KB
  counted coder and improve visits only.)
- **The runs API.** `GET /api/v1/runs` returns 20 runs unless paged with `page[limit]` and
  `page[offset]`. It lists newest first. `meta` is `{"has_more", "total"}`.
  `filter[workflow]` is ignored. Each row has `id`, `workflow.slug`, `repository.name`,
  `labels`, `lifecycle.status.kind`, `timestamps.created_at` and `timestamps.completed_at`,
  and `automation` (null for API runs). The token is `/storage/server.dev-token` in the
  container (`docs/coder-tweaks/handoff.md`).
- **`docker compose exec -T` reads the caller's stdin.** In a `while read` loop it consumes
  the rest of the list, so every call needs `</dev/null` (issue #14).
- **Context keys are in the events.** `stage.completed.properties.context_updates` holds
  every key a node published, for example `merge_eligible`. `run.created.properties.labels`
  holds the run labels.
- **API runs have no graph SHA.** Automation runs carry
  `automation.workflow_source.resolved_sha`. Scheduler runs carry only
  `{"source", "issue"}` labels. The scheduler knows the SHA at dispatch
  (`FabroClient.ensure_version` returns `tree.sha`), and `build_run_intent` already takes a
  `labels` mapping (`ops/scheduler/src/fabro_scheduler/fabro.py`). Both fire scripts clone the
  tree with `git clone --depth 1`.
- **The two report nodes publish nothing but the outcome file.** `report_blocked` and
  `mark_needs_human` have no `output_schema`. They echo the reason and `gh` output to stdout
  (`_shared/review-merge/review-merge.fabro`). `pr-review`'s `entry_failed` is the same.
- **fabro scans stdout and stderr together** for brace-balanced objects when a node declares
  `output_schema="routing"`. Text with braces next to the routing object can make an invalid
  candidate (`docs/agents/invariants-backlog.md`, the `rescue_brief` row).
- **`autofix`'s edges** are `succeeded -> prep_review`, `failed -> prep_review` and an
  unconditional edge to `rescue_brief` (`backlog/workflow.fabro`). It has no `output_schema`.
- **`fabro parse` output shape:** `{"name", "statements": [...]}`, where a statement is
  `{"GraphAttr": [[key, {"Str"|"Ident": value}], ...]}`, `{"Node": {"id", "attrs"}}` or
  `{"Edge": {"nodes": [id, ...], "attrs"}}`. An edge chain is one `Edge` statement.
- **A stdlib reader agrees with fabro on the counts.** After splicing imports, the scratch
  reader gives 69/162, 34/73, 16/36 and 24/54 (`measure-2026-10-10.txt`).
- **The decomposition prompts.** `decompose.md.j2` already says "a task that introduces a new
  module and migrates every call site to it is two tasks". `plan.md.j2` repeats the size
  budget and splits an issue whose Task map has more than 4 tasks into 2 or 3 Child issues.
  `improve.md.j2`'s `split` returns 2 to 4 slices, never splits a slice, and has a budget of
  2 splits per run.
- **`hygiene` does not count stubs.** Its erosion patterns match unlinked `TODO`/`FIXME`
  comments, not `todo!()` or `NotImplementedError` (`_shared/review-merge/review-merge.fabro`).

## Contracts

### C1. Metric forms (#3)

- `report()` keeps every line it prints today. It adds one block after M3:
  `By repo (M1 without pipe filters; M2 bytes per visit; M3 whole share by count, by bytes, whole reads >= 12 KB per visit)`,
  then one line per repository in sorted order:
  `    <repo>: M1 x.x% (n/m); M2 N; M3 a.a% / b.b% / c.cc`. The repository comes from
  `run.created` (`repo_of`, as M-env does). There is no `--repo-map` option.
- `ops/tests/fixtures/agent-tools/forms-a.jsonl` and `forms-b.jsonl` are hand-written runs in
  two repositories, with no real issue text. Between them they hold a local `coder` session
  with: `cargo test 2>&1 | grep "test result"` (a pipe filter, out of the new M1),
  `rg -n Foo src || grep -rn Foo src` (two searches, both counted), a whole `read_file` of a
  2 KB file and a whole `read_file` of a 20 KB file.
- `docs/coder-tweaks/baseline-2026-10-02-forms.txt` records the new forms on the 20 baseline
  runs (the numbers in *Verified facts*) and the command that produced them.
- The C7 table in `docs/coder-tweaks/00-overview-and-contracts.md` cites both forms: M1
  "0.4% (0.5% without pipe filters)", M3 "42.9% by count, 71.8% by bytes, 1.25 whole reads of
  12 KB or more per visit".

### C2. `ops/fabro-export-runs.sh` (#14)

```
fabro-export-runs.sh --since ISO [--until ISO] [--workflow NAME|all] [--status terminal|all] [OUTDIR]
```

- bash, `set -euo pipefail`, needs `jq`, `ssh` and nothing else on the Mac. `HOST` defaults to
  `andrew@10.10.0.32`. `SSH` defaults to `ssh`, so a test can replace it with a stub.
- `--workflow` defaults to `backlog`. `--status terminal`, the default, keeps runs whose
  `timestamps.completed_at` is not null.
- **Listing.** It reads the token on the host, inside the remote command, never on the Mac.
  It pages `GET /runs?page[limit]=100&page[offset]=N` until `meta.has_more` is false or a page's
  oldest `created_at` is before `--since`. It filters by creation time (`--since` inclusive,
  `--until` exclusive), `workflow.slug` and status on the client.
- **Export.** For each run it writes `OUTDIR/<id>.jsonl` from
  `docker compose exec -T fabro fabro events <id> --json`, with stdin from `/dev/null`. A
  file that exists and is not empty is kept (a rerun resumes). A failure writes
  `OUTDIR/<id>.err` and the export goes on to the next run. The exit is 1 when any run failed.
- **Index.** `OUTDIR/index.tsv`, also printed to stdout, with a header line and the columns
  `id`, `created`, `workflow`, `repo`, `status`, `issue`, `workflow_sha`. `issue` is
  `labels.issue`, else `labels.pr`, else `-`. `workflow_sha` is `labels.workflow_sha`, else
  `automation.workflow_source.resolved_sha`, else `-`.
- **Output directory.** The default is `${TMPDIR:-/tmp}/fabro-runs-<since, digits only>`. The
  script refuses an `OUTDIR` inside a git work tree, because event files hold issue text.

### C3. `ops/fabro-agent-tools.py --outcomes` (#14)

- `--outcomes` adds two sections after the metrics. `--prs` is valid only with `--outcomes`.
- **Per-run table**, one row per run in creation order: id (first 10 characters), repo,
  created (UTC, to the minute), minutes from `run.created` to `run.completed`, `workflow_sha`
  (first 12, or `-`), the terminal node, `refute_verdict`, `merge_eligible`,
  `autofix_ran` as `ran/visits` (or `-` before task 04), validate failures, rework visits as
  `t1/t2/t3/t4`, and failovers as `from>to xN`.
  - The terminal node is the last node reached among `report_merged`, `report_blocked`,
    `mark_needs_human`, `mark_stuck`, `close_noop`, `entry_failed` and `human_rescue`, with any
    import prefix removed. A run with none shows its `run.completed` status.
  - A key's value is the last value that `stage.completed.properties.context_updates` gave it.
  - Validate failures are `edge.selected` events from `validate` to `rework_router`.
- **Block reasons:** one line per run with `merge_block_reason`, printed in full below the
  table. With `--prs`, a run without the key but with a `pr_url` key gets the line starting
  `**Reason:**` from the newest PR comment that has one (`gh pr view <url> --json comments`).
  `gh` errors print `?` and never fail the report.
- **Set-level:** entries to `rework_router` by source node, and the 10 most common failure
  signatures over `stage.failed`, `agent.error`, `agent.route.failover` and `agent.warning`.
  A signature is the event name, the node id without its import prefix, and the first 80
  characters of the message with digits replaced by `N`.

### C4. Outcome keys (#13)

- **`merge_block_reason`**, published by `report_blocked` (from `/tmp/fabro/merge_block_reason`),
  and by `mark_needs_human` and `entry_failed` (from `/tmp/fabro/needs_human_reason`). Each
  node uses the fallback text it already has when the file is missing. The value has newlines,
  carriage returns and tabs replaced by spaces, and is cut to its first 300 bytes.
- **`autofix_ran`**: `true` when `./.fabro/fix.sh` exists and was started, `false` when it does
  not exist. **`autofix_rc`**: the exit code of `fix.sh`, or `-1` when it did not run.
- **Output rule for these four nodes.** Each declares `output_schema="routing"` and builds the
  object with `jq -nc --arg`. The last line on stdout is the routing object, and no other line
  on stdout or stderr contains `{` or `}`. `gh` output, its warnings and `fix.sh` output go to
  `/tmp/fabro/report.log` or `/tmp/fabro/autofix.log`. `autofix` prints the last 40 lines of
  its log, and `git status --short`, both through `tr -d '{}'`, before the object.
- Routing does not change: each node keeps its edges, and no edge reads the new keys.

### C5. The `workflow_sha` label (#13)

- Every client that calls `POST /runs` adds `"workflow_sha": "<40-hex SHA>"` to
  `args.labels`. The value is `git rev-parse HEAD` of the clone it registered.
- The scheduler passes `labels={"workflow_sha": sha}` to `build_run_intent` in
  `FabroClient.dispatch`, and its dispatch log line prints `sha=<first 12>` next to
  `version=`.
- `fabro-fire-backlog.sh` and `fire-pr-review.sh` add the key in their jq payloads. With
  `DRY_RUN=1` the printed payload shows it.

### C6. `ops/check-graph-invariants.py` (#12)

- python3.11, stdlib only, like `ops/check-agent-profiles.py`. Usage:
  `check-graph-invariants.py [--root DIR]` checks every package under `DIR/.fabro/workflows/`
  (a directory with a `workflow.toml`) and every `_shared/*/*.fabro`.
  `check-graph-invariants.py --dump FILE.fabro` prints the reader's statements in
  `fabro parse`'s shape, with every value as a string. It prints one line per violation,
  `<package> <rule> <node or hook> <why>`, then `VIOLATIONS: N`, and exits with
  `min(N, 125)`.
- **Reader.** It tokenizes quoted strings (the only escape is `\"`), `//`, `/* */` and `#`
  comments outside strings, identifiers `[A-Za-z0-9_.-]+`, and `{ } [ ] = ; , ->`. It reads
  `digraph NAME {`, `graph [...]`, `key=value` at graph level, node statements and edge
  chains. It refuses `subgraph`, `node [...]`, `edge [...]`, ports and HTML strings with a
  violation, so a new construct fails closed.
- **Splice.** For an import node `X [import="../_shared/P/P.fabro"]`, the running node ids are
  `X.<id>` for every node of `P` except its `Mdiamond` and `Msquare` nodes. Counted this way,
  today's four packages give 69/162, 34/73, 16/36 and 24/54, and a test asserts it.
- **Rules.** Each rule's docstring names its row in `docs/agents/invariants-*.md`.

| Id | Rule | Exceptions (in the checker, with the source) |
|---|---|---|
| R1 | A root graph that imports a phase, or has a cycle through a command node, sets `loop_restart_signature_limit` to at least 20. | none |
| R2 | A root graph's `stall_timeout` is greater than the `timeout` of every command node in it, spliced nodes included. | Agent and human nodes. An agent emits an event per stream delta, and the watchdog parks during a human wait. |
| R3 | Every class used by a node of `_shared/review-merge/` or `_shared/triage/` has its own rule in the `model_stylesheet` of every graph that imports that phase. | none |
| R4 | Stylesheet class selectors match `[a-z0-9-]+`. Every `{{ inputs.X \| default(...) }}` quotes with `'`, and `X` is not a key of that package's `[run.inputs]`. | none |
| R5 | Every hook `matcher` starts with `^` or `(^\|[.])` and ends with `$`, and it matches at least one running node id, or it is one of `^agent$` and `^shell$` (a handler type and a tool name). | none |
| R6 | No line inside a `script=` attribute starts with `#` after its indentation. No backslash other than `\"` appears anywhere in a `.fabro` file, comments included. | none |
| R7 | Each importing graph has a `checkpoint_saved` hook for each review-merge report node it must report. `backlog` needs `report_merged`, `report_blocked` and `mark_needs_human`. `pr-review` needs `report_merged`. | `pr-review` omits two, by operator decision 13 in its `workflow.toml`. |
| R8 | A command node whose script contains `context_updates` declares `output_schema="routing"`, and a node that declares it prints `context_updates`. | none (moved from `check-routing-schemas.py`) |

- **Fixtures:** `ops/tests/fixtures/graph-invariants/ok/` is a minimal package and phase that
  pass every rule. `ops/tests/fixtures/graph-invariants/r<N>/` breaks exactly rule N.
  `ops/tests/test_check_graph_invariants.py` asserts that `ok` has no violation, that each
  `r<N>` reports R<N> and nothing else, and that the real tree has no violation once task 07 is
  merged.
- **Parity** (in `ops/check-host.sh`): for each of the six `.fabro` files, the normalized
  `fabro parse` JSON equals the `--dump` JSON. Normalizing turns `{"Str": v}` and
  `{"Ident": v}` into `v`. Any difference fails `make check-host`.

## Tasks

| # | File | What | Live on push | Deploy |
|---|---|---|---|---|
| 01 | `01-metric-forms.md` | C1 | — | none |
| 02 | `02-export-runs.md` | C2 | — | none (it runs on the Mac) |
| 03 | `03-outcomes.md` | C3, and the docs that point at the tools | — | none |
| 04 | `04-outcome-keys.md` | C4 | yes (graph) | none |
| 05 | `05-workflow-sha.md` | C5 | `fire-pr-review.sh` only (the `~/bin` wrapper runs `origin/main`'s copy) | `make deploy-scheduler`, `make deploy-scripts` |
| 06 | `06-invariants-checker.md` | C6 reader, rules, fixtures, parity | — | none |
| 07 | `07-invariants-green.md` | Fix today's violations, wire R1-R8 into `make check`, shorten the rows | yes (graph) | none |
| 08 | `08-skeleton-first.md` | ADR 0021 in three prompts | yes (prompts) | none |
| 09 | `09-deploy-and-recheck.md` | Deploy 05, verify 04 and 05 live, run the first recheck with 02 and 03 | — | runs the deploys |
| 10 | `10-measure-skeleton-first.md` | ADR 0021's acceptance over 10+ writers-app architecture runs | — | none |

Order: 01, 02, 04, 05, 06 and 08 are independent. 03 needs 01 (the same file) and 02 (its
input). 07 needs 06. 09 needs 02, 03, 04, 05 and 07. 10 needs 08 and 03, and waits for runs.

## Rules every task follows

1. Run `make check` before every push. A task that changes a command node's shell adds
   fixtures to `ops/test-task-gates.sh` and runs the full `make check`, not `check-fast`.
2. A task that changes a graph runs `make check-host` and states the node and edge counts.
   None of these tasks changes a count. A changed count is a defect.
3. Every new or changed command-node script is POSIX `sh`, with `\"` as its only backslash
   and no `#` comments inside `script=`.
4. A pushed graph is live on the next fire. A pushed `ops/` change is live only after the
   `make deploy` step it needs. Say which applies in the commit message.
5. Event files and dumps hold issue text. Never commit them. Write measurements to a dated
   file in this folder or in `docs/coder-tweaks/`, and never edit an earlier result file.
