# Task 06 — Validate before this touches a real PR

**Depends on:** 01–05. **Blocks:** 07. **LLM level:** local is fine; escalate if stuck.

This workflow **force-pushes to other people's branches**. A defect here rewrites a PR.
Run every check.

Work from `~/.fabro-deploy/fabro-workflows/.fabro/workflows/pr-review/`.

## Check 1 — every inline script parses as POSIX sh

Extract each `script="..."` body, un-escape `\"` to `"`, and run `sh -n`. There are
**11** command nodes: `validate_input`, `claim`, `rebase_check`, `rebase_gate`,
`prep_review`, `standards_gate`, `spec_gate`, `fix_gate`, `validate`, `rebase_recheck`,
`deliver`, `mark_needs_human` — twelve, of which eleven print `context_updates`
(`mark_needs_human` does not).

```sh
grep -nE '\[\[|pipefail|declare |local |\+=\(' workflow.fabro
```

Must return nothing. `sh -n` sees shell structure only — it is blind inside `jq '...'`
(check 7) and inside `git` arguments.

## Check 2 — the graph validates against the container's fabro

The host CLI is a different version and resolves `@prompts` differently. Use the
container.

```sh
rsync -a ~/.fabro-deploy/fabro-workflows/ andrew@<HOST>:/tmp/prcheck/
ssh andrew@<HOST>
docker cp /tmp/prcheck/.fabro fabro-fabro-1:/tmp/prcheck-fabro
cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/prcheck-fabro/workflows/pr-review/workflow.toml
```

Expect `Workflow: PrReview (19 nodes, 40 edges)` and `Validation: OK`.

**Expect exactly one warning**, for `{{ inputs.pr_number }}` in `validate_input` — the
only node that interpolates the input. It is correct. Do **not** silence it by binding
`[run.inputs] pr_number` — see task 05.

## Check 3 — TOML parses strictly

```sh
python3.11 -c 'import tomllib; d=tomllib.load(open("workflow.toml","rb")); print(sorted(d["run"]["model"]["fallbacks"]))'
```

Expect `['coders', 'glm-5.3', 'kimi-k3']`. Three top-level keys — if you see `glm`, a
dotted key lost its quotes.

```sh
python3.11 -c 'import tomllib; print(tomllib.load(open("workflow.toml","rb"))["run"]["clone"])'
```

Expect `{'depth': 0}`. Depth defaults to 100, and a shallow clone makes
`git diff origin/<base>...HEAD` fail with `fatal: no merge base`.

## Check 4 — grep gates

Each must return nothing.

```sh
grep -nE '^[[:space:]]*#' workflow.fabro                              # '#' as a comment
grep -rn '@schemas\|schemas/' .                                       # dead mechanism
grep -o 'output_schema="[^"]*"' workflow.fabro | grep -v '"routing"'  # non-routing schema
grep -rn '{{' prompts/                                                # prompts take no vars
grep -n 'issues = "read"' workflow.toml                               # labels need issues:write
grep -rnE '^\s*\.[a-z0-9-]*_' workflow.fabro                          # underscore in a class selector
grep -rni 'structured-result\|REVIEW_ASPECTS\|ECOSYSTEMS' prompts/    # Sandcastle leftovers
grep -rn '<!--' prompts/                                              # spec commentary in a prompt
grep -o '\\.' workflow.fabro | sort -u                                 # backslashes other than \"
```

The last one must print only `\"`. `\n` inside a DOT string is a Graphviz escape, not a
shell newline: `printf '%s\n'` there becomes `printf '%sn'`.

`{{ inputs.pr_number }}` belongs in **scripts only** — the prompts grep must be empty.

## Check 5 — referenced prompts exist

```sh
grep -o 'prompt="@prompts/[^"]*"' workflow.fabro | sed 's/.*@prompts\///; s/"//' | sort -u > /tmp/ref.txt
ls prompts/ | sort > /tmp/have.txt
diff /tmp/ref.txt /tmp/have.txt
```

