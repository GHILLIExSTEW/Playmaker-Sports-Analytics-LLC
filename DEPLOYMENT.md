# Proxmox deployment guide

## Owner /api team refresh

Apply `20261008130000_owner_team_api_cache.sql` after the NFL/event cache and
`20261003060000_player_game_stats.sql` migrations. Also apply the shared request
budget migration `20261003050000_api_request_budget.sql` and activate
`API_SPORTS_BUDGET_ENABLED=true` before using this command. Deploy/restart the
bot to register lowercase `/api` (Discord does not allow uppercase slash names).
The key and budget flag are independent settings loaded at process startup.
Picker errors identify which setting the running bot did not load. Restart
after environment changes; existing process environment variables take
precedence over `.env` values. Never paste provider keys into support messages.

Only exact Discord Owner role `1347741218158678097` in the configured guild
can execute the command or use its private dropdowns. Example:
`/api sport:nfl season:2026`.
Select sport/season in the slash command, then choose league and team **by name**
from private select menus; no provider IDs need to be entered. Next/Previous
buttons page through lists longer than Discord's 25-option limit. Lists come
from the provider, not just teams already cached. NFL/NCAA have fixed leagues;
other sports load the provider league directory. Teams are scoped to the
chosen league/season and cached for future tools. Each directory request/page
uses the shared system budget, in addition to requests counted in the final
refresh report. Empty lists and lookup failures are explicitly reported.
Menus expire after three minutes; run `/api` again to reopen them. Every click
rechecks the requesting user's Owner role and guild. The refresh also validates
the team belongs to the selected league/season before storing statistics.
Split seasons such as `2026-2027` must match the provider's format.

NFL/NCAA, soccer, basketball, baseball, hockey, rugby, handball and volleyball
have team-based refresh adapters. Refresh stores the provider's full raw season
team summary where supported and all selected-team season schedule records, with timestamps,
in private service-role caches. It updates the existing schedule tables.
NFL/NCAA, soccer and basketball additionally refresh team and player stats
for each started, non-canceled season game; player snapshots use the existing
player-game cache. Every returned field is preserved, including missing values.
The American-football provider rejects `/teams/statistics` as nonexistent:
NFL/NCAA skip that endpoint and explicitly report season summaries unavailable.
They still refresh schedule and per-game team/player statistics for the season.
Other sports explicitly report unsupported player-stat coverage; no zero
values, derived player season totals or unverified endpoints are substituted.
F1/MMA need constructor/driver/fighter-specific tools and are not offered as
league/team refreshes; cricket/cycling feeds are unavailable.

This uses the shared **system** request allowance (80 per product per UTC day
in the current budget), not the member five-request allowance. It can consume
budget otherwise used by scheduled syncs. Pagination reserves one request per
page; failed attempts also consume reservations. No budget bypass is allowed.
One Owner refresh runs at a time per bot process; the database budget remains
atomic across processes. At quota/provider/database failure or the 12-minute
interaction limit, the private response explicitly reports PARTIAL / FAILED,
counts what was saved, and leaves completed cache components intact. Bot logs
contain the cause. Retrying re-fetches and consumes additional quota; it is not
an automatic resume. Future/canceled games have schedule records only.

Local tests use mocked provider responses and a real embedded PostgreSQL
migration check; subscription coverage and production API payloads must still
be verified. This command adds no public stats RPC or website player display.

## NFL matchup lab

Deploy the frontend for `/nfl/lab`; it needs only the existing NFL public-data
migration and bot cache sync. No new paid provider calls, model or bot command
are introduced. The lab is public like the existing NFL pages, reachable from
the Sports dropdown and NFL scores page. Confirm provider display/export rights
before deploying publicly. Verify a synced upcoming matchup, scoring samples,
missing-cache/retry states, calculator values and CSV downloads.
Injuries/weather/live odds are not provided; calculations use manual prices.

## Extended capper appearance

