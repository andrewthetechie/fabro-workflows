# 03. `inventory`: alerts, PRs, and the match

## Outcome

`inventory` writes `alerts.json` (C1) and the ordered PR queue `prs.json` (C2), with
each PR's `fixes` computed by C3's rule. On jelly-swipe on 2026-10-10 it would write 5
alerts and 6 PRs, every `fixes` empty, with the PRs ordered oldest first. Fixtures in
`ops/test-task-gates.sh` pin the matching rule, so a regex change that starts making
false matches fails the suite.

## Why

The rest of the graph reads these two files. Every later decision depends on them:
merge order, which alerts go to the patch path, and which Dependabot PR a patch
replaces and closes. A false match closes a PR wrongly, so the rule is tested offline
against real Dependabot titles and bodies.

## Change

Read 00: C1, C2, C3 (with task 01's final slug map) and D3, D8, D12, D24.

Replace the body of `inventory` in `.fabro/workflows/dependabot/workflow.fabro`:

1. `echo inventory > /tmp/fabro/dep/stopped_at`.
2. Alerts:
   ```sh
   R=$(jq -r .nameWithOwner /tmp/fabro/dep/repo.json)
   gh api \"repos/$R/dependabot/alerts?state=open&per_page=100\" --paginate > /tmp/fabro/dep/alerts_raw.json
   ```
   `--paginate` prints one array per page, so combine them with `jq -s 'add // []'`,
   then project each entry to C1's shape. If the `gh api` call fails, exit 1. The edge
   goes to `summarize`, and the summary says alerts could not be read. That is the S1
   failure mode, and it must be loud.
3. PRs: `gh pr list --author app/dependabot --state open --limit 100 --json
   number,title,headRefName,headRefOid,createdAt,url,body` → `prs_raw.json`.
4. The match. Write the matching program **once**, to `/tmp/fabro/dep/match.jq`, from a
   heredoc in this node (the `hygiene.sh` pattern), and run it with `jq -n
   --slurpfile a alerts.json --slurpfile p prs_raw.json -f match.jq`. Its parts:
   - `updates(pr)`: an array of `{package, to, dir, eco}`. It parses the title forms
     from S2 (`bump X from A to B`, `update X requirement from A to B`). For a grouped
     title, it parses the body rows S2 recorded. `eco` and `dir` come from
     `headRefName` (`dependabot/<slug>/<dir…>/…`) through the S2 slug map.
   - `fixes(pr)`: the alert numbers for which some update has `package == alert.package`,
     `eco == alert.ecosystem`, `dir == (alert.manifest | dirname, or "" for a root
     manifest)`, and `to` at or above `alert.first_patched`.
   - jq has no `sort -V`. Compare versions with a jq function that works as follows:
     - Strip a leading `v`, and drop build metadata after `+`.
     - Split the release from a pre-release at the first `-`.
     - Compare the release parts split on `.`: numbers as numbers, a missing part as 0.
     - On equal releases, a version with a pre-release sorts **below** the same version
       without one. Two pre-releases compare as strings.

     Put the comparator in the same file, and give it its own fixture block (below).
     An alert with `first_patched == null` is never fixed by a PR.
   - Output: C2's array, sorted by `(fixes | length == 0)` and then `created_at`.
   - **No backslash** may appear in the heredoc: `\"` is the only one a `.fabro` file
     allows. Write regex classes with brackets (`[.]`, `[ ]`), as `hygiene` does.
5. Publish `{"context_updates":{"alert_count":N,"dep_pr_count":M}}` and
   `echo > /tmp/fabro/dep/stopped_at`.

Edges change to:

```
inventory -> dep_next      [condition="outcome=succeeded && context.dep_pr_count!=0"]
inventory -> pick_group    [condition="outcome=succeeded && context.dep_pr_count=0"]
inventory -> summarize
```

`dep_next` and `pick_group` do not exist until tasks 04 and 05. Until then, route
both conditional edges to `summarize`, and change their targets in those tasks. `!=` is in
the condition grammar (the skill's REFERENCE.md, *Condition grammar*).

## Acceptance

- The graph passes `check-graph-invariants.py`. The heredoc has no backslash, and the
  file has no `#` line inside the script.
- `./ops/test-task-gates.sh` passes with the new section.
- On fixtures built from the real 2026-10-10 data (below), `prs.json` and `alerts.json`
  equal the expected files byte for byte.

## Tests

Add `dependabot — inventory` to `ops/test-task-gates.sh`. Stub `gh` with a script that
returns fixture files by argument. Fixtures go in a heredoc or under `ops/tests/fixtures/dependabot/`.

1. **jelly-swipe, 2026-10-10.** The 5 alerts and 6 PRs, from `gh` output captured in
   task 01. Expect 5 C1 entries, 6 C2 entries, all `fixes: []`, ordered by `createdAt`.
2. **A security PR.** A PR `chore(deps): bump urllib3 from 2.7.1 to 2.8.0` on
   `dependabot/uv/urllib3-2.8.0`, plus alerts 72-74 (`pip`, `uv.lock`, patched 2.8.0).
   Expect `fixes: [72,73,74]`, and the PR sorts first.
3. **Too low.** The same PR to `2.7.9`. Expect `fixes: []`.
4. **Wrong directory.** brace-expansion alerts on `frontend/package-lock.json`, and a PR
   on `dependabot/npm_and_yarn/brace-expansion-5.0.12`, which is the root. Expect
   `fixes: []`.
5. **A grouped PR.** Use one real grouped body from S2 that includes the alert's package
   at a fixing version. Expect the match.
6. **Paginated alerts.** `gh api --paginate` returns two concatenated arrays. Expect
   both pages in C1.
7. **The API fails.** `gh api` exits 1. Expect the node to exit 1, and `stopped_at` to
   say `inventory`.
8. **The version comparator.** `2.10.0 > 2.9.0`, `1.0.0-rc1 < 1.0.0`, `0.23.45 >= 0.23.45`,
   `5.0.12 > 5.0.9`, `v0.16.10 > v0.16.9` (a leading `v` is stripped).

## Depends on

02, and 01's S2 and S3 (the slug map and the title and body forms).
