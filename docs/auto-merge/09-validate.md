# Task 09 — Validate

**Depends on:** 01-08. **Blocks:** 10. **LLM level:** local is fine; escalate if a
validation error is not obvious.

Nothing reaches `main` until all of this passes. Pushing to `main` **is** the deploy:
every automation resolves `workflow_source` to `andrewthetechie/fabro-workflows@main`
at fire time, against four real repositories, with no staging branch and no rollback
other than another commit — and for this stage, not even that, because a merge that
deployed cannot be un-merged by a later commit.

## 1 — Graph validation, in the container

There is no `fabro` binary on the Mac, and the host CLI resolves `@prompts`
differently from the server.

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/backlog/workflow.toml'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/pr-review/workflow.toml'
```

| Workflow | Before this stage | After |
|---|---|---|
| `Backlog` | 38 nodes, 86 edges, clean | **38 nodes, 86 edges, clean — unchanged** |
| `PrReview` | 19 nodes, 40 edges, one warning | **28 nodes, 64 edges, one warning** |

`Backlog` must be structurally identical. Task 02 rewrites scripts inside two existing
nodes and adds nothing. **If its node or edge count moved, something was edited that
should not have been.**

`PrReview` still reports **one** warning, not two. Both `pr_number` and `auto_merge` are
unbound and both are deliberate, but fabro emits one undefined-input diagnostic per node
*attribute*, and both references live in `validate_input`'s `script`. Confirm the second
one exists by validating a scratch copy with `pr_number` literalised — it then warns
about `auto_merge`. Neither may be silenced with a `[run.inputs]` binding; see task 06.

Exactly one: a second warning on the real tree means something else regressed.

**Note:** AGENTS.md currently records a 37/85 baseline for `Backlog` and 19/40 for
`PrReview`. The 37/85 is stale — it predates the pr-review bridge, which added
`trigger_review`. The current tree is 38/86. Task 11 corrects it; do not "fix" the
graph to match the stale number.

## 2 — TOML, separately

`fabro validate` does not parse `workflow.toml` strictly, and a bad table is a 422 at
automation fire with nothing reported until then. macOS ships Python 3.9, which has no
`tomllib`.

```sh
for w in backlog pr-review; do
  python3.11 -c "import tomllib; d=tomllib.load(open('.fabro/workflows/$w/workflow.toml','rb')); print('$w OK')"
done
```

Then assert the three things tasks 06 and 08 can get wrong:

```sh
python3.11 - <<'PY'
import tomllib
d = tomllib.load(open('.fabro/workflows/pr-review/workflow.toml','rb'))
env = d['run']['environment']
assert 'id' not in env, "[run.environment] must not gain an id key"
assert env['env']['FABRO_AUTO_MERGE'] == '{{ vars.FABRO_AUTO_MERGE }}', \
    "shell-style ${X} is not interpolated here; it would pin the switch off forever"
assert 'inputs' not in d['run'], "pr_number and auto_merge stay unbound"
hooks = d['run']['hooks']
assert [h for h in hooks if h['id'] == 'discord-merged' and h['matcher'] == '^report_merged$']
assert all(h['blocking'] is False for h in hooks)
print('pr-review toml OK')
PY
```

The `id` assertion is the important one. AGENTS.md records it for `backlog`; it applies
identically here, and it fails at *fire* time, not at validate time.

## 3 — Shell and jq, which validation does not check

`fabro validate` parses DOT. It does not run `sh -n`, and `sh -n` is itself blind
inside `jq '...'`.

```sh
# every inline script, extracted and syntax-checked
# every embedded jq program, compiled standalone with jq -n
```

Check specifically:

- The nine new `pr-review` scripts and the two rewritten `backlog` ones.
- `render.jq` with the `ci_fix_result` slurp added.
- The wrapper from task 07.
- `discord-notify.sh`.

## 4 — The backslash rule

`\"` is the only backslash a `.fabro` file may contain. This stage is full of regex and
path handling, which is exactly where `\(`, `\/` and `\n` want to appear:

```sh
grep -n '\\[^"]' .fabro/workflows/*/workflow.fabro
```

Must print nothing. If it does not, the likely culprits are a `sed` range with `\/`
(use the `awk` extraction), a literal paren in an ERE (use `[(]`), or a `"\n"` in jq
(use `NL`).

## 5 — `output_schema="routing"`

