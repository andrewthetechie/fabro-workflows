# `_io/` — the Stage manifest (ADR 0016)

`_io/` has no `workflow.toml`, like `_shared/` — nothing in it is a runnable package.

- **`manifest.json`** is the human-authored source of truth for every agent stage in
  the four workflows and the two shared phases (24 stages). It is read by
  `ops/fabro-io-manifest.py`, never by `fabro-io` directly. Each stage lists its
  ordered inputs, its optional output contract (`schema` is the contract *name*, which
  the generator inlines from `schemas/<name>.schema.json`), its Sealed paths, its
  `live` flag, and — under `also` — any other `/tmp/fabro/` path the prompt names.
- **`schemas/`** holds one JSON Schema (draft 2020-12, `additionalProperties: false`
  everywhere) per output contract. `refute.schema.json` is written in task 05; the
  others are written when their stage migrates (tasks 08 and 10). The schema-compile
  gate is `cargo test` in `ops/fabro-io/`.

## Source vs generated

The source manifest groups stages by phase and by workflow, with a `live` flag and
prompt paths. The generator (`ops/fabro-io-manifest.py generate`) flattens it per
workflow — own live stages use their node id; imported live stages are prefixed with
the import node id (`review_merge.refute`) — and embeds the compact, sorted-key JSON
between C2's markers under `[run.environment.env]` in each `workflow.toml`, in the
`FABRO_IO_MANIFEST` variable. Only **live** stages appear in the generated form; tasks
07–10 turned every agent stage live, so the generated manifests now carry all of them.

## Inputs that are not files

A prompt's "Inputs" table also lists the repository *checkout* (the working directory)
and, in `scan`, repo-relative paths like `CONTEXT.md` and `docs/adr/`. Those are the
agent's directory context, not files the `inputs` tool can serve, so they are **not**
in the manifest's `inputs`. The manifest serves only absolute `/tmp/fabro/` files, in
the prompt's relative order. The prompt remains the authority for what an input
means (C2).
