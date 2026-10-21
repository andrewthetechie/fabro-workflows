# 08. Learnings

## Outcome

Every ending of a run that had at least one Iteration passes through `learn_prep`, `learn`
and `learn_post` before `exit`. A run's surviving Learnings are one comment on the target
repository's open `ralph learnings` issue. Nothing about it can fail or change the run's
ending.

## Why

ADR 0029 D5. 00: O19, O25, O26, C9.

## Change

### Nodes

- `learn_prep` (command, routing, `timeout="1m"`): C9. It records the ending: the id of the
  node that routed here, which it reads from `/tmp/fabro/ending` (each terminal node's
  replacement or a one-line append writes its own id there; `close_noop`, `return_to_triage`,
  `mark_stuck` and the four merge-stage reports). Publishes `learn=run` or `learn=skip`.
- `learn` (agent, `.learn`, `timeout="10m"`, `max_retries=0`). The comment says why no retry:
  it is optional, and a retry holds the box for nothing.
- `learn_post` (command, `timeout="2m"`): C9. It prints nothing to stdout but its log lines;
  it has no routing schema; every path exits 0.

### Edges

```
close_noop -> learn_prep
return_to_triage -> learn_prep        [condition="outcome=succeeded"]
mark_stuck -> learn_prep
report_merged -> learn_prep           (and report_blocked, report_queued, mark_needs_human)
learn_prep -> learn                   [condition="context.learn=run"]
learn_prep -> exit
learn -> learn_post                   [condition="outcome=succeeded"]
learn -> learn_post
learn_post -> exit
```

`return_to_triage -> mark_stuck` (its failure edge) stays. `exit` is reached from
`learn_prep` and `learn_post` only.

### `prompts/learn.md.j2`

- You read one finished run: its progress log, its Refute rounds, which Tasks needed many
  Iterations or a stronger model, and how it ended.
- Write at most 5 rules a future run in this repository should follow. A rule is about the
  repository or about how `ralph` should work, never about this issue.
- Each needs evidence: the Task, the Iteration or Refute round, and a `path:N` or the
  command that failed. No evidence, no rule. An empty list is a good answer.
- `home`: where the rule would live if a human accepts it.
- Submit with `submit`.

## Acceptance

- `make check` passes. Every terminal node reaches `exit` only through `learn_prep`.

## Tests

`ralph — learn_prep`: `iterations` 0 → `skip`; 3 → `run` and the inputs file holds the
ending and the Task table.

`ralph — learn_post` (`gh` stubbed): no file → no `gh` call; an entry without evidence is
dropped; an open issue with the marker → one comment and no create; none → label create,
issue create with the marker, then one comment; `gh` failing → exit 0, one stderr line.

`ralph — edges`: a reachability check that every node with an edge to `exit` is `learn_prep`
or `learn_post`.

## Depends on

07.
