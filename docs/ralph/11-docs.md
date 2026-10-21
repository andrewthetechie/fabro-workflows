# 11. Docs

## Outcome

Everything a later session needs to work on `ralph` safely is where `AGENTS.md` sends it.

## Change

- `AGENTS.md`: the layout row lists eight runnable packages, with `ralph`; the invariants list
  gains `docs/agents/invariants-ralph.md`; the *Validating* paragraph says the `Ralph` counts
  are recorded by task 12 and that `ralph` has the same deliberate `issue_number` warning as
  `Backlog`; *The server* names `~/bin/fabro-fire-ralph.sh`.
- `docs/agents/invariants-ralph.md` (new): the rules that pass `fabro validate` and fail at
  runtime, one table row each, in the house format. At least: `tasks.json` is written only by
  command nodes; `pick` publishes every key on every branch; `excerpts` publishes nothing, so
  `rung` must come from `pick`; `check_ok` is deleted at the top of `check`; `hygiene`
  publishes nothing, so its edges read `phase` from `pick` or `final_check`; `refute_gate`
  publishes `loop_exhausted` itself; the evidence rule; the round cap counts Refuter visits;
  `review_gate` writes `fix_result.json` in the shared shape; the copies and R10; every
  terminal node reaches `exit` through `learn_prep`; `ralph`'s `kimi-k3` fallback order is the
  reverse of `backlog`'s on purpose; no human gate.
- `docs/README.md`: the `docs/ralph/` row's status.
- `ops/README.md`: `fabro-fire-ralph.sh`, `SCHEDULER_RALPH_SHARE`, `--by-workflow`.
- `docs/USER-GUIDE.md`: what a `ralph` run looks like from GitHub (one PR, the `ralph
  learnings` issue, the `learning` label), and that half the issues take it during the trial.
  No IP, path or token.
- `docs/agents/invariants-merge.md`: one row at the top: a change to a shared merge node needs
  a decision about its `ralph` copy, and R10 fails until one is made.

## Acceptance

- `make check` passes.

## Depends on

01-10.
