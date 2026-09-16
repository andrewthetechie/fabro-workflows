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
| `auto_merge` label | the `pr-review-<repo>` automation row | one repo | one `PATCH /automations/<id>` |
| `FABRO_AUTO_MERGE` | `~/fabro/.env` | all four | edit + `docker compose up -d` |

Both must say yes. Either says no, or says nothing at all, and the merge does not
happen.

## The per-repo label

The `pr-review-*` automations are already config-only — read by `fire-pr-review.sh`
for `environment_id` and `target`, never fired. This adds a third field to the same
row, which is the point: one place to look for per-repo configuration.

`fire-pr-review.sh` resolves it in the block that already reads the automation
(section 1 of the script), and sends it as an input:

```sh
auto_merge="$(jq -r '.[0].labels.auto_merge // \"true\"' \"$tmp/automatch.json\")"
case "$auto_merge" in true) am=1 ;; *) am=0 ;; esac
```

`// "true"` is the default-on decision, expressed in exactly one place. Everything
downstream treats anything but the explicit enabled value as disabled.

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

`FABRO_AUTO_MERGE` in `~/fabro/.env`, passed into the fabro container by
`ops/docker-compose.yaml`, and injected into the run sandbox by `pr-review`'s
`workflow.toml`:

```toml
[run.environment.env]
FABRO_AUTO_MERGE = "${FABRO_AUTO_MERGE}"
```

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
H=\"${FABRO_AUTO_MERGE:-1}\"
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
- An automation row with no `auto_merge` label resolves to enabled.
- `auto_merge: "false"`, `auto_merge: "0"`, `auto_merge: ""` and a malformed row all
  resolve to disabled.
- With `FABRO_AUTO_MERGE=0` in the environment, `/tmp/fabro/auto_merge` is `0`
  regardless of the input.
- With the variable unset, the host switch is enabled — a host that has never heard of
  this feature does not silently disable it, and the per-repo switch still governs.
- `python3.11 -c 'import tomllib; …'` parses `pr-review/workflow.toml`, and
  `[run.environment]` has **no** `id` key.
- `fabro validate` reports exactly two warnings on `PrReview`, both unbound inputs.
- Re-running `provision-server-state.sh` against a row with `auto_merge=false` reports
  drift and changes nothing.
