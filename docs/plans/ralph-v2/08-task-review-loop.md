# Task 08: the Task review loop

Read `00-overview-and-contracts.md` first (E7-E13, C1, C3, C4, C5, *Edges*). **Invoke
`/fabro-workflow`.** Read `ops/fabro-io/src/stageend.rs`, and `local_review_prep`,
`local_review_gate` and `refute_gate` (its evidence rule) in `ralph`'s graph.

## Steps

1. Turn on what task 07 left off: C1 `pick` step 3 and the `loop=review` edge; `record` sets
   `reviewing` and prints `review_due`.
2. The eight nodes: `task_review_prep`, `task_refute`, `task_refute_check`, `task_refute_fb`,
   `task_review`, `task_review_check`, `task_review_fb`, `task_review_gate`, with the attributes in *New
   nodes*, C3, C4 and C5, and the edges in *Edges*.
3. Manifest stages from task 06, Sealed lists from C3. R18's hook lines gain `task_refute` and
   `task_review` (box stages only, sorted: `iterate_t1 learn local_spec local_standards plan plan_review
   task_plan task_refute task_review`). The `io-guard` matcher gains the four stages.
4. `task_refute_check` and `task_review_check`: one program, three names changed.
5. `pick`'s context render gains `## Review findings` (C2).
6. Checker: **R19** in `ops/check-graph-invariants.py`, for `ralph-v2` only, documented in the file header
   ("R1-R9 and R11-R19"): each of `task_refute` and `task_review` has `max_retries=0`, a `<id>_check` command
   node and a `<id>_fb` agent node with `max_retries=0` whose class resolves to a hosted model; every edge
   out of the stage and its `_fb` goes to the check; and the check has an unconditional edge that is not a
   loop back. Tests for each violation.
7. `check-host.sh` baseline: `68 nodes, 175 edges`.

## Pitfalls

- The reviewers run after `autofix` and `check`, whose checkpoints are committed: every diff is between
  commits. Never diff the working tree.
- `max_retries=0` on all four. With 1, one "try" of C4 is two sessions.
- A check node waits up to 10 minutes inside a command: `curl -m 5`, `sleep 15`, 40 polls at most, and the
  node's 15m timeout is under `stall_timeout` (R2).
- Reading `state/K.served` is the only way the gate learns that the first reviewer's result is valid.
- A hosted `_fb` stage must not claim a box: `box: false`. `coder_box_stages` must list the two box
  reviewers and not the two twins (assert it in `test_fabro_io_manifest.py`).
- A review result holds arbitrary text. Pass it through files and `--slurpfile`, never through an
  argument (128 KiB cap) and never to stdout.

## Tests (`test_ralph_v2.py`)

The checks, for both names: every row of C4's table; `_fb`'s own receipt; a stale result of an earlier
round; a result that a timed-out session had submitted (valid); `curl` absent; `FABRO_BOX_HEALTH` empty;
no pair for the pool; unhealthy for the whole wait (stub `sleep` and `curl`); the two programs are equal
but for the three names.

The prep: first and second review inputs; `delta.patch` after a rework that missed once; no patch; a
patch over the limit; `history.md` with fixed, disputed and unanswered findings; every key on every path.

The gate:

- evidence rule: file missing, evidence empty, `path:N` of a missing file, backticks only, both;
- `met=false` with and without evidence; an index outside the list; an uncovered edge case;
- disputes: withdrawn; upheld with new evidence; upheld with the same evidence in other case and spacing;
  upheld with evidence that fails the rule; no response; the owning reviewer missed; an answer in the wrong
  reviewer's `responses` (ignored);
- repeats: a dropped finding raised again in the same round and in a later one;
- verdicts at `n` = 1 to 4; warnings alone pass; both missed; one missed; a missing `served` file;
- ids unique across rounds and sources; `rounds/` written; `findings/` deleted on a pass; `progress.md`;
- `block_reason` cut at 200.

In real order, with the real scripts: plan -> work -> review (error) -> rework -> review (dispute upheld) ->
rework -> review (clean) -> next Task; four rejected reviews -> blocked -> exhausted; a gate that crashes
once, then `pick` recovers; a gate that crashes four times, then the Task is passed with its note; a
mainline conflict while a Task is `reviewing`; a Task whose stored plan is a fallback; `phase=post` skips
review; and a run whose last allowed Iteration is reviewed and reworked.

## Done when

- `make check` passes, and the v2 tests pass in the bookworm container. Counts are **68/175**.
- The generated `ralph-v2` manifest is under 120,000 bytes (report the number).
- Commit: `feat(ralph-v2): box Task reviews with rework, disputes and a retry (ralph-v2 08)`.
