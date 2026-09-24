# `autofix`: run the repository's formatters between each coder stage and its review

## Tracer-Bullet Outcome
After every `coder` or `rework_t1`–`rework_t4` stage, `backlog` runs the target
repository's optional `.fabro/fix.sh` (formatters and machine-applicable lint fixes)
before the change is diffed, validated and reviewed. A `cargo fmt` diff or an
auto-fixable clippy lint no longer fails `validate` and costs a rework round. A
repository with no `fix.sh` behaves exactly as before.

## User Story
As the operator, I want mechanical formatting and lint faults fixed by the repository's
own tools before validation, so that a run does not spend a rework stage and a second
`validate` on something a formatter fixes in seconds.

## Description
1. `.fabro/workflows/backlog/workflow.fabro`: add a new command node `autofix` directly
   before the `prep_review` node definition.
2. In the same file, retarget the six edges that go from `coder` / `rework_t1..t4` to
   `prep_review` so they go to `autofix`, and add three edges out of `autofix`.
3. `ops/README.md`: name the new optional contract file.
4. `ops/test-task-gates.sh`: add a test section.

The per-repository `fix.sh` for writers-app is task 05, in the writers-app repository.
This task only adds the node that runs it.

## Context Pack
- Source decisions: overview decision 8 and contract C4. ADR 0011 D5.
- Evidence: `validate` failed 33 times in 246 visits. 31 of those were writers-app (31 of
  154 visits there), mostly `cargo fmt` "Diff in …" and clippy lints such as
  `item in documentation is missing backticks`. Each cost a `rework_t1` stage (p50 3.5
  min) and a second `validate` (p50 1.8 min).
- Repo facts:
  - `prep` runs `./.fabro/setup.sh` once at the start of the run, so the repository's
    tools (cargo, npm packages, uv environment) are installed before any coder stage.
  - Fabro commits a checkpoint after every stage. `prep_review` then diffs committed
    history (`git diff $BASE..HEAD`), so edits made by `autofix` are in the diff the
    reviewer sees. Verbatim start of `prep_review`:
    ```
        prep_review [label="Prepare review inputs", shape=parallelogram, output_schema="routing",
            script="set -e
    BASE=$(cat /tmp/fabro/task_base_sha)
    git diff $BASE..HEAD > /tmp/fabro/review/diff.patch
    ```
  - The edges to change, verbatim, from the `// ---- Edges ----` block:
    ```
        coder -> prep_review            [condition="outcome=succeeded"]
        ...
        coder -> prep_review            [condition="outcome=partially_succeeded"]
        coder -> human_rescue

        prep_review -> rework_router    [condition="context.diff_too_large=true"]
        prep_review -> validate
        ...
        rework_t1 -> prep_review        [condition="outcome=succeeded"]
        rework_t1 -> human_rescue
        rework_t2 -> prep_review        [condition="outcome=succeeded"]
        rework_t2 -> human_rescue
        rework_t3 -> prep_review        [condition="outcome=succeeded"]
        rework_t3 -> human_rescue
        rework_t4 -> prep_review        [condition="outcome=succeeded"]
        rework_t4 -> human_rescue
    ```
    (The two `coder -> prep_review` lines have a two-line `//` comment between them. Keep
    it.)
