# Monitoring is out-of-band, and run failures stay in-band

**Status:** accepted (2026-09-16)

The factory's notification surface is `discord-notify.sh`, fired by hooks from
inside a run: `rescue`, `complete`, `failed`, `review-triggered`, `merged`,
`triage-question`, `triage-failed`. Every one of those presumes a run started and
finished well enough to execute a hook. **Anything that prevents a run from
starting or finishing — a dead container, a dead API, a dead scheduler, a run
wedged in a non-terminal state, an empty work queue, a full disk — never reaches a
hook.** That gap is what the turn-it-on monitor (`ops/fabro-monitor.sh`, see
`docs/turn-it-on/00-overview-and-contracts.md`) closes, and this ADR records what
it must never close.

The decision has two layers, made together because they are the same decision:

1. **Run-failure alerting stays in-band.** The monitor is health-only. It alerts
   on the six conditions in its table and never on a failed run, never on a
   failed stage, never on a node outcome.
2. **Host death is closed by an external heartbeat, not by moving alerting
   in-band.** The monitor pings a dead-man's URL (`FABRO_HEARTBEAT_URL`) at the
   end of every run; the external service alerts when the pings stop. A monitor
   and the thing it monitors share a host, so host death is silent to anything on
   that host by construction.

## Alternatives weighed

- **The monitor also alerts on failed runs.** Rejected: double pings. A run
  failure would page twice — once from the hook (enriched: repo, issue, PR
  links, review run id) and once from the monitor (unenriched). Two alarms per
  event trains the operator to ignore the channel, which destroys the value of
  both. Alerting is only useful at a rate a human will actually read.
- **The monitor replaces the hooks.** Rejected: it loses the enrichment. Hooks
  know *which* run, *which* repo, *which* PR; a monitor scraping the runs list
  knows a status code. Replacing richer signals with poorer ones to unify the
  pipeline is a bad trade.
- **Fabro monitors itself** (a watchdog workflow, a graph node that checks the
  server). Rejected as circular: a dead server means a dead watchdog. A monitor
  that dies with its subject reports health exactly when health reporting
  matters most.
- **A SaaS monitoring product.** Rejected for a single-tenant one-user system: a
  new dependency, a new secret, and an alerting path the operator does not
  control, where a host cron script plus the existing Discord webhook is
  sufficient. The one genuinely external piece — the heartbeat — is a generic
  URL ping, deliberately provider-agnostic.

## Consequences

- Two alert vocabularies exist and are visually distinct: in-band hook messages
  say `fabro …` and carry run context; monitor messages say `fabro monitor: …`
  and carry one line of health detail. An operator scanning Discord can tell
  which layer spoke without opening either.
- The monitor reads its credentials (dev token, Discord webhook) out of the
  container at runtime and stores nothing. Its blast radius equals the hooks'
  it complements; a token rotation breaks it loudly (its 401 condition names
  rotation as the cause), never silently.
- Because failure alerting stays in-band, the monitor's conditions are all
  mechanical — there is no judgment and no model in it — which keeps it cheap
  enough to run every 15 minutes and boring enough to trust.
- The heartbeat is optional-but-recommended and sequenced after the monitor
  works without it (turn-it-on task 03; turn-on in task 06 soft-gates on it).
  Until it exists, host death is silence, and that residual risk is recorded in
  the series overview rather than papered over.
- The dead-scheduler condition gates on "any automation has an enabled
  schedule", read live each run, so the alarm self-activates when turn-on
  enables schedules and is correctly inert before that — and an operator who
  disables every schedule mid-incident also disables the alarm, which is the
  right behavior: nothing is supposed to run.
