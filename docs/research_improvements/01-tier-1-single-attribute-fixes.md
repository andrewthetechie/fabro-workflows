# Tier 1 — Four single-attribute fixes

Each item is one graph attribute or one string. None changes a file contract, a prompt
contract, or a routing decision. Items 1 and 2 fix paths that terminate a run with no
report; items 3 and 4 fix operator-facing and model-facing quality.

Order within the tier does not matter. All four can ship in one commit.

---

## 1. `backlog` can be killed mid-run by the failure-signature circuit breaker

**Status 2026-09-24: open, low priority.** Across the 38 scheduler-dispatched runs, no run
ended with `deterministic failure cycle detected`. The worst case is run
`01M358C6G3BFF0KD1NCDC58AR9` (writers-app#963), where `validate` failed **21 times** in
one run and did not trip the breaker. Its failures were `cargo fmt` diffs, several
different clippy lints and compile errors, so the last 4096 bytes differed each time, and
so did the 240-character normalised signature. The exposure described below is real but
narrower in practice: the signature must repeat byte for byte after masking, and real CI
output seldom does. ADR 0011's D5 (`autofix` before `validate`) removes most of those
writers-app failures, which lowers the exposure further. Keep this as a one-line insurance
change. Nothing measured makes it urgent.

**Update 2026-09-25: still open.** ADR 0012 set `loop_restart_signature_limit=20` on
`arch-review` for the same reason, so a precedent now exists. `backlog` still has no
setting. Now tracked in `docs/factory-roadmap/H-housekeeping.md`.

### What happens

`CircuitBreakerLifecycle::after_node` (`lib/components/fabro-workflow/src/lifecycle/circuit_breaker.rs:93-103`)
counts failure signatures **run-wide**, never resets them on success, and at
`count >= loop_restart_signature_limit` returns an engine error:

```rust
let count = sigs.entry(sig.clone()).or_insert(0);
*count += 1;
if *count >= limit {
    return Err(CoreError::Other(format!(
        "deterministic failure cycle detected: signature {sig} repeated {count} times (limit {limit})"
    )));
}
```

That is an engine error, not an outcome. **No edge is selected.** The run does not reach
`human_rescue`, does not reach `mark_stuck`, and the claimed issue keeps
`agent-in-progress` with no comment explaining why.

The default limit is **3** (`docs/public/execution/failures.mdx`, "Failure signatures and
circuit breakers"). No graph in this repo sets `loop_restart_signature_limit`.

### Why our gates trip it

A command node's non-zero exit is classified `Deterministic` — it is the fallthrough of
`classify_failure_reason` (`lib/components/fabro-workflow/src/error.rs:192`), reached
whenever the reason matches no transient, budget, cancel or structural hint. Both tracked
categories are signature-tracked (`lib/foundation/fabro-types/src/outcome.rs:228-230`:
`Deterministic | Structural`).

The signature is `node_id|class|normalized_reason`. The reason for a command node is
`"Script failed with exit code: N"` plus the **last 4096 bytes** of merged output
(`lib/components/fabro-workflow/src/handler/command.rs:309-315`). Normalisation lowercases,
masks hex, replaces `\b\d+\b` with `<n>`, and truncates to **240 characters**
(`error.rs:200-222`).

Two consequences:

- A gate that prints one fixed line — every `*_gate` in this repo — produces a
  **byte-identical** signature on every failure.
- Digit masking means a CI failure that differs only in line numbers or timings also
  produces an identical signature.

### The exposure, concretely

`backlog`'s `validate` (`.fabro/workflows/backlog/workflow.fabro:167`) routes to
`rework_router`, which permits rounds 1–5 before `human_rescue`. Each round returns
through `prep_review` to `validate`. **`validate` can therefore be visited six times in
one task.** If CI stays red the same way — a lint rule, a test the rework does not fix —
the third failure ends the run.

The graph is designed for five rework rounds. Fabro caps identical deterministic failures
at three. The design and the engine disagree, and the engine wins silently.

Secondary exposure: `review_gate` and `improve_gate` each permit one failing exit per
task, and `next_task` resets their counters per task but the circuit breaker is run-wide.
Three tasks with one malformed contract each is enough.

`pr-review` is under the limit by construction (`fix_attempts` caps at 2, and the gate
node ids are distinct so their counters are separate). `issue-triage` reaches at most two
cycles. **This is a `backlog` problem.**

### The change

```dot
digraph Backlog {
    graph [
        goal="...",
        loop_restart_signature_limit=8,
        ...
    ]
```

8 covers the six `validate` visits with headroom. It does not disable the protection: a
genuinely stuck loop still aborts, just after the ladder has had its full run.

### Verifying it

There is no cheap live repro — it needs a PR whose CI fails identically three times. Two
cheap proxies:

- `fabro validate` must still pass (the attribute is graph-level and untyped-checked).
- Grep a completed multi-round run for the signature counter: the message text is
  `deterministic failure cycle detected`. Its absence in historical `fabro events` output
  does not prove safety — it proves we have not yet had three identical failures.

---

## 2. The stall watchdog fires before `watch_checks` can finish

**Applied 2026-09-22** (`stall_timeout="60m"` on both root graphs, `watch_checks`
re-timed to 50m). It had cost seven runs by then, all of writers-app and
lawncare-saas, each cancelled at exactly 1800s in `review_merge.watch_checks`. The
diagnosis below is what it was before the fix; the sizing that shipped is wider
than the 45m proposed here, because writers-app's CI was measured at 47m55s of
wall clock and 45m would still have left the node timeout unreachable in practice.

### What happens

`watch_checks` (`.fabro/workflows/pr-review/workflow.fabro:521`) carries `timeout="35m"`
and polls GitHub with `sleep 30`, producing **no output** until the loop breaks.

Command nodes emit exactly two events —
`CommandStarted` and `CommandCompleted`
(`lib/components/fabro-workflow/src/handler/command.rs:94` and `:155`). There is no
output-chunk event: command output is streamed to a log recorder and finalised into a
blob, never into the event stream. Confirmed against the `EventBody` enum
(`lib/foundation/fabro-types/src/run_event/mod.rs:62-210`) — no command-output variant
exists.

The stall watchdog cancels the run when no event lands within `stall_timeout`
(`lib/components/fabro-workflow/src/pipeline/execute.rs:85-116`; the cancel is line 112). The default is **30
minutes** (`lib/foundation/fabro-types/src/graph.rs:687`). No graph here sets it.

The watchdog parks only while the run is **blocked on a human**
(`execute.rs:99-105`, the `interview_blocks` branch). ADR 0002 records that behaviour for
gates; it does **not** apply to a command node.

### Consequence

A PR whose checks take longer than 30 minutes gets the run **cancelled** at 30 minutes:

- `watch_checks`'s own 35m timeout is unreachable, so the `checks_blocked=true` route
  never fires.
- No `report_blocked`. No PR comment. No `ai-review-needs-human` label.
- The 60-minute `merge_deadline` that `merge_gate` writes can never be spent.

The node's timeout and the graph's watchdog were chosen independently and the smaller one
silently wins.

### The change

Either raise the watchdog above the node timeout:

```dot
digraph PrReview {
    graph [ goal="...", stall_timeout="45m", ... ]
```

or bring the node under the watchdog:

```dot
watch_checks [label="Watch GitHub checks", shape=parallelogram, timeout="25m", ...]
```

**Prefer `stall_timeout="45m"`.** Lowering the node timeout shortens the CI budget the
merge phase was designed around; raising the watchdog costs nothing, because every other
node in the graph is well under 30 minutes of silence and agent stages emit continuously.

If both are wanted, keep the node timeout strictly below the watchdog so the *node*
timeout is the one that fires — a node timeout routes, a watchdog cancel does not.

### Verifying it

`fabro inspect` on a run that hit this shows a cancel, not a stage failure. After the
change, a `watch_checks` that exhausts its budget should route to `report_blocked` and
post the "60-minute merge budget is exhausted" comment the script already writes.

---

## 3. `[R] Retry with guidance` never renders on the rescue gate

**Status 2026-09-24: applied** in `1218167`, with the post-4a text: `"Agent stuck. Type
guidance to retry, or choose an option."` ADR 0010 Tier 1 replaces it with self-describing
edge labels.

Full diagnosis in `11-gap-human-gate-options.md`. The fix:

```dot
human_rescue [shape=hexagon,
    label="Agent stuck. Type guidance to retry at tier 4, or choose an option."]
```

A human gate's `label` **is** the question text presented to the operator
(`lib/components/fabro-workflow/src/handler/human.rs:109`, `Question::new(node.label(), …)`).
Fabro silently drops the labelled freeform edge from the option list, so the only place
the retry path can be described is the node label.

No edge changes. No routing changes. One string.

---

## 4. Agents receive an unbounded preamble

**Status 2026-09-24: applied, with one gap still open.**

- 4b shipped as `default_fidelity="truncate"` on all three graphs (`c3b14ca`), which goes
  further than the `summary:low` proposed below. AGENTS.md's invariants table records why:
  at `summary:low` the preamble was still 23% of every prompt.
- 4a shipped in `1218167`: `record_guidance` writes `/tmp/fabro/feedback/rescue.md`, and
  `rework.md.j2` reads it as its priority-1 input.
- **The per-task reset did not ship.** Nothing deletes `rescue.md`. `record_guidance`
  (`backlog/workflow.fabro:1317`) is the only line in the tree that names it. After an
  operator types guidance on task *k*, every later rework stage in the run (tasks *k*+1
  onward, every tier) still reads that guidance, and the prompt tells it the guidance
  **overrides everything else**, including that task's findings. The fix is the one this
  section always asked for: `rm -f /tmp/fabro/feedback/rescue.md` in `next_task`, with the
  other per-task resets. In `ops/test-task-gates.sh`, assert that `next_task` removes a
  `rescue.md` staged before it. In the 38 sampled runs no operator typed guidance
  (every answered gate was `[P]`), which is why this has not shown yet.
- **Update 2026-09-25: still open.** `record_guidance` is now at `backlog/workflow.fabro:1467`,
  and it is still the only line that names the file. Now tracked in
  `docs/factory-roadmap/H-housekeeping.md`.

Full diagnosis in `12-gap-agent-context-preamble.md`. The short version: the default
`compact` fidelity enumerates **every** completed stage with 25 lines of command output
each, and every non-internal context key, in front of the node's prompt.

### The change, in two parts

**4a. Make the rescue guidance file-backed.** `backlog/prompts/rework.md.j2:10-13` reads
`human.gate.text` out of the context table, and `summary:low` excludes context values
(`preamble.rs:525-540`). Setting a low fidelity without this step silently breaks the
rescue path.

Mirror `issue-triage`'s `record_answer`, which already solves this exact problem:

```dot
record_guidance [label="Record the human guidance", shape=parallelogram,
    stdin_source="human.gate.text",
    script="mkdir -p /tmp/fabro/feedback
cat > /tmp/fabro/feedback/rescue.md
echo recorded"]

human_rescue    -> record_guidance [freeform=true]
record_guidance -> rework_t4
```

Then change `rework.md.j2`'s human-guidance clause to read
`/tmp/fabro/feedback/rescue.md`, consistent with every other input in that prompt. Delete
the file in `next_task` alongside the other per-task resets, or a stale rescue from task 1
steers task 2.

**4b. Set the graph default.**

```dot
graph [ default_fidelity="summary:low", ... ]
```

on all three graphs.

### Why `summary:low` and not `truncate`

`truncate` gives the agent only the goal and run ID. `summary:low` gives the last **two**
stages plus a `(N earlier stage(s) omitted)` line (`preamble.rs:525-540`), which preserves
the one genuinely useful signal — what immediately preceded this node — at negligible
cost. Every prompt in this repo names its inputs by path, so nothing else in the preamble
is load-bearing.

### If 4a is deferred

Ship `default_fidelity="summary:medium"` instead. It keeps the context table (so
`human.gate.text` still reaches `rework_t*`) and caps stages at **five**
(`preamble.rs:478-490`). That is most of the win with none of the graph surgery. Do not
ship `summary:low` without 4a.

### Verifying it

`fabro dump <run> -o ./dump` writes `stages/{rank}-{node}@{visit}/prompt.md`, which is
"the assembled prompt (preamble + expanded prompt text)"
(`docs/public/agents/outputs.mdx`). Dump a `backlog` run before and after and diff the
`standards` stage's `prompt.md`. The preamble section should collapse from every stage to
two.

---

## Combined diff surface

| File | Change |
|---|---|
| `backlog/workflow.fabro` | `loop_restart_signature_limit=8`, `default_fidelity`, `human_rescue` label, `record_guidance` node + 2 edges, `next_task` resets `rescue.md` |
| `pr-review/workflow.fabro` | `stall_timeout="45m"`, `default_fidelity` |
| `issue-triage/workflow.fabro` | `default_fidelity` |
| `backlog/prompts/rework.md.j2` | human-guidance clause reads a file |

Validate in the container per `AGENTS.md`. Item 4a **replaces** the existing
`human_rescue -> rework_t4 [label="[R] Retry with guidance", freeform=true]` edge with two
edges through `record_guidance`, so `Backlog` nets **one node and one edge**: the
`38 nodes, 86 edges` baseline moves to `39 nodes, 87 edges`. Update it in `AGENTS.md` in
the same commit.

`rework_t4` stays reachable from `rework_router` via `condition="context.round=5"`, so
dropping the direct edge from the gate does not orphan it. Drop the `[R]` label with the
edge — Fabro discards it anyway (see `11-gap-human-gate-options.md`), and the node label
from item 3 now carries that text.
