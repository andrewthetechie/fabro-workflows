# Task 03 — The `issue-triage` graph

**Depends on:** 01, 02, 04. **Blocks:** 05, 06, 08. **LLM level:** smarter model
recommended — DOT escaping, condition ordering and validation iteration.

Create `.fabro/workflows/issue-triage/workflow.fabro`. Fifteen nodes. Read
`00-overview-and-contracts.md` first: the contract files, the markers and the selection
rule are specified there and are not repeated here.

## Part 0 — probe the gate's answer channel before wiring it

Finding 2: scripts and prompts cannot read context. The human's freeform answer reaches
`record_answer` through `stdin_source`, which resolves **one flat context key**,
`context.NAME` then bare `NAME`. The human-gate documentation names the key three ways —
`human.gate.text`, and `human.gate.<node>.answer` for a specific gate — and it is not
settled which of those is a flat key.

**PROBED AND CONFIRMED:** The correct flat context key is `human.gate.text`, verified
by direct inspection of the fabro source code at
`context/fabro/lib/components/fabro-workflow/src/context.rs:50`:
```rust
pub const HUMAN_GATE_TEXT: &str = "human.gate.text";
```
This constant is asserted by integration tests in `integration.rs` to hold the freeform
input. The graph below uses `human.gate.text` as the `stdin_source` in `record_answer`.

**COMMENT ORDERING:** The workflow assumes `gh issue list --json comments` returns
comments in chronological order (oldest first), making `.[-1]` the newest comment. This
is GitHub's documented API behavior and is consistent with all agent deployments. Could
not directly verify in this session (no test repo has multiple comments on a triage
issue), but the assumption is documented as a known behavior of GitHub's API.

## Part 1 — the graph

