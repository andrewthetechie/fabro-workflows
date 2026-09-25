# Factory roadmap: overview

**Read this file first.** This folder is the ranked improvement plan for the whole factory
(the fabro workflows, the coder scheduler and the host). There is one design record per item.
It is a plan, not a changelog. Nothing in it is implemented unless an item's **Status** line
says so.

Each record gives the problem with its evidence, a design, the dependencies, effort,
long-term feasibility and the open questions. A record is not yet a task series. When an
item is picked up, turn it into a numbered series under its own `docs/<name>/` folder in the
`docs/architecture-review/` format (`00-overview-and-contracts.md` plus one file per task),
and link that folder from the record's Status line.

Written 2026-09-25 against `main` at `eec7acf`. It replaces the untracked working notes in
`.scratch/dark-factory-roadmap*.md`. `docs/research_improvements/` stays as the evidence
behind several items, and its status table points here.

## The goal these items serve

Once a repository is added, the factory works on it continuously, and:

- it works on the **highest-value** change first, not the oldest issue;
- it does not **oscillate**: one improvement loop never undoes another;
- it does as much as possible **deterministically**. LLMs write text, and code owns every
  data format, number and routing decision;
- **features and refactors it proposes itself wait for a human before implementation**.
  Bugs it finds itself do not;
- coders can be **added and drained dynamically**;
- there is **no chat surface**. A human feeds work by filing issues (with pi or Claude), by
  promoting `proposed` issues, and by merging the PRs that policy holds back.

## Ranking

