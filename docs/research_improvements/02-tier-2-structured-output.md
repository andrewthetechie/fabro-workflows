# Tier 2 — Contract validation and the structured-output question

**One item, and it is smaller than it first looks.** The original framing of this finding
was that Fabro's `output_schema` restores the Sandcastle structured-result MCP contract.
That framing was wrong. This document records what is actually available, what it buys,
and why the faithful port is blocked.

**Status 2026-09-24: open. Park it.** The failure this pilot targets (an agent that exits
`succeeded` with no usable contract, followed by a cold re-visit) is now rare. Across the
38 scheduler-dispatched runs, the gates saw about 750 visits. Two of them bounced a
contract: `improve_gate` once in 204 visits, and `review_gate` once in 214. The merge-phase
`standards_gate`, `spec_gate` and `fix_gate` never bounced in 34 visits each. The pilot
node named below, `standards`, now runs mainly as `review_merge.standards` inside
`backlog`. Its two failures in the sample were **15-minute timeouts** on a 23-file and a
72-file PR, not bad contracts, and a repair turn cannot fix a timeout. If the bounce rate
rises, the analysis below still holds. Until then, the counters cost almost nothing.

**Update 2026-09-25: superseded by `docs/factory-roadmap/B3-structured-io-submit-tool.md`.**
The operator's goal is now a principle rather than a bounce rate: an LLM should not own a
data format. The replacement keeps property 2 below (one validating writer) without an MCP
transport. A `fabro-submit` CLI is baked into the profile images. The agent calls it through
fabro's `shell` tool with fields as flags, and the CLI builds, validates and writes the
canonical file, returning field-level errors in the same session. Re-checked on upstream
`main` 2026-09-25: the `sandbox` MCP transport still requires Daytona preview URLs, so the
transport blocker below is unchanged.

---

## The shape of the thing we hand-rolled

Thirteen gate nodes across three graphs carry sixteen attempt counters
(`decompose_attempts`, `improve_attempts`, `review_attempts`, `standards_attempts`,
`spec_attempts`, `quality_attempts`, `extra_decompose_attempts`, `merge_attempts`,
`fix_result_attempts`, `rebase_attempts`, `fix_attempts`, `gh_fix_attempts`,
`triage_attempts`, …). Every one implements the same loop:

1. `jq`-validate a contract file the agent wrote under `/tmp/fabro/`.
2. On the first failure, increment a counter file and `exit 1`.
3. The `outcome=failed` edge routes back to the agent node for a **fresh visit** — a new
   session, re-reading the diff, with none of the prior reasoning.
4. On the second failure, exit zero and publish an `invalid` status so the graph can route
   to `human_rescue` / `mark_needs_human`.

Fabro has a native equivalent for steps 2–3, and it is strictly better on step 3: the
repair happens **inside the same live agent session**.

---

## What Fabro actually provides

### The repair loop is real

`lib/components/fabro-workflow/src/handler/llm/pebble.rs:1136-1176`:

```rust
if let Some(schema) = &output_schema {
    let mut repair_attempts = 0_i64;
    loop {
        match validate_agent_output_sources(schema, &response, request.sandbox,
                                            last_file_touched.as_deref()).await {
            Ok(_) => break,
            Err(error) => {
                if repair_attempts >= node.output_retries() { return Err(...); }
                let repair_message = error.repair_message(schema, previous_validation_error.as_ref());
                response = self.prompt_live(&mut live, CodingInput::text(repair_message), ...).await?;
                repair_attempts += 1;
            }
        }
    }
}
```

`repair_message` (`structured_output.rs:262-280`) carries the schema expectation and the
per-field validation errors, and flags when a problem is unchanged from the previous
repair. `output_retries` defaults to 2 and does **not** consume node retry attempts.

### But the sources differ by schema kind, and that is the whole story

`lib/components/fabro-workflow/src/handler/agent.rs:155-189`:

