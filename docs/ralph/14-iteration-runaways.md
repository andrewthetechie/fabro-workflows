# 14 — What an Iteration that runs out of time is doing (#63)

Issue #63: on writers-app, 14.1% of `iterate_t1`'s attempts timed out (36 of 256) against
2.3% on lawncare-saas and flakeradar and 5.2% on womens-fantasy-sports. #62 kept the 60m cap
and asked this issue to name the cause from the events, then ship and measure one change.
This is the record of that reading, the change (`iterate.md.j2`'s check cadence), and the
measurement that will judge it.

Measurements were taken on 2026-11-08 from `fabro events <run> --json` over every `ralph`
run dispatched 2026-10-22..11-03, with `ops/iterate-attempts.py`; the output is
`result-2026-11-08.txt`.

## The sample, and the reproduction

| | runs | attempts | failed |
|---|---|---|---|
| writers-app | 34 | 239 | 33 (13.8%) |
| flakeradar | 29 | 86 | 2 (2.3%) |

flakeradar is the control for "is it the Rust workspace": it is also Rust plus a TypeScript
front end (172 `.rs`, 44 `.tsx`), it runs the same workflow with the same Task contract, and
its rate matches the 2.3% the issue measured. The reproduction differs from the issue's
number (13.8% against 14.1%) only by the boundary: the issue's window ended 2026-11-03, and
the sample here also includes the runs created on that day (291 attempts, 44 failures).

The unit is an **attempt**, not a visit. A timed-out visit emits `stage.started`,
`stage.failed` (`system_actor: timeout`), `stage.retrying`, then another `stage.started` under
the same `stage_id` with `attempt: 2`. Keyed by `stage_id` alone — which is what
`ops/fabro-agent-tools.py` does — the retry's `stage.completed` overwrites the timeout and
the visit reads as one success. Keyed by `(stage_id, attempt)` the timeout is its own row
with no `stage.completed`, which is what it is. Of the 33 failures, 30 are timeouts; the
other 3 are context-length failures discussed at the end.

## 1. What the last 30 minutes are

Not any of the three shapes the issue guessed at. Over the 33 failed attempts, the last 30
minutes of each held: 1,064 `shell` calls, 359 `edit_file`, 352 `read_file`, 71 `write_file`,
59 `run_tests`, 16 `task_check`, and 55 model turns (p50 per attempt, range 7-171). No
attempt has a tool-free turn at the end (p50 0), no file is re-read without being edited
afterwards (p50 4 re-reads, p50 7.5 edits in the same half hour), and 5 `restore_file` calls
in the whole sample undo probe edits. They keep working at the wall.

Two read end to end, because the two shapes are different:

- **writers#1184** (2026-10-25, `iterate_t1@4`), a passage-level reveal in the bible editor.
  The last 30 minutes are a build-and-drive loop: `cargo tauri build --debug --no-bundle`
  (15 times across the attempt, 4.3 min in builds), `/tmp/run_diag.sh` — a webdriver pilot
  that launches the app and prints probe values — 39 times (2.7 min), instrumentation
  `write_file`s into `/tmp/diag*.toml`, and it ends with two `restore_file` calls undoing its
  own probes. It ran no check at all in 60 minutes.
- **writers#1182** (2026-10-28, `iterate_t1@6`), a spellcheck measurement Task. 197 turns and
  76 edits in 60 minutes, 35 of them on `spellcheckStore.test.ts`, and a
  `__measure_1182.test.ts` it wrote, ran and rewrote 10 times; the last command deletes it.
  This one *has* a rhythm — 9 checks — and overruns anyway: 76 edits is 5× the p50.

So a timeout is not one pathology. What separates the two classes is not what the failed
attempts do at the end but how little they check on the way there.

## 2. Failed and passed attempts side by side (writers-app, p50)

| metric | failed (33) | passed (206) |
|---|---|---|
| duration | 60.0 min | 17.9 min |
| model turns | 126 | 67 |
| tool calls | 127 | 72.5 |
| minutes in tool calls | 3.2 | 3.4 |
| minutes the model was generating | 55.0 | 13.5 |
| reasoning tokens | 117,285 | 27,241 |
| output tokens | 24,078 | 10,672 |
| peak context | 208,032 | 104,746 |
| context compactions | 1 | 0 |
| turns longer than 60 s | 15 | 3 |
| checks (`run_tests`/`task_check`) | 1 | 5 |
| minutes spent in checks | 0.0 | 1.6 |
| first check, minutes in | 16.8 | 6.7 |
| edits | 15 | 12 |
| edits since the last check | 4 | 0 |

The box is not the bottleneck: 55 of the 60 minutes are the model generating, and the tool
minutes are the same 3 as in a passing attempt. What the failed attempts have that passing
ones do not is 4× the reasoning tokens and 15 turns over a minute, three of which are five
minutes long (p90 gap 79 s against 37 s). The 5-minute turns are prefill and queueing at
198k-208k context: turn latency p50 grows from 7 s under 50k to 14 s over 200k, and p99 from
101 s to 170 s. Context growth is the reason the last half hour is half as productive as the
first, not the reason the attempt started.

## 3. Is it the repository's size, or the Task's? (question 2)

No. The Task the failing attempts were given is the same size as the one the passing
attempts got, and as flakeradar's:

| Task shape, p50 | writers failed | writers passed | flakeradar passed |
|---|---|---|---|
| `.files` | 1 | 1 | 2 |
| `.acceptance` items | 3 | 4 | 4 |
| `.context` bytes | 2,581 | 3,239 | 3,533 |
| `inputs` the session loaded | 23.4 KB | 24.5 KB | 28.6 KB |

A Task-size rule in `plan` for Rust crates has nothing to bite on, which is what the issue's
own finding said from the other direction (9 of the 22 planned Tasks that timed out named at
most one file). The context a session starts with is small either way; what fills the window
is the session's own work.

Compaction, too, is neither necessary nor sufficient: 21 of the 33 failures never reached
fabro's 80% threshold (12 did, against 7 of 206 passes). Where it does happen the tail is
worse — after compacting, an attempt re-reads a file it already read 4 times (p50) and edits
one it already edited (mean 3.9), and its checks nearly stop (p50 0.5 in the tail).

What *is* different about writers-app is the length of the loop: a passing attempt takes 17.9
minutes and 67 turns against flakeradar's 5.4 minutes and 33. Same Task shape, twice the
turns, three times the time — a Rust compile plus vitest plus, on the UI Tasks, a Tauri build
and a driven app. A longer loop at the same speed puts 15% of the attempts over 60 minutes
where a 5-minute loop puts 2-3%.

## 4. Does `task_check` give a slow or unclear signal? (question 3)

It is slower — 48.9 s p50 and 86.6 s p90 over 497 calls, against flakeradar's 16.2 s and
26.5 s over 155 — but not so slow that a session cannot afford it: 49 s is 1.4% of an hour,
and a passing attempt spends 1.6 minutes in its 5 checks. The events say the failing attempts
do not *use* the signal:

- 12 of the 33 ran no check at all;
- 21 of the 33 ran at most one;
- at the wall their last check was a median of 4 edits and a p90 of 21 edits behind them;
- and every passing attempt ends on its check: 274 of 290 have zero edits after their last one.

The one place `run_tests` does give a bad signal is flag rejection: 40 calls in the sample
came back `target 'rust' does not allow --test` (or `--lib`, `--nocapture`,
`--all-features`), and 49 came back `unknown tool 'run_tests'` — the bare name when only
`mcp__io__run_tests` is registered. Neither is the cause of an hour-long attempt, but a
rejected check is a check that did not happen; the count of both is worth watching after the
change.

## 5. The cadence predicts the wall

Longest run of edits with no check in between, both repositories, 325 attempts:

| check-free edit run | attempts | failed | share |
|---|---|---|---|
| 0 (no edits) | 5 | 2 | 40.0% |
| 1-2 | 60 | 3 | 5.0% |
| 3-4 | 59 | 2 | 3.4% |
| 5-9 | 105 | 9 | 8.6% |
| 10 or more | 96 | 19 | 19.8% |

The 0 row is the read-only failure: an attempt that cannot find the code reports `progress`
with no edit at all. Above it the share doubles from 3-4 to 5-9 and again from 5-9 to 10+.

This is a correlation and is written up as one: a session near the end tests, and a session
that is lost does not. The mechanism it is a proxy for is concrete, though. Without a check
the only bound on "done" is the model's own judgment, and a turn that neither checks nor
finishes is a turn spent re-reading and re-editing what it already changed — which is exactly
what the tails of these two attempts are.

## 6. The change

`.fabro/workflows/ralph/prompts/iterate.md.j2`, section 2, one rule:

> **Ration the edits against checks.** Count the edits you make since your last `task_check`
> or `run_tests`. At five, run the check before the sixth and work from its answer: a red one
> names the next edit, and a green one over the acceptance list means the Task is finished. An
> unbroken run of edits is not progress you can see. If the same failure comes back red twice
> with nothing to show between the runs, stop and submit `progress` naming it: the next
> Iteration starts from your tree and reads it under `## Last attempt`.

and one clause in section 3: a `done` submission ends on that green check, not on an edit.

Why five: it is the last bucket before the share doubles (5-9 at 8.6%, 10+ at 19.8%), and it
is under the 15 edits a writers-app attempt makes at p50, so an ordinary attempt checks twice
and a runaway is cut at the point it starts to drift. At writers-app's `task_check` latency it
costs about a minute an hour.

It is a workflow change: nothing in `ops/` needs deploying, no image, no manifest field, so
it is live on the next fire that resolves `main`.

The second sentence — the same failure red twice — is the bound the current prompt lacks
entirely. Today a session can grind a red check for an hour; under the loop's design that is
what `progress` and `## Last attempt` are for.

## 7. What was not shipped, and why

- **A shorter `timeout`.** #62 rejected it and this sample agrees: 12 passed attempts ran
  45-55 minutes, and 4 passed after compacting.
- **A smaller context for this repository.** The inputs are already 23 KB and 21 of the 33
  failures never filled the window; there is nothing here to shrink that is worth the wish.
- **A Task-size rule in `plan` for Rust crates.** Contradicted: the Task shapes are identical
  across repositories and across the two outcomes.
- **Ending an attempt at its first compaction.** It would cut 32 of the 33 failures early,
  but it would also end 18 passing attempts at their first compaction, and the events cannot
  yet say which side is larger. Worth a trial once the cadence has been measured.

## 8. How this is judged (the issue's "done when")

Baseline, from this sample (2026-10-22..11-03, writers-app): failed share **13.8%**, checks
per attempt **1 failed / 5 passed**, edits since the last check **4 failed / 0 passed**,
passing duration **17.9 min**.

Two weeks after the ship, export the writers-app runs and recompute:

```sh
ops/fabro-export-runs.sh --since 2026-11-08 --workflow ralph /tmp/runs-63
python3.11 ops/iterate-attempts.py /tmp/runs-63/*.jsonl
```

where `/tmp/runs-63` is outside every git work tree, as that script requires (the files hold
issue text). Compare the same four numbers against the baseline, and against flakeradar's
2.3% over the same window.

The change worked if the failed share falls toward 2-3% and the passing duration stays at
17.9 minutes or below. A fall in the share with a rise in the duration is not a win: it would
mean the checks bought turns and nothing else. A rise in checks per attempt with no fall in
the share means the model did what the prompt asked and it did not matter, and the next
candidate is section 7's compaction rule.

## 9. A follow-up, not this change

Three of the window's 33 failures are not timeouts but fabro's context accounting, all in
writers#1188 (2026-10-26):

- `iterate_t1@12` fails after 2.8 minutes: `provider box-b the prompt is 1057502 tokens; the
  context holds 524288`;
- `iterate_t2@1` records a peak input of 681,732 tokens over 16 turns and 6 compactions —
  after compacting, the request grew;
- `iterate_t1@11` fails the same way at 19 minutes.

The stage reports a 262,144-token window, the box's model declares 524,288, and after a
compaction the next request was larger than the window in at least one case. That is a fabro
bug, not a `ralph` one, and it wants its own issue with these three run ids.
