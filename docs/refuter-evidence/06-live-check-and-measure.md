# 06: live check and measure (operator, after push + `make deploy-scheduler`)

Task 05 ends this series' local work. Task 06 is what happens **after** the reviewer pushes
tasks 01-05 to `main` and runs `make deploy-scheduler`. This file describes the steps; it is
carried out by the operator. Do not run them from this working session.

## 1. Live check, first 5 Refuter visits after deploy

For each of the first 5 Refuter visits after the deploy:

- `fabro events <run> -p` shows `refute_prep` and the `validation` input served;
- every command criterion is `met` citing a `.fabro/ci.sh` line, or `unverifiable` with a
  stated reason;
- **no behavioural criterion** is credited to CI.

If any behavioural criterion is credited to CI, revert the push.

## 2. Measurement at deploy + 7 days

Use `./ops/fabro-scorecard.sh` for:

- the new unverifiable-only row per day;
- fast-lane held-by-Refuter per day;
- the CI-credited count against operator fixes and reverts.

Compare against the done-when criteria and record the result in the deployment log and on
issue #40. Stream summaries; never export raw events.

## 3. Out-of-scope classes

Check whether the three out-of-scope classes (diagnosis comment, "tests pass unchanged",
lint probe) recurred (D1).
