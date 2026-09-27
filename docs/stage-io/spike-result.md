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
- `01M3GRYM9YWJ84RSGJ1YP0KHYD` — spike6b: a gate fails once and routes back into an agent;
  the definitive check-6 observation (see check 6).

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
writes `stage.json` before the session starts. When each agent's `whoami` answered, its tool
result carried `stage.json` with that agent's own node — the identity held the current stage:

```
agent_a: stage.json = { "node": "agent_a", "started": "2026-09-27T05:34:36Z", ... }  (pid 222)
agent_b: stage.json = { "node": "agent_b", "started": "2026-09-27T05:40:42Z", ... }  (pid 645)
```

Evidence: the `whoami` outputs above and the spike.log `stage_start` lines for each node.

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

### 6. Retries — PASS for re-entry (observed) and for an engine retry (source)

The requirement is: on a retried stage, does `stage_start` fire again so the retry gets a
new visit (and therefore a fresh `stage.json`)? There are two ways an agent stage runs
again, and the spike observed only the first:

- **Re-entry through an edge** (a gate's `outcome=failed` repair edge): **observed**, below.
- **An engine retry** (`max_retries`, the case the task asked about): **not observed** by the
  spike, and **established from the fabro source** in the 2026-09-27 review.
  `fabro-core/src/executor.rs:356-363` (`execute_with_retry`) calls
  `lifecycle.before_attempt` inside `for attempt in 1..=policy.max_attempts`, and
  `fabro-workflow/src/lifecycle/hook.rs:57-85` (`HookLifecycle::before_attempt`) runs the
  `StageStart` hook there, with `hook_ctx.attempt` set. So `io-stage` runs once per
  attempt, and a `max_retries` retry gets a fresh visit exactly as a re-entry does.

spike6b (`01M3GRYM9YWJ84RSGJ1YP0KHYD`) routes a gate's `outcome=failed` back into `agent_a`.
The event log shows the agent re-entered with a new visit:

```
stage.prompt agent_a visit=1 -> stage.completed agent_a succeeded
( gate runs, fails, routes back into agent_a )
stage.prompt agent_a visit=2 -> stage.completed agent_a succeeded   <- re-entry
```

and the io-stage hook re-fired, writing a second `stage_start` line and a fresh random visit
(the spike.log and the last stage.json read back from the run sandbox):

```
2026-09-27T06:31:03Z node=agent_a ... stage_start
2026-09-27T06:31:09Z node=-       ... whoami
2026-09-27T06:31:18Z node=agent_a ... stage_start   <- retry: the hook fires again
2026-09-27T06:31:24Z node=-       ... whoami
# stage.json (final) holds the retry's visit, started 06:31:18Z
```

Consequences, established by observation (re-entry) and by the source (engine retry):

- A retried stage gets a **fresh random 128-bit visit** in a rewritten `stage.json`. The
  failed attempt's `served.json` is anchored to the old visit, so `submit` (C4) and the C7
  receipt check (`_io.visit == stage.json.visit`) both reject it — the stale-contract hazard
  does not materialize.
- `inputs` needs **no** `stage_retrying` reset of `served.json`; there is no shared-run state
  to reset. Task 03 stays as designed.

(An earlier spike6 attempt used a command node with `max_retries` to observe an engine retry
live; the command did not retry — it recorded a failure signature and routed onward through
its unconditional edge — so that run observed nothing about retries. The source reading
above is what settles the engine-retry case.)

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
`sandbox` MCP transport; no `shell` fallback is needed. Checks 5 and 7 pass. Check 6 passes:
`stage_start` fires again on a re-entry (observed) and on every engine retry attempt (fabro
source, see check 6), so no `stage_retrying` reset is required. One server-side fix (mount the service at the axum router fallback, not at `/mcp`)
is already committed in the crate.
