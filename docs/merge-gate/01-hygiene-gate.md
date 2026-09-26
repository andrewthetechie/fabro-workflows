# The diff-hygiene counters block a tampered PR from auto-merging

## Tracer-Bullet Outcome
Every PR that reaches a green `validate` in the shared merge phase gets
`/tmp/fabro/review/hygiene.json`: counts of test tampering and code erosion over the
lines the PR added. `merge_gate` check 13 blocks the auto-merge when any tamper counter
is above zero, or when an erosion counter is over its threshold in a repository that set
`erosion_mode: "block"`. A clean PR merges exactly as before.

## User Story
As the operator, I want a PR that deletes, skips or empties a test to stop in front of
`main`, whatever the reviewers said about it, so that no model can get a green run by
weakening the tests.

## Description
**Before you edit any `.fabro` file, invoke the `/fabro-workflow` skill.** Read
`docs/merge-gate/00-overview-and-contracts.md` first (contracts C1, C2, C4, C5 and rules
1 to 9). This task does not depend on any other task in the series. **Do not push.**

1. `.fabro/workflows/_shared/review-merge/review-merge.fabro`: add the `hygiene` node
   (Edit 1).
2. The same file: route `validate` through it (Edit 2).
3. The same file, node `merge_gate`: add check 13 and update the check count in the DOT
   comment above the node (Edit 3).
4. `ops/test-task-gates.sh`: add two sections (Test Expectations).

## Context Pack
- Source decisions: ADR 0013 D1, D2, D3 (the program is written to a file so that task 02
  can run it again). Overview decisions 6, 7, 8, 12.
- Repo facts:
  - `validate` runs `./.fabro/setup.sh` and `./.fabro/ci.sh`. It routes
    `validate -> mark_needs_human` (CI red twice or a failure, weight 10),
    `validate -> review_fix` (CI red), and today `validate -> deliver` unconditionally
    (CI green). Only that last edge changes.
  - On success `validate` writes this placeholder to `/tmp/fabro/needs_human_reason`:
    "CI passed, but the run stopped after validate and before the review was
    delivered." It stays true while `hygiene` runs, so `hygiene` writes no reason of its
    own (rule 8).
  - `/tmp/fabro/base_ref` holds the PR base branch name. `prep_review` has already run
    `git fetch origin "$B"`, so `origin/<base>` exists in the sandbox clone.
  - `merge_gate` numbers its checks with inline `# N.` lines. It is the one node in this
    repository that keeps `#` comments inside a script. Its helper is
    `fail() { E=false; printf '%s' "$1" > /tmp/fabro/merge_block_reason; }`, and each
    check is guarded by `if [ "$E" = true ]`. After check 12, the node ends with a block
    that begins with these two lines, verbatim:
    ```
    if [ \"$E\" = true ]; then
      gh pr view \"$PR\" --json title --jq .title > /tmp/fabro/commit_subject.txt
    ```
  - The DOT comment above `merge_gate` begins:
    `// The thirteen checks (1 to 12, and 2b) are marked inline, one line each, next to the code that runs`.
  - The sandbox `awk` is **mawk** in every profile image, and `fabro-python-node` has
    **jq 1.6**. The program below was tested on both, and on gawk and jq 1.7.
- Non-goals: do not change `ci_fix_gate` (task 02), `render.jq` (task 04) or any
  stylesheet. Do not add a `.fabro/hygiene.json` to any target repository.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: local commit only; the series is pushed in task 06.

## Implementation Contract
- Expected files: `.fabro/workflows/_shared/review-merge/review-merge.fabro`,
  `ops/test-task-gates.sh`.
- Interfaces and names: node id `hygiene`; files `/tmp/fabro/hygiene.sh`,
  `/tmp/fabro/review/hygiene.json`, `/tmp/fabro/review/hygiene_reason.txt`,
  `/tmp/fabro/_hygiene/` (contract C1); JSON shape C2; config C4; counters C5.
