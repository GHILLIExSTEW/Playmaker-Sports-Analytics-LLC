# Capper API integrations

Selected cappers can integrate API-Sports reads into their own **server-side**
systems. This is an authenticated HTTP endpoint, not a Discord webhook or a
push subscription. Never give recipients the underlying provider key.

## Owner setup

1. Apply [the credential migration](supabase/migrations/20261017060000_capper_api_credentials.sql)
   after the existing shared-budget, Owner-role, member-profile and capper-roster migrations.
2. Deploy the Supabase Edge Function `capper-api` with gateway JWT verification
   disabled, as declared in [config.toml](supabase/config.toml). The function
   authenticates **every call** using its private capper key instead.
3. Configure `API_SPORTS_KEY` as an Edge Function secret. Supabase supplies
   `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. Do not commit or expose them.
4. Open Sports > API integrations (Owner only), `/integrations`.
   The recipient must be a current verified capper and must have signed in with
   Discord and completed website age verification. Create a labeled key using
   their Discord ID. Copy it once and deliver it privately.
5. Revoke keys on that page when access should end. To rotate, create a new key,
   update the recipient's system, then revoke the old one. Revocation prevents
   new reservations; calls already started may complete.

Only hashed keys are stored. The plain-text key is shown once and is not
retrievable later. If issuance times out, reload the key list and revoke any
uncertain key before retrying. Each integration key is a bearer credential:
anyone possessing it can consume the shared allowance.

## Request

`POST https://lhsevzucmmzetpshpffv.supabase.co/functions/v1/capper-api`

Headers:

```text
Authorization: Bearer YOUR_PRIVATE_CAPPER_KEY
Content-Type: application/json
```

Body:

```json
{
  "product": "american-football",
  "path": "/games",
  "params": { "league": 1, "season": 2026, "date": "2026-10-17" }
}
```

Products: `american-football` (NFL and NCAA), `basketball` (NBA and NCAA),
`football` (soccer), `hockey`, `baseball`, `rugby`, `handball`, `volleyball`,
`mma`, `formula-1`. Use the corresponding API-Sports documentation for supported
data paths and parameters. Provider subscription restrictions still apply.
Paths are relative data routes such as `/games`, `/players`, `/games/players`
or `/fixtures`; `/status`, arbitrary hosts, redirects and non-GET provider
operations are not supported. No wagering or administration is exposed.

Python standard-library example:

```python
import json
import os
from urllib.request import Request, urlopen

request = Request(
    "https://lhsevzucmmzetpshpffv.supabase.co/functions/v1/capper-api",
    data=json.dumps({
        "product": "american-football",
        "path": "/games",
        "params": {"league": 1, "season": 2026},
    }).encode(),
    headers={
        "Authorization": f"Bearer {os.environ['PLAYMAKER_CAPPER_API_KEY']}",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urlopen(request, timeout=35) as response:
    result = json.load(response)
print(result["request_id"], result["data"])
```

Store the capper key in your system's secret manager or server environment.
Never include it in browser/mobile bundles, public repositories, query strings,
Discord messages or logs. This endpoint is intended for backend integrations,
not direct cross-origin browser calls.

## Response and limits

Successful responses contain `request_id`, `product`, `path`, `provider_status`
and `data` (the API-Sports JSON response). Errors have non-success HTTP statuses
and an `error` explanation. Preserve `request_id` for support:

- `400`/`415`: invalid input/content type; no provider reservation.
- `401`: missing, invalid, revoked or no-longer-eligible credential.
- `429`: shared daily allowance exhausted, or provider rate limiting
  (the latter has a `request_id` and `provider_status`).
- `502`: provider failure, logical API error, timeout, redirect or invalid data.
- `503`: authorization/budget/audit infrastructure unavailable.
  Read the error: a provider call may already have been attempted.

There is **no five-request user cap, 20-request member product cap or member
five-minute cooldown** for this endpoint. The shared **7,500-request UTC-day**
cap remains atomic across these calls, bot jobs and other refreshes.
Calls count toward the shared system bucket; key-level usage is recorded
separately for attribution. Integration access does not bypass the limits on
ordinary member commands or grant website research, model or administration access.

One invocation forwards at most one provider GET; pagination requires another
invocation per page. Provider errors/timeouts consume the reservation too.
No automatic retries or refunds. A timed-out call's outcome may be uncertain:
do not automatically retry. Responses are limited to 2 MiB; requests to 16 KiB,
20 scalar parameters and a 20-second provider timeout. Requests fail closed if
budget checks are unavailable. Key revocation or loss of verified capper/age
eligibility denies subsequent calls.
