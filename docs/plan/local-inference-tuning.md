# Moving work off the hosted models

**Applied 2026-09-21.** Two changes to where inference runs, measured from the run store
rather than guessed: `improve` moved to the run's own local coder box, and the six
reviewers were split into six stylesheet classes so they can be tuned apart — with the
two `standards` reviewers taken down a tier to `glm-5.3-flash`.

## What the runs actually cost

Two completed `backlog` runs, read from `stage.completed.billing.tokens` via
`fabro events`. "Billable" is `input + output + cache_read`.

| run | repo/issue | wall | total | hosted | local |
|---|---|---|---|---|---|
| `01M305QY4SE7GHXSPNVGCCY1YN` | jelly-swipe#350 | 152m | 9.80M | 6.55M (66.8%) | 3.25M (33.2%) |
| `01M2ZYF5X7F97ARZ4SKXZ9NXBH` | womens-fantasy-sports#1199 | 329m | 32.37M | 17.19M (53.1%) | 15.18M (46.9%) |

The hosted half, both runs combined — 23.74M tokens:

| node | class (before) | model | tokens | % hosted | min | tok/min |
|---|---|---|---|---|---|---|
| `review_merge.review_fix` | `.review` | glm-5.3 | 4,889,364 | 20.6% | 12.1 | 404k |
| `improve` | `.improve` | glm-5.3 | 4,524,085 | 19.1% | 36.7 | 123k |
| `review_merge.standards` | `.review` | glm-5.3 | 3,352,958 | 14.1% | 11.4 | 294k |
| `review` | `.review` | glm-5.3 | 3,038,514 | 12.8% | 29.1 | 104k |
| `quality` | `.review` | glm-5.3 | 2,286,088 | 9.6% | 15.2 | 150k |
| `standards` | `.review` | glm-5.3 | 1,959,244 | 8.3% | 10.9 | 180k |
| `spec` | `.review-frontier` | kimi-k3 | 1,400,953 | 5.9% | 10.0 | 140k |
| `decompose` | `.decomp` | glm-5.3 | 1,253,869 | 5.3% | 12.1 | 104k |
| `review_merge.spec` | `.review` | glm-5.3 | 908,200 | 3.8% | 4.8 | 189k |
| `extra_decompose` | `.review-frontier` | kimi-k3 | 124,481 | 0.5% | 2.7 | 46k |

Cache reads are about 90% of every hosted line — `review_merge.review_fix` is 4.28M of
cache_read against 116k of fresh input. These stages are not thinking hard, they are
**re-reading a large context on every turn**.

## Three findings that decided it

**Context is not the constraint.** Peak `context_window.input_tokens` per node, across
both runs: the largest anywhere is `review_merge.review_fix` at **114,519** — 44% of the
local boxes' 262144 window. `coder` already peaks at 100,140 on a box. Every hosted node
would fit locally with room to spare, so nothing here is blocked by window size.

**The local boxes are not the weak link they are assumed to be.** `rework_t1` (local) ran
three times across the two runs and **never escalated**: `rework_t2`/`t3`/`t4` (glm-4.7 →
kimi-for-coding → glm-5.3) did not fire once. The escalation ladder exists and is going
unused.

**`.review` was one knob wired to six nodes in two phases** — three per-task reviewers in
`backlog` and three merge-phase reviewers in `_shared/review-merge`, the gate in front of
an unattended squash into `main`. They could not be tuned apart at all.

## What changed

**`.improve` → the run's pinned box.** 19.1% of hosted spend, per-task spec sharpening at
a 57k peak context, with `review` and the rework ladder immediately downstream to catch a
weak spec. It names `{{ inputs.coder_pool | default('coders-a') }}` — the *same* box as
`.coder`, because the scheduler leases exactly one box per run and naming the other would
queue behind whoever holds it. It also takes `reasoning_effort: medium`, matching the
other two local classes.

**Six classes instead of one:**