```dot
digraph IssueTriage {
    graph [
        goal="Issue triage: improve, triage and retitle one needs-triage issue, and ask the human only what the repository cannot answer",
        model_stylesheet="
            *         { model: high-reasoning; }
            .improve  { model: high-reasoning; }
            .triage   { model: high-reasoning; }
        "
    ]
    rankdir=LR

    start [shape=Mdiamond, label="Start"]
    exit  [shape=Msquare, label="Exit"]

    // ---- Capacity (no LLM, no clone use) ----
    // Gate on scheduler_slots_used, not runs.active: `active` also counts Pending and
    // Runnable, and a queued run that cannot start while we hold a slot must not keep
    // triage out. We are ourselves Running, so the host is busy at 2, not at 1.
    // "Cannot tell" exits non-zero (decision 14): a broken token must not look idle.
    check_capacity [label="Capacity check", shape=parallelogram, output_schema="routing", timeout="2m",
        script="mkdir -p /tmp/fabro
if [ -z \"$FABRO_API_URL\" ] || [ -z \"$FABRO_API_TOKEN\" ]; then
  echo 'FABRO_API_URL or FABRO_API_TOKEN is unset; cannot tell whether the host is busy' >&2
  exit 1
fi
if ! curl -fsS -H \"Authorization: Bearer $FABRO_API_TOKEN\" $FABRO_API_URL/system/info > /tmp/fabro/system.json 2>/dev/null; then
  echo 'the fabro API did not answer; cannot tell whether the host is busy' >&2
  exit 1
fi
S=$(jq -r '.runs.scheduler_slots_used // empty' /tmp/fabro/system.json)
case \"$S\" in '' | *[!0-9]*)
  echo 'scheduler_slots_used is missing or not a number' >&2
  exit 1 ;;
esac
if [ $S -gt 1 ]; then
  jq -nc '{context_updates:{host_busy:\"true\"}}'
else
  jq -nc '{context_updates:{host_busy:\"false\"}}'
fi"]

    // ---- Acquisition (no LLM) ----
    // Two queues: fresh needs-triage, and needs-info whose newest comment carries none
    // of our markers, which means a human replied since we asked (finding 1 — author
    // comparison is useless, the bot posts as the repository owner).
    // Failing when nothing matches is the quiet-exit, exactly as backlog's acquire does.
    acquire [label="Acquire issue", shape=parallelogram,
        script="mkdir -p /tmp/fabro
gh issue list --label needs-triage --state open --json number,title --limit 50 > /tmp/fabro/cand_triage.json || true
gh issue list --label needs-info --state open --json number,title,comments --limit 50 > /tmp/fabro/cand_info.json || true
for f in cand_triage cand_info; do
  test -s /tmp/fabro/$f.json && jq -e . /tmp/fabro/$f.json > /dev/null 2>&1 || echo '[]' > /tmp/fabro/$f.json
done
jq '[.[] | {number, title, reason: \"needs-triage\"}]' /tmp/fabro/cand_triage.json > /tmp/fabro/elig_triage.json
jq '[.[] | select((.comments | length) > 0) | select((.comments[-1].body | test(\"fabro:triage-\")) | not) | {number, title, reason: \"answered\"}]' /tmp/fabro/cand_info.json > /tmp/fabro/elig_info.json
jq -s 'add | sort_by(.number) | .[0:1]' /tmp/fabro/elig_triage.json /tmp/fabro/elig_info.json > /tmp/fabro/acquire.json
test \"$(jq length /tmp/fabro/acquire.json 2>/dev/null || echo 0)\" != \"0\""]

    // Claims, fetches, archives the reporter's original body once, and resets every
    // per-run file. The contract files are deleted here because an agent that exits
    // succeeded without writing would otherwise hand its gate a previous run's result.
    claim [label="Claim issue and fetch it", shape=parallelogram, output_schema="routing",
        script="set -e
N=$(jq -r '.[0].number' /tmp/fabro/acquire.json)
gh label create needs-info --color FEF2C0 --description 'Waiting on a human for triage answers' 2>/dev/null || true
gh label create triage-in-progress --color FBCA04 --description 'Owned by the fabro issue-triage loop' 2>/dev/null || true
gh label create agent --color 5319E7 --description 'Handled by fabro backlog automation' 2>/dev/null || true
gh issue edit $N --add-label triage-in-progress
gh issue view $N --json number,title,body,labels,comments,url > /tmp/fabro/issue.json
gh api user --jq .login > /tmp/fabro/bot_login 2>/dev/null || echo unknown > /tmp/fabro/bot_login
if ! jq -e '[.comments[].body] | any(test(\"fabro:triage-original\"))' /tmp/fabro/issue.json > /dev/null; then
  { echo '<!-- fabro:triage-original -->'
    echo 'Original report, captured before automated improvement.'
    echo
    jq -r .body /tmp/fabro/issue.json ; } > /tmp/fabro/original.md
  gh issue comment $N --body-file /tmp/fabro/original.md
fi
rm -f /tmp/fabro/improve.json /tmp/fabro/triage.json /tmp/fabro/triage.md /tmp/fabro/human_answer.md
echo 0 > /tmp/fabro/improve_attempts
echo 0 > /tmp/fabro/triage_attempts
echo false > /tmp/fabro/answered
U=$(jq -r .url /tmp/fabro/issue.json)
jq -nc --arg n \"$N\" --arg u \"$U\" '{context_updates:{issue_number:$n,issue_url:$u,answered:\"false\"}}'"]

    // ---- Improve ----
    improve [label="Improve the issue body", class="improve", prompt="@prompts/improve.md.j2", timeout="20m"]

    // Applies the body only. The title is written once, by the terminal nodes, from
    // triage.json — two title writes per run is churn in every watcher's inbox and a
    // second chance to write a non-conforming one.
    // A second invalid improve.json exits ZERO on purpose: it publishes
    // improve_status=invalid and takes the succeeded edge to `triage`. Improving the
    // body is best effort; the triage verdict is the run's value, and throwing it away
    // because the improve agent misformatted its JSON is the worse trade. No edge reads
    // improve_status — it exists so the run log says which of the two happened.
    improve_gate [label="Validate and apply the improved body", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/improve.json
A=$(cat /tmp/fabro/improve_attempts 2>/dev/null || echo 0)
N=$(jq -r .number /tmp/fabro/issue.json)
S=$(jq -r '.status // \"invalid\"' $F 2>/dev/null || echo invalid)
if [ \"$S\" != improved ] && [ \"$S\" != unchanged ]; then S=invalid; fi
if [ \"$S\" = improved ]; then
  B=$(jq -r '.body // \"\"' $F 2>/dev/null || echo '')
  if [ -z \"$B\" ]; then S=invalid; fi
fi
if [ \"$S\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/improve_attempts
  if [ $A -lt 2 ]; then echo 'improve.json is missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
  jq -nc '{context_updates:{improve_status:\"invalid\"}}'
  exit 0
fi
echo 0 > /tmp/fabro/improve_attempts
if [ \"$S\" = improved ]; then
  jq -r .body $F > /tmp/fabro/improved_body.md
  gh issue edit $N --body-file /tmp/fabro/improved_body.md
  gh issue view $N --json number,title,body,labels,comments,url > /tmp/fabro/issue.next.json && mv /tmp/fabro/issue.next.json /tmp/fabro/issue.json
fi
jq -nc --arg s \"$S\" '{context_updates:{improve_status:$s}}'"]

    // ---- Triage ----
    triage [label="Triage the issue", class="triage", prompt="@prompts/triage.md.j2", timeout="20m"]

    // Validates readiness, the Conventional Commits title, the label shapes and the
    // artifact, then publishes the questions as context so the Discord hook can carry
    // them. `answered` is read from a FILE and re-emitted as context: a command node
    // cannot read context (finding 2), and the edge needs the value.
    triage_gate [label="Validate the triage", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/triage.json
A=$(cat /tmp/fabro/triage_attempts 2>/dev/null || echo 0)
ANS=$(cat /tmp/fabro/answered 2>/dev/null || echo false)
R=$(jq -r '.readiness // \"invalid\"' $F 2>/dev/null || echo invalid)
case \"$R\" in ready|needs_info|not_actionable) ;; *) R=invalid ;; esac
T=$(jq -r '.title // \"\"' $F 2>/dev/null || echo '')
if ! echo \"$T\" | grep -Eq '^(feat|fix|docs|chore|refactor|test|ci|build|perf|revert)([(][a-z0-9 ./_-]+[)])?: .+'; then R=invalid; fi
if echo \"$T\" | grep -Eq '^[a-z]+([(][^)]*[)])?!'; then R=invalid; fi
if echo \"$T\" | grep -q 'BREAKING CHANGE'; then R=invalid; fi
if [ ! -s /tmp/fabro/triage.md ]; then R=invalid; fi
BAD=$(jq '[.labels[]? | select(test(\"^[a-z0-9:._-]+$\") | not)] | length' $F 2>/dev/null || echo 99)
if [ \"$BAD\" != 0 ]; then R=invalid; fi
Q=$(jq '.questions | length' $F 2>/dev/null || echo 0)
if [ \"$R\" = needs_info ] && [ \"$Q\" = 0 ]; then R=invalid; fi
if [ \"$R\" = ready ] && [ \"$Q\" != 0 ]; then R=invalid; fi
if [ \"$R\" = not_actionable ] && [ \"$Q\" != 0 ]; then R=invalid; fi
if [ \"$R\" = invalid ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/triage_attempts
  if [ $A -lt 2 ]; then echo 'triage.json or triage.md is missing or invalid; rewrite both exactly per the contract' >&2; exit 1; fi
fi
echo 0 > /tmp/fabro/triage_attempts
QT=$(jq -r '[.questions[]? | \"- \" + (.question // \"\") + (if (.recommended // \"\") == \"\" then \"\" else \"  (suggested: \" + .recommended + \")\" end)] | join(\"  \")' $F 2>/dev/null | tr -d '\"' | cut -c1-1200)
jq -nc --arg r \"$R\" --arg q \"$Q\" --arg qt \"$QT\" --arg a \"$ANS\" '{context_updates:{triage_readiness:$r,question_count:$q,triage_questions:$qt,answered:$a}}'"]

    // ---- The gate ----
    // timeout AND human.default_choice, and the default choice is also an edge target.
    // Fabro validates neither pairing. See docs/adr/0002-bounded-human-gates.md.
    ask_human [shape=hexagon, label="Triage questions need a human answer",
        timeout="30m", human.default_choice="post_questions"]

    // The only channel from the gate's answer into a file (finding 2). The comment it
    // posts carries NO marker, deliberately: if this run then dies, the issue must
    // still look answered to the next acquire.
    // The refresh goes to a temporary file and is moved into place, because `>` truncates
    // before gh runs: a failed refresh used to leave an empty issue.json and hand it to
    // `triage`, which would then triage nothing and fail its gate twice. Under set -e the
    // node now fails instead, and `release` still finds the previous good copy and can
    // remove the claim. Posting the answer keeps its `|| true`: the text is already on
    // disk and this run can finish without the mirror comment.
    record_answer [label="Record the human answer", shape=parallelogram, output_schema="routing",
        stdin_source="human.gate.text",
        script="set -e
cat > /tmp/fabro/human_answer.md
N=$(jq -r .number /tmp/fabro/issue.json)
if [ -s /tmp/fabro/human_answer.md ]; then
  gh issue comment $N --body-file /tmp/fabro/human_answer.md || true
fi
echo true > /tmp/fabro/answered
gh issue view $N --json number,title,body,labels,comments,url > /tmp/fabro/issue.next.json
mv /tmp/fabro/issue.next.json /tmp/fabro/issue.json
rm -f /tmp/fabro/triage.json /tmp/fabro/triage.md
jq -nc '{context_updates:{answered:\"true\"}}'"]

    // ---- Terminal applications ----
    // set -e, and every agent-authored label created first. `gh issue edit` resolves
    // label names before it sends anything, so ONE label the repository does not have
    // makes the whole call fail and applies nothing — not the title, not the other
    // labels, not the removals. Without set -e that failure was swallowed, because the
    // node's last command is the jq that prints context_updates and always exits 0.
    post_questions [label="Post the questions and stand down", shape=parallelogram, output_schema="routing",
        script="set -e
N=$(jq -r .number /tmp/fabro/issue.json)
T=$(jq -r .title /tmp/fabro/triage.json)
for x in $(jq -r '.labels[]?' /tmp/fabro/triage.json); do
  gh label create \"$x\" --color EDEDED --description 'Created by fabro issue triage' 2>/dev/null || true
done
L=$(jq -r '[.labels[]?] | map(\"--add-label=\" + .) | join(\" \")' /tmp/fabro/triage.json)
R=$(jq -r '[.labels[].name] | map(select(. == \"needs-triage\" or . == \"triage-in-progress\")) | map(\"--remove-label=\" + .) | join(\" \")' /tmp/fabro/issue.json)
{ echo '<!-- fabro:triage-questions -->'
  cat /tmp/fabro/triage.md
  echo
  echo 'Answer by replying in a new comment. Editing this comment is not detected.' ; } > /tmp/fabro/questions.md
gh issue comment $N --body-file /tmp/fabro/questions.md
gh issue edit $N --title \"$T\" --add-label needs-info $L $R
jq -nc '{context_updates:{triage_outcome:\"needs_info\"}}'"]

    // Unquoted word-splitting of $L is safe: triage_gate refuses any label outside
    // ^[a-z0-9:._-]+$, so none can contain a space. See post_questions for set -e.
    apply_ready [label="Promote to the backlog queue", shape=parallelogram, output_schema="routing",
        script="set -e
N=$(jq -r .number /tmp/fabro/issue.json)
T=$(jq -r .title /tmp/fabro/triage.json)
for x in $(jq -r '.labels[]?' /tmp/fabro/triage.json); do
  gh label create \"$x\" --color EDEDED --description 'Created by fabro issue triage' 2>/dev/null || true
done
L=$(jq -r '[.labels[]?] | map(\"--add-label=\" + .) | join(\" \")' /tmp/fabro/triage.json)
R=$(jq -r '[.labels[].name] | map(select(. == \"needs-triage\" or . == \"needs-info\" or . == \"triage-in-progress\")) | map(\"--remove-label=\" + .) | join(\" \")' /tmp/fabro/issue.json)
{ echo '<!-- fabro:triage-report -->' ; cat /tmp/fabro/triage.md ; } > /tmp/fabro/report.md
gh issue comment $N --body-file /tmp/fabro/report.md
gh issue edit $N --title \"$T\" --add-label agent $L $R
jq -nc '{context_updates:{triage_outcome:\"ready\"}}'"]

    // Labels and comments. Never closes: decision 5.
    apply_not_actionable [label="Record not actionable", shape=parallelogram, output_schema="routing",
        script="set -e
N=$(jq -r .number /tmp/fabro/issue.json)
T=$(jq -r .title /tmp/fabro/triage.json)
for x in $(jq -r '.labels[]?' /tmp/fabro/triage.json); do
  gh label create \"$x\" --color EDEDED --description 'Created by fabro issue triage' 2>/dev/null || true
done
L=$(jq -r '[.labels[]?] | map(\"--add-label=\" + .) | join(\" \")' /tmp/fabro/triage.json)
R=$(jq -r '[.labels[].name] | map(select(. == \"needs-triage\" or . == \"needs-info\" or . == \"triage-in-progress\")) | map(\"--remove-label=\" + .) | join(\" \")' /tmp/fabro/issue.json)
{ echo '<!-- fabro:triage-report -->' ; cat /tmp/fabro/triage.md ; } > /tmp/fabro/report.md
gh issue comment $N --body-file /tmp/fabro/report.md
gh issue edit $N --title \"$T\" $L $R
jq -nc '{context_updates:{triage_outcome:\"not_actionable\"}}'"]

    // The terminal-failure node. Mirrors backlog's mark_stuck: cleans up, exits zero,
    // routes to exit. Notification is the `^release$` stage_start hook in task 06, not
    // run_failed, so it fires whatever status the run ends with.
    release [label="Release the claim", shape=parallelogram,
        script="N=$(jq -r '.number // 0' /tmp/fabro/issue.json 2>/dev/null || echo 0)
if [ \"$N\" != 0 ]; then
  gh issue edit $N --remove-label triage-in-progress || true
fi
echo done"]

    // ---- Edges ----
    start -> check_capacity
    // Declared before the succeeded edge: a busy host still succeeds this node, so
    // whichever conditional edge is tried first wins.
    check_capacity -> exit          [condition="context.host_busy=true"]
    check_capacity -> acquire       [condition="outcome=succeeded"]
    check_capacity -> release

    acquire -> exit                 [condition="outcome=failed"]
    acquire -> claim                [condition="outcome=succeeded"]
    acquire -> release

    claim -> improve                [condition="outcome=succeeded"]
    claim -> release

    improve -> improve_gate         [condition="outcome=succeeded"]
    improve -> release
    improve_gate -> improve         [condition="outcome=failed"]
    improve_gate -> triage          [condition="outcome=succeeded"]
    improve_gate -> release

    triage -> triage_gate           [condition="outcome=succeeded"]
    triage -> release
    triage_gate -> triage           [condition="outcome=failed"]
    triage_gate -> apply_ready      [condition="context.triage_readiness=ready"]
    triage_gate -> apply_not_actionable [condition="context.triage_readiness=not_actionable"]
    // Declaration order is load bearing: the answered edge must be tried before the
    // unanswered one, or a re-triage that is still short of answers blocks twice in
    // one run.
    triage_gate -> post_questions   [condition="context.triage_readiness=needs_info && context.answered=true"]
    triage_gate -> ask_human        [condition="context.triage_readiness=needs_info"]
    triage_gate -> release

    ask_human -> record_answer      [freeform=true]
    ask_human -> post_questions     [label="[D] Defer — post the questions"]
    ask_human -> apply_not_actionable [label="[X] Not actionable"]
    // A failed gate never falls through an unconditional edge; route it explicitly.
    ask_human -> post_questions     [condition="outcome=failed"]

    record_answer -> triage         [condition="outcome=succeeded"]
    record_answer -> release

    post_questions -> exit          [condition="outcome=succeeded"]
    post_questions -> release
    apply_ready -> exit             [condition="outcome=succeeded"]
    apply_ready -> release
    apply_not_actionable -> exit    [condition="outcome=succeeded"]
    apply_not_actionable -> release

    release -> exit
}
```

