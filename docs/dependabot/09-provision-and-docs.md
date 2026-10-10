# 09. Provisioning, switches and docs

## Outcome

- `ops/provision-server-state.sh` creates the four `dependabot-<repo>` rows of C10, with
  enabled daily schedules and `auto_merge=true`, and reports drift on them as it does
  for `pr-review`.
- `ops/fabro-auto-merge-switch.sh` flips a `dependabot-<repo>` row's switch.
- The docs that describe four workflows now describe five, and a new invariants file
  holds the rules this package adds.

No host state changes in this task: task 10 runs the scripts.

## Why

This is D10, D11 and D17. A rebuilt host must get these rows from the script, or the
package silently never runs. The per-repository switch is only useful if the
operator's tool can flip it, and if the "switched off" message names the right tool.

## Change

Read 00: D10, D17, C10. Read `ops/provision-server-state.sh` in full: its header
comment, `provision_automation`, `check_triggers` and `description_auto_merge`.

### 1. `ops/provision-server-state.sh`

- Accept `dependabot` wherever `provision_automation` handles a workflow name. Give it
  the description and drift handling that `pr-review` has: `description_field` and the
  `existing_am` drift check. Extend both `if [ "$workflow" = "pr-review" ]` tests to
  `pr-review` or `dependabot`. The description for a new row is
  `dependabot daily run for $repo. auto_merge=$auto_merge`.
- Add a block after `arch-review`'s, with a comment citing ADR 0022 D8 and D10:
  ```sh
  echo "Provisioning dependabot automations..."
  provision_automation dependabot-jelly-swipe python andrewthetechie/jelly-swipe dependabot daily "0 5 * * *" true true || FAILED=$((FAILED+1))
  provision_automation dependabot-lawncare-saas python-node andrewthetechie/lawncare-saas dependabot daily "20 5 * * *" true true || FAILED=$((FAILED+1))
  provision_automation dependabot-womens-fantasy-sports ts andrewthetechie/womens-fantasy-sports dependabot daily "40 5 * * *" true true || FAILED=$((FAILED+1))
  provision_automation dependabot-writers-app rust-node andrewthetechie/writers-app dependabot daily "0 6 * * *" true true || FAILED=$((FAILED+1))
  ```
- Update the header comment: "sixteen automations (four per repo)" becomes twenty (five
  per repo), and the sentence that only the `arch-review` rows are created with the
  schedule enabled now names `dependabot` too.
- Run `DRY_RUN=1` against the live host. It must print `would create:` for the four new
  rows and nothing new for the old ones. It runs read-only under `DRY_RUN=1`: check
  this in the script before you run it.

### 2. `ops/fabro-auto-merge-switch.sh`

- Accept an id that matches `dependabot-*` as well as `pr-review-*`. The `owner/repo`
  form keeps selecting the `pr-review` row. Add `--workflow dependabot` to select the
  `dependabot` row by repository instead. Update the usage text and the header comment.
- For an empty description, the new text names the workflow:
  `dependabot daily run (switch only). auto_merge=$want`.
- Tests: if `ops/tests/` has a test for this script, extend it with a `dependabot-*` id
  and the `--workflow` form. If it has none, add a `DRY_RUN`-style case in the same
  style as `test_check_agent_profiles.py`: stub `curl` and check the PUT body.

### 3. The "switched off" message

`prep` (task 02) copied `rm_setup`'s off reasons, which name
`ops/fabro-auto-merge-switch.sh` with no row. Make `prep`'s repository reason name the
row: `Auto-merge is switched off for dependabot-<repo>. Flip it with
ops/fabro-auto-merge-switch.sh dependabot-<repo> on.` Leave `rm_setup` alone (rule 2).
Its generic sentence is still true for the patch PR.

### 4. Docs

| File | Change |
|---|---|
| `AGENTS.md` | "Four production Fabro workflows" → five. The repository count ("four real repositories") does not change. In the *Layout* row, add `dependabot`, and add it to the list of graphs that splice `_shared/review-merge/`. *Validating*: leave a placeholder line `Dependabot (N nodes, M edges)` for task 10 to fill in. *The server*: add `dependabot-<repo>` to the hand-fire sentence (fire it through the automation's `api:manual` trigger; task 10 records the exact command). |
| `docs/agents/invariants-dependabot.md` (new) | The rules this package adds, in the area files' table format. Each rule is one row with "what happens otherwise": rule 4 from 00 (clean checkout at each node end, and why the inner `timeout` is shorter than the node's), the shared phase spliced unchanged, `match.jq` written once and reused by `patch_gate`, the tracking-issue marker as the only lookup key, `patch` is never a git exception, and the per-repository switch is read once, in `prep`. |
| `AGENTS.md` *Writing, changing, or diagnosing a workflow* | Add the new invariants file to the list. |
| `docs/agents/invariants-merge.md` | Rows that say "both importing stylesheets" or "both graphs" (`.refute`, `io-guard`, the report hooks) now say "every importing graph". Change those words only. |
| `docs/agents/server.md` | One paragraph: `dependabot` runs come only from the four schedules and from `api:manual`. They need no input. The scheduler does not see them. |
| `docs/agents/deploying.md` | `make deploy-notify` is required when `discord-notify.sh` changes. Check that it is already listed. Add `dependabot` where the doc lists the workflows that need no deploy. |
| `ops/README.md` | The `provision-server-state.sh` row: twenty automations, five per repository. |
| `docs/README.md` | Add the `docs/dependabot/` row: "The task series for ADR 0022 … `00-overview-and-contracts.md` first." Give it a status. |
| `docs/USER-GUIDE.md` | If it lists the workflows, add one paragraph on what `dependabot` does and how to stop it: the per-repository switch, the host switch, and `fabro-automation-schedule.sh … off`. |

## Acceptance

- `make check` passes. Run the full suite: task 06 changed command-node shell.
- `bash -n` passes on both scripts. `DRY_RUN=1 ops/provision-server-state.sh` against
  the host prints exactly four `would create: dependabot-*` lines, and no new DRIFT
  lines.
- `grep -rn "four workflows\|Four production" AGENTS.md docs/agents` finds nothing
  stale.

## Tests

Covered under §2. The provisioning script has no offline test today. Its check is the
`DRY_RUN` above, recorded in the commit message.

## Depends on

08.
