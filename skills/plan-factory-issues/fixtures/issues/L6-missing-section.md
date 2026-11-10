# fix(auth): reject an expired reset token

An expired password-reset token still resets the password.

## Context

- `src/auth/reset.py:31` checks the signature and never the expiry.

## Acceptance criteria

- [ ] Using a token one second past its expiry returns 400 and leaves the password unchanged.

## Test expectations

- pytest with a frozen clock.

## Post-Deploy Checks

- The reset email still arrives.
