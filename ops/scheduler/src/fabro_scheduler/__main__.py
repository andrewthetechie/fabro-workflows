"""`python -m fabro_scheduler` — what the container runs.

The console script `fabro-scheduler` is the same entrypoint; the module form is
what the Dockerfile's `CMD` uses, so the image needs no installed script on
`PATH` to be runnable.
"""

from fabro_scheduler.app import main

if __name__ == "__main__":
    raise SystemExit(main())
