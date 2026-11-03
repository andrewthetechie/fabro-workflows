# The scheduler pauses dispatch on low disk, sampling the fabro volume

**Status:** accepted (2026-11-03, issue #51)

On 2026-10-19 the host filled. From 21:30Z until the sandboxes were cleared at
22:25Z the scheduler kept claiming issues every 5-6 seconds; every dispatch failed,
and every failure left the issue on `agent-in-progress` with no run behind it. The
failure was `OSError: [Errno 28] No space left on device` from
`tempfile.mkdtemp` in `workflow_version.clone_fabro_tree`, which `_dispatch` did not
catch. Six writers-app issues were orphaned until a restart's receipt recovery
requeued them.

Two decisions, from the issue:

**1. A free-space gate before the receipt.** `DispatchLoop.tick` samples the disk
first and, below the threshold, dispatches no new coder or background run. The
threshold is absolute free space, not a percentage: a writers-app run can write
25-60 GB, so `SCHEDULER_DISPATCH_MIN_FREE_GB` defaults to 50 GB. Dispatch resumes
only above 50 + 10 GB (`SCHEDULER_DISPATCH_DISK_HYSTERESIS_GB`), so a disk parked at
the line does not flap the loop. A transition logs once, not once per tick.

The scheduler container cannot see the host's `/`. Compose mounts the
`fabro-storage` named volume read-only at `/fabro-storage`, and the gate samples that
path plus `tempfile.gettempdir()`, taking whichever has the least free space. A
container's writable layer, the fabro volume and Docker's overlay store all live on
the host's Docker data-root filesystem; two paths because a later deployment could
split them. Free space is `os.statvfs(path).f_bavail * f_frsize`, and a path that
cannot be read is skipped (with one warning) rather than failing closed.

The gate stops *new* runs only. The producers still queue work; leases, release,
receipt recovery and the background lane's finish/evict passes keep running, so a
run already on a box is recorded when it ends. `POST /api/dispatch-once` is not
gated: a hand-fired run is the operator choosing to spend, as it is for the peak-hours
block, and the gate's state is on `/health` and the page so that choice is visible. The state is on `/health`'s `disk`
field and the queue page; `/api/pools` stays the list the monitor reads. C8
`scheduler-wedged` reads `disk.gated` and reports the pause instead of paging a
human for a condition that is already self-repairing.

**2. A pre-run failure always restores the labels.** `_dispatch` already rolled
back on `FabroError` and `WorkflowVersionError`; it now catches any other `Exception`
before a run exists — the `OSError` above is the canonical one — and does the same,
with `failed_stage=create`. `RunNotStarted` stays separate: by then the run exists,
so it is reported with its id and left for a human.

Rejected: mounting the host `/` read-only (broader than the gate needs and a new
host-coupling); a percentage threshold (50 GB of a 914 GB disk is not a useful
percentage, and one large run can consume a surprising share of it); and asking a
command node for the host's `df` (the scheduler is a separate container and should
not need a host-side helper to decide whether it can start a run).
