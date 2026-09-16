# Task 03 — Prove the API sequence against a real PR

**Depends on:** 01, 02. **Blocks:** 04. **LLM level:** smarter model recommended —
live judgment about whether the created run is genuinely correct.

Run task 02's script by hand until it reliably produces a `pr-review` run that is
**indistinguishable from a correct hand-fired one**. No workflow is modified in this
task.

## The one property that must be proven

A run created through `POST /runs` with a `workflow_version_id` must pick up
`pr-review`'s **own** `workflow.toml` — not defaults.

The source says it does: `fabro-server/src/run_intent.rs` resolves the settings layer
from `version.config_path()` and `version.files().get(&config_local)`. The whole
design rests on that, and the `house` node looked correct too until its source was
read. Prove it.

**The decisive check is clone depth.** `pr-review` sets `[run.clone] depth = 0`; the
default is 100. In a shallow clone `git diff origin/<base>...HEAD` dies with
`fatal: no merge base`, which is exactly what `prep_review` does.

Check it on the **resolved run settings**, which is race-free:

```sh
GET /api/v1/runs/<run_id>/settings     # run.clone.depth must be 0, not 100
```

> **Correction, 2026-09-15.** This task originally prescribed the sandbox check
> below. It does not discriminate: `pr-review`'s own `claim` stage runs
> `git fetch --unshallow`, so `is-shallow=false` holds under *either* depth value,
> and by the time the sandbox is reachable `claim` has usually already run.
>
> ```sh
> git rev-parse --is-shallow-repository     # false even when depth = 100
> git log --oneline | wc -l                 # >100 even when depth = 100
> ```
>
> If a sandbox-side measurement is wanted anyway, the one clean observation point is
> the window between clone completion and `claim`: poll the commit count from the
> moment `start` is called and watch what the clone lands at. `depth = 0` lands at
> full history in one step; `depth = 100` lands at exactly 100 first. Task 03 did
> this against a throwaway run with `pr_number = 999999` so `claim` fails harmlessly.

`fabro sandbox ssh <run>` gets you in; `--preserve-sandbox` is not available here
because the run is created through the API, so check while the run is live or use
`fabro dump`.

Secondary confirmations, all from `pr-review`'s `workflow.toml`:

| Setting | How to see it |
|---|---|
| `[run.run_branch] push = false` | no new `fabro/run/<id>` appears in the target repo |
| `[run.meta_branch] push = false` | no new `fabro/meta/<id>` either |
| `[run.integrations.github.permissions] issues = "write"` | `claim`'s `gh label create` calls succeed |
| `[run.model.fallbacks]` | `models` on the run summary shows the `pr-review` map, not backlog's |

## Procedure

1. Pick a **real open PR** on `jelly-swipe` that you are willing to have reviewed,
   rebased and force-pushed. `pr-review` writes: it pushes with
   `--force-with-lease`, adds a label, and comments.
2. `DRY_RUN=1` first. Read both payloads.
3. `DRY_RUN=0 .fabro/workflows/backlog/scripts/fire-pr-review.sh andrewthetechie/jelly-swipe <pr>`.
4. Confirm the run reached `runnable`, not `submitted` — `POST /runs` alone does not
   start anything, and a `submitted` run is inert and silent.
5. Watch it: `fabro events <run> -p` shows the selected edge and why.
6. Run the clone-depth check above **before** the run gets far.

## Checks on the created run

```sh
ssh andrew@10.10.0.32 "docker exec fabro-fabro-1 sh -lc '
  T=\$(cat /storage/server.dev-token)
  wget -q -O- --header=\"Authorization: Bearer \$T\" \
    http://127.0.0.1:32276/api/v1/runs/<run_id>'"
```

| Field | Expected |
|---|---|
| `workflow.graph_name` | `PrReview` |
| `repository.name` | the repo passed in |
| `sandbox.plan.image` | the image for that repo's environment, **not** `buildpack-deps:noble` |
| `labels` | the labels the script sent |
| `automation` | `null` — this is a direct run, by design |
| `lifecycle.queue_position` | record it, whatever it is |

With `PARENT_RUN_ID` set to any live run, `parent_id` must come back set and the
parent's `children_count` must increment. Test that explicitly here — task 04 derives
the value from a branch name, and this is the last point where a bad `parent_id` is
cheap to diagnose.

## Failure modes to exercise deliberately

Confirm each produces a clean non-zero exit with a usable message, because task 04
turns that message into a Discord alert:

- A PR number that does not exist.
- A repo with no `pr-review` automation.
- `FABRO_API_TOKEN` unset or wrong (expect 401).
- A deliberately corrupted `files` map (expect 422 naming the offending key).

## Acceptance

- Two consecutive runs on the same package content reuse one `workflow_version_id`.
- The created run reaches `runnable`. A run left in `submitted` is a failed fire, not
  a slow one.
- The created run is **not shallow** — `run.clone.depth` resolves to `0` on
  `GET /runs/<id>/settings`.
- The review completes and posts a comment, exactly as a hand-fired review does.
- Every failure mode above exits non-zero with one readable line on stderr.

Do not start task 04 until the clone-depth check passes. If it fails, the design's
core assumption is wrong and the bridge has to carry `pr-review`'s run config itself
— come back to `00-overview-and-contracts.md` before writing any DOT.
