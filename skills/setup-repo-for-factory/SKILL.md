---
name: setup-repo-for-factory
description: >
  Onboard a repository into a Fabro automation factory: its `.fabro` contract files, GitHub labels,
  the factory's scheduler and automation rows, and the live checks. Use when adding a repo to the
  factory, or re-checking one already in it. Run inside the target repo.
---

# Onboarding a repository into a Fabro factory

`onboard.py` holds the rules. It reads the repository's CI and the factory's own files, renders every
contract file from a plan and templates, computes each factory edit and schedule, and names the next
step. Your work is the judgment it hands you: the interview, the `review` rows in the plan, and
reading a failure.

```sh
ONBOARD=<this skill's base directory>/scripts/onboard.py   # needs uv, gh, git; cargo and ssh to the host
```

Run it from the target repo's root. **`$ONBOARD status` is the loop:** do its `NEXT`, run it again.
It is safe to re-run after any interruption.

A `STOP:` line is a fact about the repository or factory. Show it to the user. Every contract file
comes from the plan, so a change goes in the plan and then through `render`.

These change things other people see. Get the user's yes for each, in the interview if you can:
`labels --apply`, pushing the branch and opening the PR, pushing the factory commit (a push to `main`
is live), `make deploy`, `smoke --fire`, `schedules --apply`, and the real issue.

## 1. Interview

Run `$ONBOARD facts`. Show `blockers` and `warnings`; a blocker stops onboarding until it is resolved
([REFERENCE.md, Blockers](REFERENCE.md#blockers)). Confirm `repo`, then ask only
`questions_for_the_user`. Every other answer is already in `facts`. Then:

```sh
$ONBOARD init --stack <id> --priority <n> --deploy deploy|stage [--alias <name>]
```

**Done when** `init` has written the plan and printed its path.

## 2. Resolve the plan

The plan lists every step of every pull-request workflow, each with a role. Read
[REFERENCE.md, Resolving a step](REFERENCE.md#resolving-a-step), then decide each `review` row. Edit
the plan file, or settle a whole workflow at once:

```sh
$ONBOARD resolve "docker-main.yml > *" --role skip --reason "builds images; no Docker in the sandbox"
$ONBOARD plan-check
```

`plan-check` then raises `env_review` rows for variables CI takes from secrets. Set each to a literal
test value or drop it with a reason. Read the suggested `gate` rows as well: the suggestions come
from pattern matching, and a suggestion you leave standing is your decision.

**Done when** `plan-check` prints `plan ok`.

## 3. Render and check

```sh
$ONBOARD render
$ONBOARD check
```

`render` keeps any existing file that differs from the plan and says so. Compare the two, move what
the old file knows into the plan, and re-run with `--force`.

**Done when** `check` prints no `FAIL`.

## 4. Labels

`$ONBOARD labels` lists what is missing; `--apply` creates only those.

**Done when** `labels` reports the repository has every factory label.

## 5. Prove the contract

Commit `.fabro/` alone on a branch and push it, keeping the executable bits. Then run this in the
background; it runs `setup.sh` and `ci.sh` from a fresh clone in the real image on the host, and a
real suite takes minutes:

```sh
$ONBOARD contract-run
```

On a failure, read [REFERENCE.md, Contract failures](REFERENCE.md#contract-failures).

**Done when** it prints `PASS`. The pass is recorded against the `.fabro/` tree, so any later change
to the contract asks for another run.

## 6. Land the contract

Open the PR. A PR that changes `.fabro/ci.sh` or `.fabro/test.toml` trips `config_changed`, so a human
merges this one. Registration waits for it: `arch-review` and `dependabot` rows are created with their
schedules on, and a run with no contract fails at `prep`.

**Done when** `status` reports the contract on `main`.

## 7. Register

Pull the factory checkout to `origin/main`, then:

```sh
$ONBOARD register            # prints the diff; read it
$ONBOARD register --apply
$ONBOARD gate
```

Commit with the exact `git add` and message `register --apply` prints, and push.

**Done when** `gate` prints no `FAIL` and the commit is on the factory's `origin/main`.

## 8. Deploy

With `--deploy stage`, stop here and tell the user the commit, that `make deploy` is theirs to run,
and that steps 8 to 11 remain.

Otherwise run `make deploy` from a clean factory checkout at `origin/main`. It never restarts fabro.
Then run `$ONBOARD verify`.

**Done when** `verify` prints no `FAIL`. Reconcile any drift by hand, with the user; provisioning
reports drift and never rewrites a row.

## 9. Smoke

`$ONBOARD smoke --fire` hand-fires this repository's `merge-queue` and `scout` rows and waits. Neither
has an agent stage or takes a coder slot, so this proves the environment and the rows at no inference
cost.

**Done when** it prints `PASS`.

## 10. Schedules

`$ONBOARD schedules --apply` turns on the `merge-queue` and `scout` schedules, which are created off
until a hand-fired run passes. The other rows' schedules stay as provisioned
([REFERENCE.md, Automations](REFERENCE.md#automations)).

**Done when** `status` reports them on.

## 11. A real issue

Ask the user for one small, real issue and label it `agent`. This opens a real pull request. The
scheduler dispatches it; `needs-triage` fires nothing, because every `issue-triage` schedule is off.

If nothing claims it, read `coder_pools` and `peak_hours` on the scheduler's `/health` before
suspecting the repository: drained pools and peak hours are normal.

**Done when** the issue carries `agent-in-progress` and its run is listed on the server.

Then tell the user what landed, every `reason` you wrote, and what is still open.

## Reference

[REFERENCE.md](REFERENCE.md) has what the tool cannot decide for you. Run the tests after changing the
tool: `uv run --no-project --python 3.11 --with pytest --with pyyaml pytest skills/setup-repo-for-factory/tests -q`.
Writing workflow graphs is the `/fabro-workflow` skill's job; this one writes contract files only.
