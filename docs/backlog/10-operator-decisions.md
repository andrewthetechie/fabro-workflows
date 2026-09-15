# Task 10 — Open operator decisions (for the operator, not for this task to decide)

Date: 2026-09-14. Compiled by task 10 (post-deploy cleanup). These are decisions the
operator makes; this document presents options and evidence, it does not pick one.

---

## Decision A — The `*/15` cron interval on the four backlog automations

Four automations drive one `backlog` run per repository at a `*/15 * * * *` schedule:

| Automation | Environment | Target repo |
|---|---|---|
| `backlog-jelly-swipe` | `python` | `andrewthetechie/jelly-swipe` |
| `backlog-lawncare-saas` | `python-node` | `andrewthetechie/lawncare-saas` |
| `backlog-womens-fantasy-sports` | `ts` | `andrewthetechie/womens-fantasy-sports` |
| `backlog-writers-app` | `rust-node` | `andrewthetechie/writers-app` |

**The arithmetic.** 96 ticks/day × 4 automations = **384 runs/day**. The overwhelming
majority of those find no issue labelled `agent` and quiet-exit at `acquire` — but every
one still spawns a Docker sandbox and clones the repository.

**Observation made during task 10 (2026-09-14 ~16:20 UTC):** at this moment the fabro
automation API reports the `schedule` trigger (`every-15m = "*/15 * * * *"`) on all four
automations as `enabled = false`, and no host `crontab`, `systemd` timer, or other
external cron references fabro. There is no independent scheduler; the runs come from
fabro's own automation scheduler. So the `384/day` figure describes steady-state
operation when the schedule trigger is enabled; as of this check it is currently
disabled (likely paused during task 09's E2E / the redesign cutover). The decision
below still applies to whatever steady-state interval the operator wants on re-enable.

**Note re: task 12.** Task 12's daily `~/bin/fabro-branch-sweep.sh` sweeper removes the
git-branch consequence of `*/15` (the `fabro/run/*` and `fabro/meta/*` leaks). So this
is now purely a **compute-waste** question, not a repository-hygiene one. The 768/day
branch growth stops regardless of the interval chosen.

**◀ OPERATOR DECISION (2026-09-14): leave the cron OFF for now.** The operator wants
more validation and testing before going fully automated. All four automations' schedule
triggers remain `enabled = false` (already the case; no change made). When re-enabled
later, the operator picks from the options below. Task 11 should record that scheduling
is intentionally paused pending further validation/testing.

**Options (operator picks one):**

1. **Keep `*/15`.** Latency from labelling an issue to the agent starting is ≤15
   minutes. Cost: 384 sandbox spawns/day.
2. **Move to hourly (`0 * * * *`).** 96 spawns/day, ≤60 minutes latency. 4× less
   waste. Best latency/cost trade-off if sub-hour responsiveness is not required.
3. **Event-driven instead of cron.** Fire the automation from a GitHub webhook when the
   `agent` label is applied. Near-zero waste and near-zero latency, but it is new
   integration work and is not in this series' scope.

Task 11 will record whichever interval the operator chooses (or note it as pending).

---

## Decision B — `rust-node` resource allocation

Resolved **during task 10 as a change** (not deferred): the host has 16 cores / 30 GB
RAM (~28 GB available). `rust-node` was raised from 2 CPU / 4 GB to **4 CPU / 8 GB** via
`PUT /api/v1/environments/rust-node` (new revision `e5ad06eddc1bba87a49197f3651fe5bf0edc84b871f3b46942f2ffb9b8292890`) so that
a cold Rust workspace build can finish inside the `validate` stage's 20-minute
`./.fabro/ci.sh` timeout. There is still headroom if a further bump is ever wanted.

---

## Decision C — `run_title_generation` warning

Resolved during task 10 as **documented, cosmetic** (no change left in place). Every
run logs `WARN fabro_server::run_title_generation: Run title generation failed ...
model litellm/coders does not support structured output`.

Root cause: fabro picks the title-generation model via catalog
`small_default_for(ready_providers)`. No litellm model declared `small_default`, so it
fell back to the provider default `coders` (local deepseek). Structured output is gated
by the per-model `response_format` catalog capability, and none of the litellm models
declare it (`capabilities = { text, tools, reasoning }` only), so `coders` fails the
catalog gate "does not support structured output".

Investigation (what I tried and what I found):

- The knob the task anticipated *does* exist: setting `small_default = true` on a model
  in `[llm.providers.litellm.models."..."]` makes title generation (and the
  `fabro exec` summarizer) resolve to that model. I set it on `glm-5.3` and confirmed
  via `GET /api/v1/models?provider=litellm` that it then reported `small_default=true`.
- But that alone does **not** stop the warning: `glm-5.3` also lacks a declared
  `response_format` capability, so it fails the same catalog gate (message would just
  become "model litellm/glm-5.3 does not support structured output").
- I verified `glm-5.3` genuinely supports JSON-schema structured output by calling the
  litellm proxy directly (returned the expected object). I then declared
  `response_format = { json_object = true, json_schema = true }` on `glm-5.3` so the
  fabro path would pass the gate. That revealed a deeper issue: through fabro's
  structured-completion path (`POST /api/v1/completions` with a schema, `model
  litellm/glm-5.3`) the model returns an empty body — consistent 502
  "the model did not return a JSON document: EOF while parsing a value" (3/3
  attempts), even though the same model/schema works when called against the litellm
  proxy directly. So fabro→litellm structured output to `glm-5.3` is currently broken
  regardless of the catalog flag.
- There is no deployment setting to turn title generation off (no relevant `config`
  knob; the fabro CLI in the container has no `config list` subcommand).

Outcome: the setting that would silence it is therefore not effectively available in
this deployment today. The warning is **cosmetic** — the run title falls back to a
serviceable deterministic title and nothing else breaks. I **reverted** the
`settings.toml` changes to the operator's original state (no `small_default`, no
`response_format` override) so production config is exactly as the operator had it.
Task 11 should record this as a known, harmless warning and note two facts for anyone
who wants to revisit: (1) `small_default` selects the title model, and (2) fabro's
structured-output path to `glm-5.3` currently fails with an empty response, which is
the real blocker worth fixing upstream.