```rust
pub(crate) async fn validate_agent_output_sources(
    schema: &OutputSchemaKind, response_text: &str,
    sandbox: &Arc<RunSandbox>, last_file_touched: Option<&str>,
) -> Result<ValidatedStructuredOutput, StructuredOutputError> {
    if !matches!(schema, OutputSchemaKind::Routing) {
        return structured_output::validate_response_text(schema, response_text);
    }
    // routing only, below this line:
    //   response text -> status.json -> last file touched (.json/.md)
```

| Node / schema | Sources read | In-session repair |
|---|---|---|
| agent, `output_schema="routing"` | response text → `status.json` → last touched `.json`/`.md` | yes |
| agent, `output_schema="@schema.json"` | **response text only** | yes |
| command node, either | merged stdout+stderr | **no** — deterministic, non-retryable |

A **custom** schema therefore demands that the model emit the entire contract as prose in
its closing message. That is exactly the behaviour that proved unreliable in Sandcastle,
and it is why the structured-result MCP exists. For `fix_result.json`, with populated
`findings`, `fixes_applied` and `not_fixed` arrays, it is a bad bet.

**Do not put a custom `output_schema` on the large contracts.**

---

## Why the Sandcastle MCP cannot be ported as-is

Sandcastle's `submitStructuredResult` (`Sandcastle-loop/structured-result-submit.mts`) is a
validating **writer**: the agent passes the contract as a tool-call argument, the tool
validates it and returns `{ok:false, code, errors[]}` with per-field paths on failure, and
on success **writes the canonical file into the worktree** itself. Two properties matter,
and the second is the one Fabro cannot reproduce here:

1. The payload travels as a JSON-schema'd **tool parameter**, which every provider's tool
   path constrains far harder than "end your message with JSON".
2. There is exactly one writer of the contract file, and it is the validator.

Fabro's three MCP transports (`docs/public/agents/mcp.mdx:234-310`):

| Transport | Where the server runs | Verdict |
|---|---|---|
| `sandbox` | inside the run sandbox | **Unavailable.** *"The sandbox transport requires a remote sandbox provider (Daytona) that supports preview URLs."* We run Docker; `[server.sandbox.providers.daytona] enabled = false` in `ops/settings.toml.example`. |
| `stdio` | *"Fabro spawns a child process on the host"* — i.e. the fabro server container (Alpine, `/bin/sh`, no node) | Different filesystem from the sandbox. Cannot write the contract file. |
| `http` | wherever we host it | Same filesystem problem, plus a service to operate. |

Secondary blocker even if a transport existed: the MCP bundle is
`node .sandcastle/structured-result-mcp/dist/server.mjs`, and `ops/profile-images/Dockerfile.python`
is `FROM python:3.13.15-trixie` with **no node** — and that is `jelly-swipe`, the canary.
The other three images have node (`python-node`: `node:22.23.2-bookworm-slim`; `rust-node`:
`nodejs npm` via apt; `ts`: `oven/bun` with a node fallback symlink).

**A validation-only MCP is still possible** over `stdio` or `http`: the tool validates and
returns field errors, the agent writes the file itself after `ok: true`. That preserves
property 1 and loses property 2. It costs a runtime in the fabro container (or a hosted
service) and a new operational surface. **Park it** until the cheaper option below has been
measured.

---

## The recommended change: `output_schema="routing"` on the reviewer agents

The asymmetry cuts in our favour. Routing schemas get the file fallback chain, so the
agent keeps writing its contract file exactly as it does today, Fabro finds it, and the
repair turn fires only when there is genuinely nothing usable.

That covers the dominant failure — *the agent exited `succeeded` without writing a usable
contract* — which is the failure the `*_attempts` counters were built for, and it covers
it in-session.

### Two changes required to make it land

**1. The contract needs one recognised routing field.**

Today `verdict.json` is `{"decision", "findings", "summary"}`. None of those is a routing
field, so the scan reports `NoRelevantJsonObject` and the repair turn fires on a perfectly
good file. Add:

```json
{
  "decision": "approved",
  "findings": [],
  "summary": "...",
  "context_updates": { "review_decision": "approved" }
}
```

