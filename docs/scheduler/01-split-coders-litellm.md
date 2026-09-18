# Split the `coders` model group into `coders-a` and `coders-b`

## Tracer-Bullet Outcome
An operator can send a completion to `coders-a` and have it served by
`10.10.0.29`, or to `coders-b` and have it served by `10.10.0.56`, while `coders`
keeps load-balancing both. Proven by a `curl` against each name.

## User Story
As the operator, I want each inference box addressable by name so that the
scheduler can pin one run to one box instead of letting LiteLLM round-robin two
concurrent runs into each other's queue.

## Description
Live LiteLLM state only. **No repository code changes.** Two new model rows are
created that duplicate the existing `coders` deployments, differing only in
`model_name`. The existing two `coders` rows are left exactly as they are.

Afterwards the group table reads:

| model_name | api_base |
|---|---|
| `coders`   | `http://10.10.0.29:8000/v1` (existing, untouched) |
| `coders`   | `http://10.10.0.56:8000/v1` (existing, untouched) |
| `coders-a` | `http://10.10.0.29:8000/v1` (new) |
| `coders-b` | `http://10.10.0.56:8000/v1` (new) |

`coders` is retained deliberately: it is the `default('coders')` fallback in
draft 05 and the target of every hand-fired run.

## Context Pack
- Source decisions: overview decision 12 (box pinning is a LiteLLM model group,
  not a fabro provider) and decision 19. ADR 0005.
- Repo facts: `ops/README.md` § "LiteLLM `coders` deployments" documents the
  current two rows and the master-key requirement. `ops/provision-litellm-models.sh`
  is the existing precedent for scripted LiteLLM provisioning.
- Non-goals: changing `timeout`, `max_parallel_requests`, or anything about the
  existing `coders` rows. Touching z.ai or kimi rows. Any `.fabro` file.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft (repo is unchanged; this is
  server-side state)

## Implementation Contract
- Expected files: `ops/provision-coder-groups.sh` (new, executable, POSIX `sh`),
  and an `ops/README.md` table update.
- Interfaces and names: the script takes no arguments and is idempotent — it
  lists models first and creates only what is missing.
- Verified external contracts (verified against
  `https://litellm.herrington.services/openapi.json` on 2026-09-18):

  ```
  POST   /model/new                 create a model row
  POST   /model/update              replace a model row (full litellm_params)
  PATCH  /model/{model_id}/update   partial update
  GET    /v1/model/info             list rows
  POST   /model/delete              delete a row
  POST   /fallback                  fallback management (Postgres-backed, NOT values.yaml)
  ```

  Authentication: **the master key, not `FABRO_LITELLM_KEY`.** That key is an
  `internal_user` and `POST /model/update` answers, verbatim:

  ```json
  {"error":{"message":"{'error': 'User does not have permission to make this model
   call. Your role=internal_user. You can only make model calls if you are a
   PROXY_ADMIN or if you are a team admin, by specifying a team_id in the
   model_info.'}","type":"auth_error","param":"None","code":"403"}}
  ```

  Retrieve it with (kubectl context `admin@nauvoo` works from the Mac):

  ```sh
  kubectl -n litellm exec deploy/litellm -- printenv PROXY_MASTER_KEY
  ```

  The exact `litellm_params` of the existing `.29` row, copied from
  `GET /v1/model/info` on 2026-09-18 — the new rows must match this except for
  `model_name`:

  ```json
  {
    "api_base": "http://10.10.0.29:8000/v1",
    "timeout": 600.0,
    "allow_client_keepalive_override": false,
    "use_in_pass_through": false,
    "use_litellm_proxy": false,
    "use_xai_oauth": false,
    "merge_reasoning_content_in_choices": false,
    "model": "openai/deepseek-v4-flash-0731-iq3-xxs",
    "max_parallel_requests": 1
  }
  ```

  The `.56` row is byte-identical except `"api_base": "http://10.10.0.56:8000/v1"`.
  Neither row has an `api_key`: `GET /credentials/by_model/{model_id}` returns an
  empty body for both, because these are plain-http LAN endpoints.

  `POST /model/new` body shape (same envelope `/model/update` accepts and which
  was verified working on 2026-09-18):

  ```json
  { "model_name": "coders-a", "litellm_params": { ...as above... } }
  ```

- Behavior rules:
  - Idempotent. `GET /v1/model/info` first; skip creation when a row with that
    `model_name` and `api_base` already exists. Report what it skipped.
  - `DRY_RUN` defaults to `1` and prints the payloads without POSTing, matching
    `ops/fabro-auto-merge-switch.sh` and `ops/fabro-monitor.sh`.
  - The master key is read from the environment (`LITELLM_MASTER_KEY`), never
    hardcoded, never echoed, never written to a file.
- Error and security rules: exit non-zero with a one-line reason on any non-2xx.
  On `403`, the reason must say the key is not `PROXY_ADMIN`. **This repository is
  public — the key must not appear in the script, in a comment, or in output.**

## Acceptance Criteria
- [ ] `GET /v1/model/info` lists four rows whose `model_name` is one of `coders`,
      `coders-a`, `coders-b`, with the `api_base` mapping in the table above.
- [ ] A completion to `coders-a` is served by `.29`: while it runs,
      `curl http://10.10.0.29:8000/slots` reports `is_processing: true` and
      `http://10.10.0.56:8000/slots` does not.
- [ ] The same, inverted, for `coders-b` and `.56`.
- [ ] Re-running the script reports "already present" for all four and POSTs nothing.
- [ ] `git grep -i` for the master key value across the repo returns nothing.

## Test Expectations
No unit-test framework — this is live infrastructure provisioning, so the test is
the acceptance run. Record the output in the deployment log (draft 14).

Concrete verification command and its expected output:

```sh
curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  https://litellm.herrington.services/v1/model/info \
| jq -r '.data[] | select(.model_name|startswith("coders")) |
         "\(.model_name)\t\(.litellm_params.api_base)"' | sort
```

Expected exactly:

```
coders	http://10.10.0.29:8000/v1
coders	http://10.10.0.56:8000/v1
coders-a	http://10.10.0.29:8000/v1
coders-b	http://10.10.0.56:8000/v1
```

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: "Thread `coder_pool` through both root stylesheets"

## Labels
`chore`, `ops/litellm`, `priority:high`

## Estimate
Small

## Risk
3 - live shared gateway used by more than fabro; a malformed `litellm_params`
that drops `api_base` breaks the `coders` group for every run. Mitigated by
creating new rows rather than editing existing ones.

## Validator Stopping Point
The `jq` command above prints exactly the four lines shown, and a completion to
each of `coders-a` and `coders-b` returns 200.
