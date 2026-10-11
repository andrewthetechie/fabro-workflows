# 03: triage separates post-deploy checks (#28, triage half)

## Contract (G3)

```
## Post-deploy checks

- [ ] Sign in on staging; POST /api/x returns 201.
- [ ] Reload /settings in a WebKit build; the form keeps its values.
```

A criterion belongs here when no sandbox can check it: it needs a deployed environment,
a real browser build, real client traffic, external credentials, or an external answer.
It must then **not** also appear under the acceptance criteria.

## Steps

1. Read `_shared/triage/triage.fabro` end to end and find which agent writes the
   acceptance criteria into the body (`improve` -> `improved_body.md` at `:112`, or
   `triage`). **The agent that rewrites the body owns the move** — it is the only place
   that can remove a criterion from the acceptance list without text-matching. If that is
   `improve`, change `prompts/improve.md.j2`; otherwise `prompts/triage.md.j2`.
2. Add to `triage.schema.json` (or `improve.schema.json`, whichever agent owns it) a field
   `post_deploy_checks: string[]` (may be empty, required). Update the prompt that writes
   it in the same change.
3. Update `_io/manifest.json` if the stage's inputs/outputs change, and **regenerate** the
   `FABRO_IO_MANIFEST` blocks (`_io/README.md` has the command). Never hand-edit them.
4. In the gate that validates that output, add the shape check (array of non-empty
   strings, each matching nothing that would break a markdown list line — no newlines).
5. In the body writers (`:327-333` and `:363-374`), drop any existing
   `## Post-deploy checks` section with the same awk pattern used for
   `## Decisions made during triage`, and append the section when the array is non-empty.
   Write it as `- [ ] ` lines.
6. Child issues (`:429-433`, `## Acceptance criteria` of a split child): if the parent has
   post-deploy checks, carry them to the **last** child only. Note this in the commit body.
7. Tell triage in the prompt: when unsure whether a sandbox can check a criterion, keep it
   as an acceptance criterion (the refuter blocking is the safe failure).

## Tests

Extend the triage gate cases in `ops/test-task-gates.sh`: a valid output with
post-deploy checks, an empty array, a missing field (fails), a non-string item (fails).
Add a body-writer case if the suite covers those nodes: an existing section is replaced,
not duplicated.

## Done when

`make check` passes and the generated manifest blocks match. Commit:
`feat(triage): write post-deploy checks apart from acceptance criteria (#28)`.
