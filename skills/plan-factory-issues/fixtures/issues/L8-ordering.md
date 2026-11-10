# feat(invoices): email the exported CSV

<!-- fabro:split-child parent=12 after=13 -->

Blocked by #13.

## Context

- The export route lands in #13.

## Requirements

1. A button emails the export to the operator.

## Acceptance criteria

- [ ] Pressing the button sends one email with the CSV attached.

## Non-goals

- Scheduled emails.

## Test expectations

- pytest with the in-memory mailer.
