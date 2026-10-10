# A new shared abstraction is decomposed skeleton first, and its ports use it unchanged

**Status:** proposed (2026-10-10). It becomes accepted when 10 or more writers-app
`architecture` runs after the change show lower draft-visit and over-10-minute first-edit
shares than `docs/coder-tweaks/result-2026-10-08.txt`, with M8 no worse and the **Task
budget** remainder rate reported. The plan is `docs/recheck-follow-ups/`, tasks 08 and 10,
and issue #5.

The local coder model writes **draft turns**: a turn before its first edit that holds more
than 2,000 output tokens and a code fence. In writers-app they cost the most. 45% of coder
visits drafted in the 2026-10-08 window, and the worst visit (01M4AT8SAY, writers-app#1024)
took 59 minutes to its first edit while it designed a generic Rust path-map core in
reasoning. The coder prompt rule from 667e5b4 shortened the tail everywhere, but it did not
stop these drafts.

`docs/recheck-follow-ups/measure-2026-10-10.txt` answers the question #5 asked first. The
drafts are concentrated, not spread across task kinds. Tasks that create a shared
abstraction (core) or move callers onto one (port) were 19 of 42 writers-app coder visits.
They had 13 of the 19 draft visits and 84% of the draft tokens. The ports had more draft
tokens than the cores, 48% against 36%. In 01M4AT8SAY the port drafted because its task
told it to change the core's interface, so it designed a tracing callsite in reasoning.
Where the core was small and the ports used it unchanged (01M4AJV4HT), no visit drafted.

We decided that **when a task creates a shared abstraction that two or more callers will
use, and the abstraction introduces a type, trait, interface or generic parameter, the
decomposition is skeleton first**:

1. **Core skeleton.** Write the core's types, traits and signatures with stub bodies
   (`todo!()`, `throw new Error("not implemented")`, `raise NotImplementedError`), and make
   the repository's checks pass. The body lists what each port needs from the core, so that
   every interface decision is made here, in a file the compiler checks.
2. **Core bodies.** Implement the bodies against the core's own tests.
3. **One port per task.** Each port moves one caller, or one group of callers, onto the core.
   It uses the core's interface unchanged. It may add a new item to the core when the port
   cannot work without it. It never changes an existing signature. A port that needs a
   different signature is a defect in the skeleton, and the task body says to record it in
   the summary rather than redesign.

A core that adds no new type, trait or generic parameter (a plain function, a moved helper)
keeps today's rule from `decompose.md.j2`: "add the module with its tests, then migrate the
callers".

## Where the rule lives

- `backlog/prompts/decompose.md.j2`, in the size budget, next to the existing "new module
  and migration" rule.
- `backlog/prompts/improve.md.j2`, in the `split` disposition. A split is 2 to 4 slices, so
  skeleton, bodies and two ports fit in one split.
- `_shared/triage/prompts/plan.md.j2` counts skeleton and bodies as **one** Task map entry
  and says that `decompose` splits it. The Task map's size decides whether triage creates
  **Child issues** (more than 4 tasks), and a rule that adds a task to every architecture
  issue would push some of them over that line. `decompose` is bound by the Task budget of 8,
  not by the split threshold.

No contract field changes. The skeleton task is an ordinary task with `covers` naming the
parent requirement it prepares.

## Considered options

- **Another coder prompt rule.** Rejected: the 667e5b4 rule already names this moment
  ("let me design a type"), and the drafts continued.
- **The split as filed in #5, cores only.** Rejected: it leaves the ports, which hold the
  larger share of the draft tokens, unchanged.
- **Route core tasks to a hosted model.** Not decided here. It is a stylesheet lever with a
  cost per run. It is the next step if this ADR's measurement misses.

## Consequences

- An architecture issue gets one more decomposed task when it creates a typed core. Task
  10 reports how often a run then reaches the Task budget and files a **Remainder issue**.
- The skeleton must pass the repository's checks with stub bodies. A crate that denies
  `dead_code`, or a TypeScript setup that fails on unused exports, needs the skeleton's items
  to be public or to carry the narrowest allow attribute. The task body says which, from
  the repository's `ci.sh`.
- The per-task `review` sees a skeleton whose bodies do nothing. It judges the task against
  the task's own acceptance criteria, which say "compiles, interface only, stub bodies". The
  task body must say this, or `review` fails it as incomplete. The extra review and the merge
  phase see the whole run's diff, in which the bodies task has already replaced the stubs.
- `hygiene` (ADR 0013) counts unlinked `TODO`/`FIXME` comments, not `todo!()` or
  `NotImplementedError`. The bodies task replaces the stubs, so a finished run's PR has none.
  A run that stops between the two tasks (the Task budget, `[P] Accept partial`) can open a
  PR that still holds stubs. Such a PR is not auto-merged when its issue carries
  `architecture` (`merge_gate` check 2b), and that is where these tasks come from. For any
  other issue, the Refuter sees criteria that are not met. Task 08's rule keeps skeleton and bodies
  next to each other in the task order. The budget can still fall between them when the
  skeleton is a run's eighth task, and task 10 reports whether that happened.
