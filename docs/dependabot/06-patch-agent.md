# 06. The `patch` agent, its gate and its validation

## Outcome

A `glm-5.3-flash` agent patches the chosen group's manifest and lockfile with the
repository's own tooling. It submits `patch.json` (C7) through the `submit` tool.

1. `patch_gate` checks the receipt, the contract and the diff.
2. `patch_validate` runs `setup.sh && ci.sh` on the run branch.
3. A red result goes back to the agent once, with the log.
4. A patch that is still red, or that the agent reports `blocked`, never opens a PR. The
   tracking issue gets the reason and `needs-human`.

## Why

This is the only stage that writes code in this package (D1, D6). The upstream prompt
is the base for it. Its Bun-only rule, PR creation, CI watch and "do not merge" rule
are removed (D9), because graph nodes own those jobs here. Validating before the PR
opens (D25) keeps red security PRs off the repositories.

## Change

**Invoke `/fabro-workflow` first.** Read 00: D6, D8, D9, D14, D16, D25, C4, C7, C9, rules
1 and 3. Read `docs/agents/invariants-graph.md`, *Stage I/O*.

### 1. The Stage manifest and the schema

- `.fabro/workflows/_io/schemas/dep_patch.schema.json`: draft 2020-12,
  `additionalProperties: false` everywhere, and C7's shape. `status` is an enum. Every
  `changes[]` entry has `package`, `from`, `to`, `manifest`, `direct` (boolean) and
  `parent` (string, may be empty). Every `verification[]` entry has `command` and
  `result` (enum `pass`, `fail`, `skipped`). `major` is a boolean. `blocker` is a string.
  The cross-field rules stay in the gate, as `plan.schema.json`'s description says for
  its own.
- In `_io/manifest.json` under `workflows.dependabot.stages`, add `patch`. Set `live: true`,
  `prompt: .fabro/workflows/dependabot/prompts/patch.md.j2` and
  `output: {path: /tmp/fabro/dep/patch.json, schema: dep_patch}`. Inputs, in this order:

  | name | path | required | about |
  |---|---|---|---|
  | `group.json` | `/tmp/fabro/dep/group.json` | yes | the alert group to patch: package, manifest, ecosystem, alerts, the minimum patched version |
  | `group_alerts.json` | `/tmp/fabro/dep/group_alerts.json` | yes | the full Dependabot alert records for this group (vulnerable range, advisory summary, identifiers) |
  | `issue.json` | `/tmp/fabro/issue.json` | yes | the tracking issue this patch resolves |
  | `patch_feedback.md` | `/tmp/fabro/dep/patch_feedback.md` | no | the failing validation output from your last attempt; absent on the first attempt |
  | `repomap.md` | `/tmp/fabro/repomap.md` | no | the usual repomap entry, copied from another stage |

  No `"git": "write"` (rule 3).
- Run `ops/fabro-io-manifest.py generate`, then `cargo test` in `ops/fabro-io/` (the
  schema-compile gate).

`track_issue` (task 05) also writes `group_alerts.json`: the raw records from
`alerts_raw.json` whose numbers are in `group.alerts`. Add that to task 05's node if it
is not already there.

### 2. `prompts/patch.md.j2`

Write it from `context/fabro/.fabro/workflows/patch-cves/prompts/patch-cves.md`.

**Keep**, edited for one group:

- The untrusted-input paragraph.
- *Choose The Fix*, rules 1-5. Rule 2 becomes D16's: keep the major unless the current
  major has no patched version. Then set `major: true` and say why in `summary`.
- The Rust bullet of *Ecosystem Rules*.
- *Verification*'s "always verify dependency resolution" and "run the repo's
  CI-equivalent commands".

**Remove:**

- *Baseline*: the graph did that.
- *Query Alerts*: `inputs` gives the alerts.
- *Rank And Group*: `pick_group` did that.
- The Bun-only bullets.
- *Branch And Patch* steps 1 and 5, and every `git` command: the run branch is already
  checked out, and the checkpoint commits.
