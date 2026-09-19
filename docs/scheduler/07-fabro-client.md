# Fabro client: register a `.fabro`-rooted version, create and start a run

## Tracer-Bullet Outcome
`POST http://10.10.0.32:32280/api/dispatch-once` (a temporary manual trigger)
registers the `backlog` workflow version from `main`, creates a run for a named
issue pinned to a named coder pool, starts it, and returns the fabro run id. One
real run, launched by the scheduler, triggered by hand.

## User Story
As the operator, I want to prove the scheduler can launch a correctly-parameterised
fabro run before any automatic dispatch logic exists, so that draft 08's loop is
built on a mechanism already observed working.

## Description
Third vertical slice: workflow-version registration → run intent → start → run id.
No queue reading, no leases, no loop — the endpoint takes `repo`, `issue_number`
and `coder_pool` explicitly and does exactly one dispatch.

`/api/dispatch-once` is scaffolding. Draft 08 replaces its body with the real
loop; it is kept as a manual override.

## Context Pack
- Source decisions: overview decisions 7, 22 (verified rooting), 3 (the scheduler
  passes `issue_number`). Finding 2 (automations take no body).
- Repo facts: `.fabro/workflows/backlog/scripts/fire-pr-review.sh` is the working
  reference implementation of this exact three-POST sequence, fixed in draft 02.
  Read it before writing this — its comments record failures already paid for.
- Non-goals: the dispatch loop, leases, label writes, release, requeue. Draft 08
  and 09.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/fabro.py
  ops/scheduler/src/fabro_scheduler/workflow_version.py
  ops/scheduler/src/fabro_scheduler/app.py            (add POST /api/dispatch-once)
  ops/scheduler/tests/test_workflow_version.py
  ops/scheduler/tests/test_fabro.py
  ```

- Interfaces and names:

  ```python
  def build_version_payload(fabro_dir: Path, entrypoint: str) -> dict:
      """entrypoint is relative to fabro_dir, e.g. 'workflows/backlog/workflow.fabro'.
      Walks fabro_dir, reads every file as UTF-8, keys them by POSIX relative path."""

  def register_version(api: str, token: str, payload: dict) -> str:   # -> workflow_version_id
  def create_run(api: str, token: str, intent: dict) -> str:          # -> run id
  def start_run(api: str, token: str, run_id: str) -> str:            # -> lifecycle.status.kind
  def get_run(api: str, token: str, run_id: str) -> dict
  ```

- Verified external contracts (every status code and body below observed live
  against `http://10.10.0.32:32276/api/v1` on 2026-09-18):

  **1. `POST /workflow-versions`** — content-addressed and idempotent; unchanged
  content returns the same id.

  ```json
  {"entrypoint": "workflows/backlog/workflow.fabro",
   "files": {"workflows/backlog/workflow.fabro": "...", "...": "..."},
   "workflow_dependencies": {}}
  ```

  `201` → `{"workflow_version_id": "<64 hex chars>"}`.

  **The version must be rooted at `.fabro/` and the entrypoint must be the graph.**
  Both are verified failures otherwise:

  ```
  entrypoint = "workflow.toml"
    422  workflow.toml selects graph `workflow.fabro`, but the version entrypoint is `workflow.toml`
  rooted at the package dir, entrypoint = "workflow.fabro"
    422  invalid import reference in `workflow.fabro`: `../_shared/review-merge/review-merge.fabro`
  rooted at `.fabro/`, entrypoint = "workflows/backlog/workflow.fabro"
    201
  ```

  `WorkflowPath` forbids parent segments (`fabro-api.yaml:9296-9306`), so the
  shared graph cannot be carried at `../` — it must be a sibling inside the
  version. Limits: at most 512 files, 512 KiB per file, 2 MiB canonical JSON. The
  real `.fabro/` tree is 26 files / ~290 KB today.

  `workflow_dependencies` stays `{}` — it keys child-workflow **version ids** for
  `stack.child_workflow`, not `import=` graphs.

  **2. `POST /runs`** — creates but does **not** start. Body:

  ```json
  {"workflow_version_id": "<id>",
   "target": {"kind": "git", "repo": "andrewthetechie/jelly-swipe", "branch": "main"},
   "args": {"inputs": {"issue_number": 123, "coder_pool": "coders-a"},
            "labels": {"source": "scheduler", "issue": "123"}},
   "environment_id": "python"}
  ```

  `201` → `{"id": "<ULID>", "lifecycle": {"status": {"kind": "submitted"}}, ...}`.

  **`args.inputs` values are scalars only** — string, number, int, bool. A
  non-scalar is `422 run_intent_invalid`. **Numbers that reach a POSIX `case`
  guard must be JSON numbers, not strings**: `fire-pr-review.sh` records this for
  `pr_number`, whose graph does `case "$PR" in *[!0-9]*)`. Send `issue_number` as
  a JSON **number**. `args.labels` values are **strings**.

  `environment_id` must be one of the live ids, which carry `repo` labels matching
  `repos.toml`: `python`, `python-node`, `ts`, `rust-node`.

  **3. `POST /runs/{id}/start`** — `200` → body with
  `.lifecycle.status.kind`. Observed error codes from `fire-pr-review.sh`, all
  paid for in production: `401` token rejected, `404` no such run, `409` the run
  was not in `submitted`.

  A created-but-unstarted run **sits in `submitted` indefinitely and reports
  nothing** — verified by leaving one for 90s. Omitting the start call creates
  runs that never execute and never error.

  **4. `GET /runs/{id}`** — terminal detection reads
  `.lifecycle.status.kind`, which is terminal for exactly `succeeded | failed |
  dead` (`00-overview-and-contracts.md`, finding 9). There is no `cancelled`
  kind and no `errored` kind: a cancel arrives as `kind: "failed"` with
  `reason: "cancelled"`, and the event's `category` reads `"canceled"`, one L.
  `.lifecycle.queue_position` is **always `null`** in production and must not be
  used.

