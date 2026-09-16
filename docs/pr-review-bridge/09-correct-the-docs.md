# Task 09 — Correct `AGENTS.md` and `ops/README.md`

**Depends on:** 08. **Blocks:** 10. **LLM level:** local is fine.

Two documents in this repo currently describe things that are not true. One of them
was disproved by a live probe during this stage's design; the other is about to
become misleading. Both are load-bearing — they are what the next session reads
first.

## 1 — `AGENTS.md`: the pr-review fire recipe is wrong

Under "The server", `AGENTS.md` says:

```sh
curl -fsS -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"trigger":"manual","inputs":{"pr_number":N}}' \
  http://10.10.0.32:32276/api/v1/automations/pr-review-<repo>/runs
```

`POST /automations/{id}/runs` takes **no request body**. Fired this way it returns
`422 run_compile_invalid` and creates no run. Replace it with the helper:

```sh
~/bin/fabro-fire-pr-review.sh andrewthetechie/jelly-swipe 123
```

and a sentence saying why the obvious-looking curl does not work, so nobody
reconstructs it from the API surface. Keep the `backlog` fire example — that one is
correct, because `backlog` takes no inputs — but drop its body to match reality.

## 2 — `AGENTS.md`: a new deploy artefact

`fire-pr-review.sh` is tracked under `.fabro/workflows/backlog/scripts/`. The
trigger node reads it from a fresh clone of `main`, so **the workflow path needs no
deploy**; only the operator's `~/bin` copy does. Add it to the deploy block and to
the drift checks, which must each print nothing:

```sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-fire-pr-review.sh' \
  | diff - .fabro/workflows/backlog/scripts/fire-pr-review.sh
```

## 3 — `AGENTS.md`: the layout table

The `ops/` row says "No automation reads this tree." That is still true and **must
stay true** — it is why editing `ops/` is safe. Add a line to the
`.fabro/workflows/<name>/` row noting that `backlog/scripts/` is now read by a live
stage as well as by hooks, so a change there is a deploy.

Add one row for `docs/pr-review-bridge/`.

## 4 — `AGENTS.md`: deployment invariants

Add two rows to the invariants table, both of which pass `fabro validate` and fail at
runtime:

| Rule | What happens otherwise |
|---|---|
| `[run.environment.env]` in backlog's `workflow.toml` must never gain an `id` key | It pins all four backlog automations to one environment, and two of the four repos fail CI on the wrong image |
| `trigger_review` keeps `on_failure="succeed"` | Its single unconditional edge points at `exit`; without the attribute a failure would route there instead of to `human_rescue` |

## 5 — `ops/README.md`

| Section | Change |
|---|---|
| Contents table | `fire-pr-review.sh` is **not** in `ops/` — say where it is and why, so nobody moves it there for tidiness |
| Secrets | add `FABRO_API_TOKEN` → fabro vault, and record that fabro supports exactly one dev token, so the bridge's credential cannot be rotated independently of the server's |
| Install steps | new step: set the vault entry, between the existing vault step and the contract-scripts step |
| Automations table | mark the four `pr-review-*` rows **configuration only — read for `environment_id` and `target`, never fired**. Do not delete them; the bridge resolves per-repo config from them |

That last row is the one most likely to cause a future outage. Four automations that
are never fired look exactly like dead rows, and `provision-server-state.sh` will
happily recreate them without explaining why they exist.

## 6 — `docs/pr-review/00-overview-and-contracts.md`

One line reads:

> Eventually the `backlog` workflow will feed its PRs straight into this one. For now
> it is fired by hand.

Update it to point at `docs/pr-review-bridge/00-overview-and-contracts.md` and state
that `pr-review` itself was **not modified** to make that happen — its input contract,
graph and `workflow.toml` are unchanged. Someone reading that series to understand
`pr-review` should not have to discover the bridge by accident.

Leave operator decision #2 ("input is `pr_number` only") exactly as it is. The bridge
honours it.

## 7 — this series: already corrected, verify it stayed corrected

Three corrections were folded back into `docs/pr-review-bridge/` on 2026-09-15, as
soon as the live work disproved the text. They are listed here so this task *checks*
them rather than rediscovering them:

| Document | Correction |
|---|---|
| `00-overview-and-contracts.md` | The architecture list and the API contract now carry the **third call**, `POST /runs/{id}/start`. `POST /runs` creates a `submitted` run that never executes and reports nothing. |
| `00-overview-and-contracts.md` | New risk row: a lost `start` response leaves no marker, so a rescue-path revisit can fire a second review on one PR. |
| `02-fire-pr-review-script.md` | Step list gained "5. Start the run"; `queue_position` is read off the start response; the env table gained `ISSUE_NUMBER` and `CHECK_PR`. |
| `03-prove-the-api-sequence.md` | The clone-depth check is `GET /runs/<id>/settings` → `run.clone.depth == 0`. The original `git rev-parse --is-shallow-repository` check does not discriminate, because `claim` runs `git fetch --unshallow`. |

Nothing to write here unless a later change reintroduces the two-call sequence.

## Acceptance

- No document in the repository still shows a body on a `POST /automations/{id}/runs` call.
- No document anywhere describes the fire sequence as two calls:
  `grep -rn 'POST /runs' docs/ AGENTS.md ops/` shows `/runs/{id}/start` alongside
  every `POST /runs` that describes the bridge.
- `ops/README.md` marks the `pr-review-*` automations config-only.
- The `ops/` "no automation reads this tree" guarantee is intact and still accurate.
- Every drift check in `AGENTS.md` prints nothing when run against the live host.
