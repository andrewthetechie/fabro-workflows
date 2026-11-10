# feat(editor): underline misspellings

The editor shows no spelling feedback.

## Context

- `src/editor/decorations.ts:10` is where inline decorations are added.

## Requirements

1. Misspelled words are underlined; names from the project glossary are not.

## Acceptance criteria

- [ ] Misspellings are underlined in all three editors on macOS and Linux; glossary names are not.
- [ ] Manual check: typing in a long scene stays responsive.
- [ ] A screenshot of the underline is attached.

## Non-goals

- Grammar checking.

## Test expectations

- vitest: the decoration function returns one range for `teh` and none for a glossary name.
