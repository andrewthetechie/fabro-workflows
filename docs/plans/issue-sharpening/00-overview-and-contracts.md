# Issue sharpening: overview and canonical contracts

**Status:** planned 2026-11-16 from issue #68, in a grilling session with the operator. Not
built. ADR 0033 records the decision. The operator changed #68's design in five places: no human
approval, one leased run instead of two background jobs, no calibration kind, no phases, and no
switch. **Where #68's body or its 2026-11-12 comment differs from this folder, this folder is
correct.**

**Read this file first.** Every task in this folder assumes the decisions, facts and contracts
below. If a task and this file disagree, this file is correct: stop and report the conflict.
Read ADR 0033 and ADR 0031 (the box lane and its claim) before you start. **Before you edit a
`.fabro` file or a `workflow.toml`, invoke the `/fabro-workflow` skill** and read
`docs/agents/invariants-graph.md` and `docs/agents/invariants-host.md`. Before you touch
`ops/scheduler/`, read `ops/README.md` *The coder scheduler*.

## Hard rule for this series: do not push

The operator's reviewer reads the whole series before anything reaches `main`. A push to `main`
makes a workflow package live, and the scheduler goes live at the next deploy (`AGENTS.md`).

- Work tasks 01–07 in order. Commit each task locally on `main`: one commit per task, with the
  Conventional Commit subject the task gives.
- After each task, run `make check` at the repository root. For a task that touches
  `ops/scheduler/`, also run `uv run pytest` in `ops/scheduler/`. All of it must pass. On this
  Mac, `make check` needs `gawk` first on `PATH` (two backlog gate tests fail under BSD awk).
- Do not run `git push`, `make deploy*`, `make provision`, `make compose-up`,
  `make check-host`, `ssh`, or any `gh` command that writes. Do not edit a target repository.
- **Stop after task 07** and report: the commits, the `make check` and `pytest` results, the
  node and edge counts of `IssueSharpen` you expect `make check-host` to print, and anything you
  could not do. Task 08 is the reviewer's, not yours.

## Goal

Issues the precheck rates `unverifiable` merge **11–14%**; `ready` issues merge **41%**
(`GET :32280/api/background/calibration`, 2026-11-16: `unverifiable` 19 of 167, `ready` 307 of
749). The cause is mechanical: a criterion names a command `.fabro/ci.sh` does not run, asks for
PR-description text, says "must not drop", or asks for a check only a human can make. The
Refuter cannot credit such a criterion, so the PR is held for a human who is not there.

This series makes the factory rewrite those criteria itself, on the local boxes, before the issue
is worked. The factory is **dark**: no human approves a rewrite. The safety comes from three
things instead: a closed list of transformations, a mechanical preservation gate, and a fresh
three-lens audit that must come back clean before anything is written.

