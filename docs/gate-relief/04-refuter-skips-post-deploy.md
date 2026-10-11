# 04: refuter skips post-deploy checks; the human sees them (#28, merge half)

## Steps

1. `refute_prep` (`review-merge.fabro:575`): after each `gh issue view` line writes the
   issue JSON, strip the `## Post-deploy checks` section from `.body` (to the next `## `
   heading or end), and append its `- [ ]` lines to `$R/post_deploy.md` under a
   `#<N>` sub-heading when more than one issue is linked. `rm -f` it at the top with the
   other files. Do this in jq or awk inside the existing script; keep the error branch
   (`could not be read`) unchanged. The refuter's manifest input stays
   `refute_issues.json` — no manifest change is needed for the refuter. Do **not** add
   `post_deploy.md` to the refuter's inputs.
2. `prompts/refute.md.j2`: one paragraph — post-deploy checks are removed before you see
   the issue; if a `## Post-deploy checks` heading is still present, ignore everything
   under it. Do not mark a criterion `unverifiable` merely because it is about a deployed
   environment if it was listed there.
3. The report renderer (`render.jq`, `:~118-210`): add a `### Post-deploy checks` block
   rendering `post_deploy.md` verbatim as a checklist when the file is non-empty, with one
   line: "Not checked by the agents. Verify after deploy." Pass it with `--rawfile` and
   tolerate a missing file. Check the report hooks invariants in
   `docs/agents/invariants-merge.md` first.
4. PR body: `backlog`'s `open_pr`/`open_pr_prep` (near `backlog/workflow.fabro:1800-1815`,
   read `docs/agents/invariants-backlog.md`) builds the body from the issue. Append the same
   checklist from the issue body when the section exists. `pr-review` PRs are human-written:
   report only. `dependabot`'s tracking issue has no such section: nothing to do.
5. Confirm G5: an issue with no section produces no `post_deploy.md`, the same
   `refute_issues.json` as before, and the same verdict.

## Tests

`ops/test-task-gates.sh`, refute_prep and report cases:
- issue body with the section -> `refute_issues.json` body lacks it, `post_deploy.md` has
  the lines; text after the next `## ` heading is kept;
- body without the section -> byte-identical `refute_issues.json` to the current code;
- refute_gate with an ordinary `unverifiable` criterion still yields `fail` (Done-when 2);
- report renders the checklist when present and nothing when absent.

## Done when

`make check` passes. Commit: `feat(review-merge): keep post-deploy checks out of the refuter (#28)`.
