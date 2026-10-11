# Dependabot: overview and canonical contracts

**Status:** designed 2026-10-10 (ADR 0022, proposed). Tasks 01-09 are implemented, one
commit each, on local `main` (unpushed, pending review; no deploy). Task 01 is
`spike-2026-10-10.txt`; S1 passed, so the series is not blocked. Task 10 (validate on the
host, create the rows, hand-fire, enable the schedules) is the operator's and has not started.
`fabro validate` in the container passes: `Dependabot (46 nodes, 101 edges)`, no warnings.
The operator answered Q1-Q19 in a grilling session on 2026-10-10.
D20-D25 were drafted while planning, and are open to the operator's veto until task 02 lands.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. **Before you edit a `.fabro` file, a prompt or a `workflow.toml`, invoke the
`/fabro-workflow` skill** and read `docs/agents/invariants-graph.md`,
`docs/agents/invariants-merge.md` and `docs/agents/invariants-host.md`.

## What this is

A fifth workflow package, `.fabro/workflows/dependabot/`. Once a day per repository, it:

1. Lists the open Dependabot alerts and the open Dependabot PRs, and matches them.
2. **Merge path:** validates every open Dependabot PR (green checks, plus
   `setup.sh && ci.sh` on the PR merged into base) and squash-merges each one that
   passes. PRs that fix an alert go first. The loop stops after 5 PRs or 90 minutes.
3. **Patch path:** picks one alert group with no working PR, files or reuses a tracking
   issue, has a `glm-5.3-flash` agent patch the manifest and lockfile, opens an
   `agent-authored` PR, and splices `_shared/review-merge/` unchanged to review,
   validate and merge it.
4. Posts one Discord summary.

## The state on 2026-10-10 (from the live API)

| Repo | Open alerts | Groups (C4) | Open Dependabot PRs | `.fabro/` | Env |
|---|---|---|---|---|---|
| jelly-swipe | 5: urllib3 ×3 (pip, `uv.lock`, runtime, high/medium, patched 2.8.0); brace-expansion ×2 (npm, `frontend/package-lock.json`, dev, medium) | 2 | 6, all version updates (`dependencies` label), none fix an alert | ci, setup | `python` |
| lawncare-saas | 5: brace-expansion (npm, `package-lock.json`, dev, high/medium) | 1 | 0 (28 merged by a human, 2 closed) | ci, setup, fix, test.toml | `python-node` |
| writers-app | 1: rustls (rust, `Cargo.lock`, runtime, medium, patched 0.23.45) | 1 | 0 | ci, setup, fix, test.toml | `rust-node` |
| womens-fantasy-sports | 0 | 0 | 0 | ci, setup, fix, test.toml | `ts` |

Dependabot security updates are `enabled: true, paused: false` on all four
(`GET /repos/{r}/automated-security-fixes`). Only jelly-swipe and womens-fantasy-sports
have a `.github/dependabot.yml`, and jelly-swipe's has no `npm` entry for `frontend/`.
Only jelly-swipe (public) has branch protection: required check `test`, 0 reviews. The
other three are private on a free plan, so `GET .../protection` returns 403 and the
graph's own check watch is the only gate. All four have `allow_squash_merge: true`,
`allow_auto_merge: false`, `delete_branch_on_merge: true`.

## Operator decisions (settled; do not change them)

