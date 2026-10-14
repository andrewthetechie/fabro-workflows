# 01: triage asks about a Contradiction (U2-U4, C1)

## Changes

1. `_io/schemas/triage.schema.json`: add `contradictions` (C1) to `properties` and `required`.
   Run `python3.11 ops/fabro-io-manifest.py generate` so `issue-triage` and `arch-review` carry it.
2. `_shared/triage/prompts/triage.md.j2`:
   - A new section, **Contradictions**, before *Readiness*. For each acceptance criterion,
     name the surface it needs: a backend endpoint, an external API, a secret or credential,
     a migration, a deploy. Then read the non-goals and boundaries. A criterion whose surface a
     non-goal excludes is a Contradiction. Example: the Resend case from the overview.
   - A Contradiction is always a question, never a `decision`: which of the two changes is
     product scope.
   - *Readiness*: `ready` requires no Contradiction.
   - *Output*: document `contradictions` and the gate rule.
3. `_shared/triage/triage.fabro`, `triage_gate`: after the `decisions` checks, invalid when
   `R=ready` and `contradictions` is not an empty array. Also invalid when `contradictions` is
   not an array. Update the gate's comment.
4. `_shared/triage/prompts/improve.md.j2`, *Boundaries*: never resolve a conflict between an
   acceptance criterion and a non-goal by editing, moving or dropping either. Keep both as
   written. The next stage asks about them.

## Tests (`ops/test-task-gates.sh`)

Find the `triage_gate` section. Add `contradictions: []` to every existing fixture that should
stay valid. Add:

- `ready` with one contradiction (the Resend fixture) is refused: exit 1 on the first attempt,
  then `triage_readiness=invalid`.
- `needs_info` with that contradiction and one question gives `needs_info`.
- `contradictions` missing or not an array is refused.

## Done when

`make check` passes.
