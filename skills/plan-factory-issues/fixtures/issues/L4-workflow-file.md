# ci(release): build the macOS artifacts on tags

Release tags build no macOS artifacts.

## Context

- The release build runs only on Linux.

## Requirements

1. A tag build produces the macOS artifacts.

## Acceptance criteria

- [ ] `.github/workflows/release.yml` has a `macos` job that builds on tags.

## Non-goals

- Signing.

## Test expectations

- None; the workflow runs on the next tag.

## Expected files

- `.github/workflows/release.yml`
