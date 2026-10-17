# Task 05: the scout links each flaky issue it files in FlakeRadar

Read `00-overview-and-contracts.md` first: the facts under *Issue links*, F12-F15, and C11.
C11 is the contract, and this file only orders the work. If the two disagree, C11 is correct:
stop and report the conflict. Also read ADR 0028 D3-D5, and C5, C8 and C9 in
`docs/scout/00-overview-and-contracts.md`. **Before you write Rust, invoke `/ms-rust`.**
**Before you edit `workflow.fabro` or `workflow.toml`, invoke `/fabro-workflow`** and read
`docs/agents/invariants-graph.md`.

## Hard rule: commit locally, and stop after step 6

Pushing to `main` deploys, and the push needs a vault secret that only the operator can set
(F13). Commit each step on `main` locally, with a Conventional Commit subject. Your work ends
at step 6. The *Operator* section is for the operator, and you do not run any of it: no
`git push`, no `make deploy*`, no `make check-host`, no `ssh`, no `gh` command that writes, and
no request to FlakeRadar other than a `GET`.

## Goal

When the scout files a flaky issue in a covered repository, FlakeRadar shows that issue on the
test or job. A link that fails is reported in the summary line, and the issues are filed as
before.

## Steps

1. **C4b `jobs`** (`ops/fabro-io/src/flakeradar.rs`, `src/scout/flakeradar.rs`). Add
   `job_id: Option<u64>` to `FailedJob`. Add `pub jobs: Vec<JobLink>` (`fp`, `id`) to
   `FlakeradarOut` with `#[serde(default)]`, and fill it in `digest` beside the `explained`
   loop, as C11 says. Tests in `tests/scout_flakeradar_tests.rs`:
   - an occurrence of run 38779657986, attempt 1, failed job 116582572835 gives
     `{fp, id: 12}` (fixture `run_38779657986.json`);
   - a job none of whose occurrences has a lookup file is not in `jobs`;
   - a lookup entry without `job_id` gives nothing;
   - the newest matching occurrence wins when two give different ids;
   - `jobs` is sorted by `fp`;
   - an `out.json` without `jobs` deserializes to an empty list.

   `tests/flakeradar_classify_tests.rs` must pass unchanged.
2. **C5 `flakeradar`** (`src/scout/plan.rs`). Add `RadarLink { entity: String, id: u64 }` and
   `Action.flakeradar: Option<RadarLink>`, serialized only when set. Every `Action` literal
   needs the field: `test_action`'s closure, `plain_flaky`, the job `file`/`regression` push in
   `plan`, `plain`, `with_body`, and the literals in `tests/`. Set it only in the two places
   C11 names. Use `test.id` for a test, and look the job's `fp` up in `jobs` for a job, both
   only under `Radar::Read`. Tests in `tests/scout_plan_tests.rs`:
   - a test `file` action carries `{"entity":"tests","id":<id>}`;
   - a test `regression` carries it too;
   - a job `file` carries `{"entity":"jobs","id":12}` when `jobs` lists its `fp`, and has no
     key when `jobs` does not;
   - `known`, `suppressed`, `capped`, `backpressure` and `explained` actions never have the key;
   - a ratchet `file` action has no key.

   `an_uncovered_repository_plans_exactly_what_it_did_before` must pass with no edit. If a
   covered plan's expected JSON changes, the only change is the new key on the actions above.
3. **The write log and C8** (`src/scout/text.rs`). Add `link: Option<String>` and
   `link_detail: Option<String>` to `Filed`, both `#[serde(default)]`. In `summary`, add the
   C11 suffix to the `filed` part. Tests:
   - `conflict` with detail `55` gives `filed #101 flaky <short> (FlakeRadar already links #55)`;
   - `failed` with `HTTP 502` gives `... (FlakeRadar link failed: HTTP 502)`;
   - `linked` and `replaced` add nothing;
   - a write log with no `link` key gives the line it gives today.
4. **Version.** Set `ops/fabro-io/Cargo.toml` to `0.8.0` and update `Cargo.lock`. Run
   `CHECK_CARGO=always make check` and fix every failure. Commit:
   `feat(fabro-io): scout plan carries each flaky issue's FlakeRadar target (flakeradar 05)`.
