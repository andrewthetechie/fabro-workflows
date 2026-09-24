# Confirmed dead ends

Ten things that look like Fabro features we should be using instead of shell, and are
not. The tenth was added on 2026-09-24. Each carries the evidence that settles it, so the question does not get re-derived.

Checked against `context/fabro` at 0.357.0-nightly.0.

---

## 1. Discord notifications as an HTTP hook

**Looks like:** `[[run.hooks]] type = "http"` with the webhook URL would replace
`discord-notify.sh` and its out-of-band deploy to `/storage/scripts/`.

**Is not, for three independent reasons:**

- The HTTP hook POSTs the `HookContext` JSON **verbatim**:
  `lib/components/fabro-hooks/src/executor.rs:540`, `client.post(&resolved_url).timeout(timeout).json(context)`.
  There is no body template. Discord's webhook API requires `{"content": …}` or
  `{"embeds": […]}` and would reject the event context with a 400.
- HTTP hooks **fail open on non-2xx** (`docs/public/agents/hooks.mdx`, "Fail-open
  behavior"). The 400 would be swallowed; notifications would silently stop and nothing
  would report it.
- `{{ secrets.* }}` is unavailable in hooks — *"`{{ env.NAME }}` and `{{ secrets.NAME }}`
  are not available in hooks"* — and only `{{ vars.* }}` interpolates into `url` and
  `headers`. A Discord webhook URL is a credential and must not live in a server variable.

**Verdict:** `discord-notify.sh` stays. The enrichment it does (repo, issue, PR URL, review
run id, triage questions, all scraped from `/runs/{id}/state`) has no built-in equivalent
either.

---

## 2. `[run.notifications]` for Discord

**Looks like:** a first-class notification route replaces the hooks entirely.

**Is not:** the settings type has exactly one provider sub-table, `slack`
(`lib/foundation/fabro-types/src/settings/run.rs:1449`,
`pub slack: Option<NotificationProviderSettings>`). The public docs say other provider
names *"may be parsed but are not delivered by the server yet"*. Slack additionally needs
`[server.integrations.slack]` configured.

**Verdict:** no Discord path. No generic webhook path.

---

## 3. `fabro_tools` / `fabro_run_create` for the `trigger_review` bridge

**Looks like:** `[run.agent] fabro_tools = true` gives a native "create a child run" call,
replacing the 437-line `fire-pr-review.sh` and its three POSTs.

**Is not:**

- These are **agent tools**, not command-node facilities. The bridge would become an LLM
  call — a model deciding whether and how to fire a review, in place of a deterministic
  script with a double-fire guard.
- Parent-created children *"may enter `pending` with `approval_required`"*
  (`docs/public/execution/child-runs.mdx`, "Start and approval"), which exists precisely to
  stop a workflow agent launching runs without an operator checkpoint. That would stall the
  bridge behind a click.

**Also confirmed:** the existing finding that `POST /automations/{id}/runs` ignores its
request body is correct. The endpoint declares no request body
(`docs/public/execution/automations.mdx`, "API triggers": *"creates and starts a run through
the automation's API trigger"*, with no body documented). That is why the bridge must
register a workflow version and create the run itself.

**Verdict:** the three-POST script is the right shape.

---

## 4. The automation `description` hack for the per-repo auto-merge switch

**Looks like:** by 0.357 there must be a proper field for per-automation inputs or labels.

**Is not.** `struct Automation` (`lib/components/fabro-automation/src/model.rs:36-52`) is:

```rust
pub struct Automation {
    pub id, pub revision, pub name, pub description,
    pub environment_id, pub last_error,
    pub target, pub workflow, pub workflow_source, pub triggers,
}
```

No `inputs`. No `args`. No `labels`. `description` remains the only free-form writable
field, exactly as `ops/fabro-auto-merge-switch.sh` documents.

**Verdict:** unchanged at 0.357. Re-check on the next major bump, not before.

---

## 5. An `insulator` (wait) node instead of the `watch_checks` poll loop

**Looks like:** Fabro has a wait node; polling with `sleep 30` is re-implementing it.

**Is not.** `lib/components/fabro-workflow/src/handler/wait.rs` is a bare
`sleep(duration).await` returning `Outcome::success()`. It holds the run — and therefore
one of two scheduler slots — for the whole duration, with no early exit when the checks
settle.

The comment already in `watch_checks` is correct: *"A blind `sleep 1800` held one of three
concurrency slots doing nothing in the common case where the checks have already settled."*

**Verdict:** polling is right. See `01-tier-1-single-attribute-fixes.md` item 2 for the one
thing that *is* wrong about `watch_checks` — its interaction with the stall watchdog.

---

## 6. `import=` to share the reviewer + gate pairs across `backlog` and `pr-review`

**Looks like:** `standards_gate` / `spec_gate` / `quality_gate` are near-identical scripts;
a shared imported subgraph would deduplicate them.

**Is not.** The import contract requires *"exactly one exit with exactly one incoming
edge"* and *"boundary edges carry no semantic attributes (`condition`, `label`, `weight`,
`fidelity`, `thread_id`, `loop_restart`, `freeform`)"*.

Every reviewer + gate pair here has **three** exits — retry-self on `outcome=failed`,
`human_rescue` / `mark_needs_human` on `needs_human_review || invalid`, and the next
reviewer — and all three are conditioned. Not expressible.

`docs/pr-review-bridge/00-overview-and-contracts.md:89` already rejected `import` for a
different and also correct reason (parse-time merge, no runtime boundary).

**Verdict:** not expressible. The prompts are genuinely different too — see
`13-gap-prompt-fidelity-vs-sandcastle.md`.

**Superseded in part (noted 2026-09-24).** `import=` is now in use, one level up.
`_shared/review-merge/` is the **whole** merge phase, spliced into `backlog` and
`pr-review` as one unit. At that level the import contract holds: `review_merge` has one
entry and one exit, and the conditioned exits live inside the phase. The verdict above is
still right for the thing it examined, which is sharing the individual reviewer + gate
pairs.

---

## 7. `FABRO_RUN_ID` in `trigger_review` instead of deriving it from the branch name

**Looks like:** the run id must be in the stage environment somewhere, making
`basename $(git rev-parse --abbrev-ref HEAD)` unnecessary.

**Is not.** `FABRO_RUN_ID`, `FABRO_EVENT`, `FABRO_WORKFLOW`, `FABRO_NODE_ID` and
`FABRO_HOOK_CONTEXT` are injected into **hooks only**
(`lib/components/fabro-hooks/src/executor.rs:162`). Command stages receive
`[run.environment.env]` and the GitHub token, and nothing else identifying the run.

**Verdict:** branch-name derivation is the only route, and the ULID shape check in
`trigger_review` is the right guard. Worth an upstream request.

---

## 8. `[[run.prepare.steps]]` instead of `prep`'s `./.fabro/setup.sh`

**Looks like:** dependency install is exactly what prepare steps are for, and it would
move setup out of the graph.

**Is not — it is strictly worse here.** Prepare steps run *"before the workflow starts"*
and *"If any step fails, the run aborts before the workflow starts"*. `backlog`'s `acquire`
quiet-exits in seconds when no `agent`-labelled issue exists, having spawned a sandbox and
cloned but started no LLM session. `CONTEXT.md` records roughly 384 such runs per day.

Moving setup to a prepare step would run the full dependency install on **every one of
them**, and an install failure would abort with no `human_rescue` route and no issue
cleanup.

**Verdict:** `prep` stays a graph node, after `claim`.

---

## 9. Fabro's built-in pull-request creation

**Looks like:** `[run.pull_request] enabled = true` does what `open_pr` does by hand with
`git push` and `gh pr create`.

**Is not, and this is the most important one to keep settled.**

First, the factual position: `[run.pull_request]` is **not enabled anywhere** — not in any
of the three `workflow.toml`s, not in `ops/settings.toml.example`, and there is no
`.fabro/project.toml`. It defaults to `enabled = false`. Nothing is currently duplicated.

The existing decisions in `docs/backlog/07-workflow-toml-hooks-cleanup.md:117` and
`docs/backlog/00-overview-and-contracts.md:219` are correct. The mechanical reason they are
correct, from `lib/components/fabro-workflow/src/pipeline/pull_request.rs`:

| Built-in behaviour | What our chain needs |
|---|---|
| Title and body generated by an **LLM** from the goal and diff (`PR_BODY_SYSTEM_PROMPT`, `PR_CONTENT_SCHEMA`) | A deterministic Conventional Commits subject derived from the issue title, label, and bracket prefix |
| Title capped at 72 chars (`PR_TITLE_MAX_CHARS`) | Subject truncated to `72 - len(issue ref) - 4`, cut at a word boundary, with the `(#N)` reference appended after |
| Free-form body | A `<!-- fabro:commit-body:start -->` block that `pr-review`'s `merge_gate` check 12 parses back out as the squash-merge body |
| No labels | `agent-authored`, which `merge_gate` check 2 requires before it will merge |
| Fires at the **publish stage**, after a fully successful run | The PR must exist **mid-graph** so `trigger_review` can fire the review on it |

An LLM-written body satisfies none of the machine contracts downstream of it. Enabling
`[run.pull_request]` would also make `[run.run_branch] push = false` a hard config error,
which `backlog`'s branch-hygiene note wants to keep available.

**Verdict:** stays off. Permanently.

---

## 10. A setting that skips the checkpoint commit when nothing changed

**Looks like:** an empty stage (every command node that writes only under `/tmp/fabro`)
should produce no commit. Then `backlog`'s per-stage push would stop starting CI on the
PR branch.

**Is not.** `git_checkpoint` sets `options.allow_empty = true` unconditionally
(`lib/components/fabro-workflow/src/sandbox_git.rs:105`). Neither `[run.checkpoint]` nor
any graph attribute exposes it. The commit subject is fixed as
`fabro(<run>): <node> (<status>)` (`sandbox_git.rs:89`). The only other text is a fixed
footer (`lib/components/fabro-checkpoint/src/author.rs:41-51`), in which only the author
name and email can be set. So a `[skip ci]` marker cannot be added either. Measured cost: womens-fantasy-sports#1247 carries 80 commits and 78 Actions
runs, 53 of them cancelled. The `watch_checks` checkpoint `4e5e751` changes 0 files.

**Verdict:** not configurable at 0.357. The lever that exists is to stop *pushing* the
commits: `[run.run_branch] push = false`, adopted as ADR 0011 D1. Worth filing upstream as
"skip the checkpoint when the tree is unchanged".

---

## Also checked, also fine

- **`lifecycle.stop_on_terminal` does not leak containers.** Fabro calls `sandbox.stop()`
  at terminal (`lib/components/fabro-workflow/src/pipeline/finalize.rs:215`) and never
  removes. Nothing in the tree calls `docker rm`. `ops/fabro-sandbox-sweep.sh` fills a real
  gap, and its header's conclusion — *"There is no leak to fix in fabro; there is a janitor
  missing from this tree"* — is right.
- **No run-branch reaper exists.** Nothing deletes `fabro/run/<id>` after a run ends.
  `ops/fabro-branch-sweep.sh` fills a real gap.
- **The `sandbox_cleanup` hook cannot do the `docker rm`.** It fires *before*
  `sandbox.stop()` — `finalize.rs:213` runs the hooks, `finalize.rs:215` stops the sandbox —
  so the container is still running when the hook executes.
