# The mechanical graph invariants are checked offline, from our own DOT reader

**Status:** proposed (2026-10-10). It becomes accepted when `make check` runs
`ops/check-graph-invariants.py` on every push and `make check-host` shows its reader agrees
with `fabro parse` on all six `.fabro` files. The plan is `docs/recheck-follow-ups/`, tasks
06 and 07, and issue #12.

`docs/agents/invariants-graph.md` and its siblings list rules that pass `fabro validate` and
fail at runtime. Some of them are fixed syntactic patterns: a graph attribute that must be
set, a matcher that must be anchored, a character that must not appear in a script. Prose is
their only enforcement. Issue #7 shows the cost. "A root graph that runs an imported phase in
a loop sets `loop_restart_signature_limit`" was already a row and a research note, `backlog`
still lacked the setting, and run `01M4G6C8MW` lost 6.4 hours of work to the breaker on
2026-10-09.

We decided that **each mechanical invariant gets a check in `ops/check-graph-invariants.py`,
which `make check` runs before every push**. The check reads the graphs with a small DOT
reader written in Python's standard library, not with `fabro parse`. `make check-host`
compares that reader's output with `fabro parse` on every `.fabro` file, statement by
statement. When a row has a check, its text in the invariants file shrinks to one line that
names the check. The incident history stays in the deployment log.

## Why our own reader

`make check` must run on the Mac with no fabro binary and no LAN (`ops/check.sh`), because
the pre-push hook runs it. `fabro parse` exists only in the container. A check that needs the
host would join `make check-host`, which is opt-in, and the push that broke #7 would still pass.

The DOT subset in these graphs is small: `digraph`, `graph [...]`, node and edge statements,
edge chains, quoted strings whose only escape is `\"`, and `//` comments. No graph uses
`subgraph`, node or edge defaults, ports or HTML strings, and the reader refuses them, so a
new construct fails the check instead of being misread. A tokenizer that knows quoted strings does not have the problem that
`ops/check-routing-schemas.py`'s header warns about, slicing node blocks with a regex. A
scratch reader of about 90 lines reproduces `fabro validate`'s node and edge counts for all
four packages (`docs/recheck-follow-ups/measure-2026-10-10.txt`).

The reader can drift from fabro's parser. The parity gate in `make check-host` catches that
drift. It compares graph attributes, node ids, node attributes with their exact string
values, and edge chains. A reader bug therefore shows up the next time someone runs the host
gates. It cannot hide a violation indefinitely.

## Rule exceptions are part of the rule

Two of issue #12's rules, read literally, fail on deliberate choices:

- **Stall timeout.** `backlog`'s `coder` (180m) and `human_rescue` (4h) are above
  `stall_timeout="60m"`. An agent node emits an event for every stream delta, and the
  watchdog parks while a run waits on a human, so neither can be cancelled by the watchdog.
  The check covers command nodes only.
- **Terminal reports.** `pr-review` hooks only `report_merged`, by operator decision 13 in
  its `workflow.toml`. The check holds a per-graph table of the report hooks each graph must
  have. It does not require all three everywhere.

Each exception is written in the checker next to the rule, with the reason and the source.
A new exception is a change to the checker that review can see. It is never an
allowlist comment in a graph.

## Considered options

- **`fabro parse` in `make check-host` only.** Rejected: it is opt-in, and the issue it
  answers came from a push that no host gate saw.
- **Commit the parsed ASTs and check them offline.** Rejected: an AST snapshot is a second
  generated artifact to keep in step, and drift between the snapshot and the graph is the
  same gap with a new name.
- **A third-party DOT library (pydot, networkx).** Rejected: `make check` needs only
  python3.11's standard library, and the libraries parse full DOT, including escapes and HTML
  labels that fabro does not accept.
- **Regexes over the DOT text.** Rejected for the reason in `ops/check-routing-schemas.py`:
  `if [ ... ]` inside a script looks like a node declaration.

## Consequences

- The routing-schema rule moves into the new checker, so it runs offline too.
  `ops/check-routing-schemas.py` stays in `make check-host` until the parity gate has passed
  once. Then it is deleted.
- Today's graphs break three rules (`measure-2026-10-10.txt`): `issue-triage` has no breaker
  limit, backlog's `discord-rescue` matcher is unanchored, and five scripts hold `#` comments,
  plus one line-continuation backslash. The checker lands green only after those are fixed.
- A new graph, a new import or a new report hook must satisfy the checker before it can be
  pushed. This is intended, and it is the reason for the per-graph tables.
- Rules that are not syntactic stay in prose: "reset per-iteration context keys", "delete a
  contract file before the agent that writes it runs", timeouts derived from a model's
  measured speed. The checker does not claim them.
