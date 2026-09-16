# Task 06 — Opt-in plumbing and both kill switches

**Depends on:** nothing. **Blocks:** 07, 09. **LLM level:** local is fine.

Auto-merge is on by default on all four repos (operator decision 10), so what this
task builds is not an opt-in — it is two ways to turn it **off**, both failing closed.

## Why two

AGENTS.md is blunt: no staging branch, no review gate, "no rollback other than another
commit". That escape does not exist for a merge that already deployed. `lawncare-saas`
runs `deploy-main.yml` and `deploy-homelab.yml` on push to `main`.

So there must be a way to stop this that is faster than editing a graph, and a way to
stop **all of it at once** without four separate calls.

| Switch | Lives in | Scope | Cost to flip |
|---|---|---|---|
| `auto_merge` token in `description` | the `pr-review-<repo>` automation row | one repo | `fabro-auto-merge-switch.sh <repo> off` |
| `FABRO_AUTO_MERGE` server variable | fabro's variable store | all four | `fabro-auto-merge-switch.sh host off` |

**Neither switch is where this task first specified it.** The row has no `labels` field
(`POST` returns 422 `unknown field 'labels'`) and no `PATCH` (405), so the per-repo
switch is a token in `description`. And `[run.environment.env]` does not interpolate
`${X}` — it passes the literal through — so the host switch is a server variable read as
`{{ vars.FABRO_AUTO_MERGE }}`. Both are recorded as findings 7 and 8 in
`00-overview-and-contracts.md`.

Both must say yes. Either says no, or says nothing at all, and the merge does not
happen.

## The per-repo label

The `pr-review-*` automations are already config-only — read by `fire-pr-review.sh`
for `environment_id` and `target`, never fired. This adds a third field to the same
row, which is the point: one place to look for per-repo configuration.

`fire-pr-review.sh` resolves it in the block that already reads the automation
(section 1 of the script), and sends it as an input:

```sh
desc="$(jq -r '.[0].description // \"\"' \"$tmp/automatch.json\")"
am=1
case "$desc" in
  *auto_merge*)
    tok="$(printf '%s' \"$desc\" | grep -o 'auto_merge=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)"
    case "$tok" in true) am=1 ;; *) am=0 ;; esac ;;
esac
```

Three outcomes, not two: **no mention of the token at all is on** — decision 10, nobody
has touched this switch — `auto_merge=true` is on, and anything else that mentions it
(`false`, `0`, empty, `TRUE`, a bare `auto_merge`) is off. A token that does not parse is
a switch nobody can trust, so it fails closed instead of falling back to the default.
`tail -1` takes the last token so a hand edit cannot resurrect an earlier value.

`ops/provision-server-state.sh` carries the same parser. Keep the two in step: a
re-provision that disagrees with the fire path is the one way this switch is wrong at the
worst moment.

Then in the `RunIntent`, alongside `pr_number`:

```
args: { inputs: { pr_number: $pr, auto_merge: $am }, labels: {...} }
```

`auto_merge` is a JSON **number** (`0` or `1`), not a string and not a boolean, for
the same reason `pr_number` is a number: `validate_input` interpolates it into a POSIX
`case` guard, and a number is the shape that cannot carry shell syntax.

Add it to the `DRY_RUN=1` report so a dry fire shows which way the switch is set. A
kill switch you cannot observe without firing is not a kill switch.

## The host switch

A **server variable**, injected into the run sandbox by `pr-review`'s `workflow.toml`:

```toml
[run.environment.env]
FABRO_AUTO_MERGE = "{{ vars.FABRO_AUTO_MERGE }}"
```

Not `~/fabro/.env`, and not `${FABRO_AUTO_MERGE}`: that spelling is passed through
literally and would pin the switch to off forever (finding 8). Not `{{ env.X }}` either
— fabro rejects it outright and points at `vars`.

`ops/provision-server-state.sh` creates the variable at `1`, **create-if-absent and
never overwrite**, because `POST /variables` upserts and a re-provision must not re-arm a
switch an operator killed. A differing value is reported as drift.

