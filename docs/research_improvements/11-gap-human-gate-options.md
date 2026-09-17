# Gap — the rescue gate shows the wrong options

**Observation:** answering a `human_rescue` gate offers only "Accept Partial" and a cancel
control. The retry path is missing.

**Conclusion: a Fabro bug. A labelled `freeform=true` edge is dropped from the option
list.** The routing works; only the label is lost. Workaround is one string.

---

## The bug

`lib/components/fabro-workflow/src/handler/human.rs:87-101`:

```rust
let mut freeform_target: Option<String> = None;
let mut choices: Vec<Choice> = Vec::new();

for edge in &edges {
    if edge.freeform() {
        freeform_target = Some(edge.to.clone());
        continue;                       // <-- the edge never reaches `choices`
    }
    let label = edge.label().filter(|l| !l.is_empty()).unwrap_or(&edge.to);
    let key = parse_accelerator_key(label);
    choices.push(Choice { key, label: label.to_string(), to: edge.to.clone() });
}
```

An edge carrying `freeform=true` is recorded as the freeform target and **skipped** before
its label is read. There is no branch that adds a labelled freeform edge to both.

## What that does to our gate

`.fabro/workflows/backlog/workflow.fabro`:

```dot
human_rescue -> rework_t4    [label="[R] Retry with guidance", freeform=true]
human_rescue -> open_pr_prep [label="[P] Accept partial"]
human_rescue -> mark_stuck   [label="[X] Abandon"]
```

The operator is presented with:

- `[P] Accept partial`
- `[X] Abandon`
- a free-text box whose placeholder is the generic *"Or write a custom response…"*
  (`apps/fabro-web/app/components/interview-dock.tsx:354-362`)

`[R] Retry with guidance` — the one option that explains the primary recovery path — is
**never rendered**. The path itself works: free text sets
`outcome.suggested_next_ids = [freeform_target]` (`human.rs:397-404`, the assignment is line 400) and routes to
`rework_t4`. It is undiscoverable, not broken.

The "Cancel" in the observation is the dock's own control, not one of our edges.

## `issue-triage` is unaffected

```dot
ask_human -> record_answer        [freeform=true]
ask_human -> post_questions       [label="[D] Defer — post the questions"]
ask_human -> apply_not_actionable [label="[X] Not actionable"]
```

The freeform edge carries no label, so nothing is lost. This is the correct usage of the
attribute, and it is why that gate reads sensibly.

---

## The fix

A human gate's `label` **is** the question text — `human.rs:109`,
`Question::new(node.label(), question_type)`. Since Fabro discards the only edge label that
could describe the retry path, the node label is the one place left to describe it.

```dot
human_rescue [shape=hexagon,
    label="Agent stuck. Type guidance to retry at tier 4, or choose an option."]
```

No edge changes. No routing changes. One string.

## Why not split the edge instead

`Graph.edges` is a `Vec<Edge>` (`lib/foundation/fabro-types/src/graph.rs:546`) and
`outgoing_edges` filters rather than deduplicating by `(from, to)`, so parallel edges are
legal and a labelled `[R]` edge could sit alongside the freeform one.

It is still the wrong fix. A bare `[R]` click supplies **no** `human.gate.text`, so it
would route to `rework_t4` with no guidance — the degenerate case of the option it
advertises. The text box already is the correct interface; it just needs a sentence saying
so.

## Interaction with Tier 1 item 4

The guidance currently reaches `rework_t4` only through the preamble's context table, and
`summary:low` excludes context values. `01-tier-1-single-attribute-fixes.md` item 4a adds a
`record_guidance` command node with `stdin_source="human.gate.text"`, mirroring
`issue-triage`'s `record_answer`, which makes the guidance file-backed like every other
input.

**If that node is added, the label should name the file-backed behaviour rather than the
tier:**

```dot
human_rescue [shape=hexagon,
    label="Agent stuck. Type guidance to retry, or choose an option."]
```

The two changes are independent but land best together.

---

## Worth filing upstream

`build_human_gate_question` should add a labelled freeform edge to `choices` **and** set
`freeform_target`, so a gate can offer "type your answer" as a visible, accelerator-keyed
option. The current behaviour silently discards author intent: the workflow declares a
label, `fabro validate` accepts it, and it never appears anywhere.
