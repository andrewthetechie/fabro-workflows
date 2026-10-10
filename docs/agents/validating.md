# Validating: why each gate exists

The commands are in `AGENTS.md`, *Validating*. This is the reasoning behind them, moved verbatim on 2026-10-09.

There is no `fabro` binary on this Mac, and the host CLI resolves `@prompts`
differently from the server, so graphs are validated in the container.

The node and edge baselines are in `AGENTS.md`, *Validating* (the one place to update them). They date from 2026-10-02, against **fabro 0.362.0-nightly.0**. Since the previous count, ADR 0013 added four merge-phase nodes; ADR 0015 added `plan`, `plan_gate` and `apply_split`; the `rework_t1 -> rework_router` escalation edge added one backlog edge; `excerpts` added one backlog node and a net three edges; `drop_followup` added one backlog node and three edges, and `decompose_gate -> mark_stuck` one more; `rescue_brief` and `partial_remainder` added two backlog nodes and three edges; and ADR 0011 added `autofix` and `file_remainder`.

`IssueTriage` and `ArchReview` both include the 13 nodes of `_shared/triage/`. Backlog and PrReview both include the ~25
nodes of `_shared/review-merge/`, which `fabro validate` splices in; `fabro parse`
shows the unexpanded placeholder instead, so node counts only match after validate.

The two warnings are deliberate. `issue_number` unbound in `claim` is draft 10's fail-closed input, the same shape as `pr_number`. Binding
`[run.inputs] pr_number` would silence it and let a run fired with no input review PR
#1 instead of failing at admission. (`auto_merge` is another unbound `{{ inputs.* }}` in the
same node, and it is *not* silenced by being supplied at fire time — `pr_number` is
supplied that way too and still warns. fabro emits one undefined-input diagnostic per
node **attribute**, and both references live in `validate_input`'s `script`, so the
second is folded into the first. Prove it exists by validating a scratch copy with
`pr_number` literalised: it then warns about `auto_merge`.)

`fabro validate` does NOT catch a routing-schema mismatch on a command node, in
either direction, and both fail only at runtime. `ops/check-graph-invariants.py` (R8)
checks it offline, in `make check`: a node that declares `output_schema="routing"` and
prints no routing object fails deterministically with no retry, which cost run
`01M2R057XAWN8ZG0A7ZV7YPJXK` at `pr_handoff` with the PR already open.

`ops/check-graph-invariants.py` (ADR 0019) reads the six `.fabro` files with its own stdlib reader, splices the imports and checks R1-R8: the breaker floor, the stall and timeout durations, the stylesheet classes, the hook matchers, the script and backslash rules, the report hooks and the routing schema. It runs offline in `make check`. `make check-host` proves that its reader agrees with `fabro parse` on the six graphs. It does not run a script, judge a prompt, or check the rows of the invariants files that carry no `Checked by` line; those still need a run or a review.

The Agent profile (ADR 0017) is checked the same way, against the tracked template — and
in `make verify-host`, against the live overlay.

The Stage manifest (ADR 0016) is generated, never hand-edited. `fabro validate`
is blind to a manifest that has drifted from `_io/manifest.json`, so a pre-push
check regenerates it in memory and fails on any drift or contract break.

It also fails when a stage id is not an agent node, when an `imports` entry names a
node that is not in the graph, when a live stage's prompt names one of its Sealed
paths, and when a stage that is not yet live names a `/tmp/fabro/` path outside its
inputs/output/`also`. Python 3.11+ (macOS ships 3.9, which the `tomllib` check below
also needs). The schema side is checked in Rust: `cargo test` in `ops/fabro-io/`
compiles every `_io/schemas/*.schema.json` under draft 2020-12. The manifest is JSON
inside a TOML literal, so it needs no escaping — `check` also fails if it ever
contains `'''`.

