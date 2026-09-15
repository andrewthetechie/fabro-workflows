# Task 08 — Validate the workflow before it ever reaches production

**Depends on:** tasks 02–07 (every file must exist).
**Do before:** task 09. **Do not deploy without a clean pass here.**
**LLM level:** local is fine. Escalate if `fabro validate` reports an error you cannot
resolve in three attempts — DOT escaping errors can be subtle.

## Goal

Prove the redesigned workflow is admissible and runnable **before** it reaches four
production repositories. Five independent checks; run all of them.

## Why this task exists separately

The workflow repo is pinned by four production automations on a `*/15` cron. Pushing a
broken `workflow.fabro` or `workflow.toml` breaks four repositories on the next fire,
and the failure modes are silent in different ways: a DOT error fails the run at
admission, a TOML error returns 422 from the automation endpoint, and an undefined
template variable fails at run admission with no stage ever starting. Each of these is
caught by a **different** check. Run all five.

Work from `~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/`.

## Check 1 — Every inline shell script parses

`workflow.fabro` embeds POSIX-sh scripts inside DOT `script="..."` attributes. A
missing `fi`, an unbalanced quote, or a stray backslash only surfaces at stage runtime,
which is 20+ minutes into a run.

Extract each `script="..."` attribute body, un-escape `\"` back to `"`, write it to a
temp file, and run `sh -n` on it. Do them one at a time so you know which node failed.

A workable extraction, if you prefer to do it by hand: open `workflow.fabro`, and for
each node with a `script="` attribute, copy the text between that opening quote and the
closing `"]`, replace every `\"` with `"`, paste into `/tmp/chk.sh`, and run
`sh -n /tmp/chk.sh`. There are 21 command nodes: `acquire`, `claim`, `prep`,
`decompose_gate`, `next_task`, `resolve_merge_gate`, `improve_gate`, `prep_review`,
`validate`, `review_gate`, `integrate`, `rework_router`, `extra_prep`, `standards_gate`,
`spec_gate`, `quality_gate`, `extra_gate`, `open_pr_prep`, `open_pr`, `close_noop`,
`mark_stuck`.

Every one must pass `sh -n` with no output.

`sh -n` validates **shell** structure only — it is blind to the contents of a
`jq '...'` argument, which is just a string as far as the shell is concerned. A
malformed jq filter passes this check and fails at runtime, mid-run. Check 7 covers that.

**POSIX-only.** The sandbox profile images run command stages under `sh`, not bash. If
`sh -n` passes on your machine but you wrote `[[ ... ]]`, `set -o pipefail`, arrays, or
`$'...'`, you have a bash-only construct that will fail in the sandbox — and on a Mac,
`sh` may be bash in POSIX mode and let it through. Grep for them explicitly:

```sh
grep -n '\[\[\|pipefail\|declare \|local \|+=(' workflow.fabro
```

Must return nothing.

## Check 2 — The graph validates against the container's fabro

The host CLI is `0.254.0` and resolves `@prompts` / `@schemas` differently from the
server. **Validate with the container's fabro or the result is meaningless.**

```sh
# Copy the workflow tree to the host, then into the container:
rsync -a ~/.fabro-deploy/fabro-workflows/ andrew@10.10.0.32:/tmp/fabro-workflows-check/
ssh andrew@10.10.0.32
docker cp /tmp/fabro-workflows-check/.fabro fabro-fabro-1:/tmp/wfcheck-fabro
cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/wfcheck-fabro/workflows/backlog/workflow.toml
```

Must report no errors. If it reports a DOT parse error, the line/column it names is in
`workflow.fabro`; fix it there, re-copy, re-run. Common causes, in order of likelihood:

- a `#` character anywhere in `workflow.fabro` (DOT rejects it — use `//`)
- an unescaped `"` inside a `script="..."` or `label="..."` value (must be `\"`)
- a backslash that is not part of `\"` (avoid backslashes entirely in shell/jq bodies)
- an edge referencing a node id that does not exist (a typo in a `->` target)

## Check 3 — TOML parses strictly

`fabro validate` does **not** parse `workflow.toml`. The automation endpoint does, and
returns 422.

**`tomllib` needs Python 3.11+.** The Mac's default `python3` is 3.9 and will fail with
`ModuleNotFoundError: No module named 'tomllib'` — that is a broken check, not a broken
TOML. Use an explicit 3.11+ interpreter (this Mac has `python3.11`, `python3.12`, and
`python3.13`), or run the check on the host, whose `python3` is 3.14.

```sh
cd ~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog
python3.11 -c 'import tomllib; d=tomllib.load(open("workflow.toml","rb")); print(sorted(d["run"]["model"]["fallbacks"].keys()))'
```

Must print exactly:

```
['coders', 'glm-4.7', 'glm-5.3', 'kimi-for-coding', 'kimi-k3', 'long-context']
```

Six top-level keys. If you see `glm` or `4` in that list, a dotted key was left
unquoted and TOML built a nested table instead.

## Check 4 — Grep gates

Run each of these from the `backlog/` directory. Each must return **no matches**.

```sh
# No `#` used as a COMMENT. `#` inside a quoted string is legal and required
# (PR title `(#347)`, `Resolves #N`, `## ` headings in feedback/PR-body files),
# so this looks only for a `#` that starts a line or follows only whitespace:
grep -nE '^[[:space:]]*#' workflow.fabro

# Only the reserved routing keyword is allowed; a custom schema or any @schemas
# reference is the original P0 bug. This must return NOTHING:
grep -rn '@schemas\|schemas/' .
grep -o 'output_schema="[^"]*"' workflow.fabro | grep -v 'output_schema="routing"'

# Prompts may use {{ goal }} and nothing else. This finds every other variable:
grep -rn '{{' prompts/ | grep -v '{{ goal }}'

# No leftover Sandcastle mechanisms in the prompts:
grep -rni 'structured-result\|dedupe_key\|depends_on\|PRD_BODY\|REVIEW_ASPECTS' prompts/

# Coding prompts must not instruct commits:
grep -rn 'git commit\|git add\|git push' prompts/coder.md.j2 prompts/rework.md.j2
```

The last one may return lines that **forbid** those commands — read each hit. Anything
that instructs the agent to run them is a failure; anything that forbids them is fine.

## Check 5 — Every prompt referenced by the graph exists

```sh
grep -o 'prompt="@prompts/[^"]*"' workflow.fabro | sed 's/.*@prompts\///; s/"//' | sort -u > /tmp/referenced.txt
ls prompts/ | sort > /tmp/present.txt
diff /tmp/referenced.txt /tmp/present.txt
```

`diff` must be empty. A referenced-but-missing prompt fails at run admission; a
present-but-unreferenced prompt is dead weight task 07 should have deleted.

## Check 6 — Per-task context keys are cleared

Fabro's edge selection takes **every** condition that matches and breaks ties by lowest
target node id (`routing.rs` `select_edge` / `best_by_weight_then_lexical`), so a routing
key left over from a previous task can match alongside `outcome=failed` on a gate retry
and win. `next_task` must therefore reset every per-task key each time it pops a task.

```sh
grep -o 'rebase_failed[^,}]*' workflow.fabro | sort -u
```

Must show **both** `rebase_failed\":false` and `rebase_failed\":true`. If only `true`
appears, the key is sticky: after the first mainline merge conflict every later
`next_task` routes to `resolve_merge` instead of `improve` for the rest of the run.

```sh
grep -c 'review_decision\\":\\"none\\"' workflow.fabro     # expect 2 (both next_task paths)
grep -c 'task_disposition\\":\\"none\\"' workflow.fabro    # expect 2
grep -c 'standards_status\\":\\"none\\"' workflow.fabro    # expect 1 (extra_prep)
```

## Check 6b — every routing-emitting node declares `output_schema="routing"`

A command node that prints `context_updates` but lacks `output_schema="routing"` has
its routing **silently ignored**: fabro never scans its stdout, the run walks its
unconditional edges, and nothing reports an error. This is the single most damaging
thing that can be wrong with the graph while still passing `fabro validate`.

```python
#!/usr/bin/env python3
import re, sys
src = open(sys.argv[1] if len(sys.argv) > 1 else "workflow.fabro").read()
i = 0; missing = []; ok = 0
while True:
    j = src.find('script="', i)
    if j < 0: break
    head = src[:j]
    node = [m.group(1) for m in re.finditer(r'(?m)^\s{4}([a-z_0-9]+)\s*\[', head)][-1]
    decl = head[head.rfind('\n    ' + node):j]
    k = j + 8; buf = []
    while k < len(src):
        c = src[k]
        if c == '\\' and src[k+1] == '"': buf.append('"'); k += 2; continue
        if c == '"': break
        buf.append(c); k += 1
    body = ''.join(buf)
    if 'context_updates' in body:
        if 'output_schema="routing"' in decl: ok += 1
        else: missing.append(node)
    i = k + 1
print(f"{ok} emitting nodes declare routing; missing on: {missing or 'none'}")
sys.exit(1 if missing else 0)
```

Expect `14 emitting nodes declare routing; missing on: none` and exit 0.

## Check 7 — every embedded jq program compiles

The gates' routing logic lives inside `jq` filters that no other check inspects. A
typo there (`lenght` for `length`, an unbalanced paren) is invisible to `sh -n` and to
`fabro validate`, and surfaces only when the stage runs — 20+ minutes into a run.

