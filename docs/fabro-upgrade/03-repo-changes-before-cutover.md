# 03 · Repo changes that are valid on both versions (land them before cutover)

## Outcome
Two changes reach `main` and the host **while 0.354 is still running**, so the cutover in task
04 has less to do:

1. `ops/settings.toml.example` describes the 0.362 overlay: contract C1 (the `codecs` shape)
   and contract C2 (`[run.git.author]`).
2. **H6:** the coder scheduler reads each run's task progress through fabro's API
   (`GET /api/v1/runs/{id}/sandbox/file`) instead of `docker exec` over the Docker socket.
   The socket mount, the `docker` group and `fabro_scheduler/docker.py` are removed. It is
   deployed to the host and working against 0.354. The endpoint exists, unchanged, in both
   versions.

## Why
Decision 6 includes H6. Landing it now proves it against a known-good server, so if the page
misbehaves after cutover the cause is the upgrade and not this change. Mounting the host's
Docker socket is root-equivalent access, and the only reason the scheduler had it was one read
of `/tmp/fabro/tasks.json` (`ops/docker-compose.yaml`, the comment on the scheduler's
`volumes`).

## Read first
`00-overview-and-contracts.md`: contracts C1 and C2, rules 1, 2, 3 and 6. `ops/README.md`,
"The coder scheduler" → "Deploying it". `AGENTS.md`, "Deploying to the server after a merge to
`main`" (the scheduler block).

## Part A: `ops/settings.toml.example`

- Rewrite the `kimi`, `box-a`, `box-b` and `spark` provider tables to C1's shape. Keep every
  model row and comment, but update any comment that names `adapter = openai-compatible` or
  `codec = ...` (around the `# Kimi is Anthropic-shaped` comment, lines ~168–175 today).
- Append C2's `[run.git.author]` block, with a two-line comment: from 0.362 fabro derives one
  identity per run and fails the run at setup if it cannot, and setting both fields skips the
  lookup (overview, behaviour change 4).
- Add one comment above the `[llm]` section: *"0.362+: `codec` and the protocol adapter ids
  (`openai-compatible`, `anthropic`, …) are rejected; a cold start refuses to boot. Use
  `codecs = [...]`, which defaults to `["openai-chat"]`."*
- Check: `python3.11 -c 'import tomllib;tomllib.load(open("ops/settings.toml.example","rb"))'`.

**Do not** touch the live overlay in this task. That is task 04.

## Part B: H6, the scheduler reads the sandbox through the API

### What exists today (verified 2026-09-25)
- `ops/scheduler/src/fabro_scheduler/probe.py` builds `TASKS_CMD` (a `sh -c` running
  `jq -r length /tmp/fabro/tasks.json` and `cat /tmp/fabro/task_index`) and runs it with
  `DockerClient.exec(container_id, TASKS_CMD)` in `_task_progress()`. The container id comes
  from `GET /runs/{id}` → `.sandbox.instance.runtime.id`, cached per run in `_sandbox_ids`.
  `completed = clamp(task_index − 1, 0, total)`. A missing `tasks.json` yields
  `(None, None)`, shown as "—".
- `fabro_scheduler/docker.py` is the Engine-API client over the socket. It is used only by the
  probe, and `app.py:64,162` constructs it.
- `ops/scheduler/Dockerfile` adds a `docker` group (`ARG DOCKER_GID=983`) so the non-root user
  can open the socket.
- `ops/docker-compose.yaml` mounts `/var/run/docker.sock:/var/run/docker.sock:ro` on the
  scheduler, with a long comment explaining why.
- Tests: `ops/scheduler/tests/test_probe.py` imports `DockerClient` and `decode_docker_stream`.

### The endpoint (identical in 0.354 and 0.362 OpenAPI)
`GET /api/v1/runs/{id}/sandbox/file?path=<path>` returns `200` with the file contents as
`text/plain`, `404` when the run or file is not found, and `409` when the run has no active
sandbox. **Verify the path semantics live before coding against them:** with a `backlog` run
in flight on 0.354, call it with `path=/tmp/fabro/tasks.json` using the scheduler's token. If an
absolute path is refused, try the path relative to the sandbox working directory, and record
which form works. The probe's docstring must name that verified form.

### The change
1. `fabro.py`: add `read_sandbox_file(run_id, path) -> str | None`. It returns the body on 200,
   and `None` on 404 or 409. Anything else raises `FabroError`, the same as other calls. URL-
   encode `path`.
2. `probe.py`: `_task_progress()` makes two calls, `tasks.json` then `task_index`. Parse
   `tasks.json` with `json.loads`, and `total = len(array)`. A non-array, unparsable or `None`
   result gives `(None, None)`. Parse `task_index` as an int, defaulting to 0. Keep the
   clamp. Drop `_sandbox_ids` and the `sandbox.instance.runtime.id` lookup, because the API
   is addressed by run id. Delete `TASKS_CMD`. Update the module docstring, which describes the
   `docker exec` design, and keep its interval reasoning (about 2 API calls per leased box per
   beat now becomes about 3).
3. Delete `fabro_scheduler/docker.py`. Remove the `DockerClient` import and construction in
   `app.py`, and the probe's `docker` constructor parameter.
4. `Dockerfile`: remove the `DOCKER_GID` arg, the `docker` group and `gpasswd`, and the
   "no docker CLI" and socket comments that no longer apply.
5. `ops/docker-compose.yaml`: remove the scheduler's socket mount and its comment. **Leave the
   `fabro` service's own socket mount alone**: fabro needs it to create sandboxes.
6. Tests: replace `test_probe.py`'s Docker fakes with a fake fabro client that serves
   `read_sandbox_file`. Cover: pre-decompose (404 → "—"), no sandbox (409 → "—"), a normal
   count, `task_index` missing, a malformed `tasks.json`, and the clamp. Run the whole suite:
   `cd ops/scheduler && uv run pytest -q`.
7. `ops/README.md` "The coder scheduler": remove the socket and `DOCKER_GID` mentions.
   `AGENTS.md`'s scheduler deploy block mentions no socket, so leave it.

### Deploy and verify (host, 0.354 still running)
Follow `AGENTS.md`'s scheduler block exactly: `rsync` `ops/scheduler/` to
`~/fabro/scheduler/`, `scp` `ops/docker-compose.yaml` to `~/fabro/`, then
`cd ~/fabro && docker compose up -d --build scheduler`. **Name the service** (rule 3). Then:
- `docker inspect fabro-scheduler --format '{{json .Mounts}}'` shows no `docker.sock`.
- The queue page (`http://10.10.0.32:32280/`, its "Coder instances" table, rendered from the
  probe's `run_progress` view in `app.py`) shows a task fraction (for example `3/7`) for any
  leased run that has decomposed, within one probe interval (30 s).
- `docker logs --since 2m fabro-scheduler` has no `run-probe beat failed`.
- `docker inspect -f '{{.State.StartedAt}}' fabro-fabro-1` is unchanged.

## Acceptance criteria
- Both parts committed on `main` (two commits), with the scheduler suite green and
  `./ops/test-task-gates.sh` still passing (unchanged count).
- The scheduler is running on the host without the socket and shows task progress against
  0.354.
- `diff <(ssh andrew@10.10.0.32 cat ~/fabro/docker-compose.yaml) ops/docker-compose.yaml`
  prints nothing.

## Report
The verified `path` form, the test count before and after, and the page's Coder-instances row
showing a live fraction.