Apply `20261008120000_capper_full_appearance.sql` after background colors
and deploy the frontend. Authors and Owner-role editors can set separate
display-name, link and body-text colors; choose Barlow Condensed, IBM Plex Sans
or Georgia for headings; add an HTTPS banner or upload PNG/JPEG/WebP up to
10 MB; and move stats, picks, charts and settled results up/down. All four
sections remain present and use matching DOM/visual order for keyboard access.
Uploads use the existing owner-only image folder; banners resize to 1600px,
avatars to 512px. Replaced owned uploads are cleaned up after saving.

Automatic foreground colors adapt to the background. Custom colors require
the editor to choose readable contrast; the form explains this. Site header,
footer, navigation and official records remain protected. No arbitrary fonts,
CSS or HTML are accepted. Existing API saves omitting appearance preserve it.

## Capper page backgrounds and BANG presentation

Apply `20261008110000_capper_background_color.sql` after Owner-role editing
and deploy the frontend. Each capper page can have its own hex background
color; authors and current Owner-role editors use **Page background color**.
The setting is public and persistent; old saves that omit it preserve the
chosen color. Text switches between light/dark for contrast. The global
header, navigation and other pages keep their existing backgrounds.

Deploy/restart the bot for the money-emoji BANG heading and win footer.
Notifications still re-upload the original slip attachment separately into
VIP and FREE, or use the source embed image when no attachment exists.
The new presentation does not change mention targets or author/Owner-only
reaction permissions. Disable the other bot's BANG rule to avoid duplicates.

## Discord Owner-role capper editors

Apply `supabase/migrations/20261008100000_owner_role_page_editing.sql` after
the page-settings/avatar/website-insight migrations. Deploy the bot and frontend,
then restart the bot. The existing five-minute roster loop separately syncs
Owner role `1347741218158678097`; confirm `website_owner_sync_complete` in logs.
Missing roles/guilds fail with staff alerts, not empty verified rosters.

Every verified Discord identity in the fresh Owner-role roster can edit all
current capper pages and open-pick insights. No OPERATOR role is required for
this administrative permission. The capper and Owner rosters must belong to
the same guild and be less than 15 minutes old. Role removal is enforced on
the next sync; a stale roster denies the override. Owners have picks-board
access after the existing age confirmation, even while enrollment is closed;
this does not create paid/trial records or grant unrelated Discord benefits.
Regular OPERATORs retain own-page/own-insight editing only. Public profile,
link/image validation and settled-pick protections remain unchanged.

Owner uploads still use their own auth UUID avatar folder, not another user's
folder; only their own uploaded files can be cleaned up by their browser.
The private storage bucket and brand files stay untouched. Homepage/policy
editing is not included. Test an Owner without OPERATOR, a regular OPERATOR
attempting a cross-page save, and role removal while an editor is open.

## Owner-customizable capper pages

Apply `20261008090000_website_capper_insight_editing.sql` after the capper insight
and page-settings migrations to let authors add/edit insight on their own open
picks from both the expert board and their page. This uses the same stored
justification as the Discord modal; it never edits slip selections or results.
The save RPC derives Discord identity from auth and rechecks ownership, current
OPERATOR status, website access and the open/published pick state. Other authors
cannot edit a pick. Refresh to see edits from Discord or another browser.

Apply `supabase/migrations/20261008070000_capper_page_settings.sql` after the
OPERATOR website migrations, then deploy the frontend. No bot update is needed.
Also apply `20261008080000_capper_avatar_uploads.sql` after page settings
and the existing public `website-assets` bucket migration.
Sign in through Discord, visit your own capper page and select **Edit your
capper page**. Owners can change a six-digit accent color, public bio (2000
characters), HTTPS profile image URL and HTTPS Website/X/Instagram/Discord
links. Owners can upload PNG/JPEG/WebP images up to 10 MB directly into the
existing public `website-assets` bucket. The browser resizes to at most 512px
and converts to WebP. Storage policies restrict inserts/selects/deletes to
`capper-avatars/<auth-user-uuid>/<random-uuid>.webp` for the current verified
OPERATOR owner; they grant no overwrite access or access to branding paths.
The private Media bucket is untouched. A restrictive avatar-folder guard
prevents broader authenticated policies bypassing ownership. Review existing
bucket policies before rollout; this migration does not revoke unrelated grants.
An HTTPS image URL is also supported. Clear an
image/link to restore the default/remove it. Content is plain text, never HTML.

