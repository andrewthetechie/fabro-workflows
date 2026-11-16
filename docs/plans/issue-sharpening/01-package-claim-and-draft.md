# Task 01: the `issue-sharpen` package: claim, prep, draft and `draft_gate`, and checker rule R17

Read `00-overview-and-contracts.md` first (D1, D7, D9, D10, D18, D21–D23; C1–C4, C9). **Invoke
`/fabro-workflow`.** Read `.fabro/workflows/issue-precheck/` whole (graph, `workflow.toml`,
prompt), `ralph/workflow.toml`'s hooks, `_shared/triage/triage.fabro` `improve_gate`,
`ops/check-graph-invariants.py` (R10–R16), `ops/fabro-io-manifest.py`, `_io/manifest.json`'s
`issue-precheck` entry, `docs/USER-GUIDE.md` *Writing criteria the factory can verify*, and
`skills/plan-factory-issues/fixtures/` (`README.md`, `EXPECTED.toml`, `issues/`, `repo/`).

## Goal

The first half of the run: decide whether this issue needs sharpening now, give the box the
inputs it needs, and accept only a draft that changes nothing it did not declare. Tasks 02 and 03
add the rest of the graph. To keep this task's graph valid on its own, give `draft_gate`'s success
edge to `record` for now. Task 02 re-points it.

## Steps

1. **The package.** Create `.fabro/workflows/issue-sharpen/` with `workflow.toml` and
   `workflow.fabro` as C1 says. Base `workflow.toml` on `issue-precheck`'s, with these changes:
   - replace the `box-guard` hook with `ralph`'s `box-claim` hook, byte for byte;
   - keep `FABRO_API_URL` and `FABRO_BOX_HEALTH` in the env, because `box-claim` reads them;
   - write a header comment that says what the package is and that it is a leased run, not a
     background run (ADR 0033).
2. **`claim`.** Model it on `issue-precheck`'s `claim`. `issue_number` is interpolated once, in
   single quotes, behind the same `case` guard. Write `/tmp/fabro/box` from `coder_pool` as it
   comes: the lease's pool has no `-bg` suffix. Then:
   - read the issue (`number,title,body,state,url,labels`) and every comment;
   - compute the body hash with the precheck's exact pipeline (Facts);
   - apply C1's `claim` routing. The newest precheck marker is the last comment starting
     `<!-- fabro:precheck `. A `fabro:sharpen` marker names the hash when it contains
     `body=<hash> ` or `applied=<hash> `;
   - on a stop, write `/tmp/fabro/sharpen/outcome` = `not_needed` and
     `/tmp/fabro/sharpen/rule` = the reason, then emit `{"context_updates":{"sharpen_go":false}}`;
   - otherwise write `issue.json`, `comments.json` and `body_hash` under `/tmp/fabro/sharpen/`.
3. **`prep`.** Index the checkout exactly as `issue-precheck`'s `prep` does. Do not register it
   as an R10 copy: its paths differ. Write the draft inputs of C3:
   - `issue.md` in the precheck's format.
   - `criteria.md`: the bullets under `## Acceptance criteria`, numbered `AC1..ACn`. A bullet is a
     line starting `- ` or `* `, optionally followed by `[ ] ` or `[x] `. Continuation lines
     indented under it belong to it. The section ends at the next `## ` heading. **No
     `## Acceptance criteria` heading** means a stop with outcome `not_needed` and rule
     `no acceptance criteria heading`: there is nothing the closed list can act on.
   - `ci.md`: `.fabro/ci.sh`, then one level of what it calls. Lines matching
     `(bash|sh|\./)\S+\.sh`, `npm run <s>`, `bun run <s>`, `make <t>` and `uv run <s>` resolve to
     the script file, the `package.json` `scripts.<s>` entry, or the Makefile target. Cut at
     40,000 characters. A missing `.fabro/ci.sh` is a stop: `not_needed`, rule `no .fabro/ci.sh`.
   - `rules.md`: copy the package's `prompts/rules.md`. Write that file in this task: the
     USER-GUIDE section verbatim, then C2's transformation table.
   - `precheck.md`: the newest precheck marker comment.
   - `orientation.md` and `pack.md` when present, read the way `ralph`'s `prep` and `claim` read
     them.