Every command node that prints `context_updates` needs it. Without it fabro never scans
the node's stdout, routing goes inert, the run walks its unconditional edges, and **no
error appears anywhere**. This has cost one live debugging round already.

Assert that every node whose script contains `context_updates` also carries the
attribute, and that every new command node has exactly one unconditional edge pointing
at `mark_needs_human`.

Assert too that **no boolean context value is emitted as a quoted string**. Every
pre-existing gate writes `"merge_ok":false`; a `"merge_eligible":"true"` that no
condition matches routes down the unconditional edge with nothing reported anywhere,
which is the same silent-routing failure as a missing `output_schema`:

```sh
grep -nE '(merge_eligible|checks_ok|checks_blocked|ci_fix_ok|remerge_ok)..:..(true|false)' \
  .fabro/workflows/pr-review/workflow.fabro
```

Must print nothing.

## 6 — The derivation, tested offline

Task 02's subject derivation is deterministic, so it can be tested without fabro at
all. Extract it to a standalone script and run the acceptance table from task 02
through it, plus:

- Every open `agent-authored` PR title across the four repos, asserting each output
  matches the Conventional Commits regex.
- The real titles that currently fail: `agent: [Chore] Remove the unreferenced 1 MB
  frontend/public/favicon.png (#377)` must derive to a passing subject.

Round-trip the marker block: build a `pr_body.md`, extract with the task 03 `awk`
one-liner, and diff against `commit_body.md`. Byte-identical.

## 7 — The eligibility predicate, tested offline

The highest-consequence code in the stage. On three of four repos there is no branch
protection, so `merge_gate` and `watch_checks` are the *only* things between a red PR
and `main`.

Extract both to standalone scripts and drive them with fixtures:

| Fixture | Expected |
|---|---|
| `risk: 3`, `not_fixed: []`, no error findings | eligible |
| `risk: 4` | blocked, reason names the risk |
| `risk` absent | blocked (and task 01 means this file should never have got this far) |
| `not_fixed` with one entry | blocked |
| `own_findings` with `severity: error` not in `fixes_applied` | blocked |
| the same finding present in `fixes_applied` | eligible |
| `fix_outcome: needs_human` | blocked |
| `gh` call fails / returns nothing | blocked |
| checks JSON `[]` | blocked |
| all `bucket: pass` | green |
| one `bucket: fail` | red |
| one `bucket: pending` | red |
| one `bucket: cancel` | red |
| mixture of `pass` and `skipping` | green |
| one `bucket: pending`, then all `pass` | polls, then green — **not** a flat sleep |
| checks JSON `[]` for six minutes | blocked, reason names the five-minute grace, not the budget |
| a failing check whose `link` is `/actions/runs/<id>/job/<id>` | the run id is extracted and its log lands in `ci_fix.md` |
| a failing check whose `link` is `/runs/<id>` (CodeQL) | dropped by the numeric guard, no `gh run view` call |
| `fixes_applied` key absent, `own_findings` empty | eligible — **not** blocked by the check-10 jq |
| `ci_fix` touches a file `review_fix` added | allowed; the ceiling is `merge_base_files.txt`, not `changed_files.txt` |
| `ci_fix` touches a file in neither | blocked |

Every ambiguous input must land on **blocked**. Prove that by fixture, not by reading.

## 8 — The host switch actually reaches the sandbox

The one hop that cannot be checked by reading. `[run.environment.env]` does not
interpolate `${X}`, and the failure is invisible: the merge blocks with a true-sounding
"switched off host-wide" either way.

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro variable list'
```

`FABRO_AUTO_MERGE` must be present. If it is absent, every pr-review run fails to
compile rather than merging anything — loud, but it means no reviews either.

Then prove the value lands **in the run sandbox**, not merely in the server container:
fire a review, and read `/tmp/fabro/auto_merge` and `/tmp/fabro/auto_merge_off_reason`
back off the stage log. `0` with the host reason while the variable reads `1` means the
injection is broken.

## 9 — A dry fire

```sh
DRY_RUN=1 ~/bin/fabro-fire-pr-review.sh andrewthetechie/jelly-swipe 378
```

The printed `RunIntent` must carry `auto_merge` as a number, and the resolved report
must show which way the switch is set for that repo.

## Acceptance

All nine sections pass, with the exact node and edge counts above and exactly one
`PrReview` warning. Then, and only then, task 10.