The database derives ownership from provider-managed Discord identity and the
fresh OPERATOR roster on every save; callers cannot choose another page ID.
Role removal or stale roster blocks edits, even if an editor was already open.
Public page settings expose no Discord IDs. Raw settings writes/reads are
denied; public display is through a restricted RPC. Picks/results/statistics
and capper names remain system-controlled. Avatar changes also update the
existing capper-directory/current-picks avatar source. Ambiguous duplicate
capper names fail explicitly until an administrator resolves them. Saved
settings persist after role removal but the page remains roster-controlled.
Replaced managed avatars are removed after a successful save. A failed save
attempts to remove its unused upload and reports cleanup failures explicitly.
Browser closure or role removal during upload can leave an unused object;
an administrator should review such objects before deleting them.

Verify that an owner can save and reload their page, a different operator
cannot edit it, and an expired/removed role cannot save an already-open form.

## BANG win notifications

Disable the other bot's green-check BANG rule before enabling this replacement.
Apply `supabase/migrations/20261008060000_bang_notifications.sql`, then deploy
and restart this bot. An original author's or Discord Owner-role member's
green-check reaction to an open/regraded tracked official pick still settles
it as a win, and now sends BANG to both `VIP_CHAT_CHANNEL_ID` and
`FREE_CHAT_CHANNEL_ID`. VIP mentions HIGHROLLER; FREE mentions ROOKIE. Ordinary
members, other OPERATORs, server managers without Owner role, and bots do not
trigger it on someone else's pick. The only cross-author reaction override is
Owner role `1347741218158678097`. Offline reaction reconciliation also
handles wins; slash-command/manual settlements do not trigger BANG.

Each destination receives the slip attachment re-uploaded directly when
available (otherwise the existing embed image), with a link to the source.
Missing images are explicitly flagged in the post and staff alert. In testing
mode, only `TEST_CHANNEL_ID` receives a notification, without role mentions.
The bot needs attachment/embed/send access and permission to mention the
configured roles. Verify both destinations with a new test win.

The private `play_bang_notifications` table atomically claims each
play/destination once, including across restarts and reaction removal/re-add.
Partial delivery continues to the other destination and logs/alerts failures.
A crash or uncertain send can leave a claim without a message ID; automatic
retries intentionally do not resend these, avoiding duplicate role pings.
Inspect the destination and logs before a database owner removes a failed
claim. Do not clear successfully delivered claims.

## Capper-authored insight requests

Apply `supabase/migrations/20261008050000_capper_insights.sql` after the
website access/OPERATOR migrations through `20261008040000`. Deploy the matching
bot and frontend, then restart the bot so `/request_insights` is registered.
Keep general enrollment disabled.

Newly recorded open OPERATOR picks receive a request mentioning only their
original author in `CONFIRMATION_CHANNEL_ID` (`TEST_CHANNEL_ID` in testing
mode). The visible request has an **Add / edit insight** button. Only the
original author, currently holding `TRACKER_ROLE_ID` in `GUILD_ID`, can open
or submit the private modal. Submissions are checked again against the stored
author, open/published state and fresh roster in a service-role-only SQL RPC.
The modal and confirmation are private; the submitted text is intentionally
published to authorized website members, not posted back into Discord.
The original button supports edits while the pick remains open and survives
bot restarts. Tail behavior is unchanged.

A play manager can run `/request_insights` to send requests for existing open
OPERATOR picks, at most 50 per invocation. Repeat for additional batches.
Stored prompt message IDs prevent normal repeated requests across restarts;
picks already containing insight are skipped. No historical picks are
messaged automatically on startup. If a send succeeds but tracking fails,
the command stops with a partial count/error: inspect the channel and database
before retrying to avoid a duplicate request. A manually deleted tracked
request must be reviewed and its `prompt_message_id` cleared by the database
owner before requesting it again.

Insight is stored separately in the private `play_insights` table (maximum
2000 characters); browser writes and raw reads are denied. The authorized
current-picks RPC keeps its `analysis` output field but now returns only
capper-authored insight, never image extraction or `plays.play_text`.
Cards expose a collapsed **Capper insight** disclosure only when insight
exists, on both the expert board and capper pages. Refresh picks after saving.
Public settled results do not expose insight.

