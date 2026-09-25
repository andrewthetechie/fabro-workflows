# Triage decides the questions that the repository can answer

## Tracer-Bullet Outcome
When triage finds a question that it may decide, it decides it and does not ask a
human. The triage agent writes each decision to a new `decisions` array in
`triage.json`. `triage_gate` validates the array. `apply_ready` and `post_questions`
append the decisions to the issue body under `## Decisions made during triage`, where a
human can read and reverse them. Fewer issues end on `needs-info`.

## User Story
As the operator, I want triage to make the internal choices that the repository already
supports, so that most issues reach `agent` and only real product questions come to me.

## Description
**Before you edit any `.fabro` file, invoke the `/fabro-workflow` skill.** Read
`docs/architecture-review/00-overview-and-contracts.md` first (decision 4, rules 1 to
12). Task 01 is merged, so the phase is at `.fabro/workflows/_shared/triage/triage.fabro`
and the prompt is at `.fabro/workflows/_shared/triage/prompts/triage.md.j2`.

1. Edit the prompt as shown in *Prompt edits*.
2. In `triage.fabro`, edit `triage_gate`, `apply_ready` and `post_questions` as shown in
   *Graph edits*.
3. Add the checks in *Test Expectations* to the `triage phase` section of
   `ops/test-task-gates.sh`.

## Context Pack
- Source decisions: overview decision 4, ADR 0012 D4. The rule depends on who wrote the
  issue:
  - **Architecture issue** (its labels include `architecture`): decide a question that
    has a recommended answer and whose choice is internal and reversible.
  - **Human-filed issue** (no `architecture` label): decide a question only when the
    code, `CONTEXT.md` or an ADR supports the recommended answer. The decision must name
    that support in `basis`.
  - Never decide product behaviour, external contracts, data, security or permissions,
    or a fact that only a human knows.
- Repo facts:
  - The triage agent reads `/tmp/fabro/issue.json` (`gh issue view --json
    number,title,body,labels,comments,url`). `.labels` is an array of objects with a
    `name` field, for example `[{"name":"architecture"}]`.
  - `triage.json` today has this shape (from the prompt):
    ```json
    {
      "readiness": "ready | needs_info | not_actionable",
      "classification": "bug | enhancement | mixed",
      "confidence": "high | medium | low",
      "title": "fix(scope): imperative description",
      "labels": ["bug"],
      "questions": [
        { "id": "Q1", "question": "one concrete decision", "why": "what it blocks", "recommended": "an evidence-backed default, or empty" }
      ],
      "summary": "one sentence for the operator"
    }
    ```
  - `triage_gate` today ends like this (after task 01). The new checks go before the
    `if [ \"$R\" = invalid ]; then` line:
    ```
    if [ \"$R\" = ready ] && [ \"$Q\" != 0 ]; then R=invalid; fi
    if [ \"$R\" = not_actionable ] && [ \"$Q\" != 0 ]; then R=invalid; fi
    if [ \"$R\" = invalid ]; then
      A=$((A+1))
    ```
  - `apply_ready` today (after task 01) has these lines, in this order:
    ```
    gh issue comment $N --body-file /tmp/fabro/report.md
    gh issue edit $N --title \"$T\" --add-label agent $L $R
    echo ready > /tmp/fabro/triage_outcome
    ```
  - `post_questions` today has these lines, in this order:
    ```
    gh issue comment $N --body-file /tmp/fabro/questions.md
    gh issue edit $N --title \"$T\" --add-label needs-info $L $R
    echo needs_info > /tmp/fabro/triage_outcome
    ```
  - Both nodes run under `set -e` and have `N=$(jq -r .number /tmp/fabro/issue.json)`
    as their second line.
- Non-goals: do not change `improve`, `claim`, `release`, `done`, the edges, or
  `apply_not_actionable`. A not-actionable issue records no decisions. Do not change
  `issue-triage` or any `workflow.toml`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft.

## Implementation Contract
- Expected files: `.fabro/workflows/_shared/triage/prompts/triage.md.j2`,
  `.fabro/workflows/_shared/triage/triage.fabro`, `ops/test-task-gates.sh`.
- Interfaces and names: the new optional field in `triage.json`:
  ```json
  "decisions": [
    { "question": "Where does the retry policy live?", "decision": "In the HTTP client module, next to the timeout.", "basis": "src/http/client.py already owns the timeout" }
  ]
  ```
  - `decisions` is optional. Absent means `[]`.
  - If present, it is an array. Each element has a non-empty string `question` and a
    non-empty string `decision`.
  - `basis` is a string. It must be non-empty when the issue has no `architecture`
    label. It may be empty for an Architecture issue.
  - The body heading is exactly `## Decisions made during triage`.