4. **`draft`** (agent, `class="sharpen"`, `timeout="20m"`, `prompt="@prompts/draft.md.j2"`). The
   prompt says:
   - the job: make every acceptance criterion verifiable by the Refuter from the diff and
     `.fabro/ci.sh`, using only the C2 transformations;
   - "change nothing else": every other byte of the body stays;
   - nothing is ever deleted: anything that cannot be made verifiable goes to
     `post_deploy_checks` (`to_post_deploy`);
   - never resolve a criterion against a non-goal: say `cannot_sharpen` instead;
   - never put a human action in the body (D18);
   - from round 2, fix every finding in `findings.md`, and change nothing a finding does not ask
     about;
   - submit through `mcp__io__submit`, never by writing the file.

   Name each input by path, as the other box prompts do. Give one before→after example per
   transformation, taken from the USER-GUIDE and the #67 fixtures.
5. **Schema and manifest.** Add `_io/schemas/sharpen-draft.schema.json` (C2) and the `draft` stage
   in `_io/manifest.json` (`box: true`, the C3 inputs with `about` lines, the `required` flags as
   C3 gives them, output `/tmp/fabro/sharpen/draft.json`). Regenerate the `workflow.toml`
   manifest block with `ops/fabro-io-manifest.py`. Do not bump `min_binary`: nothing here needs a
   new field (Facts: `coder_box_stages` is a map).
6. **`draft_gate`**: C4 exactly, in `jq` plus `awk`, with no Python in the sandbox. Use
   `mawk`-compatible awk: the gate tests run under the system awk. Write `/tmp/fabro/sharpen/rule`
   on every refusal. Keep the round counter in `/tmp/fabro/sharpen/round` (1 on the first visit).
7. **`record`** (stub in this task, finished in task 03): emit C11's keys from the files present,
   and always exit 0.
8. **Generator and checker.**
   - `ops/fabro-io-manifest.py`: add `issue-sharpen` to `CODER_LOOP_WORKFLOWS`.
   - `ops/check-graph-invariants.py`: add `LEASED_BOX_PACKAGES` and R17 (C9). Let R15 allow
     `issue-sharpen`.
   - A checker unit test for R17 with one passing fixture and one failing fixture per bullet of
     C9, in the place where R16's tests live.
9. **Gate tests** (`ops/gate_tests/test_issue_sharpen.py`). They run the real shell extracted
   from the graph, as `test_ralph.py` does, with a stubbed `gh`. Cover:
   - `claim`: each stop reason; a `fabro:sharpen` marker naming the hash as `body=` and as
     `applied=`; the precheck marker for an older hash; go.
   - `prep`: criteria extraction (a checkbox bullet, a continuation line, a heading after the
     list); no `## Acceptance criteria`; no `ci.sh`; `ci.sh` resolution of a script, an
     `npm run` and a `make` target; the 40,000-character cut.
   - `draft_gate`: each transformation accepted; an unknown transformation; `proposed: null` on a
     non-`to_post_deploy` row; a changed bullet with no row; a byte change in an untouched
     bullet; a reordered bullet; a moved `<!-- fabro:` line; an edited `## Non-goals`; an edited
     `## Proposed task map`; a body that writes `## Post-deploy checks`; an existing Post-deploy
     section kept and extended in order; `cannot_sharpen`; a missing receipt; schema failures.
   - **The #67 corpus** (D23): for each fixture in `EXPECTED.toml` tagged L1, L2, L3, L4 or L5,
     a hand-written valid draft in `ops/gate_tests/fixtures/issue_sharpen/` must pass
     `draft_gate`, and the assembled `proposed.md` must lint clean of those rules under
     `skills/plan-factory-issues/scripts/factory_issues/lint.py` against `fixtures/repo/`. A
     hand-written **bad** draft per fixture must be refused: one that deletes a criterion, one
     that edits an untouched bullet, one that moves a marker. `good-*` fixtures must reach
     `claim`'s go only with an `unverifiable` precheck marker, and need no draft.

## Done when

- `make check` passes, R17 included, with the new package and its manifest block.
- The commit is `feat(issue-sharpen): claim, draft and the preservation gate (issue-sharpening 01)`.
