# Proxmox deployment guide

## Removing leftover trial test data

Use `supabase/maintenance/cleanup_trial_test_data.sql` as the database owner in
SQL Editor. It targets only exact synthetic test identities under
`biz_trial_test`, not live seller records. Missing trial tables are explicitly
reported and skipped. It is one self-contained `DO` statement with no temporary
tables or cross-query session dependency. The default `apply_cleanup := false`
counts matches without deleting anything; inspect the SQL Editor NOTICE output.
To persist a reviewed cleanup, change `apply_cleanup` to `true` and rerun the
entire statement. Confirm a current backup before persistent deletion.
Run on a test copy first if the database contains manually altered test fixtures.

The regression tests normally roll back, so zero matching rows is expected.
No production schema objects have been confirmed unused; this script drops no
application tables, functions, products, storage, or real audit records.
Historical paid memberships, owner grants, and real expired trial claims remain
necessary. Removing real trial claims would allow repeat-trial abuse.

## Single paid membership and full trial rollout (October 5, 2026)

Current new-sale offer: HIGHROLLER, $19.99 USD for a one-time pass lasting exactly
30 days. Verified free community members receive ROOKIE. First-time eligible
members receive a free seven-day HIGHROLLER pass with all available features,
including analytics and Member Vault, subject to identical limits. It expires
without a charge or automatic conversion. ALL-STAR and historical longer-duration
offers are retired from new sales, not revoked for existing purchasers.

The repository update does not change Whop, production configuration, or
Discord mappings. Complete these steps before enrollment:

1. In Whop, create a **new** HIGHROLLER plan under `prod_6Hh9VAzQnzNiE`:
   USD 19.99, `one_time`, exactly 30-day expiration, hidden, zero stock, and
   unlimited stock disabled. Read back the seller, product, price, billing type,
   and expiration. Do not mutate historical purchases or delete old plans.
2. Create a new $0 one-time seven-day HIGHROLLER trial under the same product,
   hidden/zero-stock, with no automatic conversion. Read back its seller,
   product, billing type and expiration. Keep the historical ALL-STAR trial
   `plan_ejj9LwTfrJp5z` hidden; do not silently expand or remap historical trials.
3. Ensure all eight historical paid offers below remain hidden/zero-stock with
   unlimited stock disabled. Preserve memberships, dates, and historical roles.
4. Add the new paid plan ID to `WHOP_PAID_PLAN_IDS` locally and on Proxmox
   **alongside all historical paid IDs**. Never include free/trial IDs.
   Add only the new verified full-access trial ID to `WHOP_TRIAL_PLAN_IDS`;
   paid and trial lists must be disjoint. An empty trial list disables trial access.
   `WHOP_HIGHROLLER_PLAN_IDS` is retired and no longer read by the code.
5. Verify HIGHROLLER product-to-role mapping for both new variants. Preserve
   historical ALL-STAR and HIGHROLLER purchased access. ROOKIE grants How to
   join, Bankroll management, Announcements, Merchandise, Parlays / Promo plays,
   Free chat and Winning slips. Keep ROOKIE after HIGHROLLER expires.
   Staff select a daily free play when suitable; do not publish unsettled premium
   selections in ROOKIE channels. No free-play automation was added.
   Shared trial/paid roles are not
   evidence of payment. Keep one role owner; do not make the bot and Whop compete.
   Leave `PAID_MEMBER_ROLE_ID` unset for Whop-owned HIGHROLLER; never target
   ROOKIE. Optional dedicated bot-owned role sync now includes verified trials.
6. Apply `supabase/migrations/20261005000000_full_membership_trials.sql`,
   then deploy/restart the bot and deploy the website. The existing seller/plan RPC
   (`20261003030000_highroller_stats_access.sql`) and owner-grant migration
   remain prerequisites. The new tables are private and service-role-only.
   Trials are stored separately, never marked paid; ordinary complimentary
   grants remain ineligible. Claims persist after expiry/cancellation and pin
   seller, buyer, Discord identity, membership and original seven-day dates.
   A prior recorded paid membership before the trial disqualifies that identity.
   Use a complete historical membership/payment sync before opening trials;
   missing/deleted provider history cannot prove first-time signup.
   The owner's
   historical `highroller` grant remains valid without fabricating payment.
