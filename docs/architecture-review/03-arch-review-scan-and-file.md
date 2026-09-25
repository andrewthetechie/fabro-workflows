# `arch-review` scans a repository and files Architecture issues

## Tracer-Bullet Outcome
The operator fires the `arch-review` package against a repository. The run reads 90
days of git history, and a hosted agent scans the codebase for deepening candidates.
A command node validates the candidates, then files at most 8 of the `Strong` and
`Worth exploring` ones as GitHub issues labelled `architecture`, `needs-triage` and
`ai-generated`. It never files a candidate whose slug an `architecture` issue already
has. Discord receives one summary, for example `jelly-swipe: 3 filed (#401 #402 #403)`.
Triage of those issues comes in task 04.

## User Story
As the operator, I want a scheduled review to propose the strongest refactors as issues,
so that the backlog gets well-described work without me writing it.

## Description
**Before you create any `.fabro` or `workflow.toml` file, invoke the `/fabro-workflow`
skill.** Read `docs/architecture-review/00-overview-and-contracts.md` first. This task
uses decisions 1, 5, 6, 9, 11, 13 and 15, contracts C2, C3, C4 and C5, and rules 1
to 12.

Create the package `.fabro/workflows/arch-review/` with three files:

1. `workflow.toml`, exactly as in *Target file 1*.
2. `workflow.fabro`, exactly as in *Target file 2*.
3. `prompts/scan.md.j2`, exactly as in *Target file 3*.

Then:

4. Add the `arch-summary` kind to `.fabro/workflows/backlog/scripts/discord-notify.sh`,
   as shown in *Notify script edits*.
5. Add the test section in *Test Expectations* to `ops/test-task-gates.sh`.

## Context Pack
- Source decisions: ADR 0012 D1, D5, D7, D9. Overview decisions listed above.
- Repo facts:
  - The package layout, from the fabro-workflow skill: `.fabro/workflows/<name>/` holds
    `workflow.toml`, `workflow.fabro` (named by `[workflow].graph`) and
    `prompts/*.md(.j2)`, which a node names as `prompt="@prompts/foo.md.j2"`.
  - The analog to copy is `.fabro/workflows/issue-triage/workflow.toml`. It runs on the
    hosted `high-reasoning` model with `kimi:kimi-k3` as the fallback, reads code with
    `contents = "read"`, writes issues with `issues = "write"`, and pushes no branch.
  - The sandbox clone is shallow and has only `main`. `git fetch --shallow-since=<date>
    origin main` deepens it. The run branch starts at `main`, so `git log HEAD` sees
    the deepened history.
  - Automation fires send no inputs. `arch-review` has no `[run.inputs]` and uses no
    `{{ inputs.* }}`.
  - The Discord script reads context keys from the run state. `checkpoints` is an
    ascending array, so a key that is published more than once is read with `tail -1`
    (contract C5). `arch_summary` is published once, but use `tail -1` for the same
    reason as the triage keys.
  - The skill that this package copies (`improve-codebase-architecture`, with the
    vocabulary of `codebase-design`) is on the operator's Mac only. Target file 3 holds
    the text that the sandbox needs. The skill's HTML report, its "which would you like
    to explore?" question, its grilling loop and its `CONTEXT.md` edits are removed on
    purpose (ADR 0012 D1).
  - `gh issue list --json` supports `body`, `state` and `stateReason` (`COMPLETED`,
    `NOT_PLANNED`), verified with gh 2.98.0. `gh issue create` prints the new issue's
    URL on stdout, for example `https://github.com/o/r/issues/402`.
- Non-goals: no triage in this task (task 04). No automation rows (task 06). No
  scheduler change. No change to `backlog` or `pr-review`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft. No automation fires the package
  until task 07, and a manual fire works end to end.

## Implementation Contract
- Expected files: `.fabro/workflows/arch-review/workflow.toml`,
  `.fabro/workflows/arch-review/workflow.fabro`,
  `.fabro/workflows/arch-review/prompts/scan.md.j2`,
  `.fabro/workflows/backlog/scripts/discord-notify.sh`, `ops/test-task-gates.sh`.
