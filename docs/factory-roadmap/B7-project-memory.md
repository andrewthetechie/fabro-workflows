# B7 · Project memory: repo-native first, Hindsight only where it fits

**Status:** proposed. **Axis:** quality. **Effort:** S (v1), M (v2).
**Feasibility:** v1 high, v2 medium. **Depends on:** nothing for v1. v2 waits for B1's
evidence.

## The question

Would a memory service such as Hindsight (retain/recall/reflect, TEMPR retrieval,
self-hostable with Docker, and an MCP server) improve on `AGENTS.md` plus the context docs
already in each repo?

## What exists

- Fabro's `project_memory` (on by default) injects `AGENTS.md` into **every** agent's system
  prompt, walking from the repository root down (plus `CLAUDE.md` for Anthropic models,
  `GEMINI.md` for Gemini, and so on).
- Every target repo already has design memory in the right form: `AGENTS.md` (1–13 KB),
  `CONTEXT.md` (7–30 KB, the domain glossary) and 19–35 ADRs. It is reviewed, versioned,
  diffable and revertible.

## Answer

**For design knowledge, no.** A vector store holding the same content would be unreviewed,
would drift from the code, and would give the scanner a second source of truth to disagree
with. ADRs are also the anti-oscillation mechanism (A3 layer 1). Moving decisions into a
retrieval index would weaken that.

**There are two real gaps:**

1. **Reading.** Only `AGENTS.md` is injected. Whether an agent reads `CONTEXT.md` or the ADR
   index depends on each prompt remembering to say so.
2. **Writing.** Nothing records what runs **learn**: "this suite flakes on X", "rework tier 1
   cannot handle the Tauri bindings", "reviewers always flag Y in this module", "`ci.sh` needs
   Postgres warm".

## v1 (S): repo-native, deterministic writes

- **Read path.** Every agent prompt that reads code lists `CONTEXT.md` and `docs/adr/`
  (index, then the relevant ADR) as inputs. B4's dossier names the specific passages per task.
- **Write path.** A bounded section, `## Factory notes (maintained by automation)`, at the end
  of each target repo's `AGENTS.md`. It has at most 30 lines, and the oldest rotate out to
  `docs/factory-notes-archive.md`, which is not injected.
- **Only gate output writes it, never agent prose**, so memory cannot fill with a model's
  rationalisations. Sources: a flaky job that passed on rerun (ADR 0011 D2), the rework tier
  reached per module, a blocked-merge reason that repeats, a `ci.sh` failure class.
- **Written out-of-band on `main`** by the scheduler (it has the token and sees every terminal
  run), as one small commit per day with a `chore(factory):` subject. It must not be written
  inside a run: the merge phase runs after the squash, so an edit there lands on a branch that
  has already been merged.

## v2 (M): Hindsight for experience facts only, and only if v1 overflows

Trigger: v1's 30 lines keep rotating out facts that B1 shows would have prevented a repeat
failure.

- Self-host Hindsight (Docker) on the fabro host. Expose its MCP over **`http`**. That works
  here, because memory does not need the sandbox filesystem (unlike B4).
- One **bank per repo**. Store experience facts only: what failed, what fixed it, which
  approach reviewers rejected, and the evidence run ids. **Design decisions stay in ADRs.**
- Register it once in fabro's MCP catalog (`/api/v1/mcp-servers`) and enable it per workflow
  in `[run.agent.mcps]`.
- **Shadow mode first:** agents may `recall`, and only the scheduler `retain`s, from gate
  outputs. Compare B1's rework and revert rates for 2 weeks before letting agents `retain`.

Its published benchmarks are conversational memory, not code-factory memory, so its value
here is unproven. That is why it is v2 and gated on evidence.

## Verification

- v1: after two weeks, the notes section exists in all four repos, stays at 30 lines or fewer,
  and every line cites a run id.
- v2: the shadow-mode comparison above.