- Verified external contracts: None.
- Behavior rules:
  - An invalid `decisions` field makes the whole triage invalid, with the same retry as
    every other invalid field: the first invalid attempt exits 1 and the agent runs
    again.
  - The body keeps only one decisions section. Before `apply_ready` or `post_questions`
    appends the section, it removes an existing one. A later re-triage (after a human
    answers) therefore replaces the old section.
  - With no decisions, the body is not edited.
- Error and security rules: None beyond `set -e`.

### Prompt edits: `_shared/triage/prompts/triage.md.j2`

1. Add this section immediately before the line `## Classification`:

```markdown
## Decide what the repository can decide

Before you write a question, try to decide it yourself. Put each decision in
`decisions`, not in `questions`. The workflow writes your decisions into the issue
body, where a human can reverse them.

- **Architecture issue** — `.labels` contains `architecture`. An automated
  architecture review wrote it. Decide every question that has a recommended answer
  and whose choice is internal to the code and reversible: where a seam goes, the
  shape of an adapter, a module or function name, which file code moves to, the order
  of the steps.
- **Any other issue** — a human wrote it. Decide a question only when the repository
  supports the recommended answer: existing code that already does it that way, a
  term in `CONTEXT.md`, or a decision in `docs/adr/`. Write that path in `basis`. An
  internal choice in a human's issue can carry intent that they did not write down,
  so without that support it stays a question.

Never decide, for any issue: product behaviour that a user sees, an external
contract (an API, a file format, a CLI flag that another system calls), data
(migrations, deletion, retention), security or permissions, or a fact that only a
human knows. Those stay questions.
```

2. In the `triage.json` example block, add this field after `"questions": [...]`:

```json
  "decisions": [
    { "question": "one choice you made", "decision": "what you chose", "basis": "the file, CONTEXT.md term or ADR that supports it" }
  ],
```

