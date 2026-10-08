# Every agent session runs the openai Agent profile

**Status:** proposed (2026-10-08). It becomes accepted when the first `backlog` run after
the change shows `spec` and `extra_decompose` sessions with `"profile":"openai"` in
`agent.memory.loaded`, loading `.codex/instructions.md`, and submitting valid verdicts. The
implementation plan is issue #2.

Pebble runs each agent **Session** on an **Agent profile**: a prompt, a tool set and a list
of instruction files. fabro picks the profile per model: the model row's
`metadata.agent.profile`, then the provider's, then a default implied by the provider's
first codec (`adapter_agent_profile` in `fabro-llm/src/catalog.rs`). Every provider here
speaks `openai-chat` except `kimi`, whose coding-plan endpoint speaks `anthropic-messages`.
So every kimi session runs the anthropic profile, which loads `AGENTS.md` and `CLAUDE.md`
and never `.codex/instructions.md`, the file that carries the **Agent guide**
(docs/coder-tweaks C1). In the 49 `backlog` runs of `docs/coder-tweaks/result-2026-10-08.txt`,
all 163 sessions without the guide were kimi-k3: `spec` and `extra_decompose`
(`.review-frontier`) and one glm-5.3 session that fell back to kimi-k3. `rework_t3`
(`kimi-for-coding`) and every other fallback to kimi have the same gap.

We decided that **every agent session runs the openai profile**, whatever model or
provider serves it. A provider whose codec implies another profile declares
`metadata.agent.profile = "openai"` in the server's settings overlay. This factory uses no
Anthropic model; the anthropic profile was an accident of Kimi's wire format. The profile
and the codec are separate choices: the pin changes the prompt, the tools and the
instruction files, and kimi requests still go out in the Anthropic message format. Pebble's
openai profile already handles a codec that refuses freeform tools: it offers `edit_file`
instead of `apply_patch`.

One profile also means one tool vocabulary. `git-guard` matches `^shell$`, and the
`fabro-io` tools and the guide name the native tools by their openai-profile names. A
second profile would have to be checked against each of them.

## Considered options

- **Also install the guide as `CLAUDE.md`** in the four entry nodes (issue #2 as first
  filed). Rejected: four graph changes and their fixtures, to keep a second profile that
  nothing needs. Every stage would still have two prompts and two tool sets to reason
  about.
- **Pebble's `kimi` profile for kimi models.** Rejected: it loads only `AGENTS.md`, so the
  guide would still be missing.
- **A per-stage profile trial** (coder-tweaks D5, issue #4), to steer `improve` to the code
  index through a different prompt. Withdrawn: it would make an exception on day one. D5's
  remaining lever is a PATH wrapper around `grep`/`rg`, which waits for issue #3's
  corrected M1.

## Consequences

- Kimi stages lose the anthropic prompt and its `Task*` tools. They get the openai prompt
  and the guide. The `spec` reviewer's first-pass rate and the `rework_t3` outcomes are
  watched for 20 or more runs. If `spec` breaks, the fix is to revert the one overlay key.
- The failure is silent, like the other deployment invariants in AGENTS.md. A new
  provider on an Anthropic or Gemini codec, or a new fallback to one, drops the guide, and
  nothing reports it. A checker (`ops/check-agent-profiles.py`) is therefore part of the
  change. It runs offline on `ops/settings.toml.example` and in `make verify-host` against
  the live overlay, and it fails on any enabled provider or model whose effective profile is
  not `openai`.