Verify with a new test pick and an existing open pick: a different operator
cannot open the modal; the author can submit/edit; removed operators and
settled picks cannot submit; only authorized website members can reveal the
saved text. Re-running `/request_insights` must not repost tracked requests.

## Complimentary OPERATOR team HIGHROLLER access

Apply `20261008020000_owner_website_preview.sql` first, then
`supabase/migrations/20261008030000_operator_website_access.sql`. Deploy the
updated frontend to recognize `operator` access. Also apply
`supabase/migrations/20261008040000_operator_highroller_benefits.sql` for
Discord stats, vault authorization, and membership reconciliation. Every verified Discord member
in the bot's fresh OPERATOR roster (role `1328120848992960543`) can view `/picks`
after age verification, even with website enrollment disabled and without a
paid pass. Other roles do not qualify. Team members have complimentary
HIGHROLLER benefits with no scheduled expiry while holding the OPERATOR role;
this is not a permanent grant after leaving the team, a payment, or a trial.
Owner access is retained.
Role removals take effect after the next five-minute bot sync and browser
access recheck; a roster older than 15 minutes fails explicitly. Checkout and
general enrollment stay closed. Keep `website_membership_config.enabled=false`.
The benefits migration preserves independent paid/trial/owner authorization.
It changes no Whop payment snapshots or offers. Team authorization expires
if roster verification stops for 15 minutes and is removed on the next sync
after role removal. Configure the exact OPERATOR role, not a moderator role.
The existing optional dedicated bot-managed membership role reconciler includes
team identities automatically; do not let it manage a Whop-owned role or enable
it without reviewing its existing role-safety requirements. Whop-managed
HIGHROLLER role assignment for complimentary team members must be configured
separately in Discord/Whop; these migrations do not themselves assign Discord
roles or charge anyone.

## Owner-only website preview while enrollment is closed

Apply `supabase/migrations/20261008020000_owner_website_preview.sql` after the
member-picks and OPERATOR-roster migrations. Leave
`website_membership_config.enabled=false`. The one verified owner Discord
identity `761388542965448767` can preview current picks with a current,
seller-matched HIGHROLLER owner grant and completed age verification.
Editable metadata, other grants, roles, paid passes, and trials cannot bypass
the closed launch gate. Revoked/expired/future owner grants cannot preview.
The OPERATOR roster must still be fresh for current picks.

After applying, click **Recheck membership access** on the account page. It
should show HIGHROLLER owner access; `/picks` should load current OPERATOR
plays. This is a database-only change, with no bot restart or frontend build
required. Checkout and general website membership access stay closed.
The original membership decision remains a private function; browser callers
can only use the new wrapper. To stop owner preview, revoke the owner grant
(this also removes its other granted benefits) or restore the original
closed-gate function through a reviewed migration.

## Sports startup cache disconnect recovery

The multi-sport service now uses the existing Supabase three-attempt transport
retry helper for daily cache-status reads, active-event reads, and idempotent
event/status upserts. Each production retry rebuilds the query against the
replacement Supabase client. A cache connection reset no longer immediately
aborts the startup sync. This does not restart the whole sync or repeat successful
API-Sports requests merely because a cache operation disconnected. Persistent
transport errors still propagate to the existing failure reporting; provider
request limits and provider errors are unchanged. No migration or environment
change is needed: deploy the bot update and restart the service.

## OPERATOR-only website cappers

For the separately authorized removal of Jatin/Doomsday's official records,
run `supabase/maintenance/delete_non_capper_official_plays.sql` as database
owner. It checks both exact pairs: user 11 / Discord 1211806245229695138 and
user 12 / Discord 759419251773014056. Default preview deletes nothing.
Review the listed play IDs/counts and confirm a backup before setting
`apply_delete := true` and rerunning the full statement. It removes only their
official plays and associated legs/tails/settlements/history, leaving accounts,
memberships, vault records, and unpublished draft legs intact. The operation
is atomic; mismatched identities fail without deleting. After applying,
refresh website results and run the bot's `/update_tracker` to rebuild cached
Discord tracker messages. Existing Discord play messages are not deleted by SQL.

