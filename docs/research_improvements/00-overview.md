# Fabro Feature Audit — Overview

**Read this first.** This folder records an audit of the three workflow packages against
the Fabro source at `context/fabro`, asking one question: *where are we hand-rolling
something Fabro already does, and where are we right not to?*

It also records four gaps observed while operating the deployment, and traces each to a
line of Fabro source.

## Method and provenance

Every claim here was checked against `context/fabro` at **0.357.0-nightly.0** (the
checkout) and cross-read against `docs/public/**`. Where the two disagree, the source
wins and the disagreement is noted.

The deployed server is **0.354.0-nightly.0** (`ops/.env.example`, `FABRO_VERSION`). Three
findings depend on that gap and say so explicitly.

Citations are `path:line` into `context/fabro`. Public-doc citations are
`docs/public/...`. Where a doc contradicts the source, the source line is given.

Nothing in this folder has been applied. It is a plan, not a changelog.

## How the series is organised

Two axes: **tiers** are the ordered work plan, **gaps** are the diagnoses behind four
operator observations. Gap documents end by naming the tier that carries their fix.

| File | What it holds |
|---|---|
| `01-tier-1-single-attribute-fixes.md` | Four graph-attribute changes. Two are live run-killers. Do these first. |
| `02-tier-2-structured-output.md` | The contract-validation question: `output_schema`, the repair loop, and why the Sandcastle MCP cannot be ported as-is. |
| `03-tier-3-smaller-items.md` | Run titles, `run_branch`, artifacts, the `meta_branch` upgrade trap, CI. |
| `04-confirmed-dead-ends.md` | Nine things that look like Fabro features we should adopt and are not. Proof for each, so nobody re-derives them. |
| `10-gap-run-issue-mapping.md` | Why the web UI cannot show which issue a run is working. |
| `11-gap-human-gate-options.md` | Why the rescue gate shows the wrong options. A Fabro bug, with a workaround. |
| `12-gap-agent-context-preamble.md` | What an agent actually receives besides its prompt. |
| `13-gap-prompt-fidelity-vs-sandcastle.md` | Whether the prompts lost substance in the port from Sandcastle-loop. |

## The ranked plan

| Tier | Items | Character |
|---|---|---|
| 1 | 4 | Single attributes and one string. No contract risk. Two fix silent run termination. |
| 2 | 1 | Structural. Pilot on one node before spreading. Smaller win than first estimated. |
| 3 | 5 | Real but not urgent. Independent of each other. |
| — | 9 | Confirmed not worth doing. Recorded so the question stays settled. |

## Headline findings

1. **The circuit breaker can terminate a `backlog` run with no route to `human_rescue`.**
   Identical deterministic command failures are capped at 3 per run; the rework ladder
   permits 6. Tier 1. See `01`.
2. **The stall watchdog fires before `watch_checks` can finish.** Its 35-minute timeout is
   unreachable behind a 30-minute default. Tier 1. See `01`. **Applied 2026-09-22**,
   after it cost seven runs; it is the only item in this directory that has shipped.
3. **`[R] Retry with guidance` never renders on the rescue gate.** Fabro drops labelled
   freeform edges from the option list. Tier 1 and gap `11`.
4. **Agents receive an unbounded preamble** — every completed stage, with 25 lines of
   command output each. Tier 1 and gap `12`.
5. **Custom `output_schema` reads response text only.** The file fallback exists for
   routing schemas and not for custom ones, which is why the obvious fix does not work.
   Tier 2 and gap `13`.
6. **`[run.pull_request]` is off everywhere and should stay off.** The existing decision is
   correct; `04` records the mechanical reason it is correct.

## What was checked and found clean

- Model fallback chains, quoted dotted keys, and the 422 failure mode.
- `stdin_source` usage in `issue-triage`'s `record_answer` — correct, and the pattern
  Tier 1 item 4 reuses.
- Hook matcher anchoring, `checkpoint_saved` over `stage_complete`, `on_failure="succeed"`
  on `trigger_review`. All three reasoned correctly in the existing docs.
- `[run.clone] depth = 0` on `pr-review`, and the `git fetch --unshallow` belt-and-braces.
- The per-repo auto-merge switch living in the automation `description`. Still the only
  free-form writable field at 0.357. See `04`.
