# Invariants: issue-sharpen

Read before changing `.fabro/workflows/issue-sharpen/`, its prompts, its `workflow.toml`, its
schemas in `_io/`, or the scheduler's `sharpen.py` (ADR 0033, `docs/plans/issue-sharpening/`).

Every rule here passes `fabro validate` and fails at runtime, usually silently. The graph,
stylesheet and hook rules in `invariants-graph.md` and the model rules in `invariants-host.md`
apply as well; this file holds what is particular to the sharpening run. The package edits an
issue's body with no human approval, so a rule broken here ships a weaker requirement and nobody
reads it.

## The run's shape

| Rule | What happens otherwise |
|---|---|
| `issue-sharpen` is a **leased box package**: its checker rule is R17, not R16. Every model is `{{ inputs.model_pool \| default('coders-?-bg') }}`; every agent node has a class the stylesheet defines; it carries ralph's `box-claim` hook exactly and **no** `box-guard`; it has no `[run.model.fallbacks]` table at all; `[run.run_branch] enabled = false`; `claim` writes `/tmp/fabro/box`; every agent stage is `"box": true` in the Stage manifest | A hosted model or a fallback spends credits the run exists to save, and a `box-guard` cancels the run that owns the box. R15 leaves the package to R17 (`LEASED_BOX_PACKAGES`). `coder_pool` (the lease's pool, no `-bg`) feeds `/tmp/fabro/box`; `model_pool` (the box's first pool with `-bg`) feeds the models |
| `record` always exits 0, and its `fail` edge is what ends a failed run failed | The scheduler's lease release retries a run that **ended failed** (D12). `record` succeeding alone would end every run succeeded, so a box fault would read as a finished sharpening and the issue would never be retried. `record -> fail [condition="context.sharpen_outcome=failed"]`, and `fail` is a node that exits 1; every other outcome, `cannot_sharpen` included, ends the run succeeded |
| A model's refusal is an ending, never a fault | `draft_gate` refusing a draft, a `cannot_sharpen` status, `capped` and `churn` all route to `record` with their outcome and the run succeeds. The scheduler does not retry them (D10, D12), and the issue runs as written |
| Nothing writes to GitHub before `apply`, and `apply` writes the original body in a comment before it edits | A stage that wrote earlier would leave a half-done sharpening on an issue the run later gave up on. A failed original-body comment fails the node with nothing edited; the retry posts a second original, and the newest is the record |
| `recheck_post`, `post` and everything after `apply` never fail the run's work | The body is already applied. `recheck`, `recheck_prep`, `recheck_gate` and `recheck_post` take their failure edge to `post` with `recheck` unset, and `post` writes one stderr line on a failed comment and exits 0. A failed run here would retry, find the applied hash named by `applied=`, and stop `not_needed`, which leaves the issue sharpened and its record wrong |

## The audit's sealing (D7)

| Rule | What happens otherwise |
|---|---|
| Each lens's manifest inputs are **exactly** the four files `draft_gate` copies into `/tmp/fabro/sharpen/audit/`, and no path outside that directory | The Goldfish is the check on the Elephant because it has not seen how the draft was made. A lens that can read `draft.json`, `issue.md` or another lens's result is the draft's own opinion twice |
| Each lens stage lists the run's other files under `sealed`, and `io-guard` is wired for `comprehend`, `verify` and `preserve` in `workflow.toml` | The guard is the sealing the Refuter uses: a guardrail against a mistake, not a security boundary. A new file the run keeps under `/tmp/fabro/sharpen/` goes in every lens's `sealed` list |
| `default_fidelity="truncate"`, and the lenses are separate agent nodes | Anything higher prepends a recap of the earlier stages, the draft's transcript among them, to the lens's prompt. Each node is its own session |
| `draft_gate` clears `/tmp/fabro/sharpen/lens/` every round, and `audit_gate` accepts a lens file only with an Input receipt whose `stage` is that lens's node id | A lens result left from round 1 could make round 2 look clean |

## The gate's text is code

| Rule | What happens otherwise |
|---|---|
| `draft_gate` is `jq` only, with the preservation rules of C4 and the closed transformation list of C2. It also refuses a proposal that leaves `## Acceptance criteria` with no bullet (every criterion moved to Post-deploy checks): the Refuter would have nothing to check, so the issue runs as written instead. Its expected refusals are tested in `ops/gate_tests/test_issue_sharpen.py` for every corpus fixture the #67 lint rates L1 to L5 | A rule that is loosened passes `fabro validate` and `make check` unless a gate test names the case. The corpus is `skills/plan-factory-issues/fixtures/issues/`, shared with #67 |
| Nothing in a script or prompt of this package uses a backslash or a `#` line inside `script=`, so the shell and `jq` here are written without `\n`: newlines come from `[10] \| implode` and whitespace classes from `[[:space:]]` | R6 forbids both, and the parser turns `\n` into a real newline inside an embedded `jq` program |
| `prompts/rules.md` is the USER-GUIDE's *Writing criteria the factory can verify* up to the workflow-file paragraph, plus the transformation table, and `prep` embeds it in its script | The package's own prompts cannot be read from the sandbox, so `prep` writes the file from a copy in its script. `test_the_rules_the_draft_reads_are_the_user_guides` fails when the guide, the file or the embedded copy drifts |
| `recheck`, `recheck_gate` and `recheck_post` are R10 copies of `issue-precheck`'s `check`, `gate` and `post` (`R10_CALIBRATION_COPIES["issue-sharpen"]`, node ids `copy:original`, with the `/tmp/fabro/precheck` prefix remapped to `/tmp/fabro/sharpen/recheck`) | A lookalike judges the applied body by different rules than the lane would, and the marker it posts is the one the scheduler's per-hash producer trusts. `make check` fails when a copy drifts; change the original and the copy in one commit |
| The body hash is `sha256(title + "\n" + body)[:12]`, in `issue-precheck`'s `claim`, `issue-sharpen`'s `claim` and `apply`, and in the scheduler's `github.body_hash` | A disagreement makes the hold, the stale check and the per-hash producer talk about different texts. `ops/scheduler/tests/test_sharpen.py` pins the Python form to the shell pipeline for a null body, a trailing newline and non-ASCII text |

## The scheduler's half

| Rule | What happens otherwise |
|---|---|
| The hold is `precheck-unverifiable`, written **before** the `sharpen` row and removed only after the row is terminal | A pass that stops halfway leaves the issue held, never queued with a stale verdict. `remainder.py`'s add-before-remove order |
| `unverifiable` is not in `PRECHECK_HOLDABLE`: the hold does not read `SCHEDULER_PRECHECK_HOLDS` or `SCHEDULER_PRECHECK_HOLD_VERDICTS` | A switch that does nothing, or one that turns the sharpen queue off by accident. Naming it is a `ConfigError` that points at ADR 0033 |
| A `sharpen` lease writes no `agent`, `agent-in-progress` or receipt label, and reconcile never requeues its issue | The receipt scan and the requeue rule exist for issue runs. The issue stays in the cache under its hold label |
| `issue-sharpen` is in `CODER_LOOP_WORKFLOWS` and in `workflow_version.ENTRYPOINTS`, and in `_io/manifest.json`'s `coder_box_stages` through the generator's `CODER_LOOP_WORKFLOWS` | The background lane fills a gap while the sharpening is in a box stage, and `box-claim` cannot tell a sibling slot's sharpening from a hosted stage. A workflow missing from `ENTRYPOINTS` makes `dispatch_job` raise |
