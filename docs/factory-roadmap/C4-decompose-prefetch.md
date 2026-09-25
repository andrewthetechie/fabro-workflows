# C4 · Decompose-ahead prefetch

**Status:** proposed (rev 2 "Lever A"). **Axis:** throughput. **Effort:** M.
**Feasibility:** medium.

## Problem

`decompose` (hosted `glm-5.3`, up to about 17 minutes, `timeout="30m"`) runs at the start of
each leased window while the coder box waits.

## Design

A lease-less `decompose-ahead` run (on A2 3b's dispatch path) takes the **next** queued issue
while the box works the current one. It writes the decomposition to a place the next
`backlog` run can read. The sandbox is gone by then, so it must be durable: a hidden marker
comment on the issue (`<!-- fabro:tasks sha=<main sha> -->` plus the JSON), or the scheduler DB.

`backlog`'s `decompose_gate` then accepts a prefetched decomposition only if `main`'s SHA
still matches, or `main` moved only in files the tasks do not name. Otherwise it decomposes
fresh. The existing `decompose_gate` validation applies to both.

## Gain and risk

It saves one `decompose` per run from the lease window (about 5–15 minutes). The risk is a
stale plan, handled by the SHA check. **Do not build it until B1 shows `decompose` is a
material share of lease time.** B2's `size` field may make it less necessary by keeping runs
small.
