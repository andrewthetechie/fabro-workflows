# B6 · Self-reported bugs: a scout that feeds the queue

**Status:** proposed. **Axis:** throughput, quality. **Effort:** M per source.
**Feasibility:** high. **Depends on:** A1 (red `main` is source 1), A2 part 3b (the
lease-less dispatch path), A3 layer 4 (fingerprints so nothing is re-filed).

## Problem

Work enters from two places: the operator (issues filed with pi or Claude) and `arch-review`
(refactor proposals). Nothing finds **defects**, which is the work the operator is content to
have done without being asked. "Continuously worked on" needs a producer of verifiable,
bounded work that is not refactor churn.

## Design

A per-repo **scout**: a new fabro workflow, `scout`, fired on a schedule or by the scheduler's
lease-less path (A2 3b). It needs no coder box. Most sources need no model at all: a command
node runs a tool, diffs the result against the previous run, and files issues. Sources in order
of value per unit of noise:

| # | Source | How | Filed as |
|---|---|---|---|
| 1 | **Red `main`** | A1 already files it | `agent`, `priority`, `bug` |
| 2 | **New linter and type findings** | `ruff check --output-format json`, `eslint -f json`, `tsc --noEmit --pretty false`, `cargo clippy --message-format json`, diffed against the last scout's result, which is stored as a JSON artifact on a pinned tracking issue or in the scheduler DB | one issue per rule per module, `needs-triage`, `bug` |
| 3 | **Flaky tests** | the `watch_checks` rerun (ADR 0011 D2) already knows which jobs passed on rerun. Record the test ids, and file when one flakes 3 times in 14 days | `needs-triage`, `flaky-test` |
| 4 | **Mutation survivors** | `mutmut` / Stryker / `cargo-mutants`, weekly, scoped to the 20 files most changed in 30 days (slow, so bounded). Each survivor's acceptance criterion is "a test that fails when this mutant is applied" | `needs-triage`, `test-gap` |
| 5 | **Production errors** (`lawncare-saas` deploys on merge) | error logs or Sentry grouped by fingerprint, new fingerprints only | `needs-triage`, `bug` |

Rules:

- Every issue carries a fingerprint marker (`<!-- fabro:scout kind=lint rule=E501 path=src/x -->`).
  Filing checks open **and** closed issues for the marker, the same dedupe shape as the
  architecture slug (contract C2), so a closed "won't fix" is never re-filed (A3 layer 4).
- A cap per scout run per source (start at 3), so a new linter rule cannot flood the queue.
- Scout issues go through triage like any other. Bugs may reach `agent` automatically.
  Anything that proposes **new behaviour** goes to `proposed` (A2).
- B2's work-mix quota caps how much of the fleet scout work can take.

## Build order

Sources 1 and 2 first: deterministic, cheap, and immediately verifiable (the finding
disappears or it does not). Then 3, which is nearly free given ADR 0011's rerun. Then 4 and 5,
which need per-repo tooling.

## Verification

- Offline fixtures for the diff-and-file logic, including "same finding at a new line number is
  not new."
- One live scout run per repo in dry-run mode (print what would be filed), reviewed by the
  operator before enabling filing.

## Open questions

1. Where does the "previous findings" baseline live? Suggest the scheduler DB (it survives
   sandboxes, and the scheduler is already stateful).
2. Should lint findings in files the factory never touches be filed at all? Suggest: only in
   files changed in the last 90 days, the same hot-spot window `arch-review` uses (ADR 0012
   D1).