Write this to `/tmp/jqcheck.py` and run it from the `backlog/` directory. It is Python
rather than shell on purpose: the extraction needs a regex containing both quote
characters and a literal `$`, and every shell-quoting version of it is a transcription
trap.

```python
#!/usr/bin/env python3
"""Compile-check every jq program embedded in a workflow.fabro."""
import re, subprocess, sys

path = sys.argv[1] if len(sys.argv) > 1 else "workflow.fabro"
src = open(path).read().replace('\\"', '"')
# next_task builds a filter by shell concatenation: jq '.['$IDX']'
# Collapse '$VAR' to a literal 0 so it reads as one program: .[0]
src = re.sub(r"'\$[A-Za-z_][A-Za-z_0-9]*'", "0", src)

progs = sorted({m.group(1) for m in re.finditer(r"jq(?:\s+-[a-zA-Z]+)*\s+'([^']*)'", src)})
bad = 0
for p in progs:
    r = subprocess.run(["jq", p], input="null", capture_output=True, text=True)
    if "syntax error" in r.stderr or "is not defined" in r.stderr:
        print(f"COMPILE ERROR: {p}\n  {r.stderr.splitlines()[0]}")
        bad += 1
print(f"{len(progs)} jq programs checked, {bad} compile errors")
sys.exit(1 if bad else 0)
```

```sh
python3 /tmp/jqcheck.py workflow.fabro
```

Expect `11 jq programs checked, 0 compile errors` and exit 0.

The `'$VAR'` collapse matters: `next_task` builds its filter by shell concatenation
(`jq '.['$IDX']'`), and without it the extractor sees the fragment `.[` and reports a
false compile error. If you get exactly that false positive, your transcription
dropped the `re.sub` line.

This check was verified to actually fail: injecting an unbalanced paren, and a
`lenght`-for-`length` typo, each produced a `COMPILE ERROR` line and exit 1.

## Check 8 — the target repositories satisfy the `.fabro` contract

`prep` runs `./.fabro/setup.sh`; `validate` runs `./.fabro/setup.sh && ./.fabro/ci.sh`.
A repository missing either file fails **every** run at `prep`, which is an expensive
thing to discover from a live run in task 09.

```sh
for r in andrewthetechie/jelly-swipe andrewthetechie/womens-fantasy-sports \
         andrewthetechie/lawncare-saas andrewthetechie/writers-app; do
  printf '%-42s ' "$r"
  s=$(gh api "repos/$r/contents/.fabro/setup.sh" --jq '.name' 2>/dev/null || echo MISSING)
  c=$(gh api "repos/$r/contents/.fabro/ci.sh"   --jq '.name' 2>/dev/null || echo MISSING)
  echo "setup.sh=$s  ci.sh=$c"
done
```

Every row must show both files. A `MISSING` is a blocker for that repository: either
add the script there first, or drop that repository from the automation set before
task 09. All four passed as of 2026-09-13.

## Done when

- All 21 command-node scripts pass `sh -n`; the bashism grep is empty.
- The container's `fabro validate` reports no errors.
- `tomllib` (via a 3.11+ interpreter) prints the six expected fallback keys.
- Check 6's three greps return the expected counts.
- Check 6b reports 14 emitting nodes with routing and none missing.
- Check 7 reports 0 compile errors across 11 jq programs.
- Check 8 shows `setup.sh` and `ci.sh` present in all four target repositories.
- All five grep gates in check 4 are clean (modulo the read-each-hit caveat).
- `diff /tmp/referenced.txt /tmp/present.txt` is empty.

## Pitfalls

- **Do not delete a `#` to make a grep pass.** Seven of them are load-bearing: the PR
  title's `(#347)`, the PR body's `Resolves #N` (which is what auto-closes the issue on
  merge), and the `## ` headings written into `feedback/rework.md` and `pr_body.md`.
  Check 4's `#` gate is deliberately anchored to line-start for this reason.
- Do not validate with the **host** `fabro` binary and call it done. It is a different
  version with different `@`-resolution, so a clean host validate proves nothing about
  what the server will do.
- Do not `cargo insta accept`, edit fabro source, or touch the fabro repository at
  `/Users/andrew/Documents/code/software-factory/context/fabro`. This series only
  changes the workflow repository and the deployment.
- Fixing a DOT escaping error by deleting the offending shell line is not a fix. The
  scripts in task 02 are load-bearing; if one will not escape cleanly, rewrite it to
  avoid the character (usually by replacing a backslash-heavy `jq` filter with a
  simpler one), do not drop its behavior.
- If `fabro validate` passes but you had to change a node's script to get there, re-run
  check 1 on that node.