| # | Decision | From |
|---|---|---|
| D1 | A new package, `dependabot`, with a merge path and a patch path. The patch path splices `_shared/review-merge/` **unchanged**. | Q1 |
| D2 | One run handles every eligible Dependabot PR (subject to D12's cap), then patches **at most one** alert group. The next group waits for the next day. | Q2 |
| D3 | **Every** open PR whose author is `app/dependabot` is in scope: security updates, version updates and grouped updates. | Q3 |
| D4 | Dependabot PRs take a deterministic merge path with no LLM: green checks, then `setup.sh && ci.sh` on the PR head merged into the base tip, then a squash-merge. A conflicting PR gets an `@dependabot rebase` comment and waits. | Q4 |
| D5 | A Dependabot PR that fixes an alert and fails validation: its alerts go to the patch path, and the run closes that PR with a comment once our patch merges. We never push to a Dependabot branch. | Q5 |
| D6 | The patch path files or reuses one tracking issue per alert group (C6), labelled `security`, `dependencies`, `ai-generated` and never `agent`. The patch PR says `Resolves #N`. | Q6 |
| D7 | The model is set in the graph's stylesheet, not in a settings alias. | Q7 |
| D8 | Every open alert is in scope, at any severity and in any scope (dev included), if it has a `first_patched_version`. An alert without one is reported and left alone. | Q8 |
| D9 | No Bun-only rule. The agent uses the repository's native tooling (npm with `package-lock.json`, uv, cargo). It never changes a repository's package manager and never adds a second lockfile. | Q9 |
| D10 | Four `dependabot-<repo>` automations with daily schedules, enabled at creation: jelly-swipe 05:00, lawncare-saas 05:20, womens-fantasy-sports 05:40, writers-app 06:00 host time. The coder scheduler does not admit these runs. | Q10 |
| D11 | Testing first, then live. No observation week with merges off. | Q11 |
| D12 | The PR loop runs in order: PRs that fix an alert first, then oldest first. It stops after **5 PRs or 90 minutes**. A PR that is behind base is validated merged locally into the base tip, so no rebase is needed. | Q12 |
| D13 | A major-version Dependabot PR merges on green validation, like any other. | Q13 |
| D14 | Models: `*`, `.dep-patch`, `.ci-fix`, `.merge-fix` and `.merge-standards` on `glm-5.3-flash`. `.merge-spec` on `glm-5.3`. `.refute` on `gpt-6.1-sol`. Fallbacks as in `pr-review`: `glm-5.3-flash → zai:glm-5.3`, `glm-5.3 → kimi:kimi-k3`, `gpt-6.1-sol → openai:gpt-6-luna`. | Q14 |
| D15 | The patch PR auto-merges under the unchanged shared `merge_gate`. When it is blocked, it ends `needs_human` or `blocked` as a `backlog` PR does. | Q15 |
| D16 | Group order: severity (critical > high > medium > low), then `runtime` before `development`, then the most alerts closed, then the oldest alert. Skip a group with an open patch PR or a `needs-human` tracking issue. A major bump is allowed only when the current major has no patched version; the risk rating and the Refuter then decide whether it merges. | Q16 |
| D17 | Merges honor `FABRO_AUTO_MERGE` and a per-repository `auto_merge=` token on the `dependabot-<repo>` row, which is created on. With either switch off, the run validates and opens PRs but merges nothing. | Q17 |
| D18 | Go-live sequence: `make check` and `make check-host` with new baselines. Hand-fire jelly-swipe, then lawncare-saas. Then enable the four schedules. | Q18 |
| D19 | Discord: one `dependabot-summary` per run. The shared phase's existing `merged`, `blocked` and `needs-human` hooks cover the patch PR. `failed` covers a failed run. | Q19 |
| D20 | *(drafted)* The merge path validates **in the run's own checkout** (`/workspace/<repo>`), not in a `git worktree`, so `fabro-io test-env` finds its checkout and the dependency caches are reused. The node restores the run branch before it ends, and it runs `setup.sh && ci.sh` under an inner `timeout 1020` (17 minutes, below the node's 20m), so a slow suite cannot leave a foreign tree for the checkpoint. | planning |
| D21 | *(drafted)* Before the patch path, `sync_base` merges `origin/<base>` into the run branch, so the patch starts from a base that includes the Dependabot PRs merged earlier in the run. Until then the run branch holds only empty checkpoint commits, so the merge cannot conflict. | planning |
| D22 | *(drafted)* A group's tracking issue closed **completed** less than 3 days ago, with its alerts still open, is skipped (Dependabot closes alerts some time after the fix lands). After 3 days, the run reopens the issue with `needs-human` and a comment that the patch merged but the alerts stayed open. | planning |
| D23 | *(drafted)* A Dependabot PR with no checks at all is not merged. This matches the shared phase's "no CI checks were reported" block. | planning |
| D24 | *(drafted)* Every command-node failure routes to `summarize`, as in `arch-review`, so a run always reports. The summary names the node where the run stopped. | planning |
| D25 | *(drafted)* The patch path gives the agent one retry after a red `setup.sh && ci.sh` (`patch_validate`), so a red patch never opens a PR. | planning |

