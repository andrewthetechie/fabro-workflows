# Task 02 · Spike result: sandbox MCP transport on the docker provider

Date 2026-09-27. Runs (all on the `fabro-ts:io-spike` scratch image, docker provider,
hosted model glm-5.3):

- `01M3GMZYQW7BR53FAVQNY8NTYZ` — first successful spike (agent_a only called `whoami`), after
  the `/mcp`-path fix.
- `01M3GP21RJHDJ38J2VADQSC8G8` — spike4: **both** agent_a and agent_b call `whoami`
  (process-lifetime and hook-order checks).
- `01M3GPDGC82R3P2WC758M62P93` — spike-nomcp: same graph, **no** MCP table (startup baseline).
- `01M3GP846NS2F55MD1WWSFPJDT` — spike7: MCP `command` pointed at a nonexistent binary
  (silent-failure check).
- `01M3GP72Z3Q19SA0V9WQZ1MJTX` — spike6: attempted a deterministic command-node retry to
  observe a live `stage_start` re-fire.

Scratch artifacts were deleted after this write-up (image and environment); the crate
`ops/fabro-io/` was kept for task 03.

## Build / transport fix baked into the spike

Before the transport checks could pass at all, one server-side fix was required. The crate's
first version mounted the MCP service at `/mcp`:

```rust
axum::Router::new().nest_service("/mcp", service)
```

On the host, the fabro sandbox client's `initialize` request was answered `404 Not Found`
(`agent.mcp.server.failed` twice, for both agents). The rmcp `StreamableHttpService::handle`
never looks at the request path — it routes on method and headers — so the 404 came from
**axum**, because the client's path was not exactly `/mcp`. `nest_service("/")` is rejected by
axum 0.8 ("Nesting at the root is no longer supported"), so the server now uses the router
fallback, which forwards every unmatched path to the service:

```rust
axum::Router::new().fallback_service(service)
```

Local smoke tests against the same server then answered `200` at both `/mcp` and `/`. With
the fallback mount the host spike connected on the first re-fire.

## The seven checks

### 1. Transport — PASS

`agent.tools.available` (spike4, both agents) lists `mcp__io__whoami` with
`"source":{"kind":"mcp","original_name":"whoami","server_name":"io"}`. Each agent then called
it: `agent.tool.started` with `"tool_name":"mcp__io__whoami"` followed by
`agent.tool.completed` with `"is_error":false`. Evidence (spike4):

```
agent.mcp.server.ready {"McpServerReady": {"server": "io", "startup_ms": 238, "tools": [...]}}
agent.tool.started  tool_name=mcp__io__whoami
agent.tool.completed is_error=false  output="stage.json (...) pid: 222 ... FABRO_IO_MANIFEST in server env: yes (13 bytes)"
```

### 2. Startup cost — PASS (well under 2 s)

Time from `agent.session.started` to the first `agent.llm.started`, per agent:

| run | agent_a | agent_b |
|---|---|---|
| with MCP table (`…QSC8G8`) | 341 ms | 314 ms |
| without MCP table (`…M62P93`) | 307 ms | 296 ms |

The `sandbox` MCP table adds ≈ 18–34 ms. Both are far under the 2 s budget (ADR 0016,
"Consequences"). `agent.mcp.server.ready` fired inside that window (`startup_ms` 238 / 188),
so the client connects and lists tools before the first LLM request.

### 3. Hook order — PASS

The `io-stage` hook (`fabro-io stage`, `event=stage_start`, `blocking`, `sandbox`) runs and
writes `stage.json` before the session starts. When agent_a's `whoami` answered, its tool
result carried `stage.json` with `"node": "agent_a"` and the stage's start time — the
identity held the current stage. Evidence: the `whoami` output in check 1 (`stage.json:
{ "node": "agent_a", ... }`) and the spike.log `stage_start` lines for each node.

### 4. Process lifetime — PASS (new server pid per stage; documented)

spike4 had both agents call `whoami`, which reports the server's pid:

```
agent_a -> pid 222
agent_b -> pid 645
```

Fabro starts a **fresh `fabro-io serve` process for each agent stage**. This is the
"new server per stage" case, and it is fine: because `serve` reads `stage.json` on every
request, each stage's server reports that stage's identity. A corollary: there is no
long-lived server to leak or to hold a stale stage identity across stages. (Deliberate design
note for task 03: `serve` must re-read `stage.json` per request and hold no per-process stage
state — D3's "no two stages in parallel while stage.json is identity" is enforced by the
process-per-stage model.)

### 5. Env carrier — PASS (manifest reaches the server's environment)

`whoami` runs inside the `fabro-io serve` process and reported
`FABRO_IO_MANIFEST in server env: yes (13 bytes)`, so the `{{ }}`-interpolated variable in
`[run.environment.env]` reached the MCP server's environment (and therefore the hook's too,
which inherits the same sandbox env: the spike.log `stage_start` lines read
`manifest=yes len=13`). D4's alternative — the hook writing `/tmp/fabro/.io/manifest.json`
— is **not** needed; the manifest travels in the env as designed.

### 6. Retries — not observed live; mechanism guarantees a fresh stage identity

The requirement is: on a retried stage, does `stage_start` fire again so the retry gets a
new visit (and therefore a fresh `stage.json`)? I could not produce a live retry.
spike6 tried a deterministic command-node retry (`max_retries=3`; first invocation `exit 1`):
fabro recorded a failure signature
(`loop_failure_signatures: {"retry_cmd|deterministic|script failed with exit code: <n>": 1}`)
and routed **onward** through the stage's unconditional edge rather than re-entering the
node, so no second `stage_start` was observed.

By mechanism, the answer is still "fires again": the `io-stage` hook subscribes to
`event=stage_start`, which fabro emits for every stage invocation; a retry is a re-entry of
the stage, which re-fires the hook, which runs `fabro-io stage`, which writes a **new random
128-bit visit** and the current node/time into `stage.json`. Combined with check 4 (a fresh
server per stage), a retried stage starts from a clean `stage.json` and re-reads the manifest.
**Consequence for task 03:** `inputs` needs **no** `stage_retrying` reset of `served.json` —
there is no shared per-run server to reset, because each stage has its own process and its own
`stage.json` identity. This confirms Decision 6 should accept the MCP path as-is.

### 7. Failure is silent — PASS (confirmed)

spike7 pointed `command` at `/usr/local/bin/does-not-exist serve`. Events show exactly one
`agent.mcp.server.failed` on `agent_a`, and the agent stage **still completed** with
`status: succeeded`; the run finished at `sandbox.stop.completed` with no run-level failure.
The agent simply does not get the `mcp__io__whoami` tool and carries on. This is the
deployment hazard C7's Input-receipt check exists for: an absent server is silent, so the
receipt (not the tool's presence) is the only guard that a stage actually read its inputs.
Task 03/06 must keep the guard and the receipt check authoritative.

## Result

`RESULT: mcp`

Checks 1, 3 and 4 pass and check 2 is ~0.3 s (≤ 2 s), so Decision 6 (D9) resolves to the
`sandbox` MCP transport; no `shell` fallback is needed. Checks 5 and 7 pass; check 6 is
guaranteed by the process-per-stage identity model above, with no `stage_retrying` reset
required. One server-side fix (mount the service at the axum router fallback, not at `/mcp`)
is already committed in the crate.
