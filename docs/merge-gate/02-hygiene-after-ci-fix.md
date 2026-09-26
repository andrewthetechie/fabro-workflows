# A CI fix that tampers with the tests is not pushed

## Tracer-Bullet Outcome
After `ci_fix_t1` or `ci_fix_t2` commits a fix, `ci_fix_gate` runs the diff-hygiene
counters again before it pushes. If any counter now blocks, the fix is not pushed, and
the run reports `blocked` with the counters' reason. A CI fix that changes only code is
pushed exactly as before.

## User Story
As the operator, I want the CI fixer, the stage most likely to make a failing test pass
by skipping it, held to the same counters as the change it is fixing, because it runs
after `merge_gate` has already approved the diff.

## Description
**Before you edit any `.fabro` file, invoke the `/fabro-workflow` skill.** Read
`docs/merge-gate/00-overview-and-contracts.md` first (decision 5, contracts C1 and C2).
This task needs task 01: it runs the `/tmp/fabro/hygiene.sh` that `hygiene` writes.
**Do not push.**

1. `.fabro/workflows/_shared/review-merge/review-merge.fabro`, node `ci_fix_gate`: add the
   hygiene re-run before the push (Edit 1), and a DOT comment above the node (Edit 2).
2. `ops/test-task-gates.sh`: add one section (Test Expectations).

## Context Pack
- Source decisions: ADR 0013 D3. Overview decision 5.
- Repo facts:
  - Every path to `ci_fix_gate` passes `hygiene` first
    (`validate → hygiene → … → merge_gate → watch_checks → ci_fix_t1|t2 → ci_fix_gate`),
    so `/tmp/fabro/hygiene.sh` always exists when this node runs.
  - `ci_fix_gate` checks the agent's report, then the file-scope ceiling, then conflict
    markers, then pushes. The push is the node's last block, and it begins with these two
    lines, verbatim:
    ```
    if git push --force-with-lease origin HEAD:\"$H\"; then
      echo '{\"context_updates\":{\"ci_fix_ok\":true}}'
    ```
    `deliver` has a similar `git push` line, but only `ci_fix_gate` is followed by
    `ci_fix_ok`. Insert only in `ci_fix_gate`.
  - `ci_fix_gate` already routes `ci_fix_ok=false` to `report_blocked`, which renders
    `/tmp/fabro/merge_block_reason`. No edge changes.
  - `hygiene.sh` prints to stdout. `ci_fix_gate` declares `output_schema="routing"`, and
    fabro reads the **last** JSON object that the node prints, so the re-run discards its
    output (`> /dev/null 2>&1`).
- Non-goals: do not run the Refuter again. Do not change `hygiene` or `merge_gate`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: local commit only.

## Implementation Contract
- Expected files: `.fabro/workflows/_shared/review-merge/review-merge.fabro`,
  `ops/test-task-gates.sh`.
- Interfaces and names: none new. It reads `hygiene.json` and `hygiene_reason.txt`
  (C1, C2).
- Verified external contracts: none new.
- Behavior rules: the re-run fails closed. A missing or invalid `hygiene.json`, or one
  without a `blocking` array, refuses the push.
- Error and security rules: none beyond the above.

### Edit 1: `ci_fix_gate`, insert immediately before the two lines quoted in Repo facts

```
sh /tmp/fabro/hygiene.sh > /dev/null 2>&1 || true
HB=$(jq -r 'if (.blocking | type) == \"array\" then (.blocking | length) else \"bad\" end' /tmp/fabro/review/hygiene.json 2>/dev/null || echo bad)
if [ \"$HB\" != 0 ]; then
  { echo 'The CI fix was not pushed: after it, the diff-hygiene counters block the merge.'; cat /tmp/fabro/review/hygiene_reason.txt 2>/dev/null || echo 'The hygiene result is missing or invalid.'; } > /tmp/fabro/merge_block_reason
  echo '{\"context_updates\":{\"ci_fix_ok\":false}}'
  exit 0
fi
```

### Edit 2: add this DOT comment immediately above the line that begins `    ci_fix_gate [label="Verify CI fix"`

```
    // Before the push, the diff-hygiene counters run again (ADR 0013 D3). A CI fixer is
    // the stage most likely to skip a failing test, and it runs after merge_gate has
    // approved the diff. The program is the one `hygiene` wrote; see that node. A
    // blocking result refuses the push and reports blocked, like every other refusal here.
```

