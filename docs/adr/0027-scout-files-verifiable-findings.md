# The scout files verifiable findings straight to the queue, and GitHub holds its state

**Status:** proposed (2026-10-16). Every decision below is the operator's, from the planning
session for issue #32 (roadmap B6). The task series is `docs/scout/`. It becomes accepted
after the 14-day measurement (task 07): at least half of the scout PRs that finished merged
without a human, and no lint suppression merged.

Work reaches the queue from three places: the operator, `arch-review`, and Dependabot.
Nothing goes looking for defects, although bounded, mechanically checkable fixes are the work
the local coder boxes do best.

## D1. Two sources: Ratchet rules and Flaky jobs

The **Scout** is a seventh package, `scout`, with command nodes only, run daily in each
target repository. It has two sources. B6 listed five; red `main` belongs to #24, and
mutation survivors, production errors and `tsc` strictness flags need per-repository tooling
first.

B6's "new lint and type findings" would find nothing as written: every target repository's
`ci.sh` already fails on its lint and type findings, so a green `main` has none. A **Ratchet
rule** is therefore a rule CI does **not** enforce yet, declared by the repository in
`.fabro/scout.toml`.

## D2. One Ratchet rule issue fixes the whole rule and enforces it

The Refuter never runs a command. It credits a command criterion only when `ci.sh` runs that
tool over the changed files (refuter-evidence D7, D8). A criterion like "`ruff check --select
B006` passes" on a rule CI does not run would be `unverifiable` on every PR. So one issue
covers one rule in the whole repository. Its PR fixes every finding **and** adds the rule to
the enforced lint config, so `ci.sh` runs it, the Refuter can credit it, and the rule only
ever tightens. A rule is filed only while it has at most 15 findings in at most 8 files.

Rejected: one issue per rule per file, with criteria written as properties of the code. It
leaves enforcement to a later manual step, and the rule can regress in the meantime.

## D3. Scout issues skip triage, and Ratchet rule issues take the fast lane

Each issue is generated from a template that already writes the acceptance criteria and
non-goals triage would add. Triage runs only twice a week (inside `arch-review`), so it would
add days of latency and little value. Scout files with `agent`. A Ratchet rule issue also
gets `fast-path`, which amends ADR 0023 D1: until now only a human added that label. A
flaky-job issue keeps the standard lane, because finding the cause needs `decompose` and
`improve`. Its PR may be held as `unverifiable` and merged by hand, which is accepted and
measured.

Suppressing a finding (`noqa`, `eslint-disable`, `#[allow]`, per-file ignores) is a non-goal
in the issue text, checked by the Refuter. No deterministic counter is added until a
suppression is found merged.

## D4. GitHub is the only store

As with the retro (ADR 0026 D3), each issue carries the label `scout` and a hidden marker
`<!-- fabro:scout fp=FINGERPRINT -->`, and every run lists every `scout` issue, open and
closed. An open issue is known. Closed as not planned (or duplicate) is suppressed forever.
Closed otherwise, and found again, files a regression that links it. A fingerprint holds no
line number, run id or SHA, so a moved finding is never new. There is no "previous findings"
baseline: existing findings are filed over time at the cap rate (3 per source per run, and
none while 3 of that source are open).

Rejected: a baseline in the scheduler database (B6's open question 1). It would be a second
copy of decisions the operator makes on issues, and it could not be rebuilt.

## D5. A package with no agent, logic in `fabro-io`

Ratchet rules need the repository's checkout and toolchain, which only the profile image
has. So scout is a Fabro workflow in each repository's environment, not a scheduler
subcommand like the retro (whose container has neither). Its decisions live in a pure
`fabro-io scout` subcommand, unit-tested in Rust. The graph's shell only fetches, runs
`setup.sh`, and calls `gh`. Each repository goes live by setting `filing = true` in its own
`scout.toml`, because automation rows cannot carry inputs. `scout.toml` is protected by
`config_changed`, so no agent PR can loosen it.

## Consequences

- The issues are authored by whichever account the run's GitHub token belongs to (`gh api user` shows it), not necessarily the operator's. The `scout` label, not the author, marks an issue as the scout's.
- One fabro run slot per repository per day, for minutes, with no coder lease.
- Changing `fabro-io` needs `make deploy-images`, and `discord-notify.sh` needs `make deploy-notify`.
- Scout never closes an issue whose finding went away without a PR.