Empty. Four prompts: `rebase`, `standards`, `spec`, `review_fix` — `rebase.md.j2` is
referenced twice, by both tiers.

## Check 6 — every routing-emitting node declares `output_schema="routing"`

The single most damaging thing that can be wrong while still passing `fabro validate`:
routing is silently ignored, the run walks its unconditional edges, and nothing reports
an error.

```sh
grep -c 'output_schema="routing"' workflow.fabro     # expect 11
```

Then confirm the set matches: every node whose script contains `context_updates` must
have it, and no node without one should.

## Check 7 — every embedded jq program compiles

`sh -n` cannot see inside `jq '...'`. Reuse the Python checker from the backlog series
(`08-validate.md` check 7), pointed at this graph. Expect 0 compile errors.

## Check 8 — the target repos satisfy the `.fabro` contract

`validate` runs `./.fabro/setup.sh && ./.fabro/ci.sh`. A repo missing either fails every
run.

```sh
for r in jelly-swipe lawncare-saas womens-fantasy-sports writers-app; do
  printf '%-26s ' "$r"
  s=$(gh api "repos/andrewthetechie/$r/contents/.fabro/setup.sh" --jq .name 2>/dev/null || echo MISSING)
  c=$(gh api "repos/andrewthetechie/$r/contents/.fabro/ci.sh"   --jq .name 2>/dev/null || echo MISSING)
  echo "setup.sh=$s ci.sh=$c"
done
```

All four were present as of 2026-09-14.

## Check 9 — the destructive paths, read by a human

No tooling substitutes for reading these three scripts once, carefully:

- `deliver` — is the push `--force-with-lease`, never `--force`? Does it push to
  `HEAD:$(cat head_ref)` and not to the base branch? And does `claim` still refuse a
  cross-repository PR, so this push can never land in the base repo of a fork PR?
- `rebase_gate` — does it check **all four** failure modes (rebase in progress, unmerged
  paths, conflict markers, base-not-ancestor), and does it `git reset --hard` back to
  `pre_rebase_sha` on failure?
- `rebase_check` — does it `git rebase --abort` on conflict before handing to the agent?
  Leaving a rebase in progress makes the next checkpoint commit conflict markers.

## Check 10 — the comment renderer produces a comment

The report is the product. Extract `validate_input`, run it, then drive the renderer
against fixtures in a throwaway git repo:

```sh
sh validate_input.sh                       # writes render.jq + render_comment.sh
sh /tmp/fabro/render_comment.sh complete   # full report
sh /tmp/fabro/render_comment.sh needs-human
```

Check all three degraded paths: no review artifacts at all, a malformed
`standards.json`, and a run whose checkpoints are all empty. None may fail; each must
fall back to `_did not run_` or `_None recorded._`. Confirm the Commits section lists
only commits whose diff is non-empty.

## Check 11 — every command node's failure lands somewhere safe

A failed node still routes and takes its **unconditional** edge (`OnFailure::Route` is
the default). For each of the 12 command nodes, follow the unconditional edge and
confirm a failure there does not walk into the happy path. `validate_input -> exit` and
`claim -> mark_needs_human` are correct; `prep_review`'s escape is folded into its
conditional edge as `context.diff_too_large=true || outcome=failed`, because its
unconditional edge goes to `standards`.

## Done when

- All 12 scripts pass `sh -n`; the bashism grep is empty.
- Container validate: `PrReview (19 nodes, 40 edges)`, `Validation: OK`, exactly one
  `pr_number` warning.
- `tomllib` prints the three expected fallback keys and `{'depth': 0}` for the clone.
- Checks 4–8 clean; check 6 counts 11.
- Check 10's renderer produced a report on all three degraded paths.
- Check 11 walked for all 12 command nodes.
- A human has read the three scripts in check 9.

## Pitfalls

- Do not delete a `#` to make check 4 pass. `#` is legal inside quoted strings.
- Do not bind `[run.inputs]` to silence check 2's warning.
- Do not "fix" a DOT error by deleting a node's logic; escape it properly instead.