7. Test a real new paid pass, historical paid pass, trial, expiry, refund, stale
   snapshot, repeated trial, changed Discord/Whop identity and trial-to-paid
   overlap/role expiry. Paid members and eligible trials have identical tool
   and vault access. Whop can still grant a role independently of bot eligibility:
   verify provider repeat-signup controls so denied trials do not see premium
   channels. IP alone is not reliable (shared households/mobile networks/VPNs);
   no IP tracking was added. Multi-account evasion remains possible.
   Refresh stays disabled until its separate
   budget/license/quota-reset/live-validation gates pass; quotas are unchanged.
8. Keep checkout closed until provider/business approval and positive production
   authorization tests pass. Only then publish the new paid offer and trial.

### Historical implementation notes

Earlier tier-specific descriptions below are retained where they explain
historical provider records or migration names. The current single-membership
decision above supersedes their access/pricing restrictions.

## Current and previous season player statistics

The primary command is now `/playerstats sport league player refresh`.
Choose sport first, select a suggested league, then type a player name.
League suggestions are sport-scoped; player suggestions are scoped to both sport
and league and use stable provider IDs behind the selected names.
Autocomplete makes database reads only, never API calls while typing.
Suggestions grow as players are cached; an empty directory does not imply no
players exist. Verified paid/trial member/owner/moderator refresh can discover a full typed name.
Ordinary cached reports require verified paid or eligible trial access.

Apply `20261003070000_player_seasons.sql` after the existing budget and player-game
cache migrations. It seeds leagues and name suggestions from existing caches,
creates private compact season totals and shared league metadata, and adds the
atomic batch reservation RPC. The primary command no longer requires a game ID.
The old game-based report remains available separately as `/gamestats`.

Both seasons are displayed. Current season comes from the selected league's
cached event metadata, not blindly from the calendar year; split seasons such as
`2026-2027` use `2025-2026` as the previous season. Populated previous-season
records are retained and not refreshed by ordinary member requests. Missing
previous seasons require a first download; empty/unavailable responses are
retried after one day rather than frozen forever. Current snapshots reuse a
five-minute freshness window. Missing values/coverage are explicitly disclosed.

NFL/NCAA and soccer display provider season totals, preserving separate team
stints. F1 displays driver season standings. Basketball aggregates only additive
fields from available season game logs and filters them using selected-league
season game IDs; per-stat reported-game counts prevent missing values being
treated as zero. Shooting percentages/ratings are not summed. Incomplete provider
coverage is not represented as complete season history. Paginated search results
requiring more pages are rejected instead of silently saving a partial response.
Full logs are not persisted; compact totals and compact shared league metadata
are stored.

A cold lookup reserves up to five requests before any network call (including
identity discovery and league metadata as needed). Every reserved request counts
toward the existing five-user/day and twenty-member/product/day limits; unused
reservations after an error are not refunded. The shared product cooldown is
applied once per batch, not between calls inside it. Existing one-call commands
share the same atomic locks/counters. A normal refresh of a known player with
previous-season data already cached costs one request, or two for basketball/
American football when current league metadata also needs refreshing.
The batch SQL transaction/concurrency behavior and production deployment must
still be verified before enabling refresh. Existing activation/quota-reset gates
remain in effect; no flags were changed and no background backfill was added.

## Optional per-game player and driver statistics

Apply `supabase/migrations/20261003060000_player_game_stats.sql` after the
membership and API-budget migrations, then restart the bot to register
`/gamestats sport game_id player refresh`.
Use a game ID from `/results` or `/schedule`; player accepts a name or provider ID.
Omit player to list up to 20 available player/driver names and IDs. Searches
still cover the whole cached response, not only the displayed roster.
Reports show per-game provider groups, not calculated season totals. Supported
adapters are NFL/NCAA football, basketball, soccer and Formula 1 session results.
For optional F1 game reports, use `/results sport:formula-1` or `/schedule sport:formula-1` to get a
session ID; practice/qualifying results are explicitly distinguished from races.
F1 schedule/results refresh is disabled; `/gamestats` driver-result refresh is game-specific.

All verified paid and eligible trial members read shared cached snapshots. Those members, the owner grant, and
approved moderators can populate/refresh a game's snapshot when
`MEMBER_STATS_REFRESH_ENABLED` and `API_SPORTS_BUDGET_ENABLED` are enabled.
Each uncached refresh makes exactly one request to the applicable product,
under the existing 20 member/product/day, five/user/day and five-minute product
cooldown limits. NFL and NCAA share American-football limits. A snapshot less
than five minutes old is reused.
Empty responses are cached but explicitly reported as unavailable, never zero.
No automatic player polling or historical backfill is enabled.
Do not enable the budget mid-day without accounting for prior provider usage.

