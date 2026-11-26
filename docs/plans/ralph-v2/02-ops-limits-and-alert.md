# Task 02: wall-clock limits, the box-degraded alert, the settings example

Read `00-overview-and-contracts.md` first (E17, A5). Read `ops/fabro-sandbox-sweep.sh`'s header,
`ops/fabro-monitor.sh` (C4, C10 and `fire`), and `GET /api/background/measure` in
`ops/scheduler/src/fabro_scheduler/app.py`.

## Steps

1. **`ops/fabro-sandbox-sweep.sh`**: `MAX_RUNNING_HOURS` 36 -> **48**, `PRESSURE_RUNNING_HOURS` 24 -> **36**,
   the defaults and every number in the header comment that states them. A v2 run adds about 3 h to v1's
   average, and v1's longest is 17.2 h (p99 14.5 h); past the limit the sandbox is **removed**. Update the
   tests in `test_ops_scripts.py` that pin 36 or 24, and `ops/README.md`.
2. **`ops/fabro-monitor.sh` C4**: add `RALPH_V2_STUCK_HOURS="${RALPH_V2_STUCK_HOURS:-24}"`, documented
   next to `ARCH_STUCK_HOURS`, and `[ "$slug" = ralph-v2 ] && lim_s=$(( RALPH_V2_STUCK_HOURS * 3600 ))`
   after the `arch-review` line. `ralph` keeps `STUCK_HOURS`.
3. **`GET /api/box-health?hours=24`** (1-168, else 400), read-only, in `app.py`, computed by
   `measure.box_health(history, stages, failovers, *, hours, now)`:
   - runs: `run_history` rows with `target_kind = 'issue'` and `finished_at` in the window that have a
     `run_summary` row with status `ok`;
   - `visits`: the sum of `run_stage_stats.visits` over their rows with `box_ms > 0`;
   - `failovers`: the sum of `run_failovers.count` over their rows whose `from_model` starts with `box-`;
   - `failover_rate`: `failovers / visits`, or null when `visits` is 0;
   - `terra`: the sum of `count` whose `to_model` is `openai/gpt-6.1-terra`;
   - `reviews`, `reviews_missed`, `miss_rate`: null in this task. Task 11 fills them.
4. **C11 `box-degraded`** in `fabro-monitor.sh`, 🟠, 24 h. It reads `SCHEDULER_URL/api/box-health` with
   the 5 s timeout C10 uses, is skipped when C7 fires, and is unevaluable (stamp untouched) on an HTTP
   error or unparseable body. It fires when `visits >= 20` and `failover_rate > 0.05`, or
   `reviews >= 20` and `miss_rate > 0.02`, or `terra > 0`. The message names each condition that held,
   with its numbers. Null counts as not held. Add it to the header list, the ordering comment and
   `ops/README.md`'s table.
5. **`ops/settings.toml.example`**: add the live overlay's row, between `[llm.providers.orfallback]` and
   its `qwen3.8-27b` row:

   ```toml
   [llm.providers.orfallback.models."hy4-preview"]
   display_name = "Tencent HY4 preview (OpenRouter)"
   api_model = "tencent/hy4-preview"
   limits = { context_tokens = 1048576, max_output_tokens = 64000 }
   capabilities = { text = true, tools = true, reasoning = true }
   ```

   (Compared with the live overlay on 2026-11-24: it is the only `[llm...]` heading the example lacks.)
   `gpt-6.1-terra` needs a row of its own, after `gpt-6-luna`'s, in the same shape (the built-in catalog
   has only `gpt-5.6-terra`, which this OpenAI project cannot use; corrected at the pre-push review,
   2026-11-26, when the live overlay got the row).
6. **No retry change** (A5). Add one sentence to `docs/agents/invariants-host.md`: fabro 0.362 exposes no
   retry count or backoff per provider or model, so a box fault lasting seconds is retried by fabro and
   one lasting minutes fails over; C11 reports it.

## Tests

`ops/scheduler/tests`: `box_health` on an empty store, on a window with box and hosted failovers, on a
run with no summary (left out), and the endpoint's 400s. `test_ops_scripts.py`: C11 with `DRY_RUN=1` for
each condition alone, none, below 20 visits, HTTP 500, and C7 firing; C4 at 23 h and 25 h for `ralph-v2`.

## Done when

- `uv run pytest` in `ops/scheduler` and `make check` pass.
- Commit: `feat(ops): C11 box-degraded, 48 h sandbox limit, ralph-v2 stuck limit (ralph-v2 02)`.
