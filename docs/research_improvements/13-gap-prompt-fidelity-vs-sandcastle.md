# Gap — did the prompts lose substance in the port from Sandcastle-loop?

**Observation:** the workflow prompts feel small and low-effort next to their
Sandcastle-loop originals (`~/Documents/code/Sandcastle-loop`).

**Conclusion: they did not lose content — they are consistently larger. Two structural
things were lost, and one of them is the real regression.**

**Status 2026-09-24: nothing further to apply.** The remedy for loss 1 was the preamble
cut in gap 12, and it shipped at `truncate`, so the role prompt now follows the system
message directly. Loss 2, the MCP-enforced contract, is parked with 02: gates bounced a
contract about 2 times in about 750 visits across 38 runs.

---

## The content is all there

Sandcastle splits every agent role into a **system** prompt and a **user** prompt. Fabro
has one `prompt` attribute, so the port merged them. Byte counts:

| Role | Sandcastle (system + user) | Fabro | Delta |
|---|---|---|---|
| standards | 5803 + 587 = 6390 | 8582 | +34% |
| spec | 5283 + 641 = 5924 | 8178 | +38% |
| reviewer | 11365 + 753 = 12118 | 14136 | +17% |
| coder | 2601 + 277 = 2878 | 8515 | +196% |

Section-by-section, `pr-review/prompts/standards.md.j2` carries every part of
`pr-standards-review-agent-system-prompt.md`: Trust boundary, Method (6 numbered steps),
the twelve-item Fowler smell baseline, the Finding bar, and the severity meanings — plus
Inputs and Output sections the Sandcastle version delegated to its user prompt.

**Nothing was dropped.** One thing was deliberately removed: Sandcastle requires
`confidence >= 70` and orders findings by it; the Fabro contract says *"No `kind`, no
`axis`, no `risk`, no `confidence`."* That was a choice, not an oversight, and it is
recorded here so it stays a choice.

---

## What was actually lost

### 1. The system/user split, and what now occupies the system slot

In Sandcastle the role definition **was** the system prompt. In Fabro there is one prompt
attribute, delivered as a user turn, and the system-prompt slot is occupied by
`project_memory` — which defaults to `true`
(`lib/foundation/fabro-types/src/graph.rs:424`) and loads the **target repository's**
`AGENTS.md` / `CLAUDE.md`.

So for the standards reviewer the ordering is now:

1. **System:** the target repo's `AGENTS.md` (not the reviewer's role).
2. **User:** the preamble — every completed stage, 25 lines of command output each (see
   `12-gap-agent-context-preamble.md`).
3. **User, continued:** the 8.5 KB role prompt.

The role instructions are intact but demoted and diluted. That combination is almost
certainly what "low effort" feels like from the outside: the prompt is not smaller, it is
buried.

**This is not straightforwardly fixable** — Fabro exposes no per-node system prompt, and
`project_memory=false` would *remove* the repo's standards from a reviewer whose entire job
is judging conformance to them, which is worse. What it argues for is
`01-tier-1-single-attribute-fixes.md` item 4: cutting the preamble restores the role prompt
to roughly where it sat in Sandcastle, right after the system message.

### 2. The MCP-enforced output contract — the real regression

`pr-standards-review-agent-system-prompt.md` opens with:

> Your deliverable MUST be submitted only through the Structured-result MCP tool
> `structured-result_submit_standards_findings`. Pass the contract JSON as the sole
> `result` argument… On validation errors, read the returned field errors, fix `result`,
> and call the tool again.

`Sandcastle-loop/structured-result-submit.mts` shows what that tool does: validate, return
`{ok:false, code, errors[]}` with per-field paths on failure, and on success **write the
canonical file** into the worktree.

Two properties:

1. The payload travels as a JSON-schema'd **tool-call argument**, which providers constrain
   far harder than "end your message with JSON".
2. Exactly one writer of the contract file, and it is the validator.

The Fabro port replaced both with *"write `/tmp/fabro/review/standards.json`"* plus an
out-of-band `jq` gate that, on failure, bounces the agent through a **cold re-visit** — a
fresh session that re-reads the diff with none of the prior reasoning.

**That is the substantive thing the port lost**, and it is the same finding as
`02-tier-2-structured-output.md`, reached from the opposite direction.

---

## Why the obvious repair does not work

The instinct is `output_schema="@schemas/standards.schema.json"` with `output_retries=2`:
JSON Schema validation with an in-session repair turn.

It does not work for the large contracts, because a **custom** schema reads the final
response text and nothing else (`lib/components/fabro-workflow/src/handler/agent.rs:161-163`).
It demands exactly the prose-JSON behaviour the MCP was built to avoid.

And the MCP cannot be ported: the `sandbox` MCP transport *"requires a remote sandbox
provider (Daytona)"* and this deployment runs Docker; `stdio` spawns on the host, in the
fabro server container, on a different filesystem from the sandbox. Full reasoning and the
image-level blocker in `02-tier-2-structured-output.md`.

The available middle ground is `output_schema="routing"`, which **does** get the file
fallback chain and therefore an in-session repair turn without any echoing. Smaller win,
but real, and it targets the failure that actually happens.

---

## Recommended reading order for anyone revisiting the prompts

1. This document — the prompts are not the problem.
2. `12-gap-agent-context-preamble.md` — what is crowding them out.
3. `02-tier-2-structured-output.md` — what to do about the contract enforcement.

## What not to do

**Do not rewrite the prompts to be longer.** They already carry more than the Sandcastle
originals. Length is not the deficit.

**Do not set `project_memory=false`** on the reviewers to free the system slot. It would
remove the documented standards those reviewers exist to enforce, and
`pr-review/prompts/standards.md.j2:59-61` explicitly instructs the agent to read them.

**Do not restore `confidence`** without deciding it independently. It was removed on
purpose; the merge gate now keys on `risk` instead, and `fix_gate` enforces that
(`docs/auto-merge/01-risk-contract-and-fix-gate.md`).