The variable must exist: an unset one fails the RunIntent at compile time, so no
pr-review run is created. `off` writes `0`; it never deletes.

**Do not add `[run.environment] id = "..."`.** AGENTS.md records this as the footgun
that pins every automation to one environment and breaks `fabro validate`, which
resolves a non-default id against the CLI's own local catalog. `[run.environment.*]`
is a sparse override on the environment the automation already selected; keep it that
way. The rule is written about `backlog`'s toml and applies identically here —
`pr-review` selects `python`, `python-node`, `ts` or `rust-node` per repo.

## `validate_input` resolves both

One place, early, fail closed, writing a single file the rest of the graph reads:

```sh
AM='{{ inputs.auto_merge }}'
case \"$AM\" in 1) ;; *) AM=0 ;; esac
H=\"${FABRO_AUTO_MERGE:-0}\"
case \"$H\" in 1) ;; *) H=0 ;; esac
if [ \"$AM\" = 1 ] && [ \"$H\" = 1 ]; then echo 1 > /tmp/fabro/auto_merge; else echo 0 > /tmp/fabro/auto_merge; fi
```

The input is interpolated exactly once, here, inside single quotes, so a non-numeric
value reaches the `case` guard as data rather than shell syntax. That is the same rule
`pr_number` already follows two lines above, and for the same reason.

### The validation warning

`pr_number` is deliberately left unbound in `[run.inputs]`, and `fabro validate`
deliberately warns about it. That warning is a guard: binding it would let a run fired
with no input review PR #1 instead of failing at admission.

`auto_merge` gets the same treatment — **no `[run.inputs]` binding**. The baseline
therefore becomes **two** warnings, `pr_number` and `auto_merge`, both intended.
Task 09 asserts exactly two and task 11 records the new baseline.

There is a second reason beyond consistency: it is unverified whether a
`[run.inputs]` table in `workflow.toml` merges with `args.inputs` sent over the API or
replaces it. TOML `[run.inputs]` replaces the whole map where `-I` merges per key. If
it replaces, a `[run.inputs] auto_merge = 0` default would wipe `pr_number` and break
every run. Not binding it avoids the question entirely. Do not bind either key to
silence a warning.

## `provision-server-state.sh`

`provision_automation` gains an `auto_merge` argument, defaulted to `true`, written
into the row's labels for `pr-review` rows only. `backlog` rows do not get it — nothing
reads it there.

The script's existing discipline holds: **idempotent, and an existing row that does
not match is reported as drift rather than silently rewritten.** A row whose
`auto_merge` was flipped off by hand during an incident must not be flipped back on by
a re-provision. That is precisely the scenario this script's no-silent-rewrite rule
was written for, and this is the first field where getting it wrong merges code.

## Acceptance

- `fire-pr-review.sh` with `DRY_RUN=1` prints the resolved `auto_merge` and includes
  it in the `RunIntent` as a number.
- An automation row whose `description` never mentions `auto_merge` resolves to enabled.
- `auto_merge=false`, `auto_merge=0`, `auto_merge=` and a bare `auto_merge` all resolve
  to disabled.
- With the `FABRO_AUTO_MERGE` variable at `0`, `/tmp/fabro/auto_merge` is `0` regardless
  of the input.
- A run whose sandbox receives the literal `${FABRO_AUTO_MERGE}` resolves to **disabled**
  — `:-0` means a broken injection never merges code.
- `provision_variable` on a host where the variable is `0` reports drift and changes
  nothing.
- `fabro-auto-merge-switch.sh host off` then `host on` round-trips, with `DRY_RUN=1`
  printing the current value first.
- `python3.11 -c 'import tomllib; …'` parses `pr-review/workflow.toml`, and
  `[run.environment]` has **no** `id` key.
- `fabro validate` reports exactly two warnings on `PrReview`, both unbound inputs.
- Re-running `provision-server-state.sh` against a row with `auto_merge=false` reports
  drift and changes nothing.
