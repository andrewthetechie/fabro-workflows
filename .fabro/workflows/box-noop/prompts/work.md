# box-noop's one stage.
#
# This file is a placeholder for real work, and it is deliberately long-running: the run is
# there to be started in a coder loop's gap and cancelled when the loop needs the box back.
# It must therefore be safe to kill at any point (D9): it writes nothing to GitHub, edits no
# file, and leaves no state that a rerun would have to clean up. A later job kind replaces
# this prompt, not this shape, and its last node writes its marker first.

You are a background run on an idle coder box. Nothing you do reaches a human, a pull
request or an issue. There is no deliverable to submit.

Walk the repository's tracked files and write, for each one you have not yet covered, a
single line:

    path/to/file — one sentence on what it is for

Work through them in the order `git ls-files` prints them, in batches of twenty, so that the
work is roughly proportional to the number of files and a cancelled run has still produced
something. Do not summarize, do not propose changes, do not open a pull request, and do not
comment on anything. If you reach the end of the file list, start again from the top and add
one more sentence per file about how it is used.

[No output is submitted for this stage. Do not call `submit`.]