- *After opening each PR*, *PR Format* and *Final Report*: the contract replaces them.

**Add:**

- **Inputs:** call `inputs` first. Read every `MORE:` part. The table above says what
  each input is.
- **Native tooling:**
  - npm with `package-lock.json`: `npm install <pkg>@<ver>` for a direct dependency.
    For a transitive one, bump the parent, or use `overrides` in `package.json` only
    when the parent has no fixed release. Never `yarn`, `pnpm` or `bun`.
  - uv with `uv.lock`: `uv lock --upgrade-package <pkg>`. Change the constraint in
    `pyproject.toml` only if it excludes the fix.
  - cargo: as upstream.

  Never add a lockfile the repository does not have. Never delete one.
- **Scope:** edit only manifests, lockfiles, and the code a breaking change forces. A
  code change goes in `summary` with its reason.
- **Verification:** run `./.fabro/setup.sh` and `./.fabro/ci.sh`, or `run_tests` for
  each target the repository declares. Record each command in `verification`. A
  failing check that `baseline_check` shows existed before your change is `skipped`,
  with the reason in `summary`.
- **Git:** you are not a git exception. Do not run `git` commands that change files.
  The run commits your work.
- **Blocked:** if no safe version exists, or the fix needs a migration bigger than this
  task, submit `status: "blocked"` with a `blocker` a human can act on, and revert your
  edits with `restore_file`.
- **Feedback:** if `patch_feedback.md` is present, your last attempt failed validation.
  Fix that failure first.
- **Output:** submit with the `submit` tool. Include a filled example, as C7 shows it.

Keep it under about 120 lines. glm-5.3-flash follows short prompts better.

### 3. Nodes

