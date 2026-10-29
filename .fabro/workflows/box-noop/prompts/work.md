# box-noop's one stage.
#
# This file is a placeholder for real work. It asks for a bounded piece of text, about five
# minutes of box time (the scheduler's `typical_box_minutes` for `box-noop`; measured median
# 3.5 min, 2026-10-29), so a run that
# nobody cancels ends `succeeded` well inside `work`'s 30m timeout, and a cancel in a coder
# loop's gap is a measurement rather than the only way the run can end.
# It must be safe to kill at any point, because a coder loop's claim cancels it whenever
# the loop needs the box back (D9): it writes nothing to GitHub, edits no
# file, and leaves no state that a rerun would have to clean up. A later job kind replaces
# this prompt, not this shape, and its last node writes its marker first.

You are a background run on an idle coder box. Nothing you do reaches a human, a pull
request or an issue. There is no deliverable to submit.

Walk the first 150 of the repository's tracked files and write, for each one, a single
line:

    path/to/file — one sentence on what it is for

Take the files in the order `git ls-files | head -n 150` prints them, and work in batches of
twenty, so that a cancelled run has still produced something. Read only as much of each file
as you need for its sentence. Do not summarize, do not propose changes, do not open a pull
request, and do not comment on anything. When the last of those files has its line, stop:
the stage is done.

[No output is submitted for this stage. Do not call `submit`.]