Neither gate runs the command nodes' shell. `backlog`'s task queue is several hundred
bytes of `jq` spread across `decompose_gate`, `improve_gate` and `next_task`, and a
cursor off-by-one there silently skips a task rather than failing — the same class of
bug, one layer down. `ops/test-task-gates.sh` extracts those `script` attributes from
the graph verbatim, rebases `/tmp/fabro` onto a scratch directory and runs them against
fixtures (821 checks on the Mac, 909 in a profile image; offline).

It needs only `jq`, `awk` and `git` — no `python3`, which `fabro-ts` and `fabro-python-node` do not ship — so it belongs in the same pre-push hook and also runs inside every profile image, the one way to test the counters against the sandbox's own mawk and jq (`jq 1.6` in `fabro-python-node`). It covers
the task-queue gates, `open_pr`, and — since 2026-09-19 — `claim`, `mark_stuck` and the
shared review-merge graph's `merge`. Since 2026-09-21 it also covers `open_pr_prep`'s
empty-diff floor, and that section is the one place here that uses the REAL `git`: it
builds an actual repo and clones it over `file://`, because what is under test is whether
`git diff --quiet origin/main HEAD` tells the truth about a branch carrying nothing but
checkpoint commits. A stub would only test the stub. It restores `$ORIG_PATH` first —
`next_task` prepends an `exit 0` `git` and never takes it off, so every later
`SAVED_PATH="$PATH"` captures that stub too. Since 2026-09-26 it also covers the merge phase's diff-hygiene
counters, each with a real-git fixture, and `refute_prep`, `refute_gate`, `merge_gate`
checks 13, 13b and 14, and the review comment's Refuter and hygiene sections (ADR 0013). It also runs
the code-index lines of every workflow's entry node (`backlog` `prep`, `pr-review` `claim`,
`arch-review` `prep`, `issue-triage` `acquire`), each with and without `fabro-code` on
`PATH`. Since 2026-10-01 it also covers `excerpts`, which copies the code the task dossier
cites into `task-code.md` for the coder: ranges, merging, the byte cap, and single lines
with and without `fabro-code`. Since 2026-10-02 it also covers `rework_router`'s
exemption lookup, `drop_followup` (real git), and the `needs_human_review` route from
`decompose_gate` to `mark_stuck` with the decomposer's reason, and the rescue gate's
`rescue_brief` summary and `partial_remainder`'s move of the unfinished tasks. Inside a profile image, where `codegraph` exists, it also runs the `fabro-code`
wrapper against a real-git fixture; on the Mac that section prints `SKIP`.
It says nothing about whether an agent fills a contract correctly.

**`claim` is in there because leaving it out cost the cutover.** Draft 10 deleted
`acquire`, `acquire` was the only node that ran `mkdir -p /tmp/fabro`, and `claim`
— now the run's first node — still redirected into that directory. Every `backlog`
run died at its second node and parked on `human_rescue` for four hours holding a
coder box; `fabro-fire-backlog.sh` failed identically; both offline gates stayed
green throughout, because neither runs a node's shell. `claim` is staged against a
sandbox path that *does not exist*, which is the only way a test can observe that a
node creates it — every other gate rebases onto a directory the harness already
made. If you add a node that writes before `prep`, stage it the same way.

`fabro validate` does not parse `workflow.toml` strictly. A dotted model key that loses
its quotes becomes a nested table and the automation fire returns 422, with nothing
reported until then. Check it separately, on 3.11+ — macOS ships 3.9, which has no
`tomllib`.

`fabro preflight` is **not** an offline check: its `LLM` check makes a real completion —
its detail line reads `Probe: basic generation` — so it fails with `server request timed
out after 30s` whenever a coder box is busy, which on this host is most of the time. The
summary it prints for that check is the model it probed, so preflight *is* the way to see
which model a stylesheet rule resolved to at real run time. Read a timeout there as
congestion, not as a graph fault; `fabro validate` and the routing checker are the gates
that need no box, and they are the ones to run before every push. Two gotchas when
preflighting `pr-review`: supply `-I pr_number=1` or it stops on the deliberate unbound
`pr_number`, and `-I auto_merge=0` as well or it then stops on the second unbound input in
the same `script` attribute.
