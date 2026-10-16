# Task 05: the `scout-summary` Discord kind, provisioning, the host check, the runbook

Read `00-overview-and-contracts.md` first (D16, D17; contracts C8-C10).

## Goal

Everything the operator needs to deploy and run scout, written and tested offline. Nothing
here touches the host. Task 07 does.

## Steps

1. **`.fabro/workflows/backlog/scripts/discord-notify.sh`**, a new kind `scout-summary`.
   Mirror `merge-queue-summary` with these differences:
   - add `scout-summary` to the usage line (line 4);
   - declare `scout_summary=""` beside `mq_summary=""`, and read it with the same
     `wget ... | grep -o '"scout_summary":"[^"]*"' | tail -1 | cut -d'"' -f4`, then the same
     `sed 's/[\\"]//g' | cut -c1-1500`, with a one-line comment naming ADR 0027 D17;
   - add `scout-summary` to the `arch-summary|dependabot-summary|merge-queue-summary)` case that
     clears `issue`;
   - the message case: `[ -n "$scout_summary" ] || exit 0`, then
     `scout_text=$(printf '%s' "$scout_summary" | sed 's/ | /\\n/g')` and
     `msg="🔭 fabro scout${subject}\\n${scout_text}"`. It does **not** use
     `merge-queue-summary`'s once-an-hour state file: scout runs once a day.

   This file is executed by hooks on the host, so changing it is a deploy
   (`make deploy-notify`, task 07).
2. **`ops/test-task-gates.sh`**: after the `merge-queue-summary` Discord kind section (around
   line 7430), add `scout: the scout-summary Discord kind`, copying that section's harness.
   Cases: an empty `scout_summary` posts nothing; a two-part summary posts one message whose
   content has the 🔭 header and both parts on separate lines; a summary holding a `\` and a `"`
   posts a message with neither.
3. **`ops/provision-server-state.sh`**: after the merge-queue block, add a commented block in
   the same style (what scout is, ADR 0027, that the schedule is created disabled and the
   operator enables it with `ops/fabro-automation-schedule.sh`, and that the rows carry no
   auto-merge token), then echo `Provisioning scout automations...` and add C10's four lines
   exactly. Update the header comment's automation count if it states one.
4. **`ops/check-host.sh`**: add `[scout]="9 nodes, 11 edges"` to the baseline map, add
   `workflows/scout/workflow.fabro` to the file list (line 50), and add `scout` to the
   `for w in ...` list (line 70). The operator confirms the counts in task 07, because there is
   no `fabro` binary on the Mac.
5. **`ops/README.md`**:
   - the `provision-server-state.sh` row: twenty-eight automations, seven per repo, and say
     the `scout` schedules are created disabled too;
   - a new runbook section `## Scout (ADR 0027)`, placed after the merge-queue section, with:
     what it does in three sentences; the four schedules (C10); how a repository goes live
     (`filing = true` in its `.fabro/scout.toml`, committed in that repository); how to fire
     one run by hand (`api:manual`: write the same command the dependabot section gives for
     `dependabot-<repo>`, with `scout-<repo>` in its place); how to read a run
     (`fabro events <run> -p`, and the `plan` node's stderr lists every action); how to stop it
     (disable the schedule with `ops/fabro-automation-schedule.sh`, or set `filing = false`);
     and how to silence one finding (close its issue as not planned).

## Done when

- `make check` passes, the full one.
- `sh -n .fabro/workflows/backlog/scripts/discord-notify.sh` is clean (it runs under busybox `sh`).
- One commit: `feat(ops): scout-summary notify kind, scout automations, host check, runbook (scout 05, #32)`.