3. In the rules list after the example (the list that begins "Rules — `triage_gate`
   enforces these:"), add these two items at the end:

```markdown
- `decisions` is an array. Each entry has a non-empty `question` and `decision`.
  For an issue without the `architecture` label, `basis` is non-empty too. Use `[]`
  when you decided nothing.
- A decided question is not in `questions`. A question you could not decide is not
  in `decisions`.
```

4. In the `triage.md` template, add the heading `### Decisions made during triage`
   after `### Acceptance boundary`.

### Graph edits: `_shared/triage/triage.fabro`

**`triage_gate`.** Insert these lines immediately before `if [ \"$R\" = invalid ]; then`:

```
DT=$(jq -r '(.decisions // []) | type' $F 2>/dev/null || echo bad)
if [ \"$DT\" != array ]; then R=invalid; fi
DB=$(jq '[(.decisions // [])[]? | select(((.question // \"\") | tostring) == \"\" or ((.decision // \"\") | tostring) == \"\")] | length' $F 2>/dev/null || echo 99)
if [ \"$DB\" != 0 ]; then R=invalid; fi
if ! jq -e '[.labels[].name] | index(\"architecture\") != null' /tmp/fabro/issue.json > /dev/null 2>&1; then
  NB=$(jq '[(.decisions // [])[]? | select(((.basis // \"\") | tostring) == \"\")] | length' $F 2>/dev/null || echo 99)
  if [ \"$NB\" != 0 ]; then R=invalid; fi
fi
```

**`apply_ready` and `post_questions`.** In each node, insert these lines immediately
before the `gh issue comment $N --body-file ...` line:

```
D=$(jq '[.decisions[]?] | length' /tmp/fabro/triage.json 2>/dev/null || echo 0)
if [ \"$D\" != 0 ]; then
  { jq -r '.body // \"\"' /tmp/fabro/issue.json | awk '/^## Decisions made during triage$/{exit} {print}'
    echo '## Decisions made during triage'
    echo
    echo 'Triage made these choices without asking. Edit this section to reverse one before the issue is worked.'
    echo
    jq -r '.decisions[] | \"- **\" + .question + \"** \" + .decision + (if (.basis // \"\") == \"\" then \"\" else \" (basis: \" + .basis + \")\" end)' /tmp/fabro/triage.json ; } > /tmp/fabro/decided_body.md
  gh issue edit $N --body-file /tmp/fabro/decided_body.md
fi
```

Add this `//` comment above `apply_ready` (not inside the script):

```dot
    // Decisions (ADR 0012 D4) are appended to the issue body, replacing any section an
    // earlier triage wrote, so a human can reverse one before `backlog` works it.
```

## Acceptance Criteria
- [ ] The prompt has the new section and the `decisions` field.
- [ ] `triage_gate` refuses a `decisions` that is not an array, an entry without
      `question` or `decision`, and an entry without `basis` on an issue that has no
      `architecture` label.
- [ ] `apply_ready` and `post_questions` write `## Decisions made during triage` once,
      even when the body already has that section.
- [ ] `./ops/test-task-gates.sh` prints `PASS: 285 checks` (276 plus 9).

## Test Expectations
Framework: the bash harness `ops/test-task-gates.sh`. Run `./ops/test-task-gates.sh`.

1. In the `triage phase` section, change the extraction loop to also extract
   `apply_ready`:

```bash
for n in claim triage_gate post_questions release done apply_ready; do
```

2. Insert these checks immediately before the lines `PATH="$SAVED_PATH"` and
   `unset GH_LOG GH_STATE` at the end of the `triage phase` section:

```bash
# 10. An Architecture issue may decide without a basis.
printf '{"number":7,"body":"b","labels":[{"name":"architecture"}]}' > "$T/issue.json"
echo 0 > "$T/triage_attempts"
echo '{"readiness":"ready","title":"refactor: x","labels":[],"questions":[],"decisions":[{"question":"q","decision":"d"}]}' > "$T/triage.json"
OUT=$(sh "$T/triage_gate.sh" 2>&1); RC=$?
check "decide arch: exit 0"            "0" "$RC"
check "decide arch: ready"             "ready" "$(jq -r '.context_updates.triage_readiness' <<<"$(lastjson "$OUT")")"

# 11. A human-filed issue needs a basis.
printf '{"number":7,"body":"b","labels":[]}' > "$T/issue.json"
echo 0 > "$T/triage_attempts"
sh "$T/triage_gate.sh" >/dev/null 2>&1; RC=$?
check "decide human, no basis: retry"  "1" "$RC"

# 12. decisions must be an array.
echo 0 > "$T/triage_attempts"
echo '{"readiness":"ready","title":"refactor: x","labels":[],"questions":[],"decisions":"x"}' > "$T/triage.json"
sh "$T/triage_gate.sh" >/dev/null 2>&1; RC=$?
check "decisions not array: retry"     "1" "$RC"

# 13. apply_ready appends one section, and replaces an old one.
printf '{"number":7,"body":"first line","labels":[]}' > "$T/issue.json"
echo '{"readiness":"ready","title":"feat: x","labels":[],"questions":[],"decisions":[{"question":"Where?","decision":"Here.","basis":"CONTEXT.md"}]}' > "$T/triage.json"
echo 'report' > "$T/triage.md"; : > "$T/gh.log"; rm -f "$T/decided_body.md"
sh "$T/apply_ready.sh" >/dev/null 2>&1
check "decided: one heading"           "1" "$(grep -c '^## Decisions made during triage$' "$T/decided_body.md")"
check "decided: keeps the body"        "1" "$(grep -c '^first line$' "$T/decided_body.md")"
check "decided: lists the decision"    "1" "$(grep -c '^- \*\*Where?\*\* Here. (basis: CONTEXT.md)$' "$T/decided_body.md")"
jq -n --rawfile b "$T/decided_body.md" '{number:7,body:$b,labels:[]}' > "$T/issue.json"
sh "$T/apply_ready.sh" >/dev/null 2>&1
check "decided again: still one"       "1" "$(grep -c '^## Decisions made during triage$' "$T/decided_body.md")"

# 14. No decisions: the body is not edited.
printf '{"number":7,"body":"b","labels":[]}' > "$T/issue.json"
echo '{"readiness":"ready","title":"feat: x","labels":[],"questions":[]}' > "$T/triage.json"
: > "$T/gh.log"
sh "$T/apply_ready.sh" >/dev/null 2>&1
check "no decisions: no body edit"     "0" "$(grep -c 'decided_body' "$T/gh.log")"
```

That is 9 new `check` calls.

## Dependencies
- Blocked by: 01 (Extract the triage phase)
- Why blocked: this task edits `_shared/triage/triage.fabro`, its prompt, and the
  `triage phase` test section, which task 01 creates.
- Blocks: 07

## Labels
`feature`, `issue-triage`, `priority:medium`

## Estimate
Small

## Risk
2 - an agent now writes into the issue body. The section is clearly labelled and
reversible, and the gate refuses a human-filed decision with no basis.

## Validator Stopping Point
`./ops/test-task-gates.sh` prints `PASS: 285 checks`.