- Non-goals: do not run `autofix` in the shared merge phase or in `pr-review`. Do not
  change `validate`, `prep_review` or any prompt. Do not write a `fix.sh` for any target
  repository here.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  .fabro/workflows/backlog/workflow.fabro
  ops/README.md
  ops/test-task-gates.sh
  ```
- **Edit 1: new node.** Insert this block (the comment and the node) directly before the
  line `    prep_review [label="Prepare review inputs", …`, with a blank line after it:
  ```
    // autofix: formatters and machine-applicable lint fixes between every coder or rework
    // stage and its review (ADR 0011 D5). It runs the target repository's optional
    // `.fabro/fix.sh` and ALWAYS succeeds: `validate` is the gate, not this node. Its
    // edits are committed by the checkpoint after this stage, before prep_review computes
    // the diff, so the reviewer reviews the formatted code. 31 of writers-app's 154
    // validate visits failed, mostly on `cargo fmt` diffs and clippy lints, and each one
    // cost a rework_t1 stage plus a second validate.
    //
    // No output_schema: this node publishes no context key. The `outcome=failed` edge
    // sends a timeout on to prep_review as well, so a hung formatter costs at most 15m
    // and never parks the run on human_rescue; the unconditional edge to human_rescue
    // exists only because every command node must have one.
    autofix [label="Autofix (formatters and lint --fix)", shape=parallelogram, timeout="15m",
        script="if [ ! -f ./.fabro/fix.sh ]; then
  echo 'autofix: no .fabro/fix.sh in this repository; nothing to do'
  exit 0
fi
if [ -x ./.fabro/fix.sh ]; then ./.fabro/fix.sh; RC=$?; else bash ./.fabro/fix.sh; RC=$?; fi
echo 'autofix: .fabro/fix.sh exited '$RC'; validate is the gate, so this stage succeeds either way'
git status --short 2>/dev/null || true
exit 0"]
  ```
  Notes: no `output_schema`, because this node prints no routing object (overview rule
  2). The script contains no backslash and no `"` character, so it needs no DOT
  escaping. Keep it that way.
- **Edit 2: edges.** Make exactly these replacements, and change nothing else:
  | Before | After |
  |---|---|
  | `    coder -> prep_review            [condition="outcome=succeeded"]` | `    coder -> autofix                [condition="outcome=succeeded"]` |
  | `    coder -> prep_review            [condition="outcome=partially_succeeded"]` | `    coder -> autofix                [condition="outcome=partially_succeeded"]` |
  | `    rework_t1 -> prep_review        [condition="outcome=succeeded"]` | `    rework_t1 -> autofix            [condition="outcome=succeeded"]` |
  | `    rework_t2 -> prep_review        [condition="outcome=succeeded"]` | `    rework_t2 -> autofix            [condition="outcome=succeeded"]` |
  | `    rework_t3 -> prep_review        [condition="outcome=succeeded"]` | `    rework_t3 -> autofix            [condition="outcome=succeeded"]` |
  | `    rework_t4 -> prep_review        [condition="outcome=succeeded"]` | `    rework_t4 -> autofix            [condition="outcome=succeeded"]` |

  Then insert these three lines, plus a blank line, directly **before**
  `    prep_review -> rework_router    [condition="context.diff_too_large=true"]`:
  ```
      autofix -> prep_review          [condition="outcome=succeeded"]
      autofix -> prep_review          [condition="outcome=failed"]
      autofix -> human_rescue
  ```
  The `outcome=failed` edge is deliberate. A timed-out `autofix` still goes on to review,
  and `validate` catches anything it left broken.
- **Edit 3: `ops/README.md`.** Replace
  ```
  10. **Contract scripts** (`.fabro/setup.sh`, `.fabro/ci.sh`) live in each target repo,
     not this repo. Every target repo must have both or every run fails at `prep`.
  ```
  with
  ```
  10. **Contract scripts** (`.fabro/setup.sh`, `.fabro/ci.sh`) live in each target repo,
     not this repo. Every target repo must have both or every run fails at `prep`. A third,
     `.fabro/fix.sh`, is optional: `backlog`'s `autofix` node runs it after every coder or
     rework stage, and it may hold only formatters and machine-applicable lint fixes
     (ADR 0011 D5). Its exit status is ignored.
  ```
- **Edit 4: test section.** Insert into `ops/test-task-gates.sh` directly before the
  final block that begins `# ------…` / `echo ""` / `if [ "$FAIL" -eq 0 ]; then`.
  `stage <node>` writes `$T/<node>.sh` from `backlog/workflow.fabro` and runs `sh -n` on
  it. `$ORIG_PATH` is the PATH with the real `git`.
  ```bash
# ---------------------------------------------------------------------------
# autofix — the optional per-repository .fabro/fix.sh, always fail-open (ADR 0011 D5)
# ---------------------------------------------------------------------------
echo ""
echo "autofix"
SAVED_PATH="$PATH"
PATH="$ORIG_PATH"
T="$WORK/autofix"; mkdir -p "$T"; stage autofix
af_repo() {
    rm -rf "$T/wt"; mkdir -p "$T/wt"
    git -C "$T/wt" init -q -b main .
    printf 'unformatted\n' > "$T/wt/a.txt"
}
af_run() { ( cd "$T/wt" && sh "$T/autofix.sh" 2>&1 ); }

# 1. No fix.sh: nothing happens, and the stage still succeeds.
af_repo
OUT=$(af_run); RC=$?
check "no fix.sh: exit 0"          "0" "$RC"
check "no fix.sh: says so"         "1" "$(grep -c 'no .fabro/fix.sh' <<<"$OUT")"

# 2. An executable fix.sh that reformats a file: the edit is left in the tree.
af_repo
mkdir -p "$T/wt/.fabro"
printf '%s\n' '#!/bin/sh' 'printf "formatted\n" > a.txt' > "$T/wt/.fabro/fix.sh"
chmod +x "$T/wt/.fabro/fix.sh"
OUT=$(af_run); RC=$?
check "fix.sh ran: exit 0"         "0" "$RC"
check "fix.sh ran: file changed"   "formatted" "$(cat "$T/wt/a.txt")"

# 3. A fix.sh that fails: output kept, stage still succeeds.
af_repo
mkdir -p "$T/wt/.fabro"
printf '%s\n' '#!/bin/sh' 'echo clippy blew up' 'exit 3' > "$T/wt/.fabro/fix.sh"
chmod +x "$T/wt/.fabro/fix.sh"
OUT=$(af_run); RC=$?
check "failing fix.sh: exit 0"     "0" "$RC"
check "failing fix.sh: rc reported" "1" "$(grep -c 'fix.sh exited 3' <<<"$OUT")"
check "failing fix.sh: output kept" "1" "$(grep -c 'clippy blew up' <<<"$OUT")"

# 4. A fix.sh without the executable bit still runs, under bash.
af_repo
mkdir -p "$T/wt/.fabro"
printf '%s\n' 'printf "formatted\n" > a.txt' > "$T/wt/.fabro/fix.sh"
OUT=$(af_run); RC=$?
check "non-executable fix.sh: exit 0" "0" "$RC"
check "non-executable fix.sh: ran"  "formatted" "$(cat "$T/wt/a.txt")"

PATH="$SAVED_PATH"
  ```
- Interfaces and names: node id `autofix`. Contract file `.fabro/fix.sh` (overview C4).
- Verified external contracts: None. It uses only `sh`, `bash` and `git status`.
- Behavior rules: an absent `fix.sh` means do nothing. An executable one runs directly.
  A non-executable one runs under `bash`. Any exit status is reported and ignored. The
  node always exits 0.
- Error and security rules: `fix.sh` runs with the sandbox's environment. The node prints
  nothing beyond `fix.sh`'s own output and `git status --short`.

## Acceptance Criteria
- [ ] `autofix` exists with `timeout="15m"` and no `output_schema`.
- [ ] No edge from `coder` or `rework_t1..t4` goes to `prep_review` any more. All six go
      to `autofix`.
- [ ] `autofix` has exactly three outgoing edges: `succeeded → prep_review`,
      `failed → prep_review`, and unconditional `→ human_rescue`.
- [ ] `./ops/test-task-gates.sh` passes with 9 more checks than before this task.

## Test Expectations
- Framework: the bash harness `ops/test-task-gates.sh`. Command: `./ops/test-task-gates.sh`.
- Cases, expected literals:
  1. no `fix.sh`: exit `0`; output contains `no .fabro/fix.sh`.
  2. executable `fix.sh` that rewrites `a.txt`: exit `0`; `a.txt` is `formatted`.
  3. `fix.sh` printing `clippy blew up` and exiting 3: exit `0`; output has
     `fix.sh exited 3` and `clippy blew up`.
  4. non-executable `fix.sh`: exit `0`; `a.txt` is `formatted`.
- This section was run against a scratch copy with Edits 1 and 2 applied (2026-09-24),
  and all 9 checks passed.
- Structural check of the edges (python3, repository root). It prints `ok`:
  ```sh
  python3 - <<'EOF'
  import re
  s = open(".fabro/workflows/backlog/workflow.fabro").read()
  assert not re.search(r"^\s+(coder|rework_t[1-4]) -> prep_review", s, re.M), "old edge left"
  assert len(re.findall(r"^\s+(coder|rework_t[1-4]) -> autofix", s, re.M)) == 6
  assert len(re.findall(r"^\s+autofix -> ", s, re.M)) == 3
  print("ok")
  EOF
  ```

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: 05 (writers-app `fix.sh`, which does nothing until this is deployed), 11
  (validate)

## Labels
`enhancement`, `backlog`, `priority:medium`

## Estimate
Small

## Risk
2 - A new node sits on every task's path. It is fail-open by construction, and it is a
no-op in every repository until one adds `fix.sh`.

## Validator Stopping Point
`./ops/test-task-gates.sh` passes, and the structural edge check prints `ok`.
