# The review comment shows the Refuter's checklist and the hygiene counters

## Tracer-Bullet Outcome
Every review comment the merge phase posts (`merged`, `blocked`, `needs-human`) has two
more rows in its verdict table, `Refuter` and `Diff hygiene`, and two more sections:
`### Refuter`, with each criterion, its status and evidence and each defect, and
`### Diff hygiene`, with the non-zero counters and their samples. A human who merges a
blocked PR by hand sees why it was blocked.

## User Story
As the operator merging a PR that the new gates blocked, I want the checklist and the
counter samples in the PR comment, so that I can judge the block without opening the run.

## Description
**Before you edit any `.fabro` file, invoke the `/fabro-workflow` skill.** Read
`docs/merge-gate/00-overview-and-contracts.md` first (contracts C1 to C3). This task
needs tasks 01 and 03. **Do not push.**

1. `.fabro/workflows/_shared/review-merge/review-merge.fabro`, node `rm_setup`: five edits,
   three to the `render.jq` heredoc and two to the `render_comment.sh` heredoc.
2. `ops/test-task-gates.sh`: add one section.

## Context Pack
- Source decisions: ADR 0013 D5 (a plain comment, not a "request changes" review).
- Repo facts:
  - `rm_setup` writes `/tmp/fabro/render.jq` and `/tmp/fabro/render_comment.sh` with
    heredocs. `report_merged`, `report_blocked` and `mark_needs_human` each run
    `sh /tmp/fabro/render_comment.sh <kind>`. One render serves all three kinds, so the
    new rows and sections appear in every comment.
  - `render_comment.sh` copies each review file into `/tmp/fabro/_render/` and replaces a
    missing or non-object file with `{}`. So the renderer never sees a missing file, only
    `{}`, which is why each new section tests a key's `type`.
  - `render.jq` already defines `NL` (a newline, built with `[10] | implode`) and `nz($a)`
    (`$a // []`).
- Non-goals: no new comment and no PR review. Do not change the three report nodes.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: local commit only.

## Implementation Contract
- Expected files: `.fabro/workflows/_shared/review-merge/review-merge.fabro`,
  `ops/test-task-gates.sh`.
- Interfaces and names: new `jq` arguments `ref`, `hyg` (slurpfiles) and `rverdict`
  (string, empty when there is no verdict file).
- Verified external contracts: none new.
- Behavior rules: a missing Refuter result renders `_did not run_` in the table and
  `_The refuter did not return a result._` in the section. Missing counters render
  `_did not run_` and `_The counters did not run._`. The render must never fail on
  missing inputs, because the report nodes fall back to a minimal comment when it does.
- Error and security rules: none.

### The five edits

Each `old` text below occurs exactly once in `review-merge.fabro`. Replace it with the
`new` text. Both are shown as they appear in the `.fabro` file (already escaped).

**Edit 1, `render.jq`: the binding line.** Replace:

```
($cfix[0] // {}) as $X
```

with:

```
($cfix[0] // {}) as $X | ($ref[0] // {}) as $RF | ($hyg[0] // {}) as $HG
```

**Edit 2, `render.jq`: the verdict table.** Replace:

```
  \"| Review and fix | \" + ($F.outcome // \"_did not run_\") + \" |\",
```

with:

```
  \"| Review and fix | \" + ($F.outcome // \"_did not run_\") + \" |\",
  \"| Refuter | \" + (if $rverdict == \"\" then \"_did not run_\" else $rverdict end) + \" |\",
  \"| Diff hygiene | \" + (if ($HG.blocking | type) != \"array\" then \"_did not run_\" elif ($HG.blocking | length) == 0 then \"clear\" else \"blocked: \" + ($HG.blocking | join(\", \")) end) + \" |\",
```

**Edit 3, `render.jq`: the new sections, before `### Commits with changes`.** Replace:

```
  \"### Commits with changes\", \"\",
```

with:

```
  \"### Refuter\", \"\",
  ( if ($RF.criteria | type) != \"array\" then \"_The refuter did not return a result._\"
    else ( [ $RF.criteria[] | \"- **\" + .status + \"** - \" + .criterion + \" - \" + .evidence ]
         + [ (nz($RF.defects) | .[]) | \"- **defect** - \" + .file + (if (.line | type) == \"number\" then \":\" + (.line | tostring) else \"\" end) + \" - \" + .scenario ] )
         | join(NL) end ),
  \"\",
  \"### Diff hygiene\", \"\",
  ( if ($HG.tamper | type) != \"object\" then \"_The counters did not run._\"
    else [ ($HG.tamper + ($HG.erosion // {})) | to_entries[] | select(.value > 0) | .key + \" \" + (.value | tostring) ] as $nz
      | (if ($nz | length) == 0 then \"No counter fired.\" else \"Counters: \" + ($nz | join(\", \")) + \". Erosion mode: \" + ($HG.erosion_mode // \"report\") + \".\" end)
        + ( nz($HG.samples) | if length == 0 then \"\"
            else NL + NL + ([ .[] | \"- `\" + .file + (if .line then \":\" + (.line | tostring) else \"\" end) + \"` \" + .counter + \" - `\" + .text + \"`\" ] | join(NL)) end ) end ),
  \"\",
  \"### Commits with changes\", \"\",
```

**Edit 4, `render_comment.sh`: the files it copies.** Replace:

```
for f in standards spec fix_result ci_fix_result; do
```

with:

```
for f in standards spec fix_result ci_fix_result refute hygiene; do
```

**Edit 5, `render_comment.sh`: the `jq` call (only the part shown changes; the rest of the line stays).** Replace:

```
--slurpfile cfix $T/ci_fix_result.json 
```

with:

```
--slurpfile cfix $T/ci_fix_result.json --slurpfile ref $T/refute.json --slurpfile hyg $T/hygiene.json --arg rverdict \"$(cat /tmp/fabro/review/refute_verdict 2>/dev/null || true)\" 
```

## Acceptance Criteria
- [ ] A `blocked` comment for a PR with a `fail` verdict shows `| Refuter | fail |`, the
      failed criterion, and the defect line.
- [ ] The `### Diff hygiene` section lists the non-zero counters and their samples.
- [ ] With no Refuter or hygiene files, the comment still renders.
- [ ] `./ops/test-task-gates.sh` prints `PASS: 402 checks`.

## Test Expectations
Framework: the bash harness `ops/test-task-gates.sh`. Insert this section immediately
before the harness's final block, after the sections that task 03 added:

```bash
# ---------------------------------------------------------------------------
# review-merge render — the Refuter and hygiene rows and sections (ADR 0013 D5)
# ---------------------------------------------------------------------------
echo ""
echo "review-merge render"
PATH="$ORIG_PATH"
T="$WORK/render"; mkdir -p "$T/review"
extract_from "$SHARED" rm_setup | sed "s#/tmp/fabro#$T#g" > "$T/rm_setup.sh"
echo 5 > "$T/pr_number"
for f in base_ref head_ref run_base_sha; do echo x > "$T/$f"; done
echo '{"url":"https://github.com/o/r/pull/5"}' > "$T/pr.json"
echo '[]' > "$T/linked_issues.json"
FABRO_AUTO_MERGE=1 sh "$T/rm_setup.sh" >/dev/null 2>&1
echo '{"summary":"s","criteria":[{"criterion":"logout works","status":"not_met","evidence":"no handler"}],"defects":[{"file":"a.py","line":9,"scenario":"crashes on empty"}]}' > "$T/review/refute.json"
echo fail > "$T/review/refute_verdict"
echo '{"tamper":{"skips_added":1},"erosion":{"lint_disable":0},"erosion_mode":"report","blocking":["skips_added"],"samples":[{"counter":"skips_added","file":"t.py","line":2,"text":"@pytest.mark.skip"}]}' > "$T/review/hygiene.json"
C=$( cd "$T" && sh "$T/render_comment.sh" blocked 2>&1 )
check "render: refuter row"                 "1" "$(grep -c '^| Refuter | fail |$' <<<"$C")"
check "render: refuter defect"              "1" "$(grep -c '^- \*\*defect\*\* - a.py:9 - crashes on empty$' <<<"$C")"
check "render: hygiene sample"              "1" "$(grep -c 't.py:2. skips_added' <<<"$C")"
```

That is 3 new `check` calls.

## Dependencies
- Blocked by: 01, 03
- Blocks: 05, 06

## Labels
`feature`, `review-merge`

## Estimate
Small

## Risk
2 - a render failure falls back to a minimal comment and never fails a run, but a
broken render hides the reason for every block.

## Validator Stopping Point
`./ops/test-task-gates.sh` prints `PASS: 402 checks`.