NFL, basketball, soccer and F1 responses were verified with bounded development
samples. NCAA uses the documented same-product endpoint; competition-specific
coverage is not yet verified live. One soccer fixture returned no stats while a
different fixture returned two team rosters: availability varies by competition.

Supplied API-Sports docs have no individual-player stat endpoints for baseball,
hockey, rugby, handball or volleyball. Those choices explain that another source
is needed instead of displaying team scores as player stats. Cricket/cycling
have no configured feed. MMA documents `fights/statistics/fighters`, but no
completed cached fight was available for schema verification, so its adapter is
not enabled. API-NBA and AFL are separate products, not silently added to the
tracked sports or existing quota model. This is not an all-sports rollout yet.

This project is designed to run as a Python service on a Linux host, such as a Proxmox LXC or VM.

## 1. Prepare the Linux server

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl
```

## 2. Clone the repository

```bash
cd /opt
sudo git clone <your-repo-url> discord-bot
cd discord-bot
```

## 3. Create the environment

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in:

- `DISCORD_TOKEN`
- `APPLICATION_ID`
- `GUILD_ID`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `OPENAI_API_KEY`
- `OPENAI_VISION_MODEL`
- `OPENAI_VISION_MODELS`
- `OFFICIAL_ROLE_IDS`
- `OPERATOR_ROLE_IDS`
- `OFFICIAL_CHANNEL_ID`
- `IMAGE_INPUT_CHANNEL_ID`
- `MEMBER_BET_CHANNEL_ID` (optional, separate member photo-submission channel)
- `TEAM_STATS_CHANNEL_ID`

## 4. Validate the Python app

```bash
. .venv/bin/activate
python -m compileall src
python -m pytest -q
```

## 5. Start the bot directly

```bash
. .venv/bin/activate
python -m src.bot
```

## 6. Run as a background service

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/discord-bot.service
```

Contents:

```ini
[Unit]
Description=Official Play Discord Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/discord-bot
ExecStart=/opt/discord-bot/.venv/bin/python -m src.bot
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now discord-bot.service
sudo systemctl status discord-bot.service
```

## 7. Useful operational commands

```bash
sudo journalctl -u discord-bot.service -f
sudo systemctl restart discord-bot.service
sudo systemctl stop discord-bot.service
```

## 8. Recommended Proxmox notes

- Run the bot in a Debian LXC or Ubuntu VM.
- Keep the repo on a persistent disk or backup to ensure the `.env` stays intact.
- Put the bot in a dedicated Linux user account if you want tighter controls.
- Keep the `SUPABASE_URL`, `SUPABASE_KEY`, and `DISCORD_TOKEN` in the `.env` file only, never in the repo.

## 9. Member Bet Vault

Before enabling this feature:

1. Apply `supabase/migrations/20261003000000_member_bet_vault.sql` to Supabase.
   Use a server-only **service-role** `SUPABASE_KEY`. The vault tables have RLS
   enabled with no member/public access policies, and `member-bet-vault` is a
   private storage bucket. Never make this bucket public.
   Also apply `supabase/migrations/20261003010000_whop_membership_access.sql`
   and configure the Whop sync below, including the full-trial migration.
   Without verified paid membership, an eligible trial, or an explicit owner grant, no new
   submission is accepted.
2. Set `MEMBER_BET_CHANNEL_ID` to a dedicated submission-only channel. It must
   differ from the official, image-input, confirmation, test, result, and
   team-stats channels. Leaving it empty disables the feature.
3. Give the bot View Channel, Read Message History, Send Messages, Embed Links,
   Attach Files, and Manage Messages in that channel. Enable Message Content Intent in the
   Discord developer portal. Give settlement moderators a configured
   `OPERATOR_ROLE_IDS` role or Manage Server permission.
4. Keep the existing vision and API-Sports configuration. Vision processing
   sends the stored photo to the configured OpenAI provider; disclose that
   processing to members. The bot never sends the photo back to a public card.
5. Restart the bot, then submit a test photo and verify deletion, private
   review, confirmation, masked publication, and moderator settlement before
   inviting members to use the channel.

