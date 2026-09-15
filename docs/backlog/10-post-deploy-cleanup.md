# Task 10 — Post-deploy cleanup and open operator decisions

**Depends on:** task 09 (a passing E2E). **Do before:** task 11.
**LLM level:** local is fine. Two items end in a question for the operator rather than
a change — write the question up, do not decide it yourself.

## Goal

Clear the loose ends the deployment review found that are not part of the workflow
redesign itself. Five items; three are changes, two are operator decisions.

## Item 1 — Duplicate sandbox images (change)

The Docker host carries both `fabro-rustnode` / `fabro-pynode` and `-`-suffixed
variants of the same images. Only one set is actually registered as an environment.

**Find out which before deleting anything:**

```sh
TOK=<dev token from FABRO-DEPLOYMENT-LOG.md>
curl -fsS -H "Authorization: Bearer $TOK" http://10.10.0.32:32276/api/v1/environments | python3 -m json.tool
ssh andrew@10.10.0.32 'docker images | grep -i fabro'
```

Cross-reference the `image` field of each registered environment against the image list.
Delete only images that **no** registered environment names:

```sh
ssh andrew@10.10.0.32 'docker rmi <unreferenced image>:<tag>'
```

If `docker rmi` refuses because a container is using it, that image is not unreferenced
— leave it and say so.

## Item 2 — `rust-node` environment resources (change or document)

The `rust-node` environment is provisioned at 2 CPU / 4 GB, while the `validate` stage
gives `./.fabro/ci.sh` a 20-minute timeout. A Rust workspace build on 2 CPUs can exceed
20 minutes cold, which surfaces as a validation timeout that looks like a code failure
and sends the run into a rework round it cannot win.

Check the current allocation and raise it if the host has headroom:

```sh
ssh andrew@10.10.0.32 'nproc; free -g'
curl -fsS -H "Authorization: Bearer $TOK" http://10.10.0.32:32276/api/v1/environments | python3 -m json.tool
```

Either raise `rust-node` to something that can finish a cold Rust build inside 20
minutes (4 CPU / 8 GB is the obvious next step if the host allows it), **or** leave it
and record in task 11 that Rust repositories are expected to hit validation timeouts on
cold builds. Do not raise the 20-minute stage timeout as the fix — a 20-minute
validation stage is already long, and extending it hides the resource problem.

## Item 3 — `run_title_generation` warning (RESOLVED: genuinely cosmetic, root cause known)

**Status: investigated to root cause 2026-09-14. Cosmetic. No further action possible
through configuration.**

Every run logs `Run title generation failed … error=model litellm/<m> does not support
structured output`, ~127 times per 24h. The chain, established empirically:

1. `fabro-server/src/server/handler/runs.rs:957` picks the title model via
   `title_catalog.small_default_for(...)`. `small_default` is a documented per-model
   boolean — *"Preferred for small utility calls such as generated run titles."*
2. The capability gate is `capabilities.response_format = { json_object, json_schema }`
   (see the generated options reference). No deployed model declared it, so fabro
   refused before calling.
3. **The real blocker:** `fabro-server/src/run_title_generation.rs:49` hardcodes
   `.max_output_tokens(64)`. Every model in this LiteLLM stack is a reasoning model
   whose preamble consumes the whole 64-token budget before any content is emitted.

Measured at exactly 64 output tokens with a `json_schema` response_format:

| model | result |
|---|---|
| `coders` | empty, `finish=length` |
| `long-context` | empty, `finish=length` |
| `glm-5.3` | empty, `finish=length` |
| `glm-4.7` | empty, `finish=length` |
| `kimi-k3` | no `choices` returned |
| `kimi-for-coding` | no `choices` returned |

The same models succeed at 512 tokens (`long-context` returns
`{"title":"Fix Genre Picker Cancel Button"}`), so the models are capable — the budget
is not. 64 is hardcoded, not configurable.

**Conclusion:** no model in this deployment can satisfy run-title generation. The run
title falls back to the truncated goal text, which is serviceable. Document it as a
known cosmetic warning and stop. Revisit only if fabro makes the title budget
configurable or a non-reasoning model is added to the stack.

Two accurate declarations were added to `long-context` while establishing this, and
left in place because they are correct and correctly aimed:

```toml
capabilities = { text = true, tools = true, reasoning = true, response_format = { json_object = true, json_schema = true } }
small_default = true
```

They move the title attempt from `coders` to the local `long-context` and get it past
the capability gate — the logged error advances from "does not support structured
output" to "the model did not return a JSON document" — but the 64-token ceiling still
defeats it. Harmless; remove them if you prefer a minimal settings file.

## Item 4 — Cron interval (DECIDED: schedules stay disabled)

**Operator decision, 2026-09-14: leave all four scheduled triggers disabled while
development and testing continue.** This is not a defect and needs no write-up of
options; record it in task 11 as a deliberate state.

Current state, for the record:

- All four automations keep `expression = "*/15 * * * *"` but `enabled: false` on the
  schedule trigger. `backlog-jelly-swipe` additionally has an `api`/`manual` trigger
  that is enabled — that is how the task 09 E2E and any test fire are triggered.
- Last scheduled batch: `2026-09-14T00:15:00Z`. Everything after is a manual fire.
- They were most likely paused during task 09 step 1 and not un-paused; the operator
  has since adopted that state deliberately.

Consequences while disabled, worth stating in the log:

- No empty-run waste and no new leaked branches — but task 12's backfill is still
  needed for the ~303 branches already there, and the sweeper should still be
  installed so it is running before the schedules come back.
- Nothing is working the backlog. Any issue labelled `agent` sits untouched until a
  manual fire or until the schedules are re-enabled.

**Re-enabling** is a per-trigger flip on each of the four automations. Do task 12 first
so the sweeper is in place before the branch-creation rate resumes.

## Item 5 — Relocate the secret-bearing deployment docs (change)

`FABRO-DEPLOYMENT-LOG.md`, `FABRO-DEPLOYMENT-PLAN.md`, `FABRO-DEPLOYMENT-PROMPT.md`,
`FABRO-QUICKSTART-GUIDE.md`, and `FABRO-vs-SANDCASTLE-REPORT.md` sit untracked in the
working tree of
`/Users/andrew/Documents/code/software-factory/context/fabro`, which is a checkout of a
**public** repository. At least two of them contain live credentials: the fabro dev
token, the LiteLLM admin key, and the Discord webhook URL.

They are untracked, so they are not published — but they are one `git add -A` away from
being published, and this series' task 09 does a `git add -A` in a different directory.
That is too close.

Move them somewhere that is not a repository working tree:

```sh
mkdir -p ~/.fabro-deploy/docs
cd /Users/andrew/Documents/code/software-factory/context/fabro
mv FABRO-DEPLOYMENT-LOG.md FABRO-DEPLOYMENT-PLAN.md FABRO-DEPLOYMENT-PROMPT.md \
   FABRO-QUICKSTART-GUIDE.md FABRO-vs-SANDCASTLE-REPORT.md ~/.fabro-deploy/docs/
chmod 600 ~/.fabro-deploy/docs/*.md
git status --short          # confirm they are gone from the working tree
```

**Do this last**, after tasks 09 and 11 — task 09 reads the dev token from
`FABRO-DEPLOYMENT-LOG.md` and task 11 appends to it. Update task 11's path reference
when you move it.

The operator has said they are relaxed about the keys themselves (LAN-scoped, rotatable
at will), so this is hygiene, not an incident. Do not rotate anything; key rotation is
explicitly out of scope for this whole series.

## Done when

- Unreferenced fabro images are gone, or you have stated which ones are referenced and
  why nothing was deleted.
- `rust-node` resources are either raised or explicitly documented as a known limit.
- The title-generation warning is root-caused and documented as cosmetic (item 3).
- Task 11 records that the schedules are deliberately disabled (item 4).
- The five `FABRO-*.md` files live in `~/.fabro-deploy/docs/` at mode 600 and
  `git status` in the fabro checkout is clean of them.

## Pitfalls

- Do not `docker system prune` on the host. It will take out sandbox containers from
  runs that are currently executing, and the fabro server has no way to recover them.
  Delete images by name, one at a time.
- Do not delete an image just because its name looks like a duplicate. Check
  `/api/v1/environments` first — the registered name and the image tag do not
  necessarily match.
- Item 4 is a decision, not a task. Present it; do not change the cron.
- When you move the `FABRO-*.md` files, do not leave a copy behind "just in case". Two
  copies of a file with a live token is worse than one.