| Node | Type | Job |
|---|---|---|
| `patch_prep` | command | On the first visit: `echo 0 > /tmp/fabro/dep/patch_visits`, `rm -f patch_feedback.md`. On every visit: `rm -f /tmp/fabro/dep/patch.json` (rule 1, *delete a contract before its writer runs*), and increment `patch_visits`. |
| `patch` | agent, `class="dep-patch"`, `timeout="45m"`, `max_retries=1`, `prompt="@prompts/patch.md.j2"` | Comment: 45m is unmeasured, copied from the `rework_t*` tier. Task 10 records the first real durations. |
| `patch_gate` | command, routing | (a) The receipt check, copied from `scan_gate` (`_io.visit` equals `.io/stage.json`'s `visit`). (b) C7's cross-field rules: `blocked` needs a non-empty `blocker` and empty `changes`; `patched` needs at least one change, and every `to` must be at or above `group.first_patched` by the task 03 comparator (load `match.jq`'s function with `jq -L` or `include`, rather than writing a second copy). (c) For `patched`: `git diff --quiet "$(cat /tmp/fabro/dep/sync_base_sha)" HEAD` must be **false**, so there is a change. `git diff --name-only --diff-filter=A` must add no file named `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lockb`, `bun.lock`, `uv.lock`, `poetry.lock` or `Cargo.lock`. An invalid contract with `patch_visits` below 3 exits 1 with one fixed line, the `scan_gate` way. That is the repair turn. Otherwise it publishes `patch_status = failed`. A valid contract publishes `patched` or `blocked`. |
| `patch_validate` | command, routing, `timeout="20m"` | `timeout 1020 sh -c './.fabro/setup.sh && ./.fabro/ci.sh'`. Green publishes `patch_ci = green`. Red writes the log tail (6000 bytes) to `patch_feedback.md`. If `patch_visits` is below 3, it publishes `patch_ci = red`. Otherwise it publishes `patch_ci = red` and writes `patch_status = failed` to a file for `patch_blocked`. |
| `patch_blocked` | command | Comments on the tracking issue: the `blocker`, or the last validation log tail in a fenced block cut to 3,000 bytes. Adds `needs-human`. Never fails the run: each `gh` call is its own statement with `|| true`. |

`sync_base` (task 05) writes `git rev-parse HEAD > /tmp/fabro/dep/sync_base_sha` as its
last step. Add that line in this task.

The `patch_visits` cap is 3: one first try plus two repairs, whether they come from
the gate or from validation. That bounds the agent at 3 visits × 45m. Name the
arithmetic in a `//` comment.

### 4. Edges

```
sync_base -> patch_prep             [condition="outcome=succeeded"]
sync_base -> summarize
patch_prep -> patch                 [condition="outcome=succeeded"]
patch_prep -> summarize
patch -> patch_gate                 [condition="outcome=succeeded"]
patch -> patch_blocked
patch_gate -> patch_prep            [condition="outcome=failed"]
patch_gate -> patch_validate        [condition="outcome=succeeded && context.patch_status=patched"]
patch_gate -> patch_blocked         [condition="outcome=succeeded && context.patch_status!=patched"]
patch_gate -> patch_blocked
patch_validate -> open_pr           [condition="outcome=succeeded && context.patch_ci=green"]
patch_validate -> patch_prep        [condition="outcome=succeeded && context.patch_ci=red && context.patch_retry=yes"]
patch_validate -> patch_blocked     [condition="outcome=succeeded && context.patch_ci=red && context.patch_retry=no"]
patch_validate -> patch_blocked
patch_blocked -> summarize
```

`patch_validate` also publishes `patch_retry` (`yes` while `patch_visits` < 3), so its
two red edges are mutually exclusive. Until task 07 adds `open_pr`, point the green
edge at `summarize`.

The repair edge `patch_gate -> patch_prep` takes `outcome=failed`. Check that edge
against the transition cascade. `scan_gate -> scan` in `arch-review` is the working
precedent.

### 5. The stylesheet

`.dep-patch { model: glm-5.3-flash; }` is already in the stylesheet from task 02. Leave
it. Name the timeout's subject in the node comment (invariants-graph, *Timeouts*: a
timeout is measured against one model).

## Acceptance

- `fabro-io-manifest.py check` passes. `cargo test` passes in `ops/fabro-io/`. The
  generated block contains `"patch"` with its 5 inputs.
- `check-graph-invariants.py` is clean (R2: 45m < 60m).
- `./ops/test-task-gates.sh` passes the new section.
- The prompt tells the agent to use no `gh pr` and no mutating `git`, and it has no
  Bun-only rule. `grep -niE 'gh pr|git (checkout|commit|push)'` matches only lines
  that forbid those commands. Bun appears only in the "never `yarn`, `pnpm` or `bun`"
  line.

## Tests

Add `dependabot — patch_gate, patch_validate`. Use a scratch repository whose HEAD is
`sync_base_sha` plus one commit, and a fixture `stage.json` with visit `v1`.

1. **A valid patch.** `patched`, receipt `v1`, `to` = `2.8.0`, `first_patched` = `2.8.0`,
   and a diff: `patch_status = patched`.
2. **No receipt.** `_io.visit` = `v0`: exit 1, the receipt line on stderr, and
   `patch_visits` unchanged by the gate.
3. **Too low.** `to` = `2.7.9`: exit 1 while visits < 3. At visits = 3:
   `patch_status = failed`.
4. **No change.** `patched` with HEAD == `sync_base_sha`: invalid.
5. **A second lockfile.** The diff adds `yarn.lock`: invalid.
6. **Blocked.** `blocked`, with a blocker and no changes: `patch_status = blocked`.
7. **`patch_validate` red, visit 1.** `patch_ci = red`, `patch_retry = yes`, and the
   feedback file has the log tail.
8. **`patch_validate` red, visit 3.** `patch_retry = no`.
9. **`patch_blocked` with `gh` failing.** The node still exits 0.

## Depends on

05.
