# Task 08 — Validate

**Depends on:** 01-07. **Blocks:** 09. **LLM level:** ordinary, but do not skip a step.

Nothing here touches a target repository. Everything here is cheaper than the thing it
prevents: pushing to `main` deploys, and the first scheduled fire is the first test.

## 1. Structure, in the container

There is no `fabro` binary on this Mac, and the host CLI resolves `@prompts`
differently from the server:

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/issue-triage/workflow.toml'
```

Expected: `IssueTriage (15 nodes, 35 edges)`, no errors. Record the exact figure —
`AGENTS.md` gains it as a baseline in task 10, and a later diff against it is how an
accidental node deletion gets caught.

Re-validate the other two while the copy is there. `Backlog (38 nodes, 86 edges)` clean
and `PrReview (28 nodes, 64 edges)` with exactly one deliberate warning
(`pr_number` unbound in `validate_input`) are the 2026-09-16 baselines. This change
should not move either; if it does, something was edited that was not meant to be.

## 2. TOML, separately

`fabro validate` does not parse `workflow.toml` strictly. On 3.11+ — macOS ships 3.9,
which has no `tomllib`:

```sh
python3.11 -c 'import tomllib; d=tomllib.load(open(".fabro/workflows/issue-triage/workflow.toml","rb")); print(len(d["run"]["hooks"]))'
```

Three. And `ops/settings.toml.example` parses.

## 3. Shell and jq, node by node

Extract each `script` to a file and run `sh -n`. Then extract every embedded `jq`
program and compile it separately with `jq -n 'def f: <program>; 1'` or equivalent —
**`sh -n` is blind inside `jq '...'`**, which is where the interesting mistakes are.

Specifically prove, with real inputs:

- `jq -s 'add | sort_by(.number) | .[0:1]'` over two empty arrays returns `[]`, so
  `acquire` quiet-exits instead of erroring.
- The marker filter keeps an issue whose newest comment is human and drops one whose
  newest comment carries `fabro:triage-`. Build both fixtures by hand.
- The label expansion `map("--add-label=" + .) | join(" ")` produces the exact argument
  string `gh issue edit` expects, and an empty `labels` array produces an empty string
  that expands to no argument rather than to `''`.
- The removal expansion emits only labels actually present on the issue. Removing a
  label the issue does not carry is a 404 from GitHub, and that would strand the claim.
- The title regex accepts `feat(api): add pagination`, rejects `agent: do a thing`,
  `feat!: drop v1`, `style: reformat` and a bare `add pagination`.

## 4. Preflight

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro preflight /tmp/check/workflows/issue-triage/workflow.toml'
```

This is what proves `high-reasoning` resolves through the catalog (task 02) and that
both `[run.environment.env]` values interpolate (task 07). A failure here is the one
that would otherwise appear as `Run config variable interpolation failed` at the first
scheduled fire, with no run created and nothing in any log but the fire's error.

## 5. The graph picture

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro graph /tmp/check/workflows/issue-triage/workflow.toml -o /tmp/wf.svg'
```

Eyeball three things: every command node's unconditional edge lands on `release`; the
two `needs_info` edges out of `triage_gate` are in the declared order; and
`human.default_choice="post_questions"` names a node the gate actually has an edge to.
Fabro validates none of the three.

## 6. The gate probe

If task 03's Part 0 probe has not been run yet, run it now. Wiring `stdin_source` to a
key that does not exist produces a `record_answer` that silently writes an empty file,
a re-triage with no new evidence, and a question batch posted to the issue as though
the human never answered — a failure that looks exactly like a human who ignored the
ping.

## Acceptance

Every command above run, with its output pasted into the task's evidence note in
`.scratch/`. A validation someone says they ran is not a validation.
