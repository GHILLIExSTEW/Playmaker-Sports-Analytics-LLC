# Capper API integrations

## Phone-friendly research tools

Approved cappers can use `/tools` on the website without an API key or terminal.
Owners approve or block Discord IDs in `/integrations`. An active integration
key also qualifies unless the Owner explicitly blocks website research access.
Website-only approval does not issue an API key or grant staff administration.
Approved cappers can see the selected matchup's existing Playmaker projected
scores, total and two-sided win estimates when an eligible pregame projection
is stored. Only summary outputs, model version, samples and timestamps are
exposed, not model internals, backtests or capper performance data.
Finished/started games and mismatched team/kickoff projections are excluded.
These are model estimates, not sportsbook probabilities or guaranteed results.
Successful team pulls queue recalculation with the existing ratings-v2 engine
from the just-saved cache. The page waits for completion before reloading the
model card. Older projections are flagged, not presented as newly calculated.
Unchanged inputs may produce identical scores; insufficient inputs remain
unavailable. Only the selected team is fetched, not both teams automatically.
The AI may explain supplied estimates, but cannot invent missing predictions.

NHL starting goalies remain explicitly unknown. API-Sports Hockey v1 documents
no lineup/goalie-confirmation endpoint. The page and AI must not infer confirmation
from rosters, prior appearances or a model estimate. SportsDataIO's
[official NHL workflow guide](https://sportsdata.io/developers/workflow-guide/nhl)
describes projected and confirmed goaltenders, but licensed real-data access is
still required; the scrambled trial remains excluded.

The page provides cached game search, recent form, supported player logs and
threshold comparisons, available NFL injuries, venue weather, and market
snapshots with timestamps and missing/stale coverage warnings.
Provider schedule placeholders with missing names or invalid team IDs are
excluded from member game searches before counting and pagination.
Inside a matchup,
select **Team to refresh**, then tap **Pull latest team data**. This fetches that
team's games for the selected matchup's season, plus the current NFL injury feed
where supported. It saves the provider results and atomically queues a model
refresh. The Edge Function returns HTTP 202 with `model_refresh: "pending"` and
the pull's `request_id`. The browser polls the user-scoped
`capper_model_refresh_status(p_request_id)` RPC before reloading the report.
Failed provider pulls do not enqueue models. A failed/unavailable model worker
is shown explicitly, without retrying the provider pull or claiming a fresh
prediction. Pending/running jobs expire after three minutes without automatic
retry. Leaving the page stops browser polling but does not cancel the saved job.
Weather, odds and player logs are not refreshed by this button; their original
cache timestamps remain visible. No SportsDataIO trial data is used.

Data pulls have a **separate** allowance: 50 per capper and 500 shared per UTC
day, with a 15-second per-user cooldown. Each button press uses one pull, including
failed provider attempts. NFL pulls reserve two API-Sports calls (games and
injuries); other leagues reserve one (games). All calls also count against the
existing shared 7,500-provider-request UTC-day ceiling. Incomplete, paginated or
invalid responses do not overwrite cached data, and no automatic retries occur.

The `capper-research` Edge Function uses the signed-in user's verified Supabase
session and database approval, never a browser-held provider key. Set its
server-only `OPENAI_API_KEY`; optionally set `OPENAI_RESEARCH_MODEL` (default
`gpt-4.1-mini`). Deploy with gateway JWT verification disabled as in
[config.toml](supabase/config.toml); the handler verifies the session through
Supabase Auth and uses the user's JWT for reads and allowance RPCs. Only the
server uses the service role to persist validated provider results; a browser
cannot write provider data or complete a pull.
Apply [the tools migration](supabase/migrations/20261017070000_capper_research_tools.sql),
then [the AI limits migration](supabase/migrations/20261017080000_capper_research_ai_limits.sql)
and [the team-pull migration](supabase/migrations/20261017090000_capper_team_pulls.sql).
Apply [the named-matchups migration](supabase/migrations/20261017100000_research_named_matchups.sql)
to omit provider placeholders from member searches and their totals.
Apply [the capper projection migration](supabase/migrations/20261017110000_capper_model_projections.sql)
and redeploy `capper-research` to include approved model summaries in AI analysis.
Deploy all three function files (`index.ts`, `handler.ts`, `pull.ts`) and
configure server-only `API_SPORTS_KEY` for data pulls.
Apply [the pull/model queue migration](supabase/migrations/20261017120000_capper_pull_model_refresh.sql)
before deploying the updated function/website and restarting the Python bot.
The bot's existing enabled `ModelProjections` cog polls every two seconds,
claims private service-only jobs, and calls the same locked
`ModelProjectionService.refresh` engine as scheduled projections, without
refreshing providers or rerunning backtests. Recalculation uses no extra
API-Sports allowance or AI action. Model internals and worker claim tokens
remain service-only. Jobs are completed only after projection writes finish;
estimates no longer produced in the current model window are removed.
The bot must have `API_SPORTS_KEY` configured (the existing model-cog enablement)
and database service-role access.

On the Proxmox bot VM/container, pull the latest repository and restart the bot.
If using the supplied [systemd unit](scripts/discord-bot.service):

```sh
cd /opt/discord-bot
git pull --ff-only
sudo systemctl restart discord-bot
sudo systemctl status discord-bot --no-pager
sudo journalctl -u discord-bot -n 50 --no-pager
```

Afterward, pull a team on `/tools`. Expect **Recalculating model...**, then the
completion message and a model generation timestamp after the pull. Check logs
for `capper_model_refreshed`; a job can validly produce zero projections.

AI extraction and analysis share 50 actions per user and 500 globally per UTC
day, with a 15-second user cooldown. Failed provider actions count; no automatic
retries or refunds. Cached browsing does not consume these actions or API-Sports
requests. Screenshot extraction accepts one PNG/JPEG/WebP up to 4 MiB, sends it
to OpenAI with `store: false`, and does not persist it in the website database.
Provider retention policies still apply. Users must review extracted notes
before analysis; notes and report dates are labeled user-supplied, unverified.
Analysis receives server-loaded cache data, not client-authored evidence.
The Team form section is rendered deterministically from cached team scores,
opponents, results and numeric margins (up to five final scored games per team
before the matchup). It is not AI-authored. Historical game scores are excluded
from AI input, and the model is instructed not to recap individual results.
Analysis must not add qualitative margin labels such as "close" or "narrow".
Responses containing those unsupported game-result labels are rejected explicitly,
not silently rewritten or automatically retried; the AI action still counts.

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