- Interfaces and names:
  - Node ids: `prep`, `history`, `scan`, `scan_gate`, `file_issues`, `summarize`,
    `finish`.
  - Context keys: `scan_status` (`ok` | `failed`), `candidate_count`, `filed_count`,
    `arch_summary`.
  - The candidates contract, `/tmp/fabro/arch/candidates.json`:
    ```json
    {
      "candidates": [
        {
          "slug": "order-intake-validation",
          "title": "refactor(orders): deepen the order intake module",
          "strength": "Strong",
          "files": ["src/orders/intake.py", "src/orders/validate.py"],
          "problem": "Markdown: why the current shape causes friction.",
          "solution": "Markdown: what would change, in plain words.",
          "benefits": "Markdown: locality, leverage, and how tests improve.",
          "diagram": "flowchart LR\n  A --> B"
        }
      ]
    }
    ```
    - `candidates` is an array of 0 to 20 entries.
    - `slug` matches `^[a-z0-9][a-z0-9-]{2,59}$` and is unique in the file.
    - `title` matches `^refactor([(][a-z0-9 ./_-]+[)])?: .+`.
    - `strength` is exactly `Strong`, `Worth exploring` or `Speculative`.
    - `files` is a non-empty array of strings.
    - `problem`, `solution`, `benefits` are non-empty strings.
    - `diagram` is optional Mermaid source. Empty means no diagram section.
- Verified external contracts: `gh issue list --json body,state,stateReason`, `gh issue
  create` prints a URL, `git fetch --shallow-since`, all listed in Repo facts.
- Behavior rules:
  - `file_issues` files candidates in this order: every `Strong`, then every `Worth
    exploring`, each group in the order the agent wrote them. It never files
    `Speculative`. It stops when 8 are filed.
  - Before each `gh issue create`, it reads the `architecture` issues again (open and
    closed) and skips a slug that any of them has (contract C2). This covers issues that
    humans closed as "not planned", and a second review that runs at the same time.
  - A candidates file that is invalid twice sets `scan_status` to `failed`. The run
    then files nothing and still sends the summary.
- Error and security rules: an issue that `gh issue create` cannot file is logged with
  `WARNING` and skipped. The run continues. No secret goes in any file.

### Target file 1: `.fabro/workflows/arch-review/workflow.toml`

```toml
_version = 1

[workflow]
graph = "workflow.fabro"

# high-reasoning is a role alias on zai:glm-5.3 (ADR 0003). When z.ai is rate-limited
# or errors, the stage falls back to Kimi's kimi-k3. No stage uses a coder box, so the
# coder scheduler does not admit this workflow (ADR 0012 D7).
[run.model.fallbacks]
high-reasoning = ["kimi:kimi-k3"]

# The review reads code and writes issues. It never writes code.
[run.integrations.github.permissions]
contents = "read"
issues = "write"

# Nothing is committed and nothing is pushed.
[run.run_branch]
push = false

[run.meta_branch]
push = false

# Discord. Hooks run with sandbox = false, inside the fabro server container, and read
# the webhook URL from /storage/secrets/discord_webhook_url. `finish` runs after
# `summarize` has published `arch_summary`, so the hook can read it.
[[run.hooks]]
id = "discord-arch-summary"
event = "stage_start"
matcher = "^finish$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh arch-summary"

[[run.hooks]]
id = "discord-failed"
event = "run_failed"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh failed"
```

### Target file 2: `.fabro/workflows/arch-review/workflow.fabro`

