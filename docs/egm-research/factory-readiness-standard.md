# Factory Readiness Standard

This standard defines the quality bar for an implementation plan that an unattended Fabro
factory will build. It replaces EGM's implementation-readiness standard for that purpose. Use
it as a custom standard in `egm-review-loop`
(see [the planning guide](04-planning-a-feature-with-egm.md)).

## Core Principle

A plan passes if it can be cut into issues that an automated pipeline can implement, *prove*
and merge without a human. Each issue is planned once, coded by fresh sessions, and judged by
a reviewer that sees only the issue, the diff and `.fabro/ci.sh`'s result. That reviewer is
told to prove the work is not done.

## The Verifiable-Criterion Rule

Every acceptance criterion must be something a reviewer can mark *met* from the code, the
diff, the tests or `.fabro/ci.sh`'s run. A criterion fails this standard when it needs any of
these:

- text in a pull request description or comment;
- a command that `.fabro/ci.sh` does not run, directly or through a script, `package.json`
  script or Makefile target it calls;
- "must not drop" or "must not decrease" on a metric, rather than a raised threshold that
  `ci.sh` enforces;
- a screenshot, a manual or visual check, a run on another operating system, a deployed
  environment, or external credentials. These belong under **Post-deploy checks**, never
  among the acceptance criteria;
- a change under `.github/workflows/`. That is a human action outside the plan's automated
  work.

## Non-goals and Contradictions

The plan must state explicit non-goals. No acceptance criterion may need a surface a non-goal
excludes, such as a backend, an external API, a secret or a migration. Such a pair is a
Contradiction. Only the author resolves it, by changing one side, and the plan fails until
they do.

## Decisions, not file boundaries

The plan must leave no *design* decision to the implementer: data shapes, API contracts,
defaults, error behaviour, validation rules and migrations are decided and written down.

The plan must **not** turn its file list into an allowlist. List the expected files to
create, modify or delete, each with a reason. The implementer may also edit callers,
fixtures, generated types and tests that the change needs. A reviewer must not report such
an integration edit as a defect of the plan.

## Slices and order

- Partition the work into phases. Each phase leaves the default branch building and its
  tests passing.
- Each phase must fit one automated run: roughly a dozen implementation tasks at most, and
  no task that needs hours of uninterrupted generation. A larger phase must be split.
- Phase order must be a simple chain: each phase depends on at most one earlier phase.
  Phases with no dependency are independent and may run in parallel. A phase that needs two
  predecessors must be restructured.
- A phase small enough for one commit (under about 200 changed lines and 10 files) is noted
  as a fast-lane candidate.

## Requirements (non-exhaustive)

For each phase, the plan states, where they apply:

- **Requirements and acceptance criteria** that meet the Verifiable-Criterion Rule.
- **Command criteria**, each naming its `.fabro/ci.sh` line or the chain of calls that
  reaches it.
- **Test expectations**: the cases that prove each criterion, including edge cases.
- **Current-state facts** the implementer relies on, with repository paths and symbols that
  exist today. A proposed new file is not a missing file.
- **Post-deploy checks** and **human actions**, listed separately from the automated work.
