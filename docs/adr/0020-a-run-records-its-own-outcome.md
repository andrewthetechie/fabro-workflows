# A run records its own outcome and graph version, and rechecks read only that record

**Status:** proposed (2026-10-10). It becomes accepted when one recheck lists, from
`fabro events --json` and `GET /runs` alone, each run's block reason, whether `autofix` ran,
and the `main` SHA the run was dispatched on. The plan is `docs/recheck-follow-ups/`, tasks
02 to 05 and 09, and issues #13 and #14.

The coder-tweaks measurement (task 08) has been rechecked three times. Each time, the
session rebuilt the same pipeline from scratch, and part of the answer was not in the run
record at all (`docs/coder-tweaks/result-2026-10-09.txt`):

- **Block reason.** `merge_gate` and the report nodes write it to
  `/tmp/fabro/merge_block_reason` and `needs_human_reason` in the sandbox, and post it in the
  PR comment. It never reaches the run context, so the recheck scraped 16 PR comments
  through `gh`.
- **Command output.** It is a `blob://sha256/...` reference in the events. `autofix`
  printed "no .fabro/fix.sh in this repository" in every visit for weeks, and nothing in the
  events showed it.
- **Graph version.** A run that the scheduler or a fire script creates through `POST /runs`
  has no `resolved_sha`, because only automation runs get one. Which graph a run used was
  worked out from dispatch times and commit times.

We decided that **a run publishes the facts a recheck needs as context keys and run labels**,
and that **the recheck tools read only events and `GET /runs`**:

1. The terminal nodes that stop without a merge publish `merge_block_reason`: the merge
   phase's `report_blocked` and `mark_needs_human`, and `pr-review`'s `entry_failed`. The
   value is one line of at most 300 bytes, the same text the PR comment carries.
2. `autofix` publishes `autofix_ran`: `true` when `.fabro/fix.sh` exists and ran, `false`
   otherwise.
3. Every client that creates a run through `POST /runs` adds the label `workflow_sha`: the
   commit SHA of the `fabro-workflows` tree it registered. Those clients are the scheduler,
   `fabro-fire-backlog.sh` and `fire-pr-review.sh`. Automation runs already carry
   `resolved_sha`.
4. `ops/fabro-export-runs.sh` exports a window of runs, and `ops/fabro-agent-tools.py
   --outcomes` reports them. A recheck is those two commands and no scratch script.

A context key is the right carrier because fabro already records it.
`stage.completed.properties.context_updates` is in the events today, and
`merge_eligible=false` is visible there. A run label is the right carrier for the SHA because
the scheduler already sets labels (`source`, `issue`), and `run.created` copies them into the
events.

## The rule a publishing node must follow

A command node that prints `context_updates` must declare `output_schema="routing"`
(`docs/agents/invariants-graph.md`). fabro then scans stdout and stderr together for
brace-balanced objects. A reason text, a `gh` error or a formatter's output can contain
braces and corrupt the one object (the `rescue_brief` rule in
`docs/agents/invariants-backlog.md`). So each of these nodes:

- builds the object with `jq -nc --arg`, never with string concatenation,
- sends `gh` output and `fix.sh` output to a log file under `/tmp/fabro/`, and prints at most
  a tail with `{` and `}` removed,
- prints exactly one routing object.

A schema failure on these nodes does not change routing. `report_blocked`,
`mark_needs_human` and `entry_failed` have only an unconditional edge. `autofix` sends both
`succeeded` and `failed` to `prep_review`. A defect in the new output therefore loses the key
and nothing else.

## Considered options

- **`fabro dump` for every run in a recheck.** It resolves command output to
  `stages/<n>-<node>@<visit>/output.log`. Rejected as the default because one run's dump was
  35.5 MB, and the facts the recheck needs are a few bytes. It stays the tool for reading one
  run's command output, recorded in `docs/coder-tweaks/handoff.md`.
- **Read the reason from the PR comment** (`--prs`). Kept only as a fallback for runs that
  were dispatched before the key existed. It needs `gh`, network access, and a comment format
  that can change.
- **A `.fabro/` tree hash instead of the commit SHA.** Rejected: the commit SHA is what an
  operator compares with `git log`, and the scheduler's version cache already uses it as its
  key.
- **Change fabro so API runs record `resolved_sha`.** Rejected: we do not fork fabro
  (coder-tweaks D4), and a label needs no fabro change. All three clients already clone the
  tree they register, so `git rev-parse HEAD` gives the SHA.

## Consequences

- `docs/agents/invariants-merge.md` gains a row: a merge-phase terminal node publishes
  `merge_block_reason` and prints only its routing object. `ops/test-task-gates.sh` covers a
  reason that holds braces, quotes and a newline.
- `discord-notify.sh` could now put the reason into the Discord message. That is a separate
  change, and it is a deploy (`make deploy-notify`). This ADR does not make it.
- A run dispatched before task 05's deploy has no `workflow_sha`. `--outcomes` prints `-` for
  it, and the dispatch-time method in `result-2026-10-09.txt` still applies.
- The export writes event files that hold issue text. Its default output directory is outside
  the repository, and the script refuses a directory inside a git work tree.
