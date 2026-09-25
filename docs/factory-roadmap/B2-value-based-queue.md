# B2 · Work on the highest-value thing: a deterministic score and a work-mix quota

**Status:** proposed. **Axis:** throughput, quality. **Effort:** M. **Feasibility:** high.
**Depends on:** A2 (the `proposed` state). **Better with:** B3 (triage picks enums through the
submit tool), B1 (to tune the weights).

## Problem

"Next" today is, in order: operator override, the `priority` label, static repo priority from
`repos.toml`, then **oldest issue number** (`ops/scheduler/src/fabro_scheduler/queue.py:108`).
Nothing encodes value, size or risk, and nothing balances *where* work comes from.
`arch-review` can file up to 8 issues per run per repo. Once they are `agent`, they compete
equally with a human-filed bug, and if the operator bumps them they take the whole fleet (as
on 2026-09-25).

## Design

### Triage classifies. It never scores.

Triage already reads each issue with a high-reasoning model. Add four **enumerated** fields to
`triage.json`, validated by `triage_gate` (or by B3's submit tool):

| Field | Values |
|---|---|
| `value` | `user-facing-bug`, `user-facing-feature`, `dev-velocity`, `hygiene` |
| `size` | `xs`, `s`, `m`, `l`, `xl` |
| `risk` | `low`, `medium`, `high` (data, auth, payments, migrations, deploy config = `high`) |
| `source` | `human`, `red-main`, `scout`, `architecture` (**derived by code** from labels and markers, not asked of the model) |

`apply_ready` writes them as labels (`value:user-facing-bug`, `size:m`, `risk:low`), so the
scheduler reads them from the inventory it already polls, and a human can correct one by
editing a label.

### The scheduler scores, in code

A weight table in `repos.toml` (versioned, operator-owned):

```
score = (value_weight[value] + age_bonus(first_seen)) / size_cost[size]
```

This is WSJF-shaped. `risk` does not change the order. It gates: a `high`-risk issue is
dispatched normally, but B2 adds `requires-human-review` (A2 part 2), so it never auto-merges.
Ranking becomes: override, then `priority` label, then **red-main issues**, then score, then
repo priority, then issue number.

### A work-mix quota

In `choose_next`, per repo, over a rolling window of the last 4 dispatches: at most 1 may have
`source=architecture` or `value=hygiene`. Scout findings (B6) are capped the same way. This
keeps the factory on human-filed work first, which is the point of it, while the improvement
loops still get a steady share.

### `xl` never dispatches

An issue triaged `size:xl` goes back through triage with a "split this" instruction (triage
can already file child issues, or it posts `needs_info` asking the human to split). ADR 0011's
data is the reason: runs with 9+ tasks used 42% of lease time and merged nothing.

## Verification

- Scheduler unit tests: ranking with scores, the quota under a queue of 8 Architecture issues
  and 1 human bug (the human bug dispatches first or second), `xl` never dispatches.
- Backtest: replay 2026-09-19..25's queue with the new ranking and compare the order with what
  actually merged (B1).

## Open questions

1. Starting weights. Suggest `user-facing-bug 8, user-facing-feature 5, dev-velocity 3,
   hygiene 1`, `size_cost xs 1, s 2, m 3, l 5`.
2. Does `size` stay the triage estimate, or get corrected by `decompose`'s task count? Suggest
   triage first, and record the actual task count in B1 to calibrate it.