Apply `supabase/migrations/20261008010000_operator_capper_roster.sql` after
the website member-picks migration, then deploy the updated bot and website.
The bot synchronizes the configured guild's `TRACKER_ROLE_ID` membership every
five minutes, starting when ready. The confirmed OPERATOR role is
`1328120848992960543` (the existing default); verify `GUILD_ID` and any production
override. The bot requires the Discord Server Members intent and full member
cache/chunk access. Sync failures log and alert staff.

Until the first successful sync, or after 15 minutes without a successful sync,
capper directory/follow/picks RPCs report an explicit unavailable-roster error.
They never fall back to all play authors or the unsynchronized `users.role`.
Current OPERATOR identities alone populate capper cards/pages, follow options,
and premium current picks. Removed operators leave those surfaces on the next
successful sync. Existing settled results remain in the public historical ledger;
no plays or member records are deleted. This does not open premium enrollment.

After restart, confirm `website_capper_sync_complete` in the bot logs and inspect
`public.website_capper_roster` as database owner. Verify Jatin/Doomsday are absent
unless they actually hold the OPERATOR role, and approved operators remain.

## Website member access and current picks (October 8, 2026)

This release adds `/picks`, sport/capper filters, current picks on capper pages,
and verified website membership status. Checkout stays closed. Website premium
access also stays closed until explicitly enabled server-side; a successful
local build/test is not completion of the live launch gates.

1. Confirm backups and separate staging/production credentials. Apply the
   existing account/profile, Whop paid, owner grant, and full-trial migrations
   first, then `supabase/migrations/20261008000000_website_member_picks.sql`.
   This revokes browser access to raw play, leg, draft, history, settlement,
   and author tables. The bot's service-role grants are unchanged; public
   settled-result RPCs remain available.
   The migration adds `users.public_avatar_url` if absent without changing
   existing avatar values. If a previous run failed with missing-column error
   `42703`, its transaction did not commit: rerun the complete corrected file
   (from `begin;` through `commit;`), not only the failing function. The older
   public-avatar migration remains necessary for avatars in public settled results.
2. As database owner, configure the singleton row in
   `public.website_membership_config` with the approved seller and the complete
   production `WHOP_PAID_PLAN_IDS` / `WHOP_TRIAL_PLAN_IDS` arrays. The initial
   paid array contains only `plan_cdPyKCHjSQeG2`; retain **all approved historical
   paid IDs**, not just the new-sale offer. The trial array starts with
   `plan_R7H8Sx7MEKzh0`. Never include trial IDs in the paid array.
   Do not expose these controls as frontend variables. Leave `enabled=false`
   in production until the relevant gates pass.
3. Deploy the website to staging and verify the Discord OAuth callback and
   allowed `/account` redirects described in `web/README.md`. Age verification
   must complete. OAuth must use the same Discord identity linked in Whop;
   changing a profile name or metadata cannot link membership.
4. Enable website access **in staging only**, start the existing verified Whop
   polling process, and test a provider-approved purchase and eligible trial.
   Confirm HIGHROLLER access, exact expiry, matching trial claim, paid/trial
   overlap, and historical paid-plan support. Verify that the account status
   and `/picks` agree. Roles alone must not authorize website picks.
5. Test sign-out, unverified age, wrong Discord account, revoked/refunded/
   chargeback-invalidated snapshots, expired passes, and polling interruption
   longer than 15 minutes. Verify direct `plays`/`play_legs` browser reads and
   unauthenticated `member_current_picks` requests are denied. Open selections
   must not appear in public results or member public profiles.
6. Publish an official test play through the normal bot confirmation flow.
   Verify its selections, odds, risk, sport, notes, and capper on the board;
   unpublished drafts must stay absent. A capper with no settled history must
   have a working page. Settle the play and confirm it leaves the board and
   appears once in the existing public ledger. Discord Tail behavior is unchanged.
7. Complete Gate A and the outstanding positive production paid-member test,
   Discord role mappings/expiry tests, alerting, and backup/recovery checks in
   `LAUNCH_PLAN.md`. Whop business review/payout clearance and professional
   policy review remain external requirements. Only then authorize production
   website access by setting `enabled=true` on the configured singleton.
   Opening Whop sales is a separate owner-controlled step; this release does
   not publish checkout or alter offer availability.

