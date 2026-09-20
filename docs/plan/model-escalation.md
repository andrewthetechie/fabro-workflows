# Model escalation alert — applied 2026-09-20

## Outcome

A Discord message fires when a stage leaves the local coder box for a hosted model.
Applied in `e0fe696` and `cb601e5`. This file is the record of what shipped and why it
differs from the design it started as.

## Scope decision

"Falling back to another model" has three causes. Only one fires reliably, and the
operator chose that one: the escalation ladder.

| Cause | Signal | Fires today |
|---|---|---|
| Fabro LLM-error fallback chain | `prompt.failover` in `[run.model.fallbacks]` | No. Zero events in about 20 recent runs. A retry restarts the chain at position 0. |
| Escalation ladder | `stage_start` on a hosted tier | Yes |
| LiteLLM-internal routing | request served by a different model | Retired. Fabro no longer goes through LiteLLM for these models (ADR 0007). |

The other two are out of scope. An alert on either would stay silent. Recorded so the
decision is not revisited.

## What the stages actually are

The design named `rework_t[2-4]` and `rebase_t2`. Three of those four were right.

| Node | Graph | Class | Model | Alerts |
|---|---|---|---|---|
| `rework_t1` | `backlog` | `coder` | local box | No — same model, one round higher |
| `rework_t2` | `backlog` | `coder-t2` | glm-4.7 | Yes |
| `rework_t3` | `backlog` | `coder-t3` | kimi-for-coding | Yes |
| `rework_t4` | `backlog` | `coder-t4` | glm-5.3 | Yes |
| `resolve_merge` | `backlog` | `coder-t4` | glm-5.3 | Yes |
| `ci_fix_t1` | `_shared/review-merge` | `rebase` | local box | No |
| `ci_fix_t2` | `_shared/review-merge` | `rebase-t2` | glm-5.3 | Yes |
| `rebase_agent_t1` | `pr-review` | `rebase` | local box | No |
| `rebase_agent_t2` | `pr-review` | `rebase-t2` | glm-5.3 | Yes |

Three corrections to the design:

- **`rebase_t2` does not exist.** The node is `rebase_agent_t2`, and it lives in
  `pr-review`, not `backlog`.
- **`resolve_merge` and `ci_fix_t2` were missing.** Both are escalations to glm-5.3.
  `ci_fix_t2` is in the shared graph, so it reaches a run through `import=` in *both*
  packages and needs covering in both.
- **`issue-triage` has no ladder.** The design said all three packages have one. Its
  stylesheet is `high-reasoning` on `*`, `.improve` and `.triage`, with no tiered nodes
  at all, so it gets no hook.

`spec` and `extra_decompose` still do not alert. They run `kimi-k3` as their normal
class, not as a fallback.

## The matcher must allow the import prefix

**A node spliced in with `import=` keeps a prefixed id at run time**
(`review_merge.ci_fix_t2`), and the hook context carries that id verbatim. So
`^ci_fix_t2$` matches neither the prefixed id nor a bare one, and **a hook that matches
nothing is silent** — the runner logs no line for an event with zero matching hooks.

The form is `(^|[.])<id>$`. The `$` still keeps `report_blocked` from matching
`report_merged` and `rework_t2` from matching a hypothetical `rework_t20`, so anchoring
is not given up.

This was not a new-code problem. The `^report_merged$`, `^report_blocked$` and
`^mark_needs_human$` hooks added in `e7eedc9` had the same defect and had been silent
since. Verified on run `01M2XEP62DVGS9JJT9CZQ7HE2Q`, which checkpointed
`review_merge.report_blocked` and produced no `Running hooks` line. AGENTS.md previously
claimed the opposite, citing run `01M2MG7F8Q7DVSBQ52VHGDB9H2` — that run predates the
import and shows a bare `report_merged`, so the claim generalised from a case that could
not show the bug. All four matchers are fixed and the invariant is corrected.

## Hook configuration

One hook per package that has a ladder. `issue-triage` gets none.

```toml
# backlog
[[run.hooks]]
id = "discord-fallback"
event = "stage_start"
matcher = "(^|[.])(rework_t[2-4]|resolve_merge|ci_fix_t2)$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh fallback"
```

`pr-review` is identical but for `matcher = "(^|[.])(rebase_agent_t2|ci_fix_t2)$"`.

- `stage_start`, not `checkpoint_saved`: the escalation begins when the stage starts,
  which is the moment the local box is left.
- `blocking = false` — a Discord outage must not stall a run.
- `sandbox = false` — the script runs in the server container, where the webhook lives.
- Absolute container path: hook `script` is a shell command, not an `@` file import.

## Discord, not Slack

The design called for a Slack incoming webhook, a second secret and a second script.
Every other notification in this system already goes to the Discord webhook, so the
alert is a new `fallback` kind in `discord-notify.sh` instead. No new secret, no
`slack-fallback.sh`, no new deploy step.

The node-to-model map lives in the script, not in the matcher, because one matcher
covers six nodes with three different models. Imported ids are keyed on the last
segment (`review_merge.ci_fix_t2` → `ci_fix_t2`). An unrecognised node exits 0 without a
message rather than sending a vague one.

That map is also a second gate. Matchers are tested against `node_id`, `handler_type`,
`edge_to`, `edge_from` and `tool_name`, so an incidental match on an edge field is
possible; the script recomputes the model from `node_id` and stays silent on anything
it does not recognise, so a stray match produces no message rather than a wrong one.

The message reads:

```
🔼 fabro model fallback: escalated to glm-4.7 (rework_t2) on andrewthetechie/jelly-swipe #378
```

### Reading `node_id` out of the context

The pattern allows whitespace around the colon:

```sh
grep -o '"node_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4
```

The enrichment greps elsewhere in the script read the fabro HTTP API, whose JSON is
compact and proven in production. This one reads the `HookContext` piped to stdin, which
is a different producer that nothing here has observed. `"node_id": "x"` with a space
would yield an empty id, an empty id takes the `*)` branch, and the hook sends nothing —
the same silent shape as the unprefixed matcher above. `cut -f4` was already
space-tolerant; only the pattern needed widening. Verified against busybox `grep` inside
the container, which supports `[[:space:]]`.

## Deploy

Workflow changes deploy on the next fire; all automations resolve the workflow from
`main`. The script is ops-side and needs the manual copy:

```sh
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh fabro-fabro-1:/storage/scripts/discord-notify.sh \
  && docker exec fabro-fabro-1 chmod 755 /storage/scripts/discord-notify.sh \
  && docker exec fabro-fabro-1 chown fabro:fabro /storage/scripts/discord-notify.sh \
  && rm /tmp/discord-notify.sh'
```

No new secret. The alert reuses `/storage/secrets/discord_webhook_url`, and the script
exits 0 when that file is absent, so a missing URL disarms the alert rather than a run.

## Verification

Done 2026-09-20:

- Every matched node id exists, and every model matches the stylesheet class it carries.
- Coverage is complete per package and matches nothing it should not.
- `fabro validate` at the recorded baselines: Backlog 59/138 and PrReview 30/65 each at
  one expected warning, IssueTriage 14/32 clean. Routing `MISMATCHES: 0`.
  `ops/test-task-gates.sh` 137 checks.
- The deployed script matches the tracked copy, is `755 fabro:fabro`, and passes `sh -n`
  inside the container.
- The `node_id` pattern parses compact, spaced and import-prefixed ids, and stays silent
  on a non-escalation node.

**Outstanding:** no escalation has fired since the hook landed, so the path is
unexercised end to end. The first `rework_t2` or `ci_fix_t2` confirms it. Watch for one
message per escalating stage and none on the happy path.