## Verified facts (sources)

Checked on 2026-10-10 against HEAD `8f4782e`, the live GitHub API and the host.

- **The shared `merge_gate` refuses every Dependabot PR.** Check 2 needs the
  `agent-authored` label. The authorship check refuses a commit not authored by
  `noreply@fabro.sh` unless its parents are on base. Check 12 needs `Resolves #` in the
  commit-body block (`_shared/review-merge/review-merge.fabro`, `merge_gate`). D1 and D4
  follow from this.
- **The merge phase's entry contract** is `/tmp/fabro/pr_number`, `base_ref`,
  `head_ref`, `pr.json` (with `.url`, `.labels`), `linked_issues.json`, `run_base_sha`,
  the PR head checked out with full history, and `FABRO_AUTO_MERGE` in the
  environment. `rm_setup` reads `/tmp/fabro/auto_merge_repo` (absent means `1`). The
  exit is `context.review_merge_outcome = merged | blocked | needs_human`
  (`review-merge.fabro`, header comment). `backlog`'s `pr_handoff` is the reference
  implementation.
- **`spec` and `review_fix` read the issue through `linked_issues.json`**, which comes
  from the PR's `closingIssuesReferences`. The PR body must therefore contain
  `Resolves #N`, so that GitHub links the issue (`_io/manifest.json`, phase
  `review-merge`).
- **The per-repository auto-merge switch** is an `auto_merge=<token>` in an automation
  row's description. An absent token means on. Any other present value, and an
  unreachable API, mean off (`backlog/workflow.fabro`, `pr_handoff`;
  `docs/agents/invariants-merge.md`, *Auto-merge kill switches*).
- **The host `gh` token can read Dependabot alerts.** On the host,
  `gh auth status` shows scopes `admin:public_key, gist, read:org, repo`, and
  `gh api repos/andrewthetechie/jelly-swipe/dependabot/alerts` returns data. The vault's
  `GITHUB_TOKEN` was captured from the same `gh auth token` (`ops/README.md`, *Secrets*).
  The sandbox token reads alerts, and reports `push: true`, on jelly-swipe (task 01, S1).
- **Fabro's run-time permission block takes `vulnerability_alerts = "read"`**
  (`context/fabro/.fabro/workflows/patch-cves/workflow.toml`). It is a request bounded
  by what the token has.
- **The coder scheduler counts only its own leases** when it spreads runs across
  repositories (`ops/scheduler/src/fabro_scheduler/dispatch.py`, `busy_repos`). A
  `dependabot` run does not block a `backlog` dispatch.
- **The server runs at most 4 runs** (`max_concurrent_runs = 4`) and queues above that
  without rejecting (ADR 0001).
- **Every stage writes a checkpoint** (`git add -A && git commit --allow-empty`) before
  the next node runs. A conflicted or foreign tree that crosses a stage boundary is
  committed, and on 0.362 a failed checkpoint commit stops the run
  (`docs/agents/invariants-graph.md`, *Routing and contracts*).
- **The upstream example** (`context/fabro/.fabro/workflows/patch-cves/`) is one agent
  node on `claude-opus-4-8` with `[run.pull_request] enabled = false` and a 132-line
  prompt. Its ranking, grouping, fix-choice and ecosystem sections are the base for
  `prompts/patch.md.j2` (task 06). Its Bun-only rule, its PR creation, its CI watch and
  its "do not merge" rule are removed: graph nodes do those jobs here.