Rollback premium access by setting `enabled=false`, not by deleting membership
snapshots or trial claims. Requests immediately fail closed; open browser boards
recheck every 30 seconds and on focus. Previously viewed/saved information cannot
be recalled. A wrong/missing RPC or database failure produces an explicit access
or feed error, not a successful empty board.

Reproducible local checks: from `web/`, run `npm.cmd run test:access`,
`npm.cmd run test:picks`, `npm.cmd run build`, and `npm.cmd run lint`.
Install the Playwright Chromium headless shell if needed as documented in
`web/README.md`. SQL tests use isolated PGlite and browser tests mock the API;
neither constitutes production payment/OAuth validation.

## Official play features (October 6, 2026)

**Apply `supabase/migrations/20261006000000_official_play_features.sql` in the
Supabase SQL Editor before pulling this code on the server.** The new code
writes `play_legs.details` and reads `plays.auto_suggested_at` and `play_tails`;
without the migration, recording plays fails. Then run
`cd /opt/discord-bot && git pull && systemctl restart discord-bot.service`.
No new environment variables are required.

- **Operators only on the tracker:** the unit tracker, Top Playmakers, and
  weekly/monthly recaps count only plays from members who currently hold the
  🧪 OPERATOR 🧪 role (`TRACKER_ROLE_ID`, default 1328120848992960543). Bet
  slips posted in the official channel by anyone without that role are ignored.
  If the role can't be read (guild not cached), the tracker falls back to all
  plays and logs `tracker_role_*`.
- **Auto-settle suggestions:** every 15 minutes, open plays whose legs the image
  reader identified (sport, teams, market, side, line) are graded against the
  cached API-Sports final scores (requires `API_SPORTS_KEY`). Full-game
  moneyline, spread, and total legs in NFL, college football, basketball,
  baseball, and hockey are supported. A "looks like a WIN/LOSS/VOID" card with
  Confirm/Dismiss buttons is posted to `CONFIRMATION_CHANNEL_ID`; nothing is
  settled until an official or moderator confirms. To grade every outstanding
  play at once (no 14-day limit), run `python scripts/settle_outstanding.py`
  for a dry run and add `--apply` to settle; it writes the database only, so
  the hourly tracker refresh picks up the results.
- **Staff alerts:** image-reading failures, tracker refresh failures, recap
  failures, and auto-settle errors post to `CONFIRMATION_CHANNEL_ID`, at most
  once per 10 minutes per alert type.
- **Recaps:** weekly (Mondays) and monthly (the 1st) at 10:00 AM Eastern in
  `RESULT_CHANNEL_ID`, with capper records, sport breakdown, best play, and the
  monthly vault leaders. `/recap` previews either recap privately or posts it.
- **Tail button:** each new official play gets a 🎯 Tail button under the
  tracked post. When an operator's image slip is confirmed, the bot reposts it
  in the official channel as one app message (play card with the operator's
  name/avatar, their caption, the re-uploaded slip image, and the Tail button),
  deletes the original, and tracks reactions/settlement on the repost. The bot
  needs **Attach Files** and **Manage Messages** there; if the repost fails the
  original stays and the Tail button is replied under it instead. The bot never
  DMs members.
- **Game picker:** `/gamestats` now takes sport → league → game → player, all
  chosen from lists (current and upcoming games from the schedule cache). No
  game, league, or player IDs are typed or shown; `/schedule` and `/results` no
  longer print IDs.
- **Member vault notices:** rejection and status notices are posted in the vault
  channel as an @mention that deletes itself after 60 seconds instead of a DM.