The gate still runs afterwards, and its `context_updates` merge later and overwrite, so a
gate that downgrades to `invalid` still wins. `jq` reads `.decision` and ignores the new
key, so the gate script needs no change.

**2. Choose the fallback source deliberately.**

`status.json` is read from the **sandbox working directory** — the repo checkout
(`agent.rs:172`, `read_sandbox_file(sandbox, "status.json")`, a relative path). It will
therefore be swept into checkpoint commits by `git add -A`, appear in the PR diff, and
enter `ci_fix_gate`'s scope check. If used:

```toml
[run.checkpoint]
exclude_globs = ["status.json"]
```

is **mandatory**, not optional.

The alternative is the last-file-touched fallback pointing at the existing
`/tmp/fabro/review/verdict.json` — no new file, no exclusion — but it requires the
contract file to be the **last** file the agent writes
(`agent.rs:198-209`, eligible extensions `.json`/`.md`, and the JSON object must be
terminal with only whitespace after it). A scratch write afterwards silently breaks it.

**Prefer `status.json` plus the exclude glob.** Unambiguous beats tidy.

### One sharp edge

The fallback chain continues only on `NoJsonObject | NoRelevantJsonObject`
(`structured_output.rs:253-259`). If the agent's prose happens to contain a JSON object
*with* a routing field that is malformed or wrongly typed, Fabro fails straight into the
repair loop instead of reading the file. Unlikely for a reviewer whose prose is findings
text; worth knowing when diagnosing a surprise repair turn.

---

## Honest accounting of what this buys

| | Before | After |
|---|---|---|
| Agent produced no usable contract | counter + cold re-visit | in-session repair with field errors |
| Contract malformed JSON | counter + cold re-visit | in-session repair with field errors |
| Contract structurally valid, domain-wrong | gate `exit 1` + cold re-visit | **unchanged** — gate still bounces |
| Payload delivered as a tool argument | yes (Sandcastle MCP) | **no** — not available on Docker |

Routing validation checks only that a recognised routing field exists and is well-typed.
It does not check `findings[]`, the "changes_requested requires a blocking finding" rule,
or the `own_findings` / `fixes_applied` accounting added after `jelly-swipe#379`. Those
stay in the `jq` gates — and would have to anyway, since JSON Schema cannot express
cross-field rules of that shape.

So the counters do not all disappear. `review_gate`'s semantic check still needs its
`exit 1` path.

---

## The pilot

**One node: `pr-review`'s `standards`.** Smallest contract, clearest enum, and
`standards_gate` (`.fabro/workflows/pr-review/workflow.fabro:307`) does nothing but
shape-check and publish one key — so nothing else moves while the mechanism is observed.

1. Add `output_schema="routing"` and `output_retries=2` to the `standards` node.
2. Add `context_updates.standards_status` to the `standards.json` contract in
   `prompts/standards.md.j2`, and instruct the agent to write `status.json` in the
   workspace root carrying the same object.
3. Add `[run.checkpoint] exclude_globs = ["status.json"]` to `pr-review/workflow.toml`.
4. Fire a review and read `fabro events <run> -p` for a repair turn.

**Success criterion:** at least one observed run where the agent's first response fails
validation and the repair turn produces a valid contract without a second stage visit.

**Failure criterion:** repair turns fire and still fail, or never fire because the file
fallback already satisfies validation on every run. The first means prose-adjacent
contracts are not enough here and the validation-only MCP becomes worth its ops cost. The
second means the counters were solving a problem we no longer have, and the cheapest
outcome is to leave the gates alone.

Do not spread to the other twelve nodes before the pilot answers that.

---

## Worth filing upstream

Custom `output_schema` should honour the same fallback chain as routing
(`agent.rs:161-163`). There is no stated reason for the asymmetry, and closing it is the
single change that would make Fabro's built-in cover this case properly — the agent could
then write a fully-schema-validated contract file with in-session repair and no echoing.