The ceiling (#68): if sharpened issues merged at the `ready` rate, about 5 points on the overall
merge rate. It is a ceiling, not a forecast.

## What this is

| Piece | Path | Task |
|---|---|---|
| The `issue-sharpen` package: claim, draft, `draft_gate` | `.fabro/workflows/issue-sharpen/`, `_io/`, `ops/gate_tests/` | 01 |
| The three audit lenses, `audit_gate`, the round loop | same | 02 |
| `apply`, the in-run `recheck` (R10 copy of the precheck), `post`, `record` | same | 03 |
| The checker rule for a leased box package (R17) and the manifest generator | `ops/check-graph-invariants.py`, `ops/fabro-io-manifest.py` | 01 |
| Scheduler: body hash in the issue cache, a precheck per body hash, the `unverifiable` hold, the sharpen queue, the back-fill | `ops/scheduler/` | 04 |
| Scheduler: the `sharpen` lease (dispatch, ranking, release, retries) | `ops/scheduler/` | 05 |
| Measurement: measure section, scorecard row, retro classes | `ops/scheduler/` | 06 |
| Docs, labels, onboarding | `docs/`, `AGENTS.md`, `ops/README.md`, `skills/setup-repo-for-factory/` | 07 |
| Review, push, deploy, first-run watch | reviewer and operator | 08 |

## Decisions (settled; do not re-open)

| # | Decision |
|---|---|
| D1 | **Local only.** Every agent stage runs on the lease's box through its `-bg` model row (`coders-a-bg` on box 1, `coders-c-bg` on box 2). There is no hosted model and no fallback. A box fault fails the stage, and the scheduler retries the run (D12). A checker rule enforces it (C9). |
| D2 | **One leased run, not background jobs.** Sharpening is a scheduler lease with `target_kind='sharpen'` on an undrained coder pool, the way the retired PR doctor leased a pool for a `pr` run. The background lane rarely gets a gap on two-slot boxes (#78), and a job split across lane hand-offs could wait days. One run does every round. |
| D3 | **Precedence.** A sharpen lease is dispatched **before** any issue run in the same tick, and **at most one** sharpen lease is active across the fleet. Sharpen candidates go in coder-queue order (repo priority, then issue number), so the issue that would be worked next is sharpened first. |
| D4 | **The peak-hours block does not stop it.** It spends no hosted credits. The disk gate does stop it. |
| D5 | **Scope: `unverifiable` only.** `unclear`, `contradiction` and `ready` issues are never sharpened. |
| D6 | **No human approval, and auto-apply only a converged proposal.** Converged means one clean audit round (C5). On `capped`, `churn` or `cannot_sharpen` the body is not touched, the hold is released, and the issue runs as written: today's behaviour. |
| D7 | **The Goldfish is isolated inside one run.** Each lens is a fresh agent session. Its Stage manifest inputs are only the files `draft_gate` copies into `/tmp/fabro/sharpen/audit/` (C3). The draft's notes and transcript are never inputs. This is the sealing the Refuter already uses. |
| D8 | **Rounds.** At most 3 draft-and-audit rounds. One clean round converges. |
| D9 | **Closed transformation list, and no deletion** (C2). `pr_text_drop` is not in the list. A criterion that cannot be made verifiable (human checks, `.github/workflows/` edits) moves to `## Post-deploy checks`, which the Refuter never reads and the PR body lists. |
| D10 | **Mechanical preservation.** `draft_gate` refuses a draft that changes, byte for byte, anything outside its change rows (C4). A refusal is not a fault and is not retried: the run ends `cannot_sharpen`. A Substitution found by the Preservation lens is a blocker. |
| D11 | **The record.** Before the body edit, the original body is posted in a `<!-- fabro:sharpen-original -->` comment. The `<!-- fabro:sharpen -->` comment carries the change table. Nothing reads either automatically; they are the audit trail. |
| D12 | **Faults retry, model mistakes do not.** A run that fails, times out or is lost is retried up to 2 more times, at least 30 minutes apart. After the third failure the outcome is `failed` and the hold is released. A `cannot_sharpen` ending is not retried. |
| D13 | **The precheck runs once per body hash**, for every issue, not once per issue. A hand edit gets a fresh verdict too. |
| D14 | **The re-precheck happens inside the run.** After `apply`, a `recheck` stage (an R10 copy of the precheck's `check` and `gate`) judges the new body and posts its precheck marker. A sharpened issue never waits on the background lane. |
| D15 | **One sharpening per issue per original body.** If `recheck` still says `unverifiable`, the issue is released and runs as written. The hold rule (C7) ignores any body hash a `fabro:sharpen` marker names as `body=` or `applied=`. |
| D16 | **The hold is its own code path.** It does not read `SCHEDULER_PRECHECK_HOLDS` or `SCHEDULER_PRECHECK_HOLD_VERDICTS`, and `unverifiable` is removed from what that variable may name. No host `.env` change. |
| D17 | **Back-fill.** At the first scheduler start with this code, every queued issue whose newest precheck job ended `verdict=unverifiable` is held and queued for sharpening. The run's `claim` decides whether that verdict still applies to the current body. |
| D18 | **Human actions live in the comment only.** They never go in the body: the Refuter reads any checklist as criteria. |
| D19 | **No new context pack** after a sharpening. The pack maps code, which a sharpening does not change. |
| D20 | **No switch.** Sharpening is on from the deploy. The off path is a revert and `make deploy-scheduler`. ADR 0033 records this exception to "no new kind turns itself on". |
| D21 | **Timeouts are bounds, not measurements.** Draft 20m, each lens 15m, `recheck` as the precheck's `check`. Task 08's two-week read re-derives them (as #79 does for `learn`). |
| D22 | **Reasoning effort.** `medium` for the draft, Comprehension and Verifiability; `high` for Preservation, where a miss is a weakened requirement. |
| D23 | **The fixture corpus is shared with #67 in tests only.** `skills/plan-factory-issues/fixtures/issues/` drives the gate tests. `lint.py` never runs inside a sandbox; `recheck` is the runtime check. |

## Facts (verified 2026-11-16)

- **Precheck body hash:** first 12 hex of SHA-256 of `title + "\n" + body`, computed in
  `issue-precheck`'s `claim` as `{ jq -j .title; echo; jq -j .body; } | sha256sum | cut -c1-12`
  (`.fabro/workflows/issue-precheck/workflow.fabro:81-82`). The Python form is
  `hashlib.sha256((title + "\n" + body).encode()).hexdigest()[:12]`.
- **Precheck marker:** `<!-- fabro:precheck verdict=$V body=$HASH main=$MAIN at=$AT run=$RUN[ blocker=N] -->`
  (`issue-precheck/workflow.fabro:217`). `post` edits the newest precheck marker comment in
  place. `claim` stops quietly when the newest precheck marker already carries the current
  `body=` (`:89-92`).
- **The precheck producer is one job per issue, ever:** `store.background_job_exists(KIND, repo, str(number))`
  matches any state (`precheck.py:210`, `store.py:1587-1599`).
- **Holds today:** `_apply_hold` adds `precheck-<verdict>` after a `done` precheck job when
  `SCHEDULER_PRECHECK_HOLDS` is on and the verdict is in `SCHEDULER_PRECHECK_HOLD_VERDICTS`
  (`background.py:781-820`). Live `.env`: holds on for `already_done,blocked`.
  `queue.build_queue` drops any issue carrying a `precheck-*` label (`queue.py:173-176`).
  `release_blocked` is the only automatic release (`precheck.py:290-345`).
- **The issue cache keeps no body.** The inventory's list call returns it, but `Issue` drops it
  (`github.py`). Task 04 adds a body hash to the cache.
- **Leases already have a `target_kind`:** `leases` and `run_history` carry
  `target_kind TEXT NOT NULL DEFAULT 'issue'`, with `'pr'` left from the PR doctor
  (`store.py:106`, `:259`). Dispatch, receipt recovery and the issue-run metrics already
  filter on `target_kind == 'issue'` (`dispatch.py:348`, `reconcile.py:688`, `store.py:1422`, `:1961`).
- **Labels a run carries:** `lane=coder`, `box=<pool>`, `workflow`, `issue` (`fabro.py:804-810`).
  `fabro-io box-claim` cancels background runs on any sibling of its box, and `box-guard` yields
  to any run labelled `lane=coder` whose current stage is a box stage. It reads that from the
  manifest's `coder_box_stages[<workflow>][<node>]`, a map keyed by workflow name
  (`ops/fabro-io/src/manifest.rs:169`, `boxclaim.rs:238-256`). **A new workflow key is data. No
  `fabro-io` change and no image-first deploy are needed.**
- **`FabroClient.dispatch_job`** takes arbitrary `inputs` and `labels` (`fabro.py:867`).
  `FabroClient.dispatch` hard-codes issue-run labels and inputs.
- **`-bg` rows:** the lane runs a box's background work on `f"{box}-bg"`, where `box` is the
  box's first pool (`background.py:630-637`). `docs/agents/invariants-host.md:19` forbids a
  fallback row on or to a `-bg` name, and R14 enforces it.
- **R10 copies** are registered in `R10_CALIBRATION_COPIES`
  (`ops/check-graph-invariants.py:1145`): source graph, node ids, prompt node, and
  string replacements.
- **Triage's `improve_gate`** assembles `## Post-deploy checks` from a `post_deploy_checks`
  array and strips any such section the agent wrote (`_shared/triage/triage.fabro:89-125`).
- **The Refuter** cuts `^## Post-deploy checks$` in shell and reads "an explicit
  acceptance-criteria list or a checklist" from the rest (`_shared/review-merge/review-merge.fabro:681-702`,
  `ralph/workflow.fabro:1067-1069`, the `refute.md.j2` prompts).
- **USER-GUIDE rules** (`docs/USER-GUIDE.md:69-90`): no criterion needs PR-description text; a
  command criterion names only a command `.fabro/ci.sh` runs; "must not drop" becomes a raised
  threshold; a human-only check goes under `## Post-deploy checks`; a `.github/workflows/`
  change is never the agent's work.
- **#67's lint** (`skills/plan-factory-issues/scripts/factory_issues/lint.py`) has rules L1
  (PR text), L2 (command `ci.sh` does not run), L3 (must not drop), L4 (workflow file) and L5
  (human check outside Post-deploy), among others. Its fixtures are in
  `skills/plan-factory-issues/fixtures/issues/`, with `EXPECTED.toml`, and run against
  `fixtures/repo/`. `EXPECTED.toml` says the corpus is shared with #68.
- **Volume:** 44 `unverifiable` verdicts in the 14 days to 2026-11-16 (`/api/background/measure`),
  about 3 a day.

## Contracts

### C1. The package and its graph

`.fabro/workflows/issue-sharpen/`, workflow name `IssueSharpen`. Inputs: `issue_number`,
`coder_pool` (the lease's pool, for example `coders-b`) and `model_pool` (the box's `-bg` row,
for example `coders-a-bg`). `workflow.toml` binds no `[run.inputs]`, has no
`[run.model.fallbacks]`, sets `[run.run_branch] enabled = false`, and has
`contents = "read"`, `issues = "write"`. Its hooks are `io-stage`, `git-guard` and **`box-claim`**
(exactly as `ralph`'s). It has **no `box-guard`**: a leased run holds the box and must not
cancel itself.

The model stylesheet:

```
*   { model: {{ inputs.model_pool | default('coders-a-bg') }}; reasoning_effort: medium; }
.sharpen  { model: {{ inputs.model_pool | default('coders-a-bg') }}; reasoning_effort: medium; }
.preserve { model: {{ inputs.model_pool | default('coders-a-bg') }}; reasoning_effort: high; }
```

The nodes, in order (a node with no condition below takes its single success edge; every node
also has an unconditional edge to `record`, R12, so a failure still records):

| Node | Type | What it does |
|---|---|---|
| `claim` | command | Writes `/tmp/fabro/box` from `coder_pool`. Reads the issue and its comments. Computes the body hash. Routes `sharpen_go=false` to `record` when the issue is not open, lacks `agent`, carries `agent-in-progress`, its newest precheck marker is not `verdict=unverifiable body=<current hash>`, or a `fabro:sharpen` marker names the current hash as `body=` or `applied=`. Writes `reason`. |
| `prep` | command | Index the checkout at `main` (as `issue-precheck`'s `prep`). Writes the draft inputs (C3). |
| `draft` | agent `.sharpen`, 20m | The Elephant. Submits `/tmp/fabro/sharpen/draft.json` (C2). |
| `draft_gate` | command | C4. Refusal routes to `record` with outcome `cannot_sharpen`. `status=cannot_sharpen` routes the same way. Otherwise writes the audit inputs (C3) and routes to `comprehend`. |
| `comprehend` | agent `.sharpen`, 15m | Lens 1 (C5). |
| `verify` | agent `.sharpen`, 15m | Lens 2. |
| `preserve` | agent `.preserve`, 15m | Lens 3. |
| `audit_gate` | command | C5 and C6. Routes `converged` to `apply`, `open` (round < 3) to `draft`, `capped` or `churn` to `post`. |
| `apply` | command | C8 steps 1–3. Stale routes to `post` with outcome `stale`. |
| `recheck` | agent `.sharpen` | R10 copy of `issue-precheck`'s `check` prompt (C10). |
| `recheck_gate` | command | R10 copy of `issue-precheck`'s `gate`, with path replacements (C10). |
| `post` | command | Writes the `fabro:sharpen` comment, and after `recheck`, the precheck marker for the new body (C8 step 4, C10). |
| `record` | command | Emits the run's result as `context_updates` (C11). Always exits 0. |

`stall_timeout` is above every node timeout (R2). A failed agent stage takes its unconditional
edge to `record`, which records `outcome=failed`. The run then ends failed, so the scheduler sees
a failure and applies D12.

**Box stages:** `draft`, `comprehend`, `verify`, `preserve` and `recheck` are `"box": true` in
the Stage manifest. The generator puts them under `coder_box_stages["issue-sharpen"]`, so a
background run on the same box yields to them (Facts).

### C2. The draft contract (`_io/schemas/sharpen-draft.schema.json`)

```json
{
  "status": "proposed | cannot_sharpen",
  "reason": "required when cannot_sharpen; ≤ 600 chars",
  "body": "the complete proposed body, WITHOUT a ## Post-deploy checks section",
  "post_deploy_checks": ["one line each, no newline; [] when none"],
  "changes": [
    {
      "criterion_ref": "AC3 — the 1-based index of the bullet under ## Acceptance criteria in the ORIGINAL body",
      "original": "the bullet's text, exactly as in the original body",
      "proposed": "the new bullet text, or null when the criterion moved to post_deploy_checks",
      "transformation": "pr_text_to_code | command_to_ci | command_to_behaviour | no_drop_to_threshold | to_post_deploy",
      "evidence": [{"where": "path:line | .fabro/ci.sh:N | issue body", "says": "≤300 chars"}]
    }
  ],
  "human_actions": ["≤ 300 chars each; [] when none"]
}
```

`changes` has 1 to 20 rows when `status=proposed`. `evidence` has at least one row per change.
`additionalProperties: false` everywhere. The transformations:

| Transformation | From | To | Required evidence |
|---|---|---|---|
| `pr_text_to_code` | a criterion that needs PR-description text | the same content as a code comment, a docs file, or a repository file the diff adds | none beyond the original quote |
| `command_to_ci` | a command `.fabro/ci.sh` does not run | the `ci.sh` command that covers the same files | the `.fabro/ci.sh:N` line (or the script line it calls) |
| `command_to_behaviour` | a command `ci.sh` does not run, with no `ci.sh` equivalent | a behaviour the Refuter can read from the diff (a test that asserts X exists) | `path:line` where the behaviour lives or will live |
| `no_drop_to_threshold` | "coverage must not drop" and the like | "`<config key>` in `<file>` is raised to the new totals, rounded down" | the `path:line` of the threshold key |
| `to_post_deploy` | a human-only check, a screenshot, a manual or staging check, a `.github/workflows/` change, or anything else the agent cannot do or the Refuter cannot verify | moved to `post_deploy_checks`, verbatim or lightly reworded | none |

### C3. Inputs and the audit's sealed directory

**Draft inputs** (`/tmp/fabro/sharpen/`, written by `prep`, declared in the manifest):

| Name | Required | What |
|---|---|---|
| `issue.md` | yes | Title, labels, body, and the last ten non-marker comments, in `issue-precheck`'s `issue.md` format. |
| `criteria.md` | yes | The original `## Acceptance criteria` bullets, numbered `AC1..ACn`, extracted by `prep`. |
| `ci.md` | yes | `.fabro/ci.sh` verbatim, then each script, `package.json` script and Makefile target it calls (one level deep), each under a heading with its path. Cut at 40,000 chars with `- (cut to fit…)`. |
| `rules.md` | yes | The USER-GUIDE section *Writing criteria the factory can verify*, copied verbatim into the package as `prompts/rules.md` and copied by `prep`, plus the C2 transformation table. |
| `precheck.md` | yes | The newest precheck marker comment's body (its summary and evidence). |
| `orientation.md` | no | The pinned `fabro-orientation` issue's body, as `ralph`'s `prep` reads it. |
| `pack.md` | no | The newest `fabro:context-pack` comment's body. |
| `findings.md` | no | From round 2: the open findings and the finding history (C6), written by `audit_gate`. |

**The audit directory** `/tmp/fabro/sharpen/audit/`. `draft_gate` deletes it and then writes it on
every round. It holds only:

| Name | What |
|---|---|
| `proposed.md` | The assembled proposed body (C4 step 5), exactly what `apply` would write. |
| `changes.md` | The change table rendered from `changes`, with evidence. |
| `original_criteria.md` | `criteria.md` unchanged. |
| `ci.md` | The draft's `ci.md` unchanged. |

Each lens's manifest inputs are these four files and nothing else. The lenses also see the
checkout at `main` through their tools. Nothing else under `/tmp/fabro/sharpen/` is a lens input.

### C4. `draft_gate`

In order. The first failure writes `outcome=cannot_sharpen` and a one-line `rule`, and routes to
`record`.

1. **Receipt and schema.** The result has an `_io` receipt and passes `sharpen-draft`'s schema.
   A missing receipt is a refusal (the precheck's `gate` pattern).
2. `status=cannot_sharpen` → `outcome=cannot_sharpen`, `rule=model:<reason>`.
3. **Transformations** are from C2's list. `proposed` is null if and only if the transformation
   is `to_post_deploy`.
4. **Preservation** (D10). Split both bodies into lines.
   - Every `<!-- fabro:` line of the original appears in the proposal, unchanged, at the same
     line index relative to the start (a marker on line 1 stays on line 1).
   - The sections `## Decisions made during triage`, `## Proposed task map` and `## Split into`,
     and every `## Non-goals` (or `## Non-goals`-prefixed) section, are byte-identical.
   - Every section other than `## Acceptance criteria` and `## Post-deploy checks` is
     byte-identical, after normalising only trailing whitespace and the final newline.
   - Under `## Acceptance criteria`: every original bullet with no `changes` row is present
     byte-identically, in the original order. Every row's `original` matches its `ACn` bullet
     exactly. A row with a non-null `proposed` has that text present as a bullet. No bullet is
     present that is neither an unchanged original nor a `proposed`.
   - The proposal's `body` has no `## Post-deploy checks` heading.
5. **Assembly.** The proposed body is `body`, plus `\n## Post-deploy checks\n\n- [ ] <item>` for
   every original Post-deploy item (kept, in order), then every `post_deploy_checks` item, then
   the `original` of every `to_post_deploy` row, de-duplicated. This is triage's `improve_gate`
   assembly.
6. Write the audit directory (C3). Emit `round=<n>`.

### C5. The audit lenses and a clean round

Each lens submits `/tmp/fabro/sharpen/lens/<lens>.json` against its schema, where `<lens>` is
`comprehend`, `verify` or `preserve` (schemas `sharpen-comprehend`, `sharpen-verify` and
`sharpen-preserve`). Each has `findings[]` of
`{severity: blocker|major|nit, criterion_ref: "ACn"|null, message: ≤400, evidence: [{where, says}]}`.

- `comprehend` also has `proposed_paraphrase` and `existing_system_paraphrase` (≤1,200 each): what
  the issue now asks for, and how the code works today, as a context-free implementer reads them.
- `verify` also has `criteria[]`, one per bullet of the proposed `## Acceptance criteria`:
  `{text, verdict: checkable_from_diff|checkable_by_ci|unverifiable, why}`.
- `preserve` also has `pairs[]`, one per change row:
  `{criterion_ref, verdict: same|substitution|contradiction_resolved, why}`. A `substitution` is a
  weaker or different behaviour presented as the same requirement. A `contradiction_resolved` is a
  change that picks a side between a criterion and a non-goal.

**`audit_gate` judges a round clean when all of these hold:**

1. All three lens files exist with an `_io` receipt and pass their schemas. A lens without one is
   `not run`, and the round is not clean.
2. No finding is a `blocker` or a `major`.
3. Every `verify.criteria[]` verdict is `checkable_from_diff` or `checkable_by_ci`, and every
   proposed bullet has a verdict.
4. Every `preserve.pairs[]` verdict is `same`, and every change row has a pair.

Rules 3 and 4 also produce blocker findings in the history (lens `verify` or `preserve`), so the
next draft sees them.

### C6. Finding history, churn and the cap

`/tmp/fabro/sharpen/history.json` is owned by `audit_gate` and never read by a lens. An entry is
`{fp, lens, criterion_ref, message, severity, round_opened, state: open|fixed, round_fixed}`.

- **Fingerprint** `fp` = SHA-256 of `lens + "|" + (criterion_ref or "-") + "|" + normalised message`.
  Normalise by lower-casing and collapsing whitespace. Identifiers, paths and numbers are kept.
- An entry open last round and absent this round becomes `fixed`.
- A finding whose `fp` matches a `fixed` entry re-opens it, and it counts as a recurrence.
- **Churn** ends the loop: a recurrence, or a criterion whose `proposed` text in this round equals
  its `proposed` text two rounds earlier while it differed in between (the text alternates). Keep
  each round's `changes` in `/tmp/fabro/sharpen/rounds/<n>.json` for this check.
- Not clean, no churn, and round < 3 → write `findings.md` (the open findings, then the history)
  and route to `draft`.
- Not clean at round 3 → `capped`.
- Emit `sharpen_result=converged|open|capped|churn` and `round`.

### C7. The hold rule (scheduler)

An issue in the queue (it carries `agent`, has no `agent-in-progress`, and holds no lease) is
**sharpen-held** when all of these are true:

1. Its newest precheck job for its **current body hash** is `done` with `verdict=unverifiable`.
2. No `sharpen` row for the issue has `body_hash` or `applied_hash` equal to the current body hash.

A sharpen-held issue gets the label `precheck-unverifiable` and a `sharpen` row in state
`queued`. The scheduler removes the label when the row reaches a terminal state, or when the
issue's body hash changes while the row is still queued (the row then becomes `superseded`). The
label is added before the row exists and removed only after the row is terminal, so a pass that
stops halfway leaves the issue held, never queued with a stale verdict (`remainder.py`'s add
before remove rule).

The back-fill (D17) runs once, at start-up. It applies rule 1 to each queued issue's newest
`done` precheck job whatever its hash, because job rows written before this series carry no hash.
The run's `claim` then checks the marker's hash against the live body.

### C8. `apply` and `post`

1. **Stale check.** Re-read the issue. If `sha256(title + "\n" + body)[:12]` is not `claim`'s
   hash, write `outcome=stale` and route to `post`. Nothing is edited.
2. Post the original body as a new comment: first line
   `<!-- fabro:sharpen-original body=<hash> run=<run_id> -->`, then a blank line, then the
   original body verbatim. If that post fails, the node fails, and nothing has been edited.
3. `gh issue edit <N> --body-file /tmp/fabro/sharpen/audit/proposed.md`. Compute the applied
   hash. Write `outcome=applied`.
4. `post` writes the one `fabro:sharpen` comment, editing the newest one in place if there is
   one. Its first line is the marker:

```
<!-- fabro:sharpen state=<applied|capped|churn|stale|cannot_sharpen> round=<n> body=<original hash> applied=<applied hash or -> recheck=<verdict or -> run=<run_id> -->
```

Below the marker, in this order: one sentence on what happened; the change table
(`ACn | original | proposed | transformation | evidence`); the human actions, under
`### Human actions (not part of the issue)`; the open findings by lens (when not applied); the
Comprehension paraphrase; then the proposed body in a `<details>` block. The comment is cut at
60,000 characters. The cut drops the `<details>` block first, then the paraphrase, and never the
marker or the change table. Tokens are redacted as the precheck's `post` does it.

`post` runs on `applied`, `capped`, `churn` and `stale`. `cannot_sharpen` and `stop` post
nothing: `record` carries their reason to the scheduler.

### C9. Checker rule R17: a leased box package

`ops/check-graph-invariants.py` gains `LEASED_BOX_PACKAGES = ["issue-sharpen"]`, and R17 fails
when any of these is not true:

- Every `model:` in the stylesheet is `{{ inputs.model_pool | default('coders-*-bg') }}`.
- Every agent node has a class the stylesheet defines.
- The hooks include exactly `ralph`'s `box-claim` hook and no `box-guard` hook.
- There is no `[run.model.fallbacks]` table.
- `[run.run_branch] enabled = false`.
- `claim` writes `/tmp/fabro/box`.
- Every agent stage is `"box": true` in the manifest.

R15 (no `box-claim` outside coder loops) allows `LEASED_BOX_PACKAGES`. R16 does not apply.
`ops/fabro-io-manifest.py` adds `issue-sharpen` to `CODER_LOOP_WORKFLOWS`, so its box stages are
in every generated `coder_box_stages`. Add it to the checker's `CODER_LOOP_PACKAGES` where R15
needs it.

### C10. `recheck` (R10 copy)

- `recheck` uses a copy of `issue-precheck/prompts/check.md.j2` in this package, registered in
  `R10_CALIBRATION_COPIES` with prompt node `check`.
- `recheck_gate` is a copy of `issue-precheck`'s `gate`, with the replacements
  `("/tmp/fabro/precheck", "/tmp/fabro/sharpen/recheck")`, registered in the same entry.
- A command node before it, `recheck_prep`, writes `issue.md` and `open_issues.md` for the
  **applied** body into `/tmp/fabro/sharpen/recheck/`, in the precheck's format.
- `post` then writes the precheck marker for the applied body exactly as `issue-precheck`'s
  `post` does: same format, same 1,500-character cap, editing the newest precheck comment in
  place.
- A failed `recheck` does not fail the run, and it is not retried: its edge goes to `post` with
  `recheck=-`. The scheduler's per-hash producer then queues an ordinary precheck (D13).

### C11. The run's record and the scheduler's outcome

`record` emits, through `context_updates` on its own `stage.completed`:
`sharpen_outcome` (`applied|capped|churn|stale|cannot_sharpen|not_needed|failed`),
`sharpen_rule` (the gate's one-line reason, or empty), `round`, `body_hash`, `applied_hash`
(or empty), `recheck` (a verdict or empty), `changes` (row count) and
`transformations` (`name=count`, comma-separated). The scheduler reads it the way
`calibration.read_record` reads `precheck-calibration`'s `record`: from the run's own events.

The `sharpen` row's terminal states are `applied`, `capped`, `churn`, `stale`, `cannot_sharpen`,
`not_needed`, `failed` and `superseded`.

### C12. The lease

- `target_kind='sharpen'`. `issue_number` is the issue's number.
- Labels: `lane=coder`, `box=<pool>`, `workflow=issue-sharpen`, `issue=<n>`.
- Inputs: `issue_number`, `coder_pool=<pool>`, `model_pool=<the box's -bg row>`.
- It is dispatched through `FabroClient.dispatch_job`.
- It writes **no** `agent`, `agent-in-progress` or receipt labels. Receipt recovery and the
  diversity pass ignore it, because they filter `target_kind == 'issue'` already. The issue-run
  metrics and the scorecard's issue rows ignore it for the same reason.
- The background lane treats it as a coder loop: `issue-sharpen` joins `CODER_LOOP_WORKFLOWS` in
  `config.py`, and its manifest's box stages block a fill while they run. It has no gap-table
  rows, so its gaps predict 0.
- Release reads C11, writes the `sharpen` row's state and attempt, removes
  `precheck-unverifiable` on a terminal state, and writes a `run_history` row with
  `target_kind='sharpen'`.
