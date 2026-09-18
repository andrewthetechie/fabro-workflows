# Scheduler skeleton: container, `repos.toml`, health endpoint

## Tracer-Bullet Outcome
`docker compose up -d scheduler` brings up a service on the host that reads
`repos.toml`, and `curl http://10.10.0.32:32280/health` returns the loaded repo
list with their priorities. Nothing is scheduled yet.

## User Story
As the operator, I want the scheduler to exist, start with the rest of the stack
and tell me what configuration it loaded, so that every later draft has somewhere
to land and a way to prove it is running.

## Description
The first vertical slice: config file → parser → HTTP surface → container →
compose. Deliberately has **no** GitHub client, **no** fabro client and **no**
queue; those are drafts 06, 07 and 08.

New tree under `ops/scheduler/`. Nothing in `.fabro/` changes — `ops/` is by
definition the tree no automation reads, which is what makes editing it safe.

## Context Pack
- Source decisions: overview decisions 4 (signed-integer priority, smallest wins),
  16 (LAN-only, no auth), 19 (Python + FastAPI + SQLite, `uv`, container in the
  `~/fabro` compose project, code in this repo).
- Repo facts: `ops/docker-compose.yaml` today declares exactly one service,
  `fabro`, with a named volume `fabro-storage` and `env_file: .env` (optional).
  Deployment convention from `AGENTS.md`: `ops/` changes need an explicit deploy,
  and each deployed file has a drift check that must print nothing.
- Non-goals: GitHub, fabro, the queue, the UI beyond `/health`, auth, TLS,
  metrics.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/pyproject.toml
  ops/scheduler/Dockerfile
  ops/scheduler/repos.toml
  ops/scheduler/src/fabro_scheduler/__init__.py
  ops/scheduler/src/fabro_scheduler/config.py
  ops/scheduler/src/fabro_scheduler/app.py
  ops/scheduler/tests/test_config.py
  ops/docker-compose.yaml            (add the service)
  ops/README.md                      (deploy + drift-check rows)
  ```

- Interfaces and names — the exact `repos.toml` schema this draft defines and
  every later draft consumes:

  ```toml
  # ops/scheduler/repos.toml
  # priority: signed integer, SMALLEST WINS. -99 beats 0 beats 7.
  [[repo]]
  name           = "andrewthetechie/jelly-swipe"
  priority       = 0
  environment_id = "python"
  enabled        = true

  [[repo]]
  name           = "andrewthetechie/lawncare-saas"
  priority       = 1
  environment_id = "python-node"
  enabled        = true

  [[repo]]
  name           = "andrewthetechie/womens-fantasy-sports"
  priority       = 1
  environment_id = "ts"
  enabled        = true

  [[repo]]
  name           = "andrewthetechie/writers-app"
  priority       = 2
  environment_id = "rust-node"
  enabled        = true
  ```

  The four `environment_id` values above are the live ones, read from
  `GET /api/v1/environments` on 2026-09-18, and each already carries a matching
  `repo` label:

  ```
  python       fabro-python:local        cpu=2 mem=4GB   repo=jelly-swipe
  python-node  fabro-python-node:local   cpu=2 mem=4GB   repo=lawncare-saas
  ts           fabro-ts:local            cpu=2 mem=4GB   repo=womens-fantasy-sports
  rust-node    fabro-rust-node:local     cpu=4 mem=8GB   repo=writers-app
  ```

  Target Python signatures:

  ```python
  @dataclass(frozen=True)
  class RepoConfig:
      name: str              # "owner/repo", validated against ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$
      priority: int          # signed; smaller is more urgent
      environment_id: str
      enabled: bool = True

  @dataclass(frozen=True)
  class SchedulerConfig:
      repos: tuple[RepoConfig, ...]
      coder_pools: tuple[str, ...]      # ("coders-a", "coders-b")
      fabro_api_url: str
      port: int = 32280

  def load_config(path: Path) -> SchedulerConfig: ...
  ```

  `load_config` raises `ConfigError` (a plain `ValueError` subclass) with the
  offending key in the message; it never falls back to a default repo list.

- Verified external contracts: none consumed in this draft. FastAPI and
  `tomllib` (stdlib on 3.11+) only.

- Behavior rules:
  - Port **32280**, chosen to sit beside fabro's 32276 and not collide with it.
  - Duplicate `name` across `[[repo]]` blocks is a hard error, not last-wins.
  - `priority` accepts negative, zero and positive integers. A non-integer is an
    error. There is no default — it must be stated.
  - `enabled = false` keeps the row but excludes the repo from all scheduling.
  - `GET /health` returns `200` with:
    ```json
    {"status":"ok","repos":[{"name":"andrewthetechie/jelly-swipe","priority":0,
     "environment_id":"python","enabled":true}],"coder_pools":["coders-a","coders-b"]}
    ```
    sorted by `(priority, name)`.
  - The container runs as a non-root user and the SQLite volume is mounted at
    `/data` (used from draft 06 onward; create the mount now so no later draft
    changes compose).

- Error and security rules: no auth (decision 16). Bind `0.0.0.0` inside the
  container; publish to the LAN. `FABRO_API_TOKEN` and `GITHUB_TOKEN` come from
  the environment via `env_file`, are never logged, and are never placed in
  `repos.toml` — **this repository is public.** `/health` must not echo them.

## Acceptance Criteria
- [ ] `cd ops/scheduler && uv run pytest` passes.
- [ ] `docker compose up -d scheduler` starts and its healthcheck goes healthy.
- [ ] `curl -fsS http://10.10.0.32:32280/health | jq -r '.repos[].name'` prints
      the four repos, ordered by `(priority, name)`.