## Acceptance Criteria
- [ ] A CI fix that adds `@pytest.mark.skip` is not pushed, `ci_fix_ok` is `false`, and
      `merge_block_reason` names `skips_added`.
- [ ] A CI fix that changes only code is pushed and reports `ci_fix_ok: true`.
- [ ] `./ops/test-task-gates.sh` prints `PASS: 376 checks`.

## Test Expectations
Framework: the bash harness `ops/test-task-gates.sh`. Insert this section immediately
before the harness's final block, after the two sections that task 01 added:

```bash
# ---------------------------------------------------------------------------
# review-merge ci_fix_gate — the counters run again before a CI fix is pushed
# (ADR 0013 D3), real git
# ---------------------------------------------------------------------------
echo ""
echo "review-merge ci_fix_gate hygiene (real git)"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/cfg"; mkdir -p "$T/review"
extract_from "$SHARED" ci_fix_gate | sed "s#/tmp/fabro#$T#g" > "$T/ci_fix_gate.sh"
extract_from "$SHARED" hygiene | sed "s#/tmp/fabro#$T#g" > "$T/hygiene_node.sh"
if ! sh -n "$T/ci_fix_gate.sh" 2>"$T/ci_fix_gate.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL ci_fix_gate is not valid POSIX sh\n'
fi

# An upstream whose `feat` branch is the PR head, cloned the way the sandbox is.
# `hygiene` runs once first, as it does in the graph, to write hygiene.sh.
cf_repo() {
    rm -rf "$T/up" "$T/wt"; mkdir -p "$T/up/tests"
    git -C "$T/up" init -q -b main .
    git -C "$T/up" config user.email t@t
    git -C "$T/up" config user.name t
    git -C "$T/up" config receive.denyCurrentBranch ignore
    printf '%s\n' 'def test_a():' '    assert 1 == 1 + 0' > "$T/up/tests/test_a.py"
    echo x > "$T/up/a.py"
    git -C "$T/up" add -A; git -C "$T/up" commit -qm init
    git -C "$T/up" checkout -qb feat; echo y >> "$T/up/a.py"; git -C "$T/up" commit -qam work
    git -C "$T/up" checkout -q main
    git clone -q "$T/up" "$T/wt"
    git -C "$T/wt" config user.email t@t
    git -C "$T/wt" config user.name t
    git -C "$T/wt" checkout -q -b feat origin/feat
    echo main > "$T/base_ref"; echo feat > "$T/head_ref"; echo 5 > "$T/pr_number"
    printf '%s\n' a.py tests/test_a.py > "$T/review/merge_base_files.txt"
    echo '{"outcome":"fixed","scope":"ci_only","summary":"s"}' > "$T/review/ci_fix_result.json"
    rm -f "$T/merge_block_reason"
    ( cd "$T/wt" && sh "$T/hygiene_node.sh" >/dev/null 2>&1 )
}
cf_run() { OUT=$( cd "$T/wt" && sh "$T/ci_fix_gate.sh" 2>&1 ); jq -r '.context_updates.ci_fix_ok' <<<"$(lastjson "$OUT")"; }

# 1. A CI fix that touches only code is pushed as before.
cf_repo
echo z >> "$T/wt/a.py"; git -C "$T/wt" commit -qam fix
check "ci fix clean: pushed"                "true"  "$(cf_run)"

# 2. A CI fix that skips the failing test is refused, not pushed, and says why.
cf_repo
printf '%s\n' 'import pytest' '@pytest.mark.skip' 'def test_a():' '    assert 1 == 1 + 0' > "$T/wt/tests/test_a.py"
git -C "$T/wt" commit -qam fix
check "ci fix skip: refused"                "false" "$(cf_run)"
check "ci fix skip: reason"                 "1"     "$(grep -c 'skips_added=1' "$T/merge_block_reason")"
check "ci fix skip: not pushed"             "2"     "$(git -C "$T/up" rev-list --count feat)"

PATH="$SAVED_PATH"
```

That is 4 new `check` calls.

## Dependencies
- Blocked by: 01
- Blocks: 05, 06

## Labels
`feature`, `review-merge`

## Estimate
Small

## Risk
2 - it adds one more fail-closed refusal to a node that already refuses on scope and on
conflict markers.

## Validator Stopping Point
`./ops/test-task-gates.sh` prints `PASS: 376 checks`.