Items are ranked by impact on **repeatability** (no silent loss of work, no loops), then
**code quality** (what lands on `main` is mergeable by a human's standard), then
**throughput** (merged PRs per coder-hour). **S** is a day or less, **M** is two to five days,
**L** is one to two weeks.

| Order | Item | File | Axis | Effort | Feasibility |
|---|---|---|---|---|---|
| 1 | A1 Andon cord | `A1-andon-cord.md` | repeatability | S–M | high |
| 2 | A2 Human gate before proposals are implemented | `A2-proposal-gate-and-intake.md` | quality, throughput | S | high |
| 3 | B1 Factory scorecard | `B1-factory-scorecard.md` | enabler | M | high |
| 4 | A3 Anti-oscillation | `A3-anti-oscillation.md` | repeatability, quality | M | high |
| 5 | A4 Diff-hygiene gate | `A4-diff-hygiene-gate.md` | quality | S–M | high |
| 6 | A5 Cross-family refuter | `A5-cross-family-refuter.md` | quality | M | high |
| 7 | B2 Value-based queue | `B2-value-based-queue.md` | throughput, quality | M | high |
| 8 | B3 Structured I/O submit tool | `B3-structured-io-submit-tool.md` | repeatability | M | high |
| 9 | B4 Shared code context | `B4-shared-code-context.md` | throughput | M | high |
| 10 | B6 Self-reported bugs | `B6-self-reported-bugs.md` | throughput, quality | M per source | high |
| 11 | B7 Project memory | `B7-project-memory.md` | quality | S / M | high / medium |
| 12 | B5 Elastic coder fleet | `B5-elastic-coder-fleet.md` | throughput | M–L | medium-high |
| 13 | B8 Fabro upgrades | `B8-fabro-upgrades.md` | repeatability | M–L | required |
| 14 | C1 Reproduction gate and scoped tests | `C1-repro-gate-and-scoped-tests.md` | quality | M–L | high |
| 15 | C2 Program-design step | `C2-program-design.md` | quality | M | high |
| 16 | C3 Parallel hosted reviewers | `C3-parallel-reviewers.md` | throughput | M | medium |
| 17 | C4 Decompose-ahead prefetch | `C4-decompose-prefetch.md` | throughput | M | medium |
| — | H Housekeeping | `H-housekeeping.md` | repeatability | S | high |

**Why this order.** A1 and A2 take a few hours each and stop losses that happen today. B1
comes early because A3, A4, A5 and B2 each need a before/after number, and a baseline can be
taken only once. **B8's first step, the move to 0.362.0-nightly.0, is already scheduled as
`docs/fabro-upgrade/`**, and it runs before any item here that changes a `.fabro` file, so
new work is written against the new version.

## The factory today (verified 2026-09-25)

- **Workflows.** `backlog` (issue → tasks → code → review → PR → the imported `review_merge`
  phase → squash-merge), `pr-review`, `issue-triage`, `arch-review` (ADR 0012), and the shared
  phases `_shared/triage/` and `_shared/review-merge/`.
- **Scheduler.** `ops/scheduler/` owns admission to the two llama.cpp boxes (`coders-a`,
  `coders-b`). Its ranking is override, then the `priority` label, then repo priority
  (`repos.toml`), then issue number (`queue.py:108`). It dispatches only `backlog`.
- **Fabro.** The host runs 0.354.0-nightly.0. Fabro's own admission is FIFO under one global
  `max_concurrent_runs` (4 on this host). Automation triggers are `api` and `schedule` only,
  and an automation fire carries no inputs. That is why the scheduler exists (ADR 0005).
- **Outcomes.** Before ADR 0011, 4 of 33 PRs were merged by the workflow. Runs dispatched on
  2026-09-25 show 4 of 5 PRs merged, and 2 more runs ended `succeeded` with no PR. The median
  run is about 4 h, with a tail of 13–23 h (scheduler `/api/history`).
- **Merge-phase models.** `standards` on `glm-5.3-flash`, `spec` and `review_fix` on `glm-5.3`,
  `ci_fix` on `glm-5.3-flash` (`backlog/workflow.fabro:156-159`). That is one vendor.
- **Target repos** already carry `AGENTS.md` (auto-injected by fabro's `project_memory`),
  `CONTEXT.md` (7–30 KB) and 19–35 ADRs each.

## Settled constraints (do not re-propose)

The operator declined these on 2026-09-24. Re-open one only with new evidence.

- Releasing the coder lease at `pr_handoff` / `open_pr`.
- A hosted coder pool, a hosted `improve`, or a hosted rebase.

Also settled:

- No chat or interactive surfaces.
- No Jev or other decision model: no decision here is short-text classification at volume.
- No GitNexus: its PolyForm Noncommercial license binds use, and `lawncare-saas` is
  commercial.
- **No MCP server that must read the run's working tree.** Fabro's `sandbox` MCP transport
  requires Daytona preview URLs, and `stdio`/`http` servers run outside the sandbox
  (`docs/public/agents/mcp.mdx`, checked on 0.354, 0.362 and upstream `main`). An MCP server
  is fine for anything that does not need the sandbox filesystem: memory, docs, GitHub reads.

## Rules every item inherits

The **Deployment invariants** table in `AGENTS.md` applies to every item. The ones these items
hit most often:

1. A command node that prints `context_updates` declares `output_schema="routing"`.
2. `\"` is the only backslash in a `.fabro` file, and there is no `#` in a `script=`.
3. A class used in `_shared/` has an explicit rule in **both** importing stylesheets.
4. A new provider that fabro does not know built-in needs `display_name`. After any
   settings-overlay edit, check that `Rejected reloaded` prints `0`.
5. A root graph's `stall_timeout` stays above every node timeout it contains.
6. Every merge-phase failure into `mark_needs_human` writes both `merge_block_reason` and
   `needs_human_reason`.
7. New shell in a graph gets a section in `ops/test-task-gates.sh`, staged the way `claim` is
   when the node writes before `prep`.
8. Invoke the `/fabro-workflow` skill before editing a `.fabro` file or a `workflow.toml`.

## Reference sources

- HumanLayer, *wsff.md* (software-factory pitfalls): maintainability erosion goes unpenalised,
  lights-off review grades itself, benchmarks see only pass/fail, horizontal plans, and no
  program design.
- `no-human-ai/no_human`: a cross-family refuter told to refute "done" (505 of 1,709 attempts
  sent back), a mechanical tamper count (44 stops), and a reproduction gate (46 refused
  proofs).
- vorflux.com: cross-lab review, and codifying judgment in the harness.
- Hindsight (hindsight.vectorize.io): retain/recall/reflect memory, self-hostable, with an
  MCP server.