Every human message in this channel is a submission, not conversation. Require
exactly one static JPEG, PNG, or WebP upload, no larger than 10 MB and 16
megapixels; a photo may contain up to ten legs. Text can specify units, but image
links, GIFs, PDFs, and text-only posts are rejected, deleted, and explained by DM.
DMs are not ephemeral interaction responses; when DMs are disabled, failures
are logged without a public reply. Deletion failures are logged and privately
reported to the uploader.

For accepted photos, the bot saves a metadata-stripped copy in private storage
and persists a ticket before deleting the source message. Discord makes the
original visible until deletion, so this is **not zero-exposure concealment**.
Storage failures preserve the original and notify the author rather than lose
the submission. The public processing/draft card contains no extracted game or
selection. Only the uploader and moderators can inspect extracted details through
an ephemeral button response. The uploader confirms actual units, ticket odds,
and public matchup names; selection/event/rule extraction errors require a new,
clearer photo. Confirmed tickets are locked. No member tickets enter official
plays, trackers, website results, or capper records.

After confirmation, public cards show matchup names, units, odds, and a hidden
selection. Safe automatic grading initially supports straight NFL, NCAA
American-football, and basketball full-game moneylines, spreads, and totals
**only when including overtime is explicit**, the author confirms the extracted
fields, and exact home/away names plus the Eastern event date identify one
cached API-Sports event. Pregame checks wait until the scheduled start. Event
requests are shared across tickets within each polling pass, with successful
in-progress checks repeated every 15 minutes. Final FT/AOT scores grade supported
markets; tied moneylines, parlays, props, other sports, unknown rules/dates,
promotions, and cash-outs must not be automatically graded. The author must not
confirm incorrect extraction or special-rule/cashed-out slips as ordinary bets.
API verification verifies an outcome, not proof that a wager was placed.

API/processing failures retry after 5, 10, 20, and 40 minutes; after the fifth
failure the ticket requires moderator review. Cancelled/suspended/postponed
events, unsupported finals, or events unresolved 24 hours after scheduled start
also require review, with selections still hidden. A moderator privately reviews
the ticket and uses **Moderator settle**, entering win/loss/void and a reason.
Authors cannot settle tickets. Only confirmed tickets can settle. Unreadable
photos that exhaust processing retries must be resubmitted; they cannot be
graded. Public cards distinguish `API-verified` from `Moderator-settled`, reveal
sanitized selection text, and never reveal the original photo. Result and audit
reason are written atomically; duplicate settlement requests cannot overwrite
an existing result.

Private reviews include a complete text attachment so long parlays do not lose
details to Discord embed limits. Settled cards attach full sanitized selection
text when it exceeds the embed field limit; open cards never attach that text.

Tickets, private photos, pending checks, and card-update retries persist across
restarts. Persistent card buttons look up the ticket in the database rather than
holding selection data in Discord component IDs. The bot checks pending work
every minute. Moderators should monitor cards marked **Moderator review required**
and the `member_vault_*` / `member_*` error logs. Original photos and audit records
remain in private storage until an administrator removes them under the business's
retention policy; there is no automatic retention cleanup in this release.

## 10. Whop paid and trial membership verification

### Membership stats tools

#### Cached reports and limited member/moderator refresh

The single-membership change enables the same cached and refresh permissions
for all approved verified paid plans and eligible full-access trials. Those members
and configured-guild members holding moderator roles
`1328120848992960543`, `1347741218158678097`, or `1328149760766640190`
may request `refresh: true`. Role grants apply only while held and only to
stats, never vault or billing/admin access. Existing owner grants also qualify.

Apply `20261003050000_api_request_budget.sql` before activation. Set
`API_SPORTS_BUDGET_ENABLED=1` and `MEMBER_STATS_REFRESH_ENABLED=1` together,
after the provider's daily quota resets and with no other unmetered API callers.
Both default off. Existing calls before activation or outside this bot are not
counted; do not enable mid-day assuming a fresh 100 requests are available.
Confirm the provider's reset timezone (the ledger uses UTC days) and data-display
license before enabling. SQL reservation behavior still requires live validation.

