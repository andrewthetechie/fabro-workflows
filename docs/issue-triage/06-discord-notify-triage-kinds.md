# Task 06 — `discord-notify.sh`: the `triage-question` and `triage-failed` kinds

**Depends on:** 03. **Blocks:** 08. **LLM level:** ordinary — but the script runs in
Alpine with no bash, no jq and no curl, so nothing here may assume otherwise.

Two new kinds in `.fabro/workflows/backlog/scripts/discord-notify.sh`. It lives under
`backlog/scripts/` and is called by hooks in **all three** workflows by absolute
container path; do not tidy it into `issue-triage/scripts/`.

## Part 1 — carry the questions in the message

The existing enrichment block greps the run-state stream for `issue_number` and
`pr_url`. Add `triage_questions` and `issue_url` the same way:

```sh
questions=$(… state stream … | grep -o '"triage_questions":"[^"]*"' | head -1 | cut -d'"' -f4)
```

Sanitise exactly as `review_err` already does — `sed 's/[\\"]//g'` — then truncate hard.
Discord rejects a payload over 2000 characters and the message is built by string
concatenation, so an agent-authored question containing a quote or a backslash would
otherwise produce invalid JSON and a silently dropped notification. `triage_gate`
already truncates to 1200; truncate again here rather than trusting the producer.

**Correction (2026-09-16, harness-proved).** Sanitising here is too late for a quote.
`grep -o '"triage_questions":"[^"]*"'` runs against the *serialized* run state, so it
stops at the first `\"` — a fixture question containing `the \"gold\" one` delivered
`- Which fixture set is canonical, the ` and dropped every question after it. The
payload stayed valid JSON, so the acceptance check below passes on its first half and
fails on its second. Fixed at the producer: `triage_gate` strips `"` from the value
before publishing it, so no escape ever reaches this grep. The `sed` stays for
backslashes.

**`issue_url` is not added.** Task 06 originally asked for it alongside
`triage_questions`, but nothing consumes it — the links block already builds the issue
URL from `repo_url` and `issue` — and each of these greps re-streams the whole run
state, which can be megabytes. Dropped.

## Part 2 — the two kinds

```sh
  triage-question)
    msg="❓ fabro issue triage needs answers${subject}
${questions}
Answer within 30 minutes at ${base_url}/runs/${run_id}, or it will post them on the issue."
    ;;
  triage-failed)
    msg="🟠 fabro issue triage released a claim without finishing${subject}"
    ;;
```

The 30-minute figure and the run link are the whole point of this kind: Discord
**cannot answer a gate** — Fabro's chat integration for that is Slack — so the message
has to say where to go and how long there is. Keep the two facts together; a ping that
says only "needs answers" costs a browser trip to discover the deadline.

`subject` already resolves to the repository and issue number through the existing
enrichment, so no new lookup is needed for it.

**Correction (2026-09-16, found while verifying the deployed copy in-container).** It
did not. The enrichment greps `'"issue_number":[0-9]*'`, which assumes the value is a
JSON *number* — true for `backlog`, whose state carries `"issue_number":2599`, but not
for `issue-triage`, whose `claim` builds the key with `jq --arg` so it arrives as
`"issue_number":"1195"`. The pattern matched zero digits, `issue` came back empty, and
every triage notification lost both its `#1195` and its issue deep link, degrading to a
bare repository URL — on the one message whose entire job is to send a human to a
specific issue inside 30 minutes. The grep now accepts an optional opening quote and
pulls the digits with `tr -dc`, which covers both workflows. Verified against a real
triage run *and* a real backlog run from inside `fabro-fabro-1`.

## Part 3 — deploy it

This is the one file in the change with two copies; the host's is authoritative at run
time:

```sh
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
```

A host copy that predates this task is not a failure: the `*)` fallback sends
`ℹ️ fabro run <id> notification (triage-question)`. That is the safety net, not the
plan — an operator who cannot see the questions will not answer inside 30 minutes.

## Acceptance

- `sh -n` passes, and it still runs under `/bin/sh` on Alpine: no `[[ ]]`, no arrays,
  no `curl`, no `jq`.
- Exercised directly in the container against a finished triage run:
  `docker exec fabro-fabro-1 sh -c 'FABRO_RUN_ID=<id> /storage/scripts/discord-notify.sh triage-question < /dev/null'`
  posts a message carrying the questions.
- A question string containing `"` and `\` still produces a valid payload and a
  delivered message.
- Every failure path still exits 0. A notification must never fail a run.
