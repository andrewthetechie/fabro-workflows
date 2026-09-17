# Gap — what an agent receives besides its prompt

**Observation:** agents appear to receive far more than the `prompt` attribute, including
information about other stages.

**Conclusion: correct, and it is unbounded.** Under the default fidelity every agent node
receives a summary of *every* completed stage, with up to 25 lines of command output each,
plus the whole context table — prepended to the prompt.

---

## The mechanism

When an agent or prompt node starts, Fabro assembles a **preamble** from the run context
and prepends it to the node's prompt. How much it contains is the `fidelity` setting.

None of the three graphs sets `fidelity`, `default_fidelity`, or an edge-level override.
The default is `compact`.

`lib/components/fabro-workflow/src/handler/llm/preamble.rs:391`:

```rust
for node_id in completed_nodes {
    if is_meta_handler(graph, node_id) { continue; }
    ...
    parts.push(format!("- **{node_id}**: {status}"));
    let details = render_compact_stage_details(node_id, node, outcome);
    parts.extend(details);
```

A plain iteration over every completed node. **No cap, no window, no recency filter.**

For a command node, `render_compact_stage_details` (`preamble.rs:181-213`) emits the script
text and the output:

```rust
lines.push(format!("  - Script: `{cmd}`"));
...
lines.push(tail_lines(output.trim(), COMPACT_OUTPUT_MAX_LINES, "    "));
```

with `COMPACT_OUTPUT_MAX_LINES = 25` (`preamble.rs:10`). Then
`append_filtered_context` adds every non-internal context key.

## The bounded modes, with the actual numbers

| fidelity | stages shown | context table | source |
|---|---|---|---|
| `compact` (default) | **all** | yes | `preamble.rs:391` |
| `summary:high` | all, richer detail | yes | `preamble.rs:447` |
| `summary:medium` | last **5** | yes | `preamble.rs:478-490`, `let recent_count = 5;` |
| `summary:low` | last **2** | **no** | `preamble.rs:525-540`, `let recent_count = 2;` |
| `truncate` | none | no | goal and run ID only |

`summary:medium` and `summary:low` also emit `(N earlier stage(s) omitted)`, so the model
knows the history was trimmed rather than absent.

## What this costs us in practice

`backlog` on a three-task issue runs roughly: `acquire`, `claim`, `prep`, `decompose`,
`decompose_gate`, then per task `next_task`, `improve`, `improve_gate`, `coder`,
`prep_review`, `validate`, `review`, `review_gate`, `integrate` — plus any rework rounds —
then `extra_prep`, `standards`, `standards_gate`, `spec`, `spec_gate`, `quality`,
`quality_gate`, `extra_decompose`, `extra_gate`.

By the time `standards` runs it is well past thirty completed stages, most of them command
nodes. Each contributes a script line and up to 25 lines of `jq` gate output. That is tens
of thousands of tokens of preamble in front of an 8.5 KB prompt, and almost all of it is
gate bookkeeping the reviewer must not act on.

**The architecture here is already file-backed.** Every prompt names its inputs by path —
`/tmp/fabro/review/diff.patch`, `/tmp/fabro/issue.json`, `/tmp/fabro/current_task.json`.
Nothing in these prompts needs the preamble to do its job. We are paying for context we
deliberately designed away, and it dilutes the role prompt described in
`13-gap-prompt-fidelity-vs-sandcastle.md`.

---

## The one thing that does read the context table

`backlog/prompts/rework.md.j2:10-13`:

> **Human guidance, if present.** If the run context contains human guidance from a rescue
> gate (`human.gate.text`), it **overrides everything else**.

`human.gate.text` reaches the rework agent **only** through the preamble's context section.
`summary:low` excludes context values, so setting it without first making the guidance
file-backed silently disables the rescue path — the operator types guidance, the gate
routes correctly, and the agent never sees it.

This is why `01-tier-1-single-attribute-fixes.md` item 4 is in two parts, and why 4a must
land before 4b.

---

## The fix

Detail in `01-tier-1-single-attribute-fixes.md` item 4. In summary:

1. Add a `record_guidance` command node with `stdin_source="human.gate.text"` between
   `human_rescue` and `rework_t4`, mirroring `issue-triage`'s `record_answer`, and point
   `rework.md.j2` at the file it writes.
2. Set `graph [default_fidelity="summary:low"]` on all three graphs.

If step 1 is deferred, use `summary:medium` — it keeps the context table and still caps
stages at five.

## Verifying it

`fabro dump <run> -o ./dump` writes `stages/{rank:03}-{node_id}@{visit}/prompt.md`, which
`docs/public/agents/outputs.mdx` defines as *"The assembled prompt (preamble + expanded
prompt text)"*. Dump a `backlog` run before and after the change and diff the `standards`
stage's `prompt.md`; the `## Completed stages` section should collapse from every stage to
two plus an omission count.

---

## Things worth knowing that are not problems

- **Large values are already demoted.** In fidelity modes that render context or stage
  output, one value contributes at most 8 KiB inline; larger ones become a blob with a size,
  a file path, and a 300-character preview the agent can read on demand. So the preamble is
  large by *count* of stages, not by any single value.
- **`internal.`, `current`, `graph.`, `thread.` and `response.` keys are already excluded**
  from preambles, so the full text of every prior agent response is not being re-injected.
- **`project_memory` defaults to `true`** (`lib/foundation/fabro-types/src/graph.rs:424`),
  so the target repository's `AGENTS.md` is supplied as the **system prompt** on every agent
  node. That is separate from the preamble and is desirable — but it is also why the role
  prompt sits in a user turn. See `13-gap-prompt-fidelity-vs-sandcastle.md`.
