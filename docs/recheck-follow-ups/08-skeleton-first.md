# 08. Skeleton-first decomposition in `decompose`, `improve` and `plan` (#5)

## Outcome
When a task creates a typed shared abstraction for two or more callers, `decompose` emits a
core skeleton, the core bodies, and one port per task (ADR 0021). `improve`'s `split` does
the same. The triage `plan` counts skeleton and bodies as one Task map entry.

## Why
In writers-app, core and port tasks hold 13 of 19 draft visits and 84% of the draft tokens
(`measure-2026-10-10.txt`). The ports drafted because their task asked them to change the
core's interface. A file the compiler has checked moves those decisions out of the coder's
reasoning, and a port that uses the core unchanged has nothing left to design.

## Change
Invoke `/fabro-workflow` first. Prompts are live on push.

1. `backlog/prompts/decompose.md.j2`, *Task size budget*: after "A task that introduces a new
   module *and* migrates every call site to it is two tasks", add the skeleton-first rule:
   - **When it applies:** the task creates a shared abstraction that two or more callers will
     use, and the abstraction introduces a type, trait, interface or generic parameter.
     Otherwise the two-task rule above applies.
   - **Skeleton task:** types, traits and signatures with stub bodies (name the stub form for
     the repository's language). The repository's checks must pass. The body has a section
     `## What each port needs`, one bullet per later port task, naming the items it will use.
     Its acceptance criteria say "compiles; interface only; stub bodies". Without that line
     the per-task `review` fails the task as incomplete.
   - **Bodies task:** comes directly after the skeleton. It implements the bodies against
     the core's own tests and does not change a signature.
   - **Port tasks:** one caller, or one group of callers, per task. Each lists the core's
     file in `files`, so `excerpts` copies its signatures into `task-code.md`. Each body says
     "Use the core's interface unchanged. You may add a new item to the core if this port
     cannot work without it. Never change an existing signature. If one is wrong, say so in
     your summary and keep the port to the current signature."
   - **Lint:** if the repository denies dead code or unused exports (read `.fabro/ci.sh`), the
     skeleton body names the narrowest allowance.
2. `backlog/prompts/improve.md.j2`, *Size budget*: the same rule for `split`, in three
   lines that point at the decomposer's rule. Skeleton, bodies and two ports are four
   slices, the most a split may return. A core with more than two ports keeps the extra ports
   in one slice.
3. `_shared/triage/prompts/plan.md.j2`, *What a task is*: one line saying that a typed shared
   core is one Task map entry, and that `decompose` splits it into skeleton and bodies. Do not
   change the split rule's numbers.
4. Prompt fixtures: if `ops/test-task-gates.sh` or a check greps these prompts for text you
   moved, update that check.

## Acceptance
- `make check` passes. `make check-host`: baselines unchanged.
- The three prompts state the rule in the same terms, and `plan.md.j2` still says "The size
  budget is the same one `decompose` (backlog) uses".
- Dry check by hand: give the new `decompose` prompt the parent issue of 01M4AT8SAY
  (writers-app#1024) in a scratch session, outside the factory. It should produce a skeleton
  whose `## What each port needs` covers the upkeep-event design that the port task
  `migrate-scene-path-map-to-core` drafted in reasoning. Record the result in the commit
  message. Do not commit the issue text.

## Tests
No gate changes. `decomposition.schema.json` and `improve.schema.json` are unchanged, so
`ops/fabro-io-manifest.py check` and `cargo test` see no difference.

## Depends on
Nothing. Task 10 measures it.