Every request through the NFL and multi-sport transports, including scheduled
refreshes and vault settlement, reserves a persistent allowance before network
I/O. American-football is shared by NFL and NCAA. Strict product daily ceilings:
80 system requests and 20 member requests; five member refreshes per Discord
user per UTC day across all products. Member calls share a five-minute product
cooldown. Reservations count failed attempts and are not refunded.
At exhaustion, no provider request is made; errors are surfaced/logged.
The system ceiling can defer settlement checks and scheduled refreshes.
This prevents member usage from consuming the protected system allocation;
it does not guarantee that 80 requests suffice for all operational demand.

The initial member refresh fetches today's UTC events only (one request),
updates the shared cache, and rereads the report. Other dates remain cached.
Fresh event cache data within five minutes is reused without a provider call.
Responses disclose this scope and cache age; no historic/weekly refresh or
real-time guarantee is offered. Cooldown/quota denials are explicit rather than
silently presenting stale data as fresh.

#### Lifetime owner exception

After the stats-access migration, apply
`supabase/migrations/20261003040000_owner_highroller_access.sql`. It creates
private owner grants and insert/update/delete audit snapshots, then grants
Discord ID `761388542965448767` lifetime HIGHROLLER tools and vault access
for seller `biz_rCNwfXRlnl0bFU`. Null expiration means lifetime, not a fabricated
payment or a distant expiry date. The grant does not expire when Whop polling
finds no paid membership. Existing RPC names are preserved for callers, but
now return authorized access (paid or owner grant), not proof of payment.
Ordinary free/complimentary Whop memberships still do not qualify.

This does not assign a Discord role or bypass channel permissions. Manually
assign the existing HIGHROLLER role to the owner for channel access; no
moderation/admin permissions are granted. Keep sync enabled for stats tools.
Whop must not be relied on to preserve a manually assigned owner role:
verify its behavior and restore channel access if its integration removes it.
Revoke the entitlement with an audited update of `revoked_at = now()` in
`owner_membership_grants`; remove the channel role separately.
Never write fake payment snapshots. Professional policy review should account
for this narrow owner exception.

Apply `supabase/migrations/20261003030000_highroller_stats_access.sql` after
the prepaid migration, then deploy/restart the bot. This adds a service-role-only
seller/plan-scoped lookup; missing schema fails closed without preventing the
rest of the bot from starting. Paid stats use all `WHOP_PAID_PLAN_IDS`, including
historical paid offers and the new $19.99 pass, scoped to `WHOP_ACCOUNT_ID`.
Eligible trials use the separate seller/plan-scoped RPC and claim ledger.
Sync must be enabled. No Discord-role or operator bypass grants these tools.

Commands `/matchup`, `/teamstats`, `/schedule`, and `/results` are private,
guild-only, and require approved verified paid/trial access or authorized owner/moderator
grants. Ineligible/repeated trials, ROOKIE-only access,
refunds, expiry, and stale snapshots deny access. Supported sports: NFL, college
football, basketball, soccer, hockey, and baseball. Use full team names.
Unique shortened/expanded team names resolve against cached names (for example,
`LSU Tigers` can resolve `LSU`). Exact names take precedence; ambiguous names
produce suggestions, never merged records. No name matching triggers API calls.
Schedule/matchup reads cover the next seven days; results/recent-form reads
cover the past 30 days. Up to ten events are displayed from bounded cached
queries; recent form is not complete season standings. Reports disclose cache
age, missing scores, and incomplete data. Without refresh activation these
commands make no member-triggered provider calls. Existing bot refresh jobs own
freshness until activation. These caches are also used by public website
features; membership sells convenience, not exclusive underlying data.
Confirm API-Sports display rights before advertising/launching.

### Historical prepaid offers (retired from new sales)

The owner replaced automatic renewal with one-time prepaid terms. All eight
offers were created under the existing ALL-STAR/HIGHROLLER products (formerly
Gold/Platinum) and read back to
verify the seller, product, upfront price, expiration, hidden visibility, zero
stock, and disabled unlimited stock. No automatic renewal applies.

| Term | Exact access | Discount | ALL-STAR upfront USD | ALL-STAR plan | HIGHROLLER upfront USD | HIGHROLLER plan |
| --- | --- | --- | --- | --- | --- | --- |
| 1 month | 30 days | 0% | 9.99 | `plan_pkIXO8mx8lOvb` | 29.99 | `plan_10PuOcOt9rHNL` |
| 3 months | 90 days | 5% | 28.47 | `plan_HIqBGKDODgWRI` | 85.47 | `plan_8nWXoogPnIovE` |
| 6 months | 180 days | 10% | 53.95 | `plan_McwH52818Zpyi` | 161.95 | `plan_IFDLF4SdPxcQh` |
| 12 months | 365 days | 15% | 101.90 | `plan_jfOCNP5Ow8Q8g` | 305.90 | `plan_XU4sWniXTjODJ` |