| node | graph | class | model |
|---|---|---|---|
| `review` | backlog | `.review-task` | glm-5.3 |
| `standards` | backlog | `.review-standards` | **glm-5.3-flash** |
| `quality` | backlog | `.review-quality` | glm-5.3 |
| `standards` | review-merge | `.merge-standards` | **glm-5.3-flash** |
| `spec` | review-merge | `.merge-spec` | glm-5.3 |
| `review_fix` | review-merge | `.merge-fix` | glm-5.3 |

`backlog`'s `.review-frontier` (`spec`, `extra_decompose`) is untouched on kimi-k3.

**Both importers name every imported class explicitly.** `review-merge` is spliced into
`backlog` *and* `pr-review`, and `pr-review`'s `*` is `glm-5.3` while `backlog`'s is
`coders-a`. A class added to the shared graph and forgotten in one stylesheet inherits
that graph's `*` — which in `backlog` is a **silent downgrade of the auto-merge gate to a
local box**, not a visible error. So all five shared classes (`merge-standards`,
`merge-spec`, `merge-fix`, `rebase`, `rebase-t2`) carry an explicit rule in both.

## `glm-5.3-flash`

Same z.ai coding plan, less capable than full glm-5.3, more capable than a local box, and
a **262144** window against full 5.3's 202752. Added to the settings overlay as a bare
model table — `zai` is a fabro built-in provider, so no `display_name`-bearing provider
table is needed (unlike `kimi`, whose omission cost 14 hours on 2026-09-20).

```toml
[llm.providers.zai.models."glm-5.3-flash"]
display_name = "GLM 5.3 Flash (z.ai coding plan)"
api_model = "glm-5.3-flash"
limits = { context_tokens = 262144, max_output_tokens = 16384 }
capabilities = { text = true, tools = true, reasoning = true }
```

It escalates **up**, never sideways: `"glm-5.3-flash" = ["zai:glm-5.3"]` in both
`workflow.toml` fallback tables, so a flash outage degrades into a better review rather
than a worse one. The key must stay quoted or it becomes a nested table and the
automation fire returns 422.

Verified live before any stylesheet named it: catalog reload clean
(`grep -c "Rejected reloaded"` → 0), `fabro model list` shows it at 262k under `zai` with
no provider collision, and `fabro model test -m glm-5.3-flash` → `ok`.

## Why standards, and not the rest

A standards review reads a documented rubric; that is the cheapest judgement to move down
a tier, and the one whose failure mode is visible (a missed rule) rather than subtle.
Left alone deliberately:

- **`decompose`** (5.3% of hosted) — the highest-leverage step in the run. An oversized
  decomposition cascades into every task after it, which `docs/perf/04` records as a
  sizing alarm. Not worth saving 5%.
- **`spec` / `extra_decompose`** (6.4% combined) — frontier on purpose, and cheap.
- **`review_merge.review_fix`** (20.6%, and the best tokens-per-minute on the board at
  404k) — tempting, and still the last gate before an unattended squash into `main`.
  After the two merge failures of 2026-09-20 this is the wrong week to also weaken the
  reviewer behind it.

`.merge-standards` *is* part of that gate. It is the most mechanical of the three, and
reverting it is a one-line stylesheet edit in two files.

## The trade being made

Local is **~3.1× slower per turn** than glm-5.3, measured: 31.6 s/turn on box-a/box-b
(455 turns / 239.8 min) against 10.2 s/turn for hosted `improve` (216 turns / 36.7 min).
The box is leased for the whole run either way, so moving work onto it adds no contention
— it makes each run longer, which costs factory throughput. `improve` alone is roughly
+37 min on a run averaging 240.

## What to measure next

Whether the flash standards reviews change `fix_outcome`, the rework rate, or the number
of findings the merge-phase `review_fix` has to fold in. If a cheaper standards pass
causes even one extra rework tier, it has cost more than it saved.

The next dial after that is the per-task `.review-task` and `.review-quality` (22.4% of
hosted together) moving to a box — worth about +58 min per run at the measured rate.
