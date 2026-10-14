# Tools for code questions

These tools answer a code question in one call and return only what you ask for. A
`grep` plus a `read_file` of the matching file costs far more.

| You want | Use |
|---|---|
| where a function, class or variable is defined | `code_def` NAME |
| the source of one function or class | `code_show` NAME, or `code_show` path:line |
| who calls a function or class | `code_callers` NAME |
| every match of a regex, grouped under the function or class it is in | `code_search` PATTERN [path] |
| what a change to a symbol could break | `code_impact` NAME |
| the tests that import a file | `code_tests` PATHS |
| whether a failure existed before your change | `baseline_check` COMMAND |
| to undo your change to one file | `restore_file` PATH |
| whether your change passes its tests | `run_tests` TARGET [paths] |
| what the gate will run for your change, and whether it passes | `task_check` |

Run `task_check` before you finish: it runs the checks and the tests the Task validation
gate selected for your diff, so you fix what the gate will report.

If the shell says a test needs a database or a service, use `run_tests`; the repository
declares how to start it.

If a tool above is not in your tool list, the shell has the same verbs:
`fabro-code def|show|search|callers|impact|tests ARGS`, and for the two test tools
`fabro-test TARGET [paths]` and `fabro-test --task`.

`grep` and `rg` are right for text that is not a code name: a string, a config key, an
error message. The index knows calls and imports by name only, so confirm with
`grep -rnw NAME` before you conclude that something is unused.

Read a range, not a whole file: `code_show path:line`, or `read_file` with `offset`
and `limit`.

Do not run git commands that change files, the index or branches (`stash`, `checkout`,
`restore`, `reset`, `commit`, `fetch`). To undo your change to a file, use
`restore_file`. To see whether a failure predates your change, use `baseline_check`.
The one exception: if your task prompt says you are the git exception (a merge or
rebase to finish), run the git commands it lists.