Discounts apply to base price times the term length, rounded once to cents.
These are fixed-day passes, not calendar-month expiration. Keep these eight
plan IDs in the paid allowlist alongside the new $19.99 plan to preserve
historical purchases. The earlier recurring plans
below remain hidden/zero-stock and are no longer allowlisted; do not publish
them.

On October 3 the owner consolidated to two tier roles. ALL-STAR product
`prod_0Bi4ERPCfSWz1` now has a free seven-day one-time trial
`plan_ejj9LwTfrJp5z` alongside the four paid passes. The trial has no automatic
charge or conversion and never belongs in `WHOP_PAID_PLAN_IDS`. HIGHROLLER
product `prod_6Hh9VAzQnzNiE` remains paid-only. ALL-STAR trial members receive
the same tier role but cannot submit vault tickets; payment verification, not
the role, enforces that restriction. Retire the old ROOKIE Discord configuration
without deleting shared access or changing existing tier mappings. Verify the
ALL-STAR role ID configuration and trial-to-paid overlap before launch.
Product-filtered API listings verified ALL-STAR is attached to Discord
experience `exp_CJFzS7gTGLLsj2` and HIGHROLLER to
`exp_xiSK1tNQP20bvc`; both experiences are private. The experience detail
response's `products` array was empty even for mapped apps, so use the
product-filtered listing to verify attachments. The legacy ROOKIE app was
renamed `Retired ROOKIE` and kept private; its Discord role was not deleted.
The original separate trial and recurring variants are hidden, zero-stock,
with unlimited stock disabled. Nothing was published by this consolidation.

Apply `supabase/migrations/20261003020000_whop_prepaid_access.sql` after the
membership migration. It permits a verified paid snapshot with Whop's
`completed` status to qualify through its finite paid-through date. For that
status the bot also verifies that the matching seller's plan is `one_time` and
has a positive expiration duration. Missing period dates, payment evidence, or
verified identity still deny access; no lifetime access is inferred. Test
actual prepaid membership dates/status and Whop role expiration before launch.
Production polling was enabled on the server and successfully returned zero
memberships. Local sync remains disabled. Checkout remains closed; successful
paid production eligibility, expiry, refund, and trial-to-paid overlap are
still unverified.

### Original draft offers (superseded for paid access)

Created draft offers: Free Trial (`plan_i9kjEGeSzcuOV`, free seven-day
expiration without renewal), Gold (`plan_rAvIzb0XD2Jdz`, USD 9.99 every
30 days), and Platinum (`plan_pvonMenMO9YDa`, USD 29.99 every 30 days).
Each has its own product for tier-specific access configuration. Products and
plans are hidden, plan stock is zero, and unlimited stock is disabled. Do not
open availability until launch gates pass. Only Gold and Platinum belong in
`WHOP_PAID_PLAN_IDS`; the free plan does not qualify for vault submissions.

Whop handles checkout/subscriptions; this bot owns vault authorization and can
optionally own a dedicated Discord paid role. Checkout on the website remains
closed. Do not enable payment collection until Whop has approved the accurately
described sports-analysis/picks service, including member-ticket tracking, and
provided its complete fee schedule.

Apply `supabase/migrations/20261003010000_whop_membership_access.sql` after the
vault migration. This creates private membership snapshots and an audit of
changes, plus server-only eligibility functions. No browser/anonymous account
can write payment evidence, Discord identities, or grant itself access.

Configure these server-only settings (never put them in Vite/browser variables):

- `WHOP_MEMBERSHIP_SYNC_ENABLED=1` to enable API synchronization; default is off.
- `WHOP_API_KEY`: account-scoped key with permission to read memberships,
  payments, and buyer social-account identities. Confirm required scopes in
  Whop's dashboard; incomplete responses fail closed.
- `WHOP_ACCOUNT_ID`: your `biz_...` seller account ID.
- `WHOP_PAID_PLAN_IDS`: comma-separated approved `plan_...` paid plans. Do not
  include free plans.