- **New commands:** `/unsettle` (reopen a play settled in the last 7 days),
  `/edit_play` (edit an open play and refresh its card), `/mystats` (private:
  official, tailed, and vault records; members with the Whop-managed
  HIGHROLLER (PLATINUM) role, paid or trial, or an owner grant get a "Share to
  VIP CHAT" button that tags @HIGHROLLER, everyone else gets "Share to FREE
  CHAT" that tags @ROOKIE. The card posts once through a bot-owned channel
  webhook ("Playmaker Stats Share") showing the member's name and avatar, and
  the private /mystats message is deleted after sharing. VIP access is
  rechecked on click. Override channels with `FREE_CHAT_CHANNEL_ID` /
  `VIP_CHAT_CHANNEL_ID` and roles with `DISCORD_PLATINUM_ROLE_ID` (HIGHROLLER,
  default 1328120234749464739) / `DISCORD_GOLD_ROLE_ID` (ROOKIE, default
  1556484440396660757). The bot needs Manage Webhooks, Send
  Messages, Embed Links, and permission to mention those roles in both chats),
  `/vault_leaderboard` (this month), and
  `/recap`. `/settle` now includes regraded plays, the 🌓 reaction settles a
  play as partial, regrades refresh the play card, the tracker breakdown shows
  net units and ROI per capper, and `/update_tracker` is limited to officials and
  moderators.

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

### Whop draft setup completed October 5

Both variants were created under `prod_6Hh9VAzQnzNiE` and read back through
the Whop API for seller `biz_rCNwfXRlnl0bFU`:

| Offer | Plan ID | Billing | Price USD | Exact expiry |
| --- | --- | --- | ---: | ---: |
| HIGHROLLER paid | `plan_cdPyKCHjSQeG2` | `one_time` | 19.99 | 30 days |
| HIGHROLLER full trial | `plan_R7H8Sx7MEKzh0` | `one_time` | 0.00 | 7 days |

Both have zero renewal price, hidden visibility, zero stock, and unlimited stock
disabled. Sales remain closed. The dashboard showed all memberships = 0,
inactive memberships = 0, and all payments = 0. With owner authorization, twelve
obsolete checkout links were deleted: the eight historical prepaid variants,
two recurring variants, old ALL-STAR trial, and retired ROOKIE trial.
Products and Discord experiences were not deleted or remapped.
The historical plan table below is reference only, not a list of live offers.

Local `.env` has the new paid/trial IDs, retains historical paid IDs, and leaves
sync disabled. Production configuration was not changed. On Proxmox, add the
paid ID to `WHOP_PAID_PLAN_IDS` and set
`WHOP_TRIAL_PLAN_IDS=plan_R7H8Sx7MEKzh0` only after applying the trial migration.
Whop also displayed a business-information request with payouts temporarily
paused; the owner must complete that review. No payout settings were changed.

The creation steps below are now a configuration reference; do not create
duplicate variants. Production deployment, Discord mapping, trial-abuse and
overlap/expiry verification remain required before enrollment:

1. In Whop, create a **new** HIGHROLLER plan under `prod_6Hh9VAzQnzNiE`:
   USD 19.99, `one_time`, exactly 30-day expiration, hidden, zero stock, and
   unlimited stock disabled. Read back the seller, product, price, billing type,
   and expiration. Do not mutate historical purchases. Delete an obsolete offer
   only with owner approval and verified absence of payments/memberships.
2. Create a new $0 one-time seven-day HIGHROLLER trial under the same product,
   hidden/zero-stock, with no automatic conversion. Read back its seller,
   product, billing type and expiration. The old ALL-STAR trial was deleted
   after verifying no memberships/payments; do not silently remap historical trials.
3. Obsolete zero-usage offers have been deleted with owner approval.
   Preserve any future memberships, dates, and historical roles.
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
`/gamestats sport league game player refresh`.
League, game, and player are picked from lists: the game list shows current and
upcoming games (last 12 hours through the next 7 days) for the chosen league,
and the player list comes from that game's cached stats. No IDs are typed or
shown. Omit player to list up to 20 available player/driver names. Searches
still cover the whole cached response, not only the displayed roster.
Reports show per-game provider groups, not calculated season totals. Supported
adapters are NFL/NCAA football, basketball, soccer and Formula 1 session results.
For F1, the game list shows sessions; practice/qualifying results are explicitly
distinguished from races.
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
links, GIFs, PDFs, and text-only posts are rejected, deleted, and explained with
an in-channel @mention that deletes itself after 60 seconds (members are never
DMed). Notice and deletion failures are logged.

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