- Verified external contracts: `git diff -U0` hunk headers are
  `@@ -<old>[,<n>] +<new>[,<n>] @@`. A deleted file shows `+++ /dev/null`, and a rename
  shows no `---`/`+++` pair at all (git's rename detection is on by default). Both are
  covered by fixtures.
- Behavior rules:
  - `hygiene.sh` always exits 0 after it writes `hygiene.json`. A failure to count is
    recorded as `blocking: ["hygiene_error"]`, which fails closed.
  - Check 13 blocks unless `.blocking` is an **empty array**. A missing file, invalid
    JSON, or a file with no `blocking` key blocks.
- Error and security rules: the config is read from `origin/<base>`, never from the
  working tree, and a PR that touches `.fabro/hygiene.json` trips `config_changed`.

### Edit 1: add the `hygiene` node

Insert this block, followed by one blank line, immediately before the line that begins
`    deliver [label="Push and label"`. It is already escaped for the `.fabro` file, so
paste it as it is:

```
    // ---- Diff hygiene (ADR 0013 D1 to D3) ----
    // Counts test tampering and code erosion over the lines this PR added, and writes
    // review/hygiene.json. merge_gate check 13 blocks on its `blocking` list.
    //
    // The counter program is written to /tmp/fabro/hygiene.sh and then run, rather than
    // run inline, because ci_fix_gate runs the SAME program again after a CI fix. Two
    // copies of it would drift.
    //
    // It reads .fabro/hygiene.json from the BASE branch, never the PR head, and a PR that
    // touches that file trips `config_changed`: a PR cannot loosen the rules that judge
    // it. The patterns are written with bracket expressions ([.], [(], [[]) because the
    // escaped quote is the only backslash a .fabro file may hold. The fixtures in
    // ops/test-task-gates.sh are the contract for what each counter matches
    // (docs/merge-gate/00-overview-and-contracts.md, C5). The sandbox awk is mawk.
    //
    // It prints no routing object and never chooses a route. A failure to COUNT is
    // written into hygiene.json as `hygiene_error`, which blocks the merge; only a failure
    // of the node itself (it cannot write the program) takes the edge to mark_needs_human.
    hygiene [label="Diff hygiene counters", shape=parallelogram, timeout="5m",
        script="set -e
mkdir -p /tmp/fabro/review
cat > /tmp/fabro/hygiene.sh <<'HYGIENE_SH'
B=$(cat /tmp/fabro/base_ref)
R=/tmp/fabro/review
W=/tmp/fabro/_hygiene
O=$R/hygiene.json
rm -rf $W && mkdir -p $W $R
rm -f $O $R/hygiene_reason.txt
herr() {
  jq -n --arg why \"$1\" '{version:1, config:\"default\", error:$why, tamper:{}, erosion:{}, blocking:[\"hygiene_error\"], samples:[]}' > $O
  echo 'The diff-hygiene counters could not be computed ('\"$1\"'), so the merge is refused.' > $R/hygiene_reason.txt
  exit 0
}
jq -n '{erosion_mode:\"report\", erosion_threshold:3, erosion_thresholds:{},
  test_paths:[\"(^|/)(tests?|__tests__|spec)/\", \"_test[.][a-z]+$\", \"[.](test|spec)[.][a-z]+$\", \"(^|/)test_[^/]*[.]py$\"],
  exclude_paths:[\"(^|/)(package-lock[.]json|yarn[.]lock|pnpm-lock[.]yaml|bun[.]lockb?|Cargo[.]lock|poetry[.]lock|uv[.]lock)$\", \"[.]min[.](js|css)$\", \"(^|/)(dist|build|vendor|node_modules)/\"]}' > $W/default.json
CFG=default
cp $W/default.json $W/cfg.json
if git cat-file -e \"origin/$B:.fabro/hygiene.json\" 2>/dev/null; then
  CFG=repo
  if ! git show \"origin/$B:.fabro/hygiene.json\" > $W/repo.json 2>/dev/null || ! jq -s '.[0] + .[1]' $W/default.json $W/repo.json > $W/cfg.json 2>/dev/null || [ \"$(jq -r '(.erosion_mode == \"report\" or .erosion_mode == \"block\")
        and ((.erosion_threshold | type) == \"number\")
        and ((.erosion_thresholds | type) == \"object\")
        and ((.test_paths | type) == \"array\") and ((.test_paths | length) > 0)
        and all(.test_paths[]; type == \"string\")
        and ((.exclude_paths | type) == \"array\") and all(.exclude_paths[]; type == \"string\")' $W/cfg.json 2>/dev/null)\" != true ]; then
    CFG=invalid
    cp $W/default.json $W/cfg.json
  fi
fi
TP=$(jq -r '.test_paths | map(\"(\" + . + \")\") | join(\"|\")' $W/cfg.json)
EX=$(jq -r '.exclude_paths | map(\"(\" + . + \")\") | join(\"|\")' $W/cfg.json)
git diff -U0 --no-color --no-ext-diff \"origin/$B...HEAD\" > $W/diff 2>/dev/null || herr 'git diff against the base failed'
CC=$(git diff --name-only \"origin/$B...HEAD\" 2>/dev/null | grep -cxF .fabro/hygiene.json || true)
awk -v tp=\"$TP\" -v ex=\"$EX\" '
function istest(f) { return tp != \"\" && f ~ tp }
function skipped(f) { return ex != \"\" && f ~ ex }
function assertish(f) { return istest(f) || f ~ /[.]rs$/ }
function emit(c, f, n, t) { print c S f S n S substr(t, 1, 160) }
function trim(s) { gsub(/^ +| +$/, \"\", s); return s }
function flush(  i, d) {
  d = ra - aa
  for (i = 1; i <= d; i++) emit(\"asserts_removed\", file, rl[i], rt[i])
  ra = 0; aa = 0
}
function same(t,  s, a, b, p) {
  if (match(t, /expect[(][^()]*[)][.]to(Be|Equal|StrictEqual)[(][^()]*[)]/)) {
    s = substr(t, RSTART, RLENGTH)
    a = substr(s, 8); a = substr(a, 1, index(a, \")\") - 1)
    p = index(s, \").to\"); b = substr(s, p + 4); b = substr(b, index(b, \"(\") + 1); b = substr(b, 1, length(b) - 1)
    if (trim(a) == trim(b)) return 1
  }
  if (match(t, /(assert_eq!|assertEqual|assertEquals)[(][^(),]*,[^(),]*[)]/)) {
    s = substr(t, RSTART, RLENGTH)
    s = substr(s, index(s, \"(\") + 1); s = substr(s, 1, length(s) - 1)
    p = index(s, \",\")
    if (trim(substr(s, 1, p - 1)) == trim(substr(s, p + 1))) return 1
  }
  if (match(t, /assert +[a-zA-Z0-9_.]+ *== *[a-zA-Z0-9_.]+ *($|,|#)/)) {
    s = substr(t, RSTART + 6, RLENGTH - 6); sub(/ *($|,|#)$/, \"\", s)
    p = index(s, \"==\")
    if (trim(substr(s, 1, p - 1)) == trim(substr(s, p + 2))) return 1
  }
  return 0
}
BEGIN {
  S = sprintf(\"%c\", 31); ra = 0; aa = 0; del = 0; head = 0
  SKIP_ANY = \"#[[]ignore[]]|@pytest[.]mark[.](skip|xfail)|pytest[.](skip|xfail)[(]|@unittest[.]skip|@(Disabled|Ignore)([^a-zA-Z]|$)\"
  SKIP_TEST = \"(^|[^a-zA-Z0-9_])(it|describe|test|context|suite)[.](skip|only|todo)[(]|(^|[^a-zA-Z0-9_.])x(it|describe|test)[(]\"
  ASSERT = \"(^|[^a-zA-Z0-9_])(assert[a-zA-Z_]*[ .(!]|expect[(])\"
  TAUT = \"expect[(] *(true|false|null|undefined|[0-9]+) *[)]|(^|[^a-zA-Z0-9_])assert +(True|1 *== *1) *($|,|#)|assert![(] *true *[)]\"
  BROAD = \"except +(Exception|BaseException)( |:|$)|except *:|catch *([(][^)]*[)])? *[{] *[}]\"
  TYPE = \"#[ ]*type: *ignore|[ (]as any([^a-zA-Z0-9_]|$)|as unknown as|@ts-ignore|@ts-expect-error|cast[(] *Any\"
  LINT = \"eslint-disable|noqa|#!?[[]allow[(]|pylint: *disable|biome-ignore\"
  UNWRAP = \"[.]unwrap[(][)]|[.]expect[(]\"
  TODO = \"(^|[^a-zA-Z0-9_])(TODO|FIXME|XXX)([^a-zA-Z0-9_]|$)\"
  LINKED = \"#[0-9]+|/issues/[0-9]+\"
}
/^diff --git / { flush(); head = 1; del = 0; file = \"\"; oldf = \"\"; next }
head && /^--- / { oldf = substr($0, 7); next }
head && /^[+][+][+] / {
  if ($0 == \"+++ /dev/null\") { file = oldf; del = 1; if (istest(oldf) && !skipped(oldf)) emit(\"tests_deleted\", oldf, \"\", \"file deleted\") }
  else file = substr($0, 7)
  next
}
/^@@ / {
  flush(); head = 0
  split($0, p, \" \")
  split(substr(p[2], 2), q, \",\"); ol = q[1] + 0
  split(substr(p[3], 2), q, \",\"); nl = q[1] + 0
  next
}
head { next }
/^[+]/ {
  t = substr($0, 2)
  if (!skipped(file)) {
    if (t ~ SKIP_ANY || (istest(file) && t ~ SKIP_TEST)) emit(\"skips_added\", file, nl, t)
    if (assertish(file) && t ~ ASSERT) aa++
    if (assertish(file) && (t ~ TAUT || same(t))) emit(\"tautologies_added\", file, nl, t)
    if (t ~ BROAD) emit(\"broad_except\", file, nl, t)
    if (t ~ TYPE) emit(\"type_escape\", file, nl, t)
    if (t ~ LINT) emit(\"lint_disable\", file, nl, t)
    if (file ~ /[.]rs$/ && !istest(file) && t ~ UNWRAP) emit(\"rust_unwrap\", file, nl, t)
    if (t ~ TODO && t !~ LINKED) emit(\"todo_unlinked\", file, nl, t)
  }
  nl++
  next
}
/^-/ {
  t = substr($0, 2)
  if (!del && !skipped(file) && assertish(file) && t ~ ASSERT) { ra++; rl[ra] = ol; rt[ra] = t }
  ol++
  next
}
END { flush() }
' $W/diff > $W/hits || herr 'the counter program failed'
jq -Rn --slurpfile cfg $W/cfg.json --arg config \"$CFG\" --argjson cc \"${CC:-0}\" --arg base \"$(git rev-parse \"origin/$B\" 2>/dev/null)\" --arg head \"$(git rev-parse HEAD 2>/dev/null)\" '
  ([31] | implode) as $S
  | $cfg[0] as $c
  | [\"tests_deleted\", \"skips_added\", \"asserts_removed\", \"tautologies_added\"] as $TK
  | [\"broad_except\", \"type_escape\", \"lint_disable\", \"rust_unwrap\", \"todo_unlinked\"] as $EK
  | [inputs | split($S) | {counter: .[0], file: .[1], line: (.[2] | tonumber? // null), text: .[3]}] as $h
  | (reduce $TK[] as $k ({}; .[$k] = ([$h[] | select(.counter == $k)] | length)) | .config_changed = $cc) as $tamper
  | (reduce $EK[] as $k ({}; .[$k] = ([$h[] | select(.counter == $k)] | length))) as $erosion
  | ( [$tamper | to_entries[] | select(.value > 0) | .key]
      + (if $c.erosion_mode == \"block\"
         then [$erosion | to_entries[] | select(.value > ($c.erosion_thresholds[.key] // $c.erosion_threshold)) | .key]
         else [] end)
      + (if $config == \"invalid\" then [\"config_invalid\"] else [] end) ) as $blocking
  | {version: 1, base: $base, head: $head, config: $config,
     erosion_mode: $c.erosion_mode, erosion_threshold: $c.erosion_threshold,
     tamper: $tamper, erosion: $erosion, blocking: $blocking,
     samples: ($h | map(. + {o: (if (.counter | IN($TK[])) then 0 else 1 end)}) | sort_by(.o) | map(del(.o)) | .[:20])}
' $W/hits > $W/out.json 2>/dev/null || herr 'the counters could not be summarised'
mv $W/out.json $O
jq -r '. as $r | (.blocking | length) as $n | if $n == 0 then empty else
  \"Diff hygiene blocks the merge: \"
  + ([.blocking[] | . as $k | $k + (if $k == \"config_invalid\" then \"\" else \"=\" + ((($r.tamper + $r.erosion)[$k]) | tostring) end)] | join(\", \"))
  + (if .config == \"invalid\" then \". The base branch .fabro/hygiene.json is not valid, so the defaults were used\" else \"\" end)
  + \". Samples: \"
  + ([.samples[] | select(.counter as $c | $r.blocking | index($c) != null) | .file + (if .line then \":\" + (.line | tostring) else \"\" end) + \" \" + .counter + \" `\" + .text + \"`\"] | .[:5] | join(\"; \"))
  end' $O > $R/hygiene_reason.txt 2>/dev/null || true
[ -s $R/hygiene_reason.txt ] || rm -f $R/hygiene_reason.txt
exit 0
HYGIENE_SH
sh /tmp/fabro/hygiene.sh
jq -r '\"diff hygiene: config \" + .config + \", blocking [\" + (.blocking | join(\", \")) + \"]\"' /tmp/fabro/review/hygiene.json"]
```

### Edit 2: route `validate` through `hygiene`

In the edges section, replace this line:

```
    validate -> deliver
```

with:

```
    validate -> hygiene

    hygiene -> deliver                 [condition="outcome=succeeded"]
    hygiene -> mark_needs_human
```

(Task 03 changes `hygiene -> deliver` to `hygiene -> refute_prep`.)

### Edit 3: `merge_gate` check 13

Insert this block immediately before the two lines quoted in Repo facts
(`if [ \"$E\" = true ]; then` followed by `gh pr view ... > /tmp/fabro/commit_subject.txt`):

```
# 13. diff hygiene: no tamper counter, and no erosion counter over its threshold in block mode (ADR 0013 D1)
if [ \"$E\" = true ]; then
  HB=$(jq -r 'if (.blocking | type) == \"array\" then (.blocking | length) else \"bad\" end' /tmp/fabro/review/hygiene.json 2>/dev/null || echo bad)
  if [ \"$HB\" != 0 ]; then fail \"$(cat /tmp/fabro/review/hygiene_reason.txt 2>/dev/null || echo 'The diff-hygiene result is missing or invalid, so the merge is refused.')\"; fi
fi
```

Then, in the `//` DOT comment above `merge_gate`, change `The thirteen checks (1 to 12,
and 2b)` to `The fourteen checks (1 to 13, and 2b)`.

## Acceptance Criteria
- [ ] A PR that deletes a test file, adds a skip, removes an assertion, adds an
      assertion that cannot fail, or edits `.fabro/hygiene.json` gets a non-empty
      `blocking` list, and `merge_gate` reports it as not eligible, with a reason that
      names the counter and shows samples.
- [ ] A PR that moves a test file, or rewrites an assertion in place, is not blocked.
- [ ] Erosion counters are counted and never block in `report` mode. In `block` mode,
      configured on the base branch, they block above the threshold.
- [ ] An invalid base-branch config blocks (`config_invalid`). A base that cannot be
      diffed blocks (`hygiene_error`).
- [ ] `./ops/test-task-gates.sh` prints `PASS: 372 checks`.

## Test Expectations
Framework: the bash harness `ops/test-task-gates.sh`. Run `./ops/test-task-gates.sh`.

Insert both sections below, in this order, immediately before the harness's final block
(the `# ----` rule line followed by `echo ""` and `if [ "$FAIL" -eq 0 ]; then`). `$SHARED`
already names `_shared/review-merge/review-merge.fabro`. The first section uses the real
`git`, so it restores `$ORIG_PATH` first (see the comment at the top of the harness).

```bash
# ---------------------------------------------------------------------------
# review-merge hygiene — tamper and erosion counters (ADR 0013 D1, D2), real git
# ---------------------------------------------------------------------------
# The fixtures ARE the counters' contract (docs/merge-gate/00, C5): a pattern
# change starts here. Real git, because what is under test is what
# `git diff -U0 origin/main...HEAD` shows for each kind of change.
echo ""
echo "review-merge hygiene (real git)"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/hyg"; mkdir -p "$T"
extract_from "$SHARED" hygiene | sed "s#/tmp/fabro#$T#g" > "$T/hygiene_node.sh"
if ! sh -n "$T/hygiene_node.sh" 2>"$T/hygiene_node.syntax"; then
    FAIL=$((FAIL + 1)); printf '  FAIL hygiene is not valid POSIX sh\n'
fi

# A fresh upstream with a Python test, a module and a Rust file, cloned onto a run
# branch. $1, when given, is the base branch's .fabro/hygiene.json.
hy_repo() {
    rm -rf "$T/up" "$T/wt" "$T/review" "$T/_hygiene"
    mkdir -p "$T/up/tests" "$T/up/src" "$T/up/.fabro"
    git -C "$T/up" init -q -b main .
    git -C "$T/up" config user.email t@t
    git -C "$T/up" config user.name t
    printf '%s\n' 'def test_a():' '    assert add(1, 2) == 3' '    assert add(0, 0) == 0' > "$T/up/tests/test_math.py"
    printf '%s\n' 'def add(a, b):' '    return a + b' > "$T/up/src/math.py"
    printf '%s\n' 'fn f() -> u8 { 1 }' > "$T/up/src/lib.rs"
    if [ -n "${1:-}" ]; then printf '%s' "$1" > "$T/up/.fabro/hygiene.json"; fi
    git -C "$T/up" add -A
    git -C "$T/up" commit -qm init
    git clone -q "$T/up" "$T/wt"
    git -C "$T/wt" config user.email t@t
    git -C "$T/wt" config user.name t
    git -C "$T/wt" checkout -qb fabro/run/01TEST
    echo main > "$T/base_ref"
}
hy_commit() { git -C "$T/wt" add -A; git -C "$T/wt" commit -qm change; }
hy_run() { ( cd "$T/wt" && sh "$T/hygiene_node.sh" >/dev/null 2>&1 ); }
hy() { jq -r "$1" "$T/review/hygiene.json"; }

# 1. A real change with no test impact is clear, and leaves no reason file.
hy_repo
printf '%s\n' 'def add(a, b):' '    """Add."""' '    return a + b' > "$T/wt/src/math.py"; hy_commit; hy_run
check "hygiene clean: nothing blocks"       "0"       "$(hy '.blocking | length')"
check "hygiene clean: default config"       "default" "$(hy .config)"
check "hygiene clean: no reason file"       "absent"  "$([ -f "$T/review/hygiene_reason.txt" ] && echo present || echo absent)"

# 2. Moving a test file is a rename, not a deletion, and must pass.
hy_repo
mkdir -p "$T/wt/tests/unit"; git -C "$T/wt" mv tests/test_math.py tests/unit/test_math.py; hy_commit; hy_run
check "hygiene moved test: nothing blocks"  "0" "$(hy '.blocking | length')"

# 3. tests_deleted, and the deleted file's assertions are not counted twice.
hy_repo
git -C "$T/wt" rm -q tests/test_math.py; hy_commit; hy_run
check "tests_deleted: counted"              "1"             "$(hy .tamper.tests_deleted)"
check "tests_deleted: blocks alone"         "tests_deleted" "$(hy '.blocking | join(",")')"
check "tests_deleted: asserts not doubled"  "0"             "$(hy .tamper.asserts_removed)"
check "tests_deleted: reason names it"      "1"             "$(grep -c 'tests_deleted=1' "$T/review/hygiene_reason.txt")"

# 4. skips_added, with the new-file line number as the sample.
hy_repo
printf '%s\n' 'import pytest' '@pytest.mark.skip' 'def test_a():' '    assert add(1, 2) == 3' '    assert add(0, 0) == 0' > "$T/wt/tests/test_math.py"; hy_commit; hy_run
check "skips_added: pytest mark"            "1" "$(hy .tamper.skips_added)"
check "skips_added: sample line"            "2" "$(hy '.samples[0].line')"

# 5. A JS skip/only counts in a test file, and `stream.skip(` in source does not.
hy_repo
mkdir -p "$T/wt/web"
printf '%s\n' 'it.skip("x", () => {})' 'describe.only("y", () => {})' > "$T/wt/web/a.test.ts"
printf '%s\n' 'stream.skip(3)' > "$T/wt/web/a.ts"; hy_commit; hy_run
check "skips_added: js test file only"      "2" "$(hy .tamper.skips_added)"

# 6. Rust keeps tests beside the code, so #[ignore] counts in any file.
hy_repo
printf '%s\n' 'fn f() -> u8 { 1 }' '#[test]' '#[ignore]' 'fn t() {}' > "$T/wt/src/lib.rs"; hy_commit; hy_run
check "skips_added: rust ignore"            "1" "$(hy .tamper.skips_added)"

# 7. asserts_removed: one assertion gone, nothing added in its hunk.
hy_repo
printf '%s\n' 'def test_a():' '    assert add(1, 2) == 3' > "$T/wt/tests/test_math.py"; hy_commit; hy_run
check "asserts_removed: counted"            "1" "$(hy .tamper.asserts_removed)"

# 8. ...but an assertion rewritten in place is not a removal.
hy_repo
printf '%s\n' 'def test_a():' '    assert add(1, 2) == 3' '    assert add(0, 0) == 0 and add(0, 1) == 1' > "$T/wt/tests/test_math.py"; hy_commit; hy_run
check "asserts_removed: rewrite is not"     "0" "$(hy .tamper.asserts_removed)"

# 9. tautologies_added: a literal subject, a same-argument matcher, `assert True`
#    and `assert x == x`; `expect(y).toEqual(z)` is not one.
hy_repo
mkdir -p "$T/wt/web"
printf '%s\n' 'expect(true).toBe(true)' 'expect(x).toEqual(x)' 'expect(y).toEqual(z)' > "$T/wt/web/b.spec.ts"
printf '%s\n' 'def test_a():' '    assert True' '    assert add(1, 2) == 3' '    assert add(0, 0) == 0' '    assert x == x' > "$T/wt/tests/test_math.py"; hy_commit; hy_run
check "tautologies_added: four"             "4" "$(hy .tamper.tautologies_added)"

# 10. Erosion counters report, and do not block, in the default report mode.
hy_repo
printf '%s\n' 'def add(a, b):' '    try:' '        return a + b' '    except Exception:' '        return 0  # type: ignore' '    # TODO fix this' '    # TODO(#12) linked' > "$T/wt/src/math.py"; hy_commit; hy_run
check "erosion: broad_except"               "1" "$(hy .erosion.broad_except)"
check "erosion: type_escape"                "1" "$(hy .erosion.type_escape)"
check "erosion: only the unlinked TODO"     "1" "$(hy .erosion.todo_unlinked)"
check "erosion: report mode never blocks"   "0" "$(hy '.blocking | length')"

# 11. Four lint disables: over the default threshold of 3. Report mode lets it
#     through; block mode on the base branch blocks it; a per-counter threshold of 5
#     lets it through again.
hy_lint() { printf '%s\n' 'x = 1  # noqa' 'y = 2  # noqa' 'z = 3  # noqa' 'w = 4  # noqa' > "$T/wt/src/math.py"; hy_commit; hy_run; }
hy_repo; hy_lint
check "lint_disable: counted"               "4"            "$(hy .erosion.lint_disable)"
check "lint_disable: report mode passes"    "0"            "$(hy '.blocking | length')"
hy_repo '{"erosion_mode":"block"}'; hy_lint
check "lint_disable: block mode blocks"     "lint_disable" "$(hy '.blocking | join(",")')"
check "block mode: config read from base"   "repo"         "$(hy .config)"
hy_repo '{"erosion_mode":"block","erosion_thresholds":{"lint_disable":5}}'; hy_lint
check "per-counter threshold raises it"     "0"            "$(hy '.blocking | length')"

# 12. rust_unwrap in non-test Rust.
hy_repo
printf '%s\n' 'fn f() -> u8 { "1".parse().unwrap() }' > "$T/wt/src/lib.rs"; hy_commit; hy_run
check "rust_unwrap: counted"                "1" "$(hy .erosion.rust_unwrap)"

# 13. A PR that edits .fabro/hygiene.json trips config_changed, and its own
#     loosened threshold is NOT the one used.
hy_repo
mkdir -p "$T/wt/.fabro"; printf '%s' '{"erosion_mode":"report","erosion_threshold":99}' > "$T/wt/.fabro/hygiene.json"; hy_commit; hy_run
check "config_changed: tamper"              "1" "$(hy .tamper.config_changed)"
check "config_changed: PR config ignored"   "3" "$(hy .erosion_threshold)"

# 14. An invalid base config blocks rather than falling back silently.
hy_repo '{"erosion_mode":"loud"}'; hy_lint
check "invalid config: blocks"              "config_invalid" "$(hy '.blocking | join(",")')"

# 15. Lockfiles are excluded entirely.
hy_repo
printf '%s\n' '# TODO nothing' > "$T/wt/Cargo.lock"; hy_commit; hy_run
check "exclude_paths: lockfile ignored"     "0" "$(hy .erosion.todo_unlinked)"

# 16. A removed SQL comment is `--- ...` in the diff body, not a file header.
hy_repo
printf '%s\n' '-- a comment' 'select 1;' > "$T/wt/src/q.sql"; hy_commit
printf '%s\n' 'select 1;' > "$T/wt/src/q.sql"; hy_commit; hy_run
check "diff body '---' is not a header"     "0" "$(hy '.blocking | length')"

# 17. A base that cannot be diffed fails closed.
hy_repo
echo nosuch > "$T/base_ref"; hy_run
check "no base: hygiene_error blocks"       "hygiene_error" "$(hy '.blocking | join(",")')"

PATH="$SAVED_PATH"

# ---------------------------------------------------------------------------
# review-merge merge_gate — hygiene and Refuter checks (ADR 0013 D1, D5)
# ---------------------------------------------------------------------------
# The architecture section above stops at check 2b. These cases need a PR that
# passes checks 1 to 12, so the stub also answers the GraphQL thread count and the
# fixture carries a Conventional title and a commit-body block.
echo ""
echo "review-merge merge_gate hygiene and refuter"
PATH="$ORIG_PATH"
SAVED_PATH="$PATH"
T="$WORK/mgate2"; mkdir -p "$T/bin" "$T/review"
extract_from "$SHARED" merge_gate | sed "s#/tmp/fabro#$T#g" > "$T/merge_gate.sh"
cat > "$T/bin/gh" <<'STUB'
#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1" = api ]; then echo 0; exit 0; fi
if [ "$1 $2" = "pr view" ]; then
  J=""; P=""
  for a in "$@"; do [ "$P" = "--jq" ] && J="$a"; P="$a"; done
  if [ -n "$J" ]; then jq -r "$J" "$GH_STATE/pr.fixture.json"; else cat "$GH_STATE/pr.fixture.json"; fi
  exit 0
fi
exit 0
STUB
chmod +x "$T/bin/gh"
PATH="$T/bin:$SAVED_PATH"
export GH_LOG="$T/gh.log" GH_STATE="$T"

# Everything green: every check from 1 to 14 passes.
mg_green() {
    echo 1 > "$T/auto_merge"; echo 5 > "$T/pr_number"; echo main > "$T/base_ref"
    echo '{"url":"https://github.com/o/r/pull/5"}' > "$T/pr.json"
    jq -n '{labels:[{name:"agent-authored"}], state:"OPEN", isDraft:false, reviewDecision:"APPROVED",
            commits:[], title:"fix: a thing",
            body:(["x","<!-- fabro:commit-body:start -->","Resolves #1","<!-- fabro:commit-body:end -->"] | join("\n"))}' > "$T/pr.fixture.json"
    echo '{"outcome":"no_changes_needed","risk":1,"not_fixed":[],"own_findings":[],"fixes_applied":[]}' > "$T/review/fix_result.json"
    echo '{"blocking":[]}' > "$T/review/hygiene.json"
    echo pass > "$T/review/refute_verdict"
    rm -f "$T/merge_block_reason" "$T/review/hygiene_reason.txt" "$T/review/refute_reason.txt"
}
mg_run() { OUT=$( cd "$T" && sh "$T/merge_gate.sh" 2>&1 ); jq -r '.context_updates.merge_eligible' <<<"$(lastjson "$OUT")"; }

# 1. The all-green fixture really is eligible, so every block below is the new check.
mg_green
check "all green: eligible"                 "true"  "$(mg_run)"

# 2. Check 13 blocks on a non-empty `blocking`, with the counters' own reason.
mg_green
echo '{"blocking":["skips_added"]}' > "$T/review/hygiene.json"
echo 'Diff hygiene blocks the merge: skips_added=1.' > "$T/review/hygiene_reason.txt"
check "hygiene blocking: not eligible"      "false" "$(mg_run)"
check "hygiene blocking: reason"            "1"     "$(grep -c 'skips_added=1' "$T/merge_block_reason")"

# 3. ...and fails closed on a missing file, or one with no `blocking` array.
mg_green; rm -f "$T/review/hygiene.json"
check "hygiene missing: not eligible"       "false" "$(mg_run)"
check "hygiene missing: reason"             "1"     "$(grep -c 'missing or invalid' "$T/merge_block_reason")"
mg_green; echo '{}' > "$T/review/hygiene.json"
check "hygiene without blocking: blocks"    "false" "$(mg_run)"

PATH="$SAVED_PATH"
unset GH_LOG GH_STATE
```

That is 37 new `check` calls: 31 in the first section and 6 in the second.

## Dependencies
- Blocked by: None
- Blocks: 02, 03, 04

## Labels
`feature`, `review-merge`, `priority:high`

## Estimate
Small (the code is given; the work is pasting it exactly and running the harness)

## Risk
3 - it adds a fail-closed check to the shared merge phase that both `backlog` and
`pr-review` import. A false positive blocks an auto-merge, and a human merges by hand.

## Validator Stopping Point
`./ops/test-task-gates.sh` prints `PASS: 372 checks`.
