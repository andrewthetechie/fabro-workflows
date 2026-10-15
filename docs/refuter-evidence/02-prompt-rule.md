# 02: the Refuter credits `ci.sh` for command criteria

## Steps

Edit `.fabro/workflows/_shared/review-merge/prompts/refute.md.j2`:

(a) In `## Inputs`, the first paragraph lists what `inputs` returns. Add "which validation
passed on this exact tree, with the text of `.fabro/ci.sh`," to that list.

(b) In `## Operating rules`, replace this bullet:

```
- `./.fabro/ci.sh` passed on this exact HEAD before you started. Do not run it, and do
  not run tests or builds that write files. A green CI is not evidence that a
  requirement is met: it shows only that the tests that exist pass.
```

with:

```
- Do not run `./.fabro/ci.sh`, and do not run tests or builds that write files. The
  `validation` input says whether `./.fabro/setup.sh` and `./.fabro/ci.sh` passed on this
  exact tree, and quotes `.fabro/ci.sh`. A green CI is not evidence that a behavioural
  requirement is met: it shows only that the tests that exist pass. Command criteria,
  described under Method, are the one exception.
```

(c) In `## Method`, step 2, after the three status bullets (`met`, `not_met`,
`unverifiable`), add this paragraph at the same indentation as those bullets' parent text:

```
   **Command criteria.** A criterion whose whole content is that a named command
   succeeds ("`bunx tsc --noEmit` passes", "`bun run test:unit` passes", "`ruff check`
   is clean") is a command criterion. Do not run the command. It is `met` when both of
   these hold:
   - the `validation` input says a validation passed on this exact tree, and
   - `.fabro/ci.sh` runs the same tool over every file this PR changes, either directly
     or through a script or hook it calls (a `package.json` script, a pre-commit hook).
     A different config file or a narrower scope is fine when it still covers every
     changed file.

   Cite the `.fabro/ci.sh` line, and the script or hook it calls, in `evidence`. For
   example: "`.fabro/ci.sh:13` runs `bun run --filter frontend build`, whose `build`
   script runs `tsc -p tsconfig.build.json`, which covers every changed file."
   It is `unverifiable` when the `validation` input says no validation is recorded for
   this tree, or when `.fabro/ci.sh` does not run that tool over the changed files. Say
   which in `evidence`. Checks in `.fabro/test.toml` are not evidence: they did not run
   on this tree. Never use this rule for a criterion that describes behaviour, even when
   a test that CI runs covers it. For those, read the code.
```

Make no other changes to the prompt.

## Done when

`make check-fast` passes. Commit:
`feat(review-merge): the Refuter credits ci.sh for command criteria (refuter-evidence 02, #40)`.
