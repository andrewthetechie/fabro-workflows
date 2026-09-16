# Task 07 — Validate

**Depends on:** 04, 05, 06. **Blocks:** 08. **LLM level:** local is fine; escalate if
a validation error is not obvious.

Nothing reaches `main` until all of this passes. Pushing to `main` **is** the deploy:
every automation resolves `workflow_source` to `andrewthetechie/fabro-workflows@main`
at fire time, against four real repositories, with no staging branch and no rollback
other than another commit.

## 1 — Graph validation, in the container

There is no `fabro` binary on the Mac, and the host CLI resolves `@prompts`
differently from the server. Always validate with the container's fabro:

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/backlog/workflow.toml'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/pr-review/workflow.toml'
```

Expected against the 2026-09-15 baselines:

| Workflow | Baseline | After this stage |
|---|---|---|
| `Backlog` | 37 nodes, 85 edges, clean | **38 nodes, 86 edges, clean** |
| `PrReview` | 19 nodes, 40 edges, one warning | **unchanged — 19/40, same one warning** |

`PrReview` must be **byte-identical**. This stage does not modify it. If its node or
edge count moved, something was edited that should not have been.

Its one warning — `pr_number` unbound in `validate_input` — is deliberate and stays.
Binding `[run.inputs] pr_number` would silence it and let a run fired with no input
review PR #1 instead of failing at admission. The 422 probe in
`00-overview-and-contracts.md` is that guard working.

## 2 — TOML, separately

`fabro validate` does not parse `workflow.toml` strictly, and a bad table is a 422 at
automation fire with nothing reported until then. macOS ships Python 3.9, which has
no `tomllib`:

```sh
for w in backlog pr-review; do
  python3.11 -c "import tomllib,sys; d=tomllib.load(open('.fabro/workflows/$w/workflow.toml','rb')); print('$w OK')"
done
```

Then assert the two things task 05 can get wrong:

```sh
python3.11 - <<'EOF'
import tomllib
d = tomllib.load(open('.fabro/workflows/backlog/workflow.toml','rb'))
env = d['run']['environment']
assert 'id' not in env, "run.environment.id would pin all four automations to one environment"
assert env['env']['FABRO_API_TOKEN'] == '{{ secrets.FABRO_API_TOKEN }}'
assert len(d['run']['hooks']) == 4
print("backlog toml assertions OK")
EOF
```

## 3 — Shell and jq

`sh -n` is blind inside `jq '...'`, so compile embedded jq separately.

```sh
# extract trigger_review's script to a file first, then:
sh -n /tmp/trigger_review.sh
sh -n .fabro/workflows/backlog/scripts/discord-notify.sh
bash -n .fabro/workflows/backlog/scripts/fire-pr-review.sh
```

## 4 — Escaping audit on the changed DOT

Grep the two changed nodes specifically. These are mechanical and each one has cost a
live debugging round before:

| Check | Command | Expected |
|---|---|---|
| Only `\"` backslashes | `grep -n '[^\\]\\[^"]' workflow.fabro` | no hits in `open_pr` or `trigger_review` |
| No `#` outside a quoted string | read the two nodes | `//` is the comment |
| `output_schema="routing"` present | `grep -c 'output_schema="routing"' workflow.fabro` | one more than before |
| Hook matcher anchored | `grep 'matcher' workflow.toml` | `^trigger_review$`, with the `^` and `$` |

## 5 — Secret audit

The repository is public. Before pushing:

```sh
git diff --cached | grep -iE 'ghp_|gho_|bearer [A-Za-z0-9]|discord.com/api/webhooks' && echo LEAK
```

`FABRO_API_TOKEN` must appear only as a **name**, never a value, in
`.fabro/workflows/backlog/workflow.toml` and the docs.

## Acceptance

Every check above passes, `Backlog` reads 38/86 clean, and `PrReview` is unchanged at
19/40 with its single deliberate warning.