- Behavior rules:
  - Fetch `.fabro/` by shallow-cloning `andrewthetechie/fabro-workflows@main`
    (public, no credential), exactly as `fire-pr-review.sh` does. Cache the
    resulting `workflow_version_id` keyed by the clone's commit sha; re-register
    only when `main` moves. Registration is idempotent, so a redundant call is
    cheap, not wrong.
  - Enumerate from the directory, never a hardcoded file list — a prompt added
    later would otherwise fail at admission on an unresolved `@prompts` reference.
  - Non-UTF-8 content is a hard error, not a skip.
  - `args.inputs.issue_number` is **inert** until draft 10 collapses
    `acquire`/`claim`. `backlog`'s `acquire` still selects the issue itself and
    publishes `issue_number` as its own context update, so a run started by this
    draft works on whatever `acquire` picks, not the number in the request. The
    input is passed anyway, because it is the contract draft 10 relies on and
    because `run.settings.inputs` is where its arrival is visible.

#### Corrected against the live server, 2026-09-19

Two observations from the acceptance run, neither of which changes a decision:

* An unknown `environment_id` is a **`404`**, not a `422`. The body is flat —
  `{"detail":"environment `nope` not found"}` — not `{"errors":[{...}]}`. The
  criterion's point holds exactly (fabro's own sentence, not a generic error) and
  the client reads both envelopes, so the only thing that was wrong was the
  status code in this draft. The unit test keeps the drafted `422` body because
  that shape is real on other routes.
* The whole sequence was re-verified live: registration is idempotent across
  processes (the same 64-hex id for the same commit, from two different processes,
  and `version_reused: true` on the second call in one process), and the run
  reached `prep` about 30 seconds after the start call.
- Error and security rules: `FABRO_API_TOKEN` from the environment; never logged.
  Every non-2xx raises with the HTTP status and the response body's first error
  `detail`, because fabro's 422s carry the actionable message in
  `.errors[0].detail`.

## Acceptance Criteria
- [ ] `uv run pytest` passes.
- [ ] `POST /api/dispatch-once` with `{"repo":"andrewthetechie/jelly-swipe","issue_number":<real>,"coder_pool":"coders-a"}`
      returns a run id, and that run leaves `submitted` and reaches `prep`.
- [ ] Calling it twice with unchanged `main` registers the version once (the
      second call reuses the cached id) and creates two runs.
- [ ] A bad `environment_id` surfaces fabro's `422` detail text, not a generic error.
- [ ] `build_version_payload` output has `workflows/_shared/review-merge/review-merge.fabro`
      among its `files` keys and no key containing `..`.

## Test Expectations
Framework: **pytest**, `uv run pytest` from `ops/scheduler/`. HTTP faked with
`respx`; `build_version_payload` tested against a real directory tree in `tmp_path`.

Concrete case in `tests/test_workflow_version.py`:

```python
def test_rooted_at_fabro_includes_shared_and_has_no_parent_segments(tmp_path):
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/_shared/review-merge").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (tmp_path / "workflows/_shared/review-merge/review-merge.fabro").write_text("digraph S {}")
    payload = build_version_payload(tmp_path, "workflows/backlog/workflow.fabro")
    assert payload["entrypoint"] == "workflows/backlog/workflow.fabro"
    assert "workflows/_shared/review-merge/review-merge.fabro" in payload["files"]
    assert not any(".." in k for k in payload["files"])
    assert payload["workflow_dependencies"] == {}
```

Concrete case in `tests/test_fabro.py`: fake `POST /runs` returning `422` with
`{"errors":[{"detail":"unknown environment id `nope`"}]}` and assert the raised
exception message contains `unknown environment id`.

## Dependencies
- Blocked by: "Fix `fire-pr-review.sh`: root the workflow version at `.fabro/`";
  "Scheduler skeleton: container, `repos.toml`, health endpoint"
- Why blocked: draft 02 proves the `.fabro`-rooted mechanism by hand and supplies
  the reference implementation; draft 04 supplies the app and config to hang this on.
- Blocks: "Lease state machine and dispatch loop"

## Labels
`feature`, `ops/scheduler`, `priority:high`

## Estimate
Medium

## Risk
3 - creates real runs on the live server. Mitigated because a created run does
nothing until started, so every step before `start_run` is reversible.

## Validator Stopping Point
`uv run pytest` green, and one hand-triggered dispatch producing a fabro run that
reaches `prep`.