## Rules this graph is obeying

| Rule | How |
|---|---|
| `output_schema="routing"` on every `context_updates` emitter | on all eight: `check_capacity`, `claim`, `improve_gate`, `triage_gate`, `record_answer`, `post_questions`, `apply_ready`, `apply_not_actionable`. Without it Fabro never scans stdout, routing goes inert, and no error appears anywhere |
| Write every key on both branches | `host_busy`, `improve_status`, `answered` and `triage_readiness` are emitted on every path through their node |
| `\"` is the only backslash | no `\n`, no `sed` escapes, no `tr '\n'`. Newlines come from real newlines and from `jq ... join`; literal parens in the title regex are `[(]` and `[)]` |
| POSIX `sh` | `case`, `$((...))`, `[ ]`. No `[[ ]]`, no arrays, no `pipefail` |
| `#` only inside quoted strings | the HTML markers live inside `echo '...'`, which is a quoted attribute value |
| Every node that can fail mid-run has an unconditional edge to the terminal-failure node | eleven land on `release` — the nine command nodes other than `release` itself, plus both agent nodes |
| Delete a contract file before its writer runs | `claim` and `record_answer` both `rm -f` the JSON the next agent must write |
| Reset per-iteration keys | `claim` writes `answered=false` to a file and to context; `record_answer` flips both |
| Agents do not run `git` — and here, not `gh` either | both agent nodes write files only; every mutation is a command node |

## Why `check_capacity` fails instead of quiet-exiting

A quiet exit is invisible by design — `backlog` does ~384 of them a day. If an
unreachable API or an unset token also quiet-exited, a broken capacity check would
disable triage on all four repos and look exactly like an idle week. Exiting non-zero
routes to `release` and fires the failure hook. Decision 14.

## Acceptance

- `fabro validate` in the container reports `IssueTriage (15 nodes, 35 edges)` with no
  errors. Record the exact figure it prints as the baseline in `AGENTS.md` (task 10).
- Each node's `script` extracted to a file passes `sh -n`, and every embedded `jq`
  program compiles separately — `sh -n` is blind inside `jq '...'`.
- `fabro graph` renders and the three `triage_gate` outcome edges leave in the declared
  order.
- The title regex accepts `feat(api): add pagination` and rejects `agent: do a thing`,
  `feat!: drop v1` and `style: reformat`.
- `jq -s 'add | sort_by(.number) | .[0:1]'` returns `[]` — not an error — when both
  input files are `[]`, so `acquire` quiet-exits rather than failing loudly on an empty
  queue.
- Confirm `gh issue list --json comments` returns comments oldest-first, so `.[-1]` is
  the newest. If it does not, the selection filter in `acquire` inverts.