- **`arch-review`** is the analog for a scheduled package outside the coder scheduler:
  `prep` installs the code index and the agent guide, every failure flows to
  `summarize`, and `finish` is the hook target for the summary
  (`.fabro/workflows/arch-review/workflow.fabro`).
- **No target repository has a `.fabro/hygiene.json`** (checked 2026-10-10: 404 on all four),
  so the shared phase's default `exclude_paths` applies, and it already drops every lockfile.
  A repository that adds one with its own `exclude_paths` replaces that list (task 07, step 7).
- **The sandbox's `/bin/sh` is dash** (the profile images are Debian). `ops/test-task-gates.sh`
  runs every node under dash when it is installed, because macOS `sh` (bash) accepts bashisms
  such as `<<<` that dash rejects at parse time.
- **`check-graph-invariants.py` finds packages by themselves** (any directory with a
  `workflow.toml`). `ops/test-task-gates.sh` and `ops/fabro-io-manifest.py` name each
  workflow explicitly, so each needs an edit.

Facts that task 01 must establish before the graph is written: see task 01.

## Graph shape

```
start → prep → inventory ─┬─► dep_next ◄───────────────────────────────┐
                          │     │ more PRs, under cap                  │
                          │     ▼                                      │
                          │   dep_checks → dep_validate → dep_merge ───┘
                          │     │ no more / cap reached
                          ▼     ▼
                        pick_group ── none ──────────────────────────► summarize → finish → exit
                          │ a group
                          ▼
                       track_issue → sync_base → patch_prep → patch → patch_gate → patch_validate
                                                                            │ green
                                                       (red twice: patch_blocked → summarize)
                                                                            ▼
                     open_pr → pr_handoff → review_merge[import] → after_merge → summarize
```

Every command node's unconditional edge goes to `summarize` (D24). The edges listed above
are the conditional happy paths. `inventory` with no PRs routes straight to `pick_group`.

## Contracts

All run-local files live under `/tmp/fabro/dep/` unless the merge phase's contract fixes
the path under `/tmp/fabro/`.

### C1. `alerts.json`: the open alerts (written by `inventory`)

```json
[{"number": 73, "ecosystem": "pip", "package": "urllib3", "manifest": "uv.lock",
  "severity": "high", "scope": "runtime", "first_patched": "2.8.0",
  "ghsa": "GHSA-xxxx-xxxx-xxxx", "cve": "CVE-2026-0000", "created_at": "2026-10-01T00:00:00Z",
  "url": "https://github.com/andrewthetechie/jelly-swipe/security/dependabot/73"}]
```

- Read from `gh api "repos/<r>/dependabot/alerts?state=open&per_page=100" --paginate`, then
  `jq -s 'add'`.
- `ecosystem` is `.dependency.package.ecosystem`, verbatim (`npm`, `pip`, `rust`, …).
  `manifest` is `.dependency.manifest_path`. `scope` is `.dependency.scope`, or
  `"unknown"` when null. `first_patched` is
  `.security_vulnerability.first_patched_version.identifier`, or `null`. `cve` may be
  `null`.
- If the API call fails, `inventory` fails (D24). An empty array is valid.

### C2. `prs.json`: the Dependabot PR queue (written by `inventory`)

```json
[{"number": 442, "title": "chore(deps): bump sqlalchemy from 2.1.0 to 2.1.1",
  "head_ref": "dependabot/uv/sqlalchemy-2.1.1", "head_sha": "abc…", "created_at": "…",
  "url": "…", "fixes": [73, 74]}]
```

- Read from `gh pr list --author app/dependabot --state open --limit 100 --json
  number,title,headRefName,headRefOid,createdAt,url,body`.
- `fixes` lists the C1 alert numbers this PR fixes, by the matching rule that task 01
  fixes (C3). It is `[]` for a pure version update.
- Order: `fixes` non-empty first, then `created_at` ascending (D12).

### C3. The PR-to-alert matching rule

A PR fixes an alert when all three hold:

1. The PR's update list names the alert's package. The list is the `bump X from A to B`
   or `update X requirement from A to B` title, or for a grouped PR, each update row
   of the body.
2. The ecosystem and directory agree. Dependabot's head branch is
   `dependabot/<ecosystem-slug>/<dir…>/<package>-<version>`. The slug-to-ecosystem map
   (for example `npm_and_yarn` → `npm`, `uv` → `pip`, `cargo` → `rust`) is **task 01's
   finding**. The directory segment agrees with `dirname(manifest)`.
3. The target version `B` is at or above `first_patched`, by the version comparator in
   `match.jq` (task 03: numeric release parts, a pre-release below its release).

Verified by task 01 (`spike-2026-10-10.txt`, S2-S3). The slug map is `npm_and_yarn`→`npm`,
`uv`→`pip`, `cargo`→`rust`, `bun`→`npm` (no bun alert in history), and `github_actions`,
`docker`, `docker_compose`, `pre_commit` map to themselves (no alert in history).
The directory comes from the title (`in /dir`) or the grouped body (`in the /dir directory`),
and must agree with the branch prefix `dependabot/<slug>/<dir>/`. Package names compare
case-insensitively. A requirement-form target (`>=3.3.4,<4.0.0`) uses its `>=` version.
On history: 0 false matches over 48 PRs and 212 pairs; 132 of 189 alerts matched.

### C4. Alert groups and `group.json` (written by `pick_group`)

A group is the open alerts that share `(ecosystem, manifest, package)`. Its key is
`<ecosystem>:<manifest>:<package>`, for example `npm:frontend/package-lock.json:brace-expansion`.

```json
{"key": "pip:uv.lock:urllib3", "ecosystem": "pip", "manifest": "uv.lock",
 "package": "urllib3", "severity": "high", "scope": "runtime",
 "alerts": [72, 73, 74], "first_patched": "2.8.0", "replaces": [], "issue": 0}
```

- `severity` and `scope` are the group's worst. `first_patched` is the highest
  `first_patched` among the alerts, by the version comparator.
- `replaces` holds the Dependabot PR numbers whose `fixes` hit this group and that did
  not merge in this run (D5).
- `issue` is filled in by `track_issue` (C6).
- **Eligible groups:** at least one alert with a non-null `first_patched` (D8). Alerts
  fixed by a Dependabot PR merged **in this run** are dropped first. A group is
  skipped when D16 or D22 says so. Groups are ranked by D16, and the first one is
  written.
- `pick_group` publishes `patch_group = <key>` or `patch_group = none`. It also writes
  `/tmp/fabro/dep/no_patch.json`, the alerts with `first_patched == null`, for the
  summary.

### C5. `results.json`: the merge loop's record (appended by `dep_merge`)

```json
[{"pr": 442, "outcome": "merged", "reason": "", "fixes": []},
 {"pr": 440, "outcome": "red", "reason": "GitHub check test failed", "fixes": [70]}]
```

`outcome` is one of:

| Outcome | Meaning |
|---|---|
| `merged` | All checks passed and the PR was squash-merged. |
| `switch_off` | All checks passed, but D17 forbids the merge. |
| `red` | A GitHub check failed. |
| `pending` | Checks were still pending after 15 minutes. |
| `no_checks` | No checks were reported (D23). |
| `conflict` | The PR conflicts with base. The run commented `@dependabot rebase`. |
| `validate_failed` | `setup.sh` or `ci.sh` failed on the merged tree. |
| `stale` | The head moved, or the PR closed, during the loop. |
| `merge_failed` | `gh pr merge` failed after retries. |
| `skipped_cap` | The loop stopped (D12) before reaching this PR. |

`reason` is one line, at most 300 bytes. A PR whose `fixes` is non-empty and whose outcome
is neither `merged` nor `switch_off` feeds `replaces` in C4.

### C6. The tracking issue

- **Title:** `fix(deps): patch <package> security alerts in <manifest>`. A title over 72
  characters is cut at a word boundary, as `backlog`'s subject is.