- [ ] A duplicate `[[repo]]` name makes the service exit non-zero at startup with
      a message naming the duplicate.
- [ ] `ssh andrew@10.10.0.32 'cat ~/fabro/docker-compose.yaml' | diff - ops/docker-compose.yaml`
      prints nothing.
- [ ] `git grep -nE '(ghp_|github_pat_|Bearer [A-Za-z0-9]{20,})' ops/scheduler` finds nothing.

## Test Expectations
Framework: **pytest**, run with `uv run pytest` from `ops/scheduler/`. Tests in
`ops/scheduler/tests/`. No network, no container — `load_config` is pure.

Concrete case in `tests/test_config.py`:

```python
def test_priority_accepts_negative_and_orders_smallest_first(tmp_path):
    p = tmp_path / "repos.toml"
    p.write_text(
        '[[repo]]\nname="o/late"\npriority=7\nenvironment_id="python"\n'
        '[[repo]]\nname="o/first"\npriority=-99\nenvironment_id="ts"\n'
    )
    cfg = load_config(p)
    assert [r.name for r in sorted(cfg.repos, key=lambda r: (r.priority, r.name))] \
        == ["o/first", "o/late"]

def test_duplicate_repo_name_is_an_error(tmp_path):
    p = tmp_path / "repos.toml"
    p.write_text(
        '[[repo]]\nname="o/a"\npriority=0\nenvironment_id="python"\n'
        '[[repo]]\nname="o/a"\npriority=1\nenvironment_id="ts"\n'
    )
    with pytest.raises(ConfigError, match="o/a"):
        load_config(p)
```

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: "GitHub inventory with ETag caching and a read-only queue page";
  "Fabro client: register a `.fabro`-rooted version, create and start a run"

## Labels
`feature`, `ops/scheduler`, `priority:high`

## Estimate
Medium

## Risk
2 - new isolated service; the only shared artifact touched is
`ops/docker-compose.yaml`, and adding a service cannot affect the `fabro` one.

## Validator Stopping Point
`uv run pytest` green, the service healthy in compose, `/health` returning the
four configured repos, and the compose drift diff empty.