- `WHOP_TRIAL_PLAN_IDS`: separate approved free one-time seven-day full-access
  trial IDs. Empty disables trial access; never overlap with paid IDs.
- `PAID_MEMBER_ROLE_ID`: optional dedicated bot-owned membership role (paid,
  eligible trial, or owner grant). Leave unset for Whop-owned HIGHROLLER and
  never point at ROOKIE. Configure `GUILD_ID`,
  enable Members Intent, give the bot Manage Roles, and place its role above
  this role. Do not reuse operator/official/administrator roles.

API contract: `https://api.whop.com/api/v1`, pinned with
`Api-Version-Date: 2026-09-29`, using the current membership `account.id`,
`user_id`, `plan_id`, `current_period_start/end`, `updated_at`, and
`cancel_at_period_end` fields. User identities come from `social_accounts`
entries with platform `discord`, a numeric `external_id`, and `verified=true`;
missing, unverified, or multiple distinct Discord identities do not qualify.
API/database timestamp readers normalize fractional seconds before parsing,
including PostgreSQL timestamps with trimmed trailing zeros, for Python 3.10
deployment compatibility.
Members must connect their own Discord account inside Whop. No typed username,
email match, client metadata, or Discord role is accepted as identity/payment
proof. Verify the actual account's API responses before launch.

The bot paginates the seller's memberships every five minutes and reads payments
for approved plans. Paid eligibility requires an active, unexpired period and a
positive successful current-period payment for the same seller, membership,
and plan. Trials, zero-cost memberships, past-due states, any refunded amount,
automatic refunds, dispute alerts, and missing period/payment evidence do not
qualify. Cancellation at period end preserves eligibility through the paid
period. Timestamp comparisons use a five-minute boundary tolerance for payment
creation/collection; older successful payments cannot satisfy a later cycle.
One-time indefinite purchases are not supported; expiring prepaid purchases
are supported by the prepaid-access migration described above.

This first integration uses **API polling, not a public webhook receiver**.
Full trial entitlement is separate from paid eligibility above. Approved trial
plans must verify as one-time with seven-day expiration and a verified buyer/
Discord identity. The original 168-hour window is fixed in the claim ledger,
not reset by polling, an extension, or a repeated membership. Access requires
active/completed status, original dates, and a snapshot fresher than 15 minutes.
Unknown free plans do not qualify. The application does not collect IP addresses.

For local SQL regression tests, apply the membership migrations to a disposable
PostgreSQL database with Supabase roles, then execute
`supabase/tests/full_membership_trials.sql` with stop-on-error enabled.
The fixture transaction rolls back. Checks cover first/repeat claims, identity
changes, fixed dates, cancellation, exact expiry/staleness boundaries, former
paid customers, denied payment fabrication, role identities, audit and permissions.
Embedded PostgreSQL validation is not a production concurrency or role-mapping test.

Changes normally propagate on the next five-minute pass. Snapshots older than
15 minutes cannot authorize new tickets. If synchronization fails, it logs
`whop_membership_reconciliation_failed` and does not run mass role removal.
Successful role synchronization grants/removes only the dedicated paid role,
and retries Discord failures on the next pass. Configure the vault channel so
@everyone cannot Send Messages and the paid role can; inspect other role
overrides for unintended posting grants. The bot still checks private paid
eligibility independently, before downloading a photo, again before saving,
and at confirmation. Moderators do not get a payment bypass for new tickets.
Already-confirmed tickets remain eligible for settlement after membership ends.

Choose one owner for paid-role automation. If this bot owns the role, do not
also configure Whop's Discord app to manage that same role. An expired or stale
role does not authorize vault submission. If Members Intent, Manage Roles,
hierarchy, or configured role safety checks fail, the bot logs the error.

Members can use `/membership_status` for an ephemeral eligibility check without
exposing billing details. No Whop secrets, payment amount, original receipt, or
buyer email are published to Discord.

Before launch, test a paid member, free/trial member, cancellation with remaining
paid time, expiry, failed renewal, refund, dispute, missing Discord link, API
failure, role hierarchy failure, and bot restart against your actual Whop
account. Local tests mock API responses; they do not prove live scopes or
account approval. Signed webhooks, website Whop OAuth/account linking, embedded
checkout, and instant refund/dispute revocation are follow-up work, not part of
this polling release.