5. **The graph** (`.fabro/workflows/scout/`).
   - `workflow.fabro`, `file` node: add the C11 write-back after `record "$FP" filed "$N" ''`.
     Put it in a shell function, `link()`, defined beside `record()`. Read `kind`, `issue`,
     `.flakeradar.entity` and `.flakeradar.id` from `$A` with `jq`. Remember R6: in the
     `.fabro` source every `"` inside the script is `\"`, and no other backslash appears. Then
     update the `//` comment above the node. It must say that the node links each filed
     flaky issue in FlakeRadar, that a link outcome never stops the loop (F12), what a `409`
     does (F14), and that links are never removed (F15).
   - `workflow.toml`: add `FLAKERADAR_API_TOKEN = "{{ secrets.FLAKERADAR_API_TOKEN }}"` under
     `[run.environment.env]`. Above it, write a comment that names F13: every node can read the
     token, which is accepted, and the vault entry must exist before this line reaches `main`.
   - `ops/test-task-gates.sh`, scout section. Extend the `curl` stub without changing what it
     does for a `GET`. `-X` sets `METHOD`, and `-H` and `--data` each take a value. A `GET`
     still logs `curl <url>`. A `PUT` logs
     `curl PUT <url> key=<the X-API-Key value> body=<the --data file's contents>`. A
     `PUT .../api/<entity>/<id>/issue` answers from `$SC_FR/link_<entity>_<id>.code` and
     `.json`, and with `?replace=true` from `link_<entity>_<id>_replace.code` and `.json`.
     A missing `.code` file is `200` and a missing `.json` file is `{}`. `SC_FR_DOWN` works for
     a `PUT` as it does for a `GET`. Then add these cases after case 21, in the shape of cases 9
     to 11. They need a live config, two actions (a flaky test `file` with
     `"flakeradar":{"entity":"tests","id":7}` first, then a ratchet `file`),
     `FLAKERADAR_URL=https://fr.test` and `FLAKERADAR_API_TOKEN=tok`.
     22. Linked: one `PUT` to `https://fr.test/api/tests/7/issue` with `key=tok` and
         `body={"number":101}`. The entries are `filed,filed`, the first has `link` `linked`,
         and the second has no `link` key.
     23. Conflict on a `file`: `link_tests_7.code` is `409`, and its `.json` names `#55`. The
         entry is `conflict` with detail `55`, no `?replace=true` request is sent, and the
         ratchet issue is still created.
     24. Regression replaces its own stale link: the action is a `regression` with `issue: 55`,
         and the `409` names `#55`. One plain `PUT`, then one `?replace=true` `PUT`, and the
         entry is `replaced`.
     25. Regression meets someone else's link: `issue: 55`, and the `409` names `#77`. There is
         no replace request, and the entry is `conflict` with detail `77`.
     26. A `502` is `failed`, `HTTP 502`. The loop goes on: two creates.
     27. FlakeRadar down (`SC_FR_DOWN=1`): the entry is `failed`, and its detail starts
         `no response`. Two creates.
     28. `FLAKERADAR_API_TOKEN` empty: no `PUT` is logged, and the entry is `failed`,
         `FLAKERADAR_API_TOKEN is empty`.
     29. Dry config: no `PUT` and no create.
     30. The token never appears in the node's stderr (`$SC_DIR.err`) or in `filed.json`.

   Run `make check` and fix every failure. Commit:
   `feat(scout): link each filed flaky issue in FlakeRadar (flakeradar 05, ADR 0028)`.
6. **Docs.** Commit with `docs(flakeradar): task 05 built; write-back awaits the vault secret`.
   - ADR 0028 D4: the scout records each issue it files (task 05, F12). Closed issues stay
     linked (F15). D5: the scout run now has the FlakeRadar token (F13). D5 stays the rule for
     the MCP and the coder agents.
   - `docs/scout/00-overview-and-contracts.md` C9: the sentence that says `FLAKERADAR_URL` is
     the only `[run.environment.env]` key now names `FLAKERADAR_API_TOKEN` too (F13).
   - `ops/README.md`, the *Secrets* list: the fabro vault holds `FLAKERADAR_API_TOKEN`, from
     the copies named in 00's *Issue links* facts, used by the scout run only. Read
     `ops/README.md` around the scout runbook (search for `FLAKERADAR_URL`) and add one sentence
     there: the scout links each issue it files.
   - `00-overview-and-contracts.md` status and `docs/README.md`'s `docs/flakeradar/` row:
     task 05 built, not deployed.

   Run `make check` once more, then stop. Report each commit, the result of `make check`, and
   anything in C11 that you could not build as written.

## Operator (after review; the implementer runs none of this)

1. **Vault first.** Inside the fabro container on the host, run
   `fabro secret set FLAKERADAR_API_TOKEN <token>`, with the token from the copies named in
   00's *Issue links* facts. Confirm by name only: `fabro secret list | grep FLAKERADAR_API_TOKEN`.
   Without it, every scout run aborts at startup once step 2 lands.
2. Run `make check`, then push. The graph is safe on the 0.7.0 images, because their plan has no
   `flakeradar` key and the new shell then sends nothing.
3. `make check-host`: `Scout (9 nodes, 11 edges)`, unchanged.
4. `make deploy-images` from a clean deploy checkout at `origin/main`. Confirm fabro-io 0.8.0 in
   the images.
5. **Backfill** lawncare-saas #2919 (test 1869302), #2920 (test 9554) and #2921 (test 9525):
   `curl -sS -X PUT -H "X-API-Key: $FLAKERADAR_API_TOKEN" -H 'Content-Type: application/json' -d '{"number": 2919}' https://flakes.herrington.services/api/tests/1869302/issue`,
   and the same for the other two. Each must answer 200, and
   `GET /api/tests/<id>/history?limit=1` must show `.test.github_issue_number` set.
6. **Live check.** lawncare-saas has 3 open flaky issues, which is `MAX_OPEN`, so it files no
   flaky issue until one closes. The other repositories are dry. The first live write-back is
   the first flaky issue filed after that. In that run, the `file` node's stderr shows
   `file: FlakeRadar tests/<id> #N: linked`, and FlakeRadar shows the number on the test.
   Record the result in the deployment log.