- **First body line:** `<!-- fabro:dep-group key=<key> -->`. The issue is found by this
  marker and nothing else.
- **Body:** the `> Filed by the daily dependabot run (fabro-workflows ADR 0022).` notice,
  then a table of the alerts (number with link, severity, scope, vulnerable range,
  `first_patched`, GHSA and CVE), then `replaces` as a list of Dependabot PR links.
  `track_issue` rewrites the body each run to the group's current alerts.
- **Labels:** `security`, `dependencies`, `ai-generated`. `track_issue` creates them
  if they are missing (`gh label create … || true`). Never `agent`.
- **Lifecycle:**
  - The issue is open while the group is unfixed.
  - `open_pr` adds the `Review` label (the `backlog` convention).
  - A merged patch closes the issue through `Resolves #N`.
  - A blocked patch leaves the issue open. `mark_needs_human` labels the **PR**.
    `after_merge` copies `needs-human` onto the issue so D16 skips the group.
  - A human removes `needs-human` to let the group retry.
- **Skip rules for `pick_group`, in order:**
  1. The open issue has `needs-human` → skip.
  2. The open issue has `Review` and an open PR links to it → skip. If the issue has
     `Review` but no open PR links to it, remove `Review` and the group is eligible.
  3. The issue was closed `COMPLETED` less than 3 days ago → skip.
  4. Closed 3 or more days ago with the alerts still open → reopen it, add
     `needs-human`, comment, and skip (D22).

  The open-PR test (verified, S4): `gh pr list -R <repo> --state open --json number,closingIssuesReferences`
  filtered to the PRs whose `closingIssuesReferences` contain the issue number. The
  link appears within about 20 s of the PR's creation, so a check made at once can miss it.

### C7. `patch.json`: the patch contract (written by `patch` through `submit`)

```json
{"status": "patched",
 "summary": "Bumped urllib3 2.7.1 → 2.8.0 in uv.lock; no API changes.",
 "changes": [{"package": "urllib3", "from": "2.7.1", "to": "2.8.0",
              "manifest": "uv.lock", "direct": false, "parent": "requests"}],
 "verification": [{"command": "uv lock --upgrade-package urllib3", "result": "pass"},
                  {"command": "./.fabro/ci.sh", "result": "pass"}],
 "major": false,
 "blocker": ""}
```

- `status` is `patched` or `blocked`. `blocked` requires a non-empty `blocker` and an
  empty `changes`. `patched` requires one or more `changes`, each `to` at or above the
  group's `first_patched` by the version comparator.
- The schema `_io/schemas/dep_patch.schema.json` encodes the shape. `patch_gate`
  checks the receipt, the cross-field rules above, and the diff against
  `run_base_sha`: non-empty for `patched`, and it adds no lockfile that is absent on
  base (D9).

### C8. Labels

| Label | On | Set by |
|---|---|---|
| `security`, `dependencies`, `ai-generated` | tracking issue, patch PR | `track_issue`, `open_pr` |
| `agent-authored` | patch PR | `open_pr` (the shared `merge_gate` requires it) |
| `Review` | tracking issue | `open_pr` |
| `needs-human` | patch PR (shared phase), tracking issue | `mark_needs_human`, `after_merge` |

### C9. Context keys

| Key | Values | Writer |
|---|---|---|
| `alert_count`, `dep_pr_count` | integers | `inventory` |
| `dep_pr_next` | `yes` / `no` | `dep_next` (reset on every visit) |
| `dep_checks` | `green` / `red` / `pending` / `no_checks` / `stale` | `dep_checks` |
| `dep_valid` | `pass` / `fail` / `conflict` | `dep_validate` |
| `patch_group` | group key / `none` | `pick_group` |
| `patch_status` | `patched` / `blocked` / `failed` | `patch_gate` |
| `patch_ci` | `green` / `red` | `patch_validate` |
| `patch_retry` | `yes` / `no` | `patch_validate` |
| `issue_number` | the tracking issue | `track_issue` (task 08) |
| `pr_url` | URL | `open_pr` |
| `review_merge_outcome` | `merged` / `blocked` / `needs_human` | the shared phase |
| `dep_summary` | one line, at most 1,500 bytes | `summarize` |
| `stopped_at` | node id, or empty | `summarize` |