````dot
digraph ArchReview {
    graph [
        goal="Architecture review: scan the repository for deepening candidates, file the strongest as issues, and report",
        default_fidelity="truncate",
        // Above every node timeout: `scan` is 50m.
        stall_timeout="60m",
        model_stylesheet="
            *         { model: high-reasoning; }
            .scan     { model: high-reasoning; }
            .improve  { model: high-reasoning; }
            .triage   { model: high-reasoning; }
        "
    ]
    rankdir=LR

    start [shape=Mdiamond, label="Start"]
    exit  [shape=Msquare, label="Exit"]

    // Resets every run-local file. scan_status starts as `failed` and only scan_gate
    // sets it to `ok`, so any path that skips the scan reports it as failed.
    prep [label="Prepare the workspace", shape=parallelogram,
        script="set -e
mkdir -p /tmp/fabro/arch
rm -f /tmp/fabro/arch/candidates.json /tmp/fabro/arch/existing.json /tmp/fabro/arch/hotspots.txt
echo 0 > /tmp/fabro/arch/scan_attempts
echo '[]' > /tmp/fabro/arch/filed.json
echo failed > /tmp/fabro/arch/scan_status
date +%s > /tmp/fabro/run_started
echo ready"]

    // 90 days of history for the hot spots (ADR 0012 D1), and the architecture issues
    // that already exist, open and closed, so the scan does not propose them again.
    history [label="Read history and existing issues", shape=parallelogram,
        script="set -e
git fetch --shallow-since=\"90 days ago\" origin main || true
git log --since=\"90 days ago\" --name-only --format= HEAD 2>/dev/null | grep -v '^$' | sort | uniq -c | sort -rn | head -60 > /tmp/fabro/arch/hotspots.txt || true
gh issue list --label architecture --state all --limit 500 --json number,title,state,stateReason,body > /tmp/fabro/arch/existing_raw.json
jq '[.[] | {number, title, state, stateReason, slug: ([(.body // \"\") | capture(\"fabro:arch-candidate slug=(?<s>[a-z0-9-]+)\")? | .s][0])}]' /tmp/fabro/arch/existing_raw.json > /tmp/fabro/arch/existing.json
echo 'history: '$(wc -l < /tmp/fabro/arch/hotspots.txt)' hot files, '$(jq length /tmp/fabro/arch/existing.json)' existing architecture issues'"]

    scan [label="Scan for deepening candidates", class="scan", prompt="@prompts/scan.md.j2", timeout="50m", max_retries=1]

    // Validates the candidates contract. One repair turn; a second invalid file sets
    // scan_status=failed and exits ZERO, so the run still reports (ADR 0012 D5).
    scan_gate [label="Validate the candidates", shape=parallelogram, output_schema="routing",
        script="F=/tmp/fabro/arch/candidates.json
A=$(cat /tmp/fabro/arch/scan_attempts 2>/dev/null || echo 0)
OK=$(jq -r 'if (.candidates | type) != \"array\" then \"no\"
  elif (.candidates | length) > 20 then \"no\"
  elif ([.candidates[] | select(
      ((.slug // \"\") | tostring | test(\"^[a-z0-9][a-z0-9-]{2,59}$\") | not)
      or ((.title // \"\") | tostring | test(\"^refactor([(][a-z0-9 ./_-]+[)])?: .+\") | not)
      or (((.strength // \"\") as $s | $s == \"Strong\" or $s == \"Worth exploring\" or $s == \"Speculative\") | not)
      or ((.files | type) != \"array\") or ((.files | length) == 0)
      or ((.problem // \"\") == \"\") or ((.solution // \"\") == \"\") or ((.benefits // \"\") == \"\")
    )] | length) > 0 then \"no\"
  elif ([.candidates[].slug] | length) != ([.candidates[].slug] | unique | length) then \"no\"
  else \"yes\" end' $F 2>/dev/null || echo no)
if [ \"$OK\" != yes ]; then
  A=$((A+1))
  echo $A > /tmp/fabro/arch/scan_attempts
  if [ $A -lt 2 ]; then echo 'candidates.json is missing or invalid; rewrite it exactly per the contract' >&2; exit 1; fi
  echo failed > /tmp/fabro/arch/scan_status
  jq -nc '{context_updates:{scan_status:\"failed\",candidate_count:0}}'
  exit 0
fi
echo ok > /tmp/fabro/arch/scan_status
jq -c '{context_updates:{scan_status:\"ok\",candidate_count:(.candidates | length)}}' $F"]

    // Files at most 8 Strong / Worth exploring candidates. The architecture issues are
    // read again before every create, so a slug filed by a second review running at the
    // same time is still skipped (ADR 0012 D5). A create that fails is skipped, not
    // fatal. Labels are created first because `gh issue create` rejects an unknown one.
    file_issues [label="File Architecture issues", shape=parallelogram, output_schema="routing",
        script="set -e
F=/tmp/fabro/arch/candidates.json
gh label create architecture --color 0E8A16 --description 'Architecture review: a deepening candidate (ADR 0012)' 2>/dev/null || true
gh label create needs-triage --color D4C5F9 --description 'Waiting for triage' 2>/dev/null || true
gh label create ai-generated --color EDEDED --description 'Written by an agent' 2>/dev/null || true
jq -c '[.candidates[] | select(.strength == \"Strong\" or .strength == \"Worth exploring\")] | sort_by(if .strength == \"Strong\" then 0 else 1 end)' $F > /tmp/fabro/arch/eligible.json
echo '[]' > /tmp/fabro/arch/filed.json
K=$(jq length /tmp/fabro/arch/eligible.json)
I=0
while [ $I -lt $K ]; do
  if [ \"$(jq length /tmp/fabro/arch/filed.json)\" -ge 8 ]; then break; fi
  jq --argjson i $I '.[$i]' /tmp/fabro/arch/eligible.json > /tmp/fabro/arch/cand.json
  I=$((I+1))
  S=$(jq -r .slug /tmp/fabro/arch/cand.json)
  gh issue list --label architecture --state all --limit 500 --json body > /tmp/fabro/arch/live.json
  if jq -e --arg s \"$S\" '[.[] | (.body // \"\") | capture(\"fabro:arch-candidate slug=(?<s>[a-z0-9-]+)\")? | .s] | index($s) != null' /tmp/fabro/arch/live.json > /dev/null; then
    echo 'skip '$S': an architecture issue already has this slug'
    continue
  fi
  jq -r '([10]|implode) as $nl | \"<!-- fabro:arch-candidate slug=\" + .slug + \" -->\" + $nl + $nl
    + \"> Filed by an automated architecture review (fabro-workflows ADR 0012). Strength: **\" + .strength + \"**.\" + $nl + $nl
    + \"## Files\" + $nl + $nl + ([.files[] | \"- `\" + . + \"`\"] | join($nl)) + $nl + $nl
    + \"## Problem\" + $nl + $nl + .problem + $nl + $nl
    + \"## Solution\" + $nl + $nl + .solution + $nl + $nl
    + \"## Benefits\" + $nl + $nl + .benefits + $nl
    + (if (.diagram // \"\") == \"\" then \"\" else $nl + \"## Before and after\" + $nl + $nl + \"```mermaid\" + $nl + .diagram + $nl + \"```\" + $nl end)' /tmp/fabro/arch/cand.json > /tmp/fabro/arch/body.md
  T=$(jq -r .title /tmp/fabro/arch/cand.json)
  URL=$(gh issue create --title \"$T\" --body-file /tmp/fabro/arch/body.md --label architecture --label needs-triage --label ai-generated) || URL=''
  M=$(printf '%s' \"$URL\" | sed 's|.*/||')
  case \"$M\" in
    '' | *[!0-9]*) echo 'WARNING: could not file '$S >&2; continue ;;
  esac
  jq --argjson m \"$M\" '. + [$m]' /tmp/fabro/arch/filed.json > /tmp/fabro/arch/filed.next
  mv /tmp/fabro/arch/filed.next /tmp/fabro/arch/filed.json
  echo 'filed '$S' as #'$M
done
jq -c '{context_updates:{filed_count:length}}' /tmp/fabro/arch/filed.json"]

    // One line for Discord (ADR 0012 D9). `\"` is stripped: the hook reads the value
    // with a grep that stops at the first quote.
    summarize [label="Summarize the review", shape=parallelogram, output_schema="routing",
        script="S=$(cat /tmp/fabro/arch/scan_status 2>/dev/null || echo failed)
F=$(jq length /tmp/fabro/arch/filed.json 2>/dev/null || echo 0)
L=$(jq -r 'map(\"#\" + tostring) | join(\" \")' /tmp/fabro/arch/filed.json 2>/dev/null || echo '')
REPO=$(gh repo view --json name --jq .name 2>/dev/null || echo repository)
if [ \"$S\" = ok ]; then M=\"$REPO: $F filed\"; else M=\"$REPO: scan failed, 0 filed\"; fi
if [ -n \"$L\" ]; then M=\"$M ($L)\"; fi
M=$(printf '%s' \"$M\" | tr -d '\"')
echo \"$M\"
jq -nc --arg m \"$M\" '{context_updates:{arch_summary:$m}}'"]

    // A no-op whose stage_start fires the `discord-arch-summary` hook, after
    // `summarize` has published `arch_summary`.
    finish [label="Finish", shape=parallelogram, script="echo done"]

    // ---- Edges ----
    // `summarize` is the failure sink: every earlier node's unconditional edge lands on
    // it, so a failed review still reports.
    start -> prep
    prep -> history                 [condition="outcome=succeeded"]
    prep -> summarize
    history -> scan                 [condition="outcome=succeeded"]
    history -> summarize
    scan -> scan_gate               [condition="outcome=succeeded"]
    scan -> summarize
    scan_gate -> scan               [condition="outcome=failed"]
    scan_gate -> file_issues        [condition="outcome=succeeded && context.scan_status=ok"]
    scan_gate -> summarize          [condition="outcome=succeeded && context.scan_status=failed"]
    scan_gate -> summarize
    file_issues -> summarize        [condition="outcome=succeeded"]
    file_issues -> summarize
    summarize -> finish             [condition="outcome=succeeded"]
    summarize -> finish
    finish -> exit
}
````

The `.improve` and `.triage` rules are here now because task 04 imports the triage
phase, which uses those classes (rule 7).

### Target file 3: `.fabro/workflows/arch-review/prompts/scan.md.j2`

```markdown
# Scan this repository for deepening candidates

You look for architectural friction and propose **deepening candidates**: refactors
that turn shallow modules into deep ones. The aim is testability and code that an AI
agent can find its way around. You write one JSON file. The workflow validates it and
files the strongest candidates as GitHub issues. Nobody reads your chat output.

## Inputs

- The repository checkout in the current working directory, read-only.
- `/tmp/fabro/arch/hotspots.txt`: the files changed most often in the last 90 days,
  as `count path` lines, most changed first.
- `/tmp/fabro/arch/existing.json`: the architecture issues that already exist, open
  and closed, as `[{number, title, state, stateReason, slug}]`.
- `CONTEXT.md` at the repository root, if it exists: the domain glossary.
- `docs/adr/`, if it exists: decisions you must not re-argue.

## Boundaries

- Inspect only. Do not edit files, commit, push, install dependencies, or run `git`
  or `gh`. The workflow files the issues. You do not.
- You may run read-only searches and read any file.

## Vocabulary

Use these terms exactly. Do not write "component", "service", "API" or "boundary".

- **Module**: anything with an interface and an implementation: a function, class,
  package, or a slice through several layers.
- **Interface**: everything a caller must know to use the module correctly: the type
  signature, and also the invariants, ordering constraints, error modes, required
  configuration and performance characteristics.
- **Implementation**: the code inside a module.
- **Depth**: the behaviour a caller or test can use per unit of interface it must
  learn. A module is **deep** when a lot of behaviour sits behind a small interface,
  and **shallow** when the interface is nearly as complex as the implementation.
- **Seam**: the place where a module's interface lives, where behaviour can change
  without an edit in that place.
- **Adapter**: a concrete thing that satisfies an interface at a seam.
- **Leverage**: what callers get from depth: more capability per unit of interface.
- **Locality**: what maintainers get from depth: change, bugs and knowledge in one
  place instead of spread across callers.

Use the domain words from `CONTEXT.md` for the domain. If it defines "Order", write
"the Order intake module", not "the FooBarHandler".

## Principles

- **The deletion test.** Imagine you delete the module. If complexity disappears, it
  was a pass-through. If complexity reappears across many callers, it earned its
  place. A shallow module that fails the deletion test is a candidate.
- **The interface is the test surface.** Callers and tests cross the same seam. If a
  test must reach past the interface, the module has the wrong shape.
- **One adapter is a hypothetical seam. Two adapters is a real one.** Do not propose a
  seam unless something varies across it, usually production and test.
- Classify each candidate's dependencies, because that decides how its tests work:
  in-process (merge and test directly), local-substitutable (test with a local stand-in
  such as an in-memory database), remote but owned (a port with an HTTP adapter and an
  in-memory adapter), or true external (an injected port with a mock adapter).

## Process

1. **Scope first.** A deeper module pays off when the code around it changes often.
   Start from the top of `hotspots.txt`. If the changes are scattered with no clear
   hot spot, widen the search.
2. Read `CONTEXT.md` and the ADRs in the area you are looking at.
3. Explore. Note where you feel friction:
   - Where does one concept require you to move between many small modules?
   - Where are modules shallow, with an interface nearly as complex as the
     implementation?
   - Where were pure functions extracted only for testability, while the real bugs are
     in how they are called?
   - Where do tightly coupled modules leak across their seams?
   - Which parts are untested, or hard to test through their current interface?
4. Apply the deletion test to everything you think is shallow.
5. Drop any candidate that an entry in `existing.json` already covers, whatever its
   state. An entry with `stateReason` `NOT_PLANNED` was rejected by a human. Never
   propose it again in other words.
6. Drop a candidate that contradicts an ADR, unless the friction is severe. If you keep
   one, say which ADR it contradicts and why it is worth reopening, in `problem`.

## Strength

- `Strong`: it passes the deletion test clearly, it is in a hot spot, and the new
  interface makes tests simpler. You would do it first.
- `Worth exploring`: the friction is real, but the right shape of the deeper module is
  not yet clear, or the benefit is smaller.
- `Speculative`: a possible improvement without evidence of friction. It is not filed.

Zero candidates is a correct result for a healthy repository.

## Output

Write `/tmp/fabro/arch/candidates.json`:

    {
      "candidates": [
        {
          "slug": "order-intake-validation",
          "title": "refactor(orders): deepen the order intake module",
          "strength": "Strong",
          "files": ["src/orders/intake.py", "src/orders/validate.py"],
          "problem": "Why the current shape causes friction. Markdown.",
          "solution": "What would change, in plain words. Markdown. No interface design yet.",
          "benefits": "Locality and leverage, and how the tests improve. Markdown.",
          "diagram": "Mermaid source that shows before and after, or an empty string."
        }
      ]
    }

Rules, which `scan_gate` enforces:

- At most 20 candidates, best first.
- `slug`: lowercase letters, digits and `-`, 3 to 60 characters, starting with a letter
  or digit, unique in the file. It names the concept, not the files, so that a later
  review of the same friction produces the same slug.
- `title`: a Conventional Commit of type `refactor`, for example
  `refactor(orders): deepen the order intake module`. No `!`.
- `strength`: exactly `Strong`, `Worth exploring` or `Speculative`.
- `files`: the paths involved, relative to the repository root. At least one.
- `problem`, `solution`, `benefits`: non-empty.

After you write the file, stop.
```

The four-space indent in the Output section is deliberate: the JSON example is an
indented code block inside the Markdown file. It keeps the file free of nested fences.

### Notify script edits: `.fabro/workflows/backlog/scripts/discord-notify.sh`

1. In the usage line at the top, add `|arch-summary` after `triage-failed`.
2. In the variable list before the enrichment block, add `arch_summary=""`.
3. Inside the enrichment `if`, after the `issue_url=$(...)` assignment that task 01
   added, add:

```sh
  arch_summary=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
    | grep -o '"arch_summary":"[^"]*"' | tail -1 | cut -d'"' -f4)
  arch_summary=$(printf '%s' "$arch_summary" | sed 's/[\\"]//g' | cut -c1-800)
```

4. In the `case "$kind" in triage-question|triage-failed)` block that task 01 added,
   add this arm, because an `arch-review` run publishes `issue_number` for every issue
   it triages and the summary names no single issue:

```sh
  arch-summary)
    issue="" ;;
```

5. In the message `case`, add this arm before the `*)` arm:

```sh
  arch-summary)
    msg="🧭 fabro architecture review${subject}\\n${arch_summary}"
    ;;
```

## Acceptance Criteria
- [ ] The three package files exist with the content above.
- [ ] `python3.11 -c 'import tomllib; tomllib.load(open(".fabro/workflows/arch-review/workflow.toml","rb"))'` succeeds.
- [ ] `sh -n .fabro/workflows/backlog/scripts/discord-notify.sh` exits 0, and the file
      has an `arch-summary)` message arm.
- [ ] `./ops/test-task-gates.sh` prints `PASS: 301 checks` (285 plus 16).

## Test Expectations
Framework: the bash harness `ops/test-task-gates.sh`. Run `./ops/test-task-gates.sh`.
The helpers `extract_from`, `check` and `lastjson` already exist (task 01 describes them).

1. After the `SHARED_TRIAGE=...` lines near the top, add:

```bash
ARCH="$REPO_ROOT/.fabro/workflows/arch-review/workflow.fabro"
[ -f "$ARCH" ] || { echo "ERROR: $ARCH not found" >&2; exit 1; }
```

2. Insert this section immediately before the final `echo ""` and
   `if [ "$FAIL" -eq 0 ]; then` block:

```bash
# ---------------------------------------------------------------------------
# arch-review — scan_gate, file_issues, summarize (ADR 0012)
# ---------------------------------------------------------------------------
echo ""
echo "arch-review scan and file"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/arch"; mkdir -p "$T/bin" "$T/arch"
for n in scan_gate file_issues summarize; do
    extract_from "$ARCH" "$n" | sed "s#/tmp/fabro#$T#g" > "$T/$n.sh"
    if ! sh -n "$T/$n.sh" 2>"$T/$n.syntax"; then
        FAIL=$((FAIL + 1)); printf '  FAIL %s is not valid POSIX sh\n' "$n"
    fi
done
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue list")   cat "$GH_STATE/live.json"; exit 0 ;;
  "issue create") C=$(cat "$GH_STATE/counter" 2>/dev/null || echo 400); C=$((C+1)); echo $C > "$GH_STATE/counter"
                  echo "https://github.com/o/r/issues/$C"; exit 0 ;;
  "repo view")    echo "jelly-swipe"; exit 0 ;;
esac
exit 0
STUB
chmod +x "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

cand() { # cand <slug> <strength>
    printf '{"slug":"%s","title":"refactor: %s","strength":"%s","files":["a.py"],"problem":"p","solution":"s","benefits":"b","diagram":""}' "$1" "$1" "$2"
}

# 1. A valid file.
echo 0 > "$T/arch/scan_attempts"
printf '{"candidates":[%s,%s]}' "$(cand one-a Strong)" "$(cand two-b 'Worth exploring')" > "$T/arch/candidates.json"
OUT=$(sh "$T/scan_gate.sh" 2>&1); RC=$?
check "scan_gate valid: exit 0"        "0"  "$RC"
check "scan_gate valid: ok"            "ok" "$(jq -r '.context_updates.scan_status' <<<"$(lastjson "$OUT")")"
check "scan_gate valid: count"         "2"  "$(jq -r '.context_updates.candidate_count' <<<"$(lastjson "$OUT")")"

# 2. A duplicate slug: one repair turn, then failed.
echo 0 > "$T/arch/scan_attempts"
printf '{"candidates":[%s,%s]}' "$(cand one-a Strong)" "$(cand one-a Strong)" > "$T/arch/candidates.json"
sh "$T/scan_gate.sh" >/dev/null 2>&1; RC=$?
check "scan_gate dup: first retries"   "1" "$RC"
OUT=$(sh "$T/scan_gate.sh" 2>&1); RC=$?
check "scan_gate dup: second exit 0"   "0" "$RC"
check "scan_gate dup: failed"          "failed" "$(cat "$T/arch/scan_status")"

# 3. An unknown strength.
echo 0 > "$T/arch/scan_attempts"
printf '{"candidates":[%s]}' "$(cand one-a Maybe)" > "$T/arch/candidates.json"
sh "$T/scan_gate.sh" >/dev/null 2>&1; RC=$?
check "scan_gate bad strength: retry"  "1" "$RC"

# 4. file_issues: 5 Strong, 5 Worth exploring, 1 Speculative; c0 already filed.
{
  printf '{"candidates":['
  for i in 0 1 2 3 4; do printf '%s,' "$(cand c$i Strong)"; done
  for i in 5 6 7 8 9; do printf '%s,' "$(cand c$i 'Worth exploring')"; done
  printf '%s]}' "$(cand spec-one Speculative)"
} > "$T/arch/candidates.json"
printf '[{"body":"<!-- fabro:arch-candidate slug=c0 -->\\nold"}]' > "$T/live.json"
rm -f "$T/counter"; : > "$T/gh.log"
OUT=$(sh "$T/file_issues.sh" 2>&1)
check "file: caps at 8"                "8" "$(grep -c '^issue create' "$T/gh.log")"
check "file: skips an existing slug"   "0" "$(grep -c 'refactor: c0 --body-file' "$T/gh.log")"
check "file: never Speculative"        "0" "$(grep -c 'refactor: spec-one --body-file' "$T/gh.log")"
check "file: stops after the cap"      "0" "$(grep -c 'refactor: c9 --body-file' "$T/gh.log")"
check "file: all three labels"         "8" "$(grep -c -- '--label architecture --label needs-triage --label ai-generated' "$T/gh.log")"
check "file: marker is line one"       "<!-- fabro:arch-candidate slug=c8 -->" "$(head -1 "$T/arch/body.md")"
check "file: filed_count"              "8" "$(jq -r '.context_updates.filed_count' <<<"$(lastjson "$OUT")")"

# 5. summarize: the two shapes of the line.
echo ok > "$T/arch/scan_status"; echo '[401,402]' > "$T/arch/filed.json"
check "summary: filed"                 "jelly-swipe: 2 filed (#401 #402)" \
    "$(jq -r '.context_updates.arch_summary' <<<"$(lastjson "$(sh "$T/summarize.sh" 2>&1)")")"
echo failed > "$T/arch/scan_status"; echo '[]' > "$T/arch/filed.json"
check "summary: scan failed"           "jelly-swipe: scan failed, 0 filed" \
    "$(jq -r '.context_updates.arch_summary' <<<"$(lastjson "$(sh "$T/summarize.sh" 2>&1)")")"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE
```

That is 16 new `check` calls. The literal `\\n` in the `live.json` fixture is a JSON
newline escape, and `printf` turns `\\n` into `\n`.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: 04 (it adds the triage loop to this graph), 06

## Labels
`feature`, `arch-review`, `priority:high`

## Estimate
Large

## Risk
2 - a new package that only creates issues. Nothing fires it until task 07, and the
worst manual-fire result is up to 8 issues that a human can close.

## Validator Stopping Point
`./ops/test-task-gates.sh` prints `PASS: 301 checks`, the tomllib check passes, and
`sh -n` passes on the notify script. The operator runs `fabro validate` in task 07.