Write every per-iteration key on every branch (`docs/agents/invariants-graph.md`,
*Routing and contracts*).

### C10. Automation rows

| id | env | repo | schedule id | expression | description |
|---|---|---|---|---|---|
| `dependabot-jelly-swipe` | `python` | `andrewthetechie/jelly-swipe` | `daily` | `0 5 * * *` | `auto_merge=true` |
| `dependabot-lawncare-saas` | `python-node` | `andrewthetechie/lawncare-saas` | `daily` | `20 5 * * *` | `auto_merge=true` |
| `dependabot-womens-fantasy-sports` | `ts` | `andrewthetechie/womens-fantasy-sports` | `daily` | `40 5 * * *` | `auto_merge=true` |
| `dependabot-writers-app` | `rust-node` | `andrewthetechie/writers-app` | `daily` | `0 6 * * *` | `auto_merge=true` |

`workflow = "dependabot"`, `workflow_source = andrewthetechie/fabro-workflows@main`, an
`api:manual` trigger and the schedule trigger, created **enabled** (D10).

## Rules every task must follow

1. Every rule in `docs/agents/invariants-graph.md` and `invariants-merge.md`, in
   particular: `\"` is the only backslash in a `.fabro` file, there is no `#` line inside a
   `script`, scripts are POSIX `sh`, `output_schema="routing"` exactly when a script
   prints `context_updates`, and contract files are deleted before the agent that
   writes them runs.
2. The shared phase is spliced **unchanged** (D1). A task that needs it changed stops and
   reports instead.
3. No agent runs `git`. `patch` is not a git exception: it must not carry
   `"git": "write"` in the manifest.
4. No command node leaves the checkout off the run branch or dirty at the end of its
   script (D20). `dep_next` asserts this on every visit.
5. No token, key or URL in a tracked file. Secrets are referenced by name.
6. Every node timeout is below the graph's `stall_timeout` (`60m`), and
   `loop_restart_signature_limit=20` (R1, R2).

## How to check your work offline

```sh
make check-fast                                     # every offline gate but the task-gate suite
./ops/test-task-gates.sh                            # the node fixtures (task 03 onward adds a section)
python3.11 ops/fabro-io-manifest.py check           # the Stage manifest and the agent guide
python3.11 ops/check-graph-invariants.py            # R1-R8, now over five packages
```

`fabro validate` runs only in the container (`make check-host`).

## Task index

| # | Task | Where | Depends on |
|---|---|---|---|
| 01 | [Spike: the facts the graph needs](01-spike.md) | host, live API | none |
| 02 | [Package skeleton: `prep`, `inventory` stub, `summarize`](02-package-skeleton.md) | this repo | 01 |
| 03 | [`inventory`: alerts, PRs, and the match](03-inventory.md) | this repo | 02 |
| 04 | [The Dependabot PR merge loop](04-merge-loop.md) | this repo | 03 |
| 05 | [`pick_group` and `track_issue`](05-group-and-issue.md) | this repo | 03, 04 |
| 06 | [The `patch` agent, its gate and its validation](06-patch-agent.md) | this repo | 05 |
| 07 | [Open the PR and splice the merge phase](07-splice-review-merge.md) | this repo | 06 |
| 08 | [Summary and Discord](08-summary-and-discord.md) | this repo | 07 |
| 09 | [Provisioning, switches and docs](09-provision-and-docs.md) | this repo | 08 |
| 10 | [Validate, hand-fire, go live](10-validate-and-go-live.md) | host (operator) | 09 |

Tasks 02-09 are each one commit to `main`, and each leaves `main` valid. No automation
fires `dependabot` until task 10 creates the rows, so a half-built package is never
run.
