# AI Handoff: Playmaker Picks

Use this document to onboard a new AI assistant to the product, repository, decisions, risks, and next work. Read it together with `LAUNCH_PLAN.md` before changing code. The launch plan is the operational checklist; this file preserves the conversation context and distinguishes confirmed decisions from ideas that still need owner approval.

## Product and Owner Context

- Legal entity name supplied by the owner: **Playmaker Sports Analytics, LLC**.
- Public brand/community: **Playmaker Picks**.
- Domain owned by the owner: `playmakersportsanalytics.com`.
- Owner says they are the sole owner. Revenue should be deposited into and retained in the business account; the owner decides whether to spend, reinvest, or distribute profit.
- Do not describe collaborators/admins/cappers as equity partners unless the owner later establishes that in writing. Capper/admin payments, if any, need a separate documented arrangement.
- Product: publish sports analysis and informational picks for people to follow. The business says it does not accept, place, transmit, or hold bets.
- The website is intended to be built in this private repository and hosted with AWS Amplify. The Discord bot stays on the existing private Proxmox host.
- Current workspace has appeared as `/workspaces/Discord_Bot`; the owner said the repo was renamed. Do not assume this path remains accurate after reopening. Discover the active repository root before using absolute paths.

## Important Scope and Risk Boundaries

- This is not a sportsbook product. Do not add user bet placement, bet custody, member-funded cash pools, pooled stakes, or a feature that matches members' wagers.
- Do not describe legal or payment rules as a loophole or guarantee that picks are exempt from regulation. Sports-analysis services can still face state-specific laws, advertising rules, age restrictions, promotion requirements, and processor rules. Provider policy can be stricter than law.
- Before taking payments, require written approval from the selected provider for the accurately described paid sports-analysis/picks service and obtain its complete fee schedule. Stripe's public restricted-business materials have listed sports forecasting/odds-making and some gambling-related advisory services; exact applicability needs written review. Do not assume Stripe is usable.
- Prior informal fee figures for Whop, DubClub, Patreon, and Stripe were not conclusively verified in the conversation. Do not repeat them as confirmed facts. Compare current written schedules, including platform, processing, payout, dispute, refund, and buyer fees.
- Do not accept member-funded cash pools. The provisional promotion approach is company-funded prizes, free entry, no purchase advantage, and published rules reviewed for applicable jurisdictions. Temporary premium access can be a prize, with expiration and no cash value; confirm promotion rules before launch.
- Business and legal guidance discussed with the owner was general planning, not legal, tax, or accounting advice. Avoid drafting final legal policies as if attorney-approved.
- Avoid claims such as “lock,” “guaranteed winner,” “risk-free,” or promised profit. No daily play volume should be guaranteed; no-play days are possible.

## Product Decisions and Proposals

### Confirmed Product Direction

- Owner selected Whop for subscription/payment integration. The bot keeps a
  private Whop-verified paid-access ledger and gates new Member Vault submissions
  independently of Discord roles. Initial integration polls the API; checkout
  remains closed pending approval/configuration and live validation. See
  `DEPLOYMENT.md` for the migration, settings, eligibility rules, and limitations.

- Public experience should combine a **bold, energetic public face** with a **calm, restrained private clubhouse**.
- Show the full verified historical record publicly. Trust through evidence is a core acquisition strategy.
- Public capper treatment: brief, strong summaries. Inside the clubhouse: full profiles and records.
- The product should support both sport-specific browsing and capper-based feeds.
- The owner selected a full web community as a desired direction, in addition to Discord. Build a phased product that can support durable discussion on the site, but discuss moderation, scope, and timing before creating a full social network.
- Visitors should see a daily slate overview and a designated free play. Live selections must be protected server-side; never send locked paid selection text to the browser and merely blur or hide it with CSS.
- First-time visitors care about three actions: verified results, free Discord/community access, and membership plans. Evidence should lead visually while the other actions remain easy to find.

### Current Membership Decision (October 5, 2026)

Owner adopted Robin's setup: verified free members get ROOKIE; eligible
first-time seven-day trials and paid members get HIGHROLLER. Single paid
HIGHROLLER pass is still $19.99 USD for exactly 30 days, one-time and
non-auto-renewing. No automatic trial charge/conversion. Retire ALL-STAR
and historical longer-duration offers from new sales; preserve historical
purchases and their paid verification IDs. Trials now include all available
member features, including stats and vault, with the same quota/feature flags.
Free chat and Winning slips show free discussion and settled premium results;
aim for one staff-selected free play daily when suitable, without a guarantee.
No automated free-play selection/publication or open premium disclosure was added.
Migration `20261005000000_full_membership_trials.sql` creates separate trial
snapshots/audit and persistent one-trial claims per seller/buyer/Discord identity,
pins original dates, and rejects prior recorded paid customers. Trials stay
outside payment snapshots. `WHOP_TRIAL_PLAN_IDS` is separate from paid IDs.
IP-only tracking was not implemented; it cannot prove first-time identity.
No IP collection/storage was added. Multi-account evasion remains possible.
Refresh flags remain off until existing budget/licensing/live-test gates pass.
The owner lifetime grant remains intact under its historical database tier.
Whop drafts created and API-verified October 5: `plan_cdPyKCHjSQeG2`
($19.99 one-time, 30 days) and `plan_R7H8Sx7MEKzh0` ($0 one-time,
seven days), both on HIGHROLLER product, hidden/zero-stock/unlimited-stock off.
Owner authorized deletion of twelve obsolete checkout links after dashboard
verification of zero memberships (all statuses) and payments. Products and
Discord experiences were retained. Local allowlists updated; local sync stays off.
Production allowlists, migration/deployment and live role/expiry/anti-abuse tests
remain pending. Whop displayed updated-business-information request and payout
pause; owner must address it. Do not claim checkout opened or production deployed.
Owner reports Discord roles are adjusted; the assistant did not administer them.
Whop owns the HIGHROLLER role; do not also enable bot role sync for it or for
ROOKIE. Provider offers changed as described above; Discord mappings did not.
See `LAUNCH_PLAN.md` and `DEPLOYMENT.md` for current offers and rollout.

### Historical Membership and Stats Decisions (Superseded Where Inconsistent)

Season-stat update supersedes the game-ID requirement: `/playerstats` now takes
sport, league and player, with cached league/player autocomplete filtered by both
scope fields. Current+previous seasons display together. Populated prior-season
records are frozen for ordinary refresh; initial loads still cost requests.
New migration `20261003070000_player_seasons.sql` seeds a private name directory
from existing game caches and adds compact season totals, compact league metadata
and an atomic batch reservation RPC. A cold lookup can reserve up to five calls;
all reserved calls count against existing limits and the cooldown is applied once
per batch. No provider calls while typing, no background backfill, no activation
flag changes. `/gamestats` preserves optional per-game detail separately.
NFL/NCAA/soccer use provider season totals, F1 driver standings, basketball
explicitly incomplete additive totals from available selected-league game logs
with per-stat denominators. Split seasons derive from cached league events.
Pagination needing extra pages is rejected rather than treated as complete.
Four season response samples were verified; SQL deployment/concurrency and live
Discord command authorization remain unverified. Suggestions grow with cached
players; do not claim a complete preloaded global player directory.

Earlier player-stat update: `/gamestats` reuses the same private guild-only
membership/owner/moderator gate and refresh flags. Migration
`20261003060000_player_game_stats.sql` creates a service-role-only shared
per-game JSON cache. NFL/NCAA, basketball, soccer and F1 session-result adapters
are implemented; refresh requests fetch one complete game's athletes with one
budgeted provider call. Omit player to list available names/IDs, then select by
unique name or ID. Soccer sometimes supplies ID 0: retain its named stats,
explicitly mark the ID unavailable and allow name-only lookup, never invent IDs.
F1 schedules/results expose session IDs and types, not fictional team scores.
Paid ALL-STAR reads cache; trials remain excluded. No background player polling
or historical backfill. Production migration/deployment and live authorization
remain unverified; refresh activation gates still apply.

User-supplied API-Sports PDF docs and bounded live samples verified NFL,
basketball, soccer and F1 contracts; NCAA shares the documented NFL product.
Actual basketball/soccer/F1 samples produced 24/40/22 individual reports offline.
One soccer fixture had no stats, so coverage must not be assumed universal.
Baseball/hockey/rugby/handball/volleyball docs expose no individual stat endpoints;
these choices explicitly report the source gap. Cricket/cycling have no feed.
MMA documents `fights/statistics/fighters`, but no completed cached fight existed
to verify its schema; leave its adapter disabled until verified. API-NBA and AFL
are separate products and have not been added to the quota model.

Later owner decision: paying ALL-STAR gets cached stats; HIGHROLLER gets
limited today-only provider refresh via `refresh: true`. Moderator role IDs
1328120848992960543, 1347741218158678097, 1328149760766640190 grant stats
refresh only while held in configured GUILD_ID, not vault access.
Migration `20261003050000_api_request_budget.sql` adds persistent atomic
product/day and member/day reservations: 80 system + 20 member per product,
5 member refreshes per user/day, 5-minute shared product cooldown, UTC reset.
NFL/NCAA share American-football. All NFL/multi transports are hooked,
including vault calls. Flags API_SPORTS_BUDGET_ENABLED and
MEMBER_STATS_REFRESH_ENABLED default off; activate together only after
confirming reset timezone/current provider usage and applying migration.
Fresh today-cache is reused; other dates remain cached. SQL concurrency,
actual provider requests, and positive live refresh are not verified.

HIGHROLLER cached tools implemented: guild-only ephemeral `/matchup`,
`/teamstats`, `/schedule`, `/results`. New migration
`20261003030000_highroller_stats_access.sql` adds a seller/plan-scoped,
service-role-only eligibility RPC. Deploy it before using the commands;
missing RPC errors are logged and fail closed, not a bot startup blocker.
Current HIGHROLLER plan IDs default in config but must be a subset of the
paid allowlist. Tools do not invoke provider APIs; existing refresh jobs own
cache freshness. Team form is up to ten recent finals within 30 days, not
season standings; schedules cover seven days. ALL-STAR/trials are excluded.
145 affected tests and live read-only NFL/basketball/college schedule cache
reads passed. Positive live HIGHROLLER authorization is not verified.
API-Sports display licensing remains a launch gate.

Owner explicitly authorized lifetime HIGHROLLER stats and vault access for
Discord ID `761388542965448767`. Migration
`20261003040000_owner_highroller_access.sql` grants separate private,
audited owner access with null expiry, not a Whop payment. Existing access RPCs
include the grant; ordinary free/trial accounts remain denied. Grant deployment
and positive authorization must be verified after the user runs the migration.
Discord HIGHROLLER role assignment remains separate; no admin permissions.

Current October 3 decision: only ALL-STAR and HIGHROLLER membership roles.
ALL-STAR product `prod_0Bi4ERPCfSWz1` contains the free seven-day one-time trial
`plan_ejj9LwTfrJp5z` and four existing paid passes. Trial expires without a
charge and is excluded from the eight-plan paid allowlist. HIGHROLLER product
`prod_6Hh9VAzQnzNiE` is paid-only. Product and paid variant titles were renamed;
the old separate ROOKIE product/plan and old recurring plans remain retired,
hidden, zero-stock, unlimited-stock disabled. Website and policy copies use
the two current names. Verify Whop's ALL-STAR role configuration and
trial-to-paid overlap; no automatic deletion of the Discord ROOKIE role was
performed. Private Discord experiences for both current products were verified
using product-filtered API listings; the detail `products` array is unreliable
for attachment verification. Legacy ROOKIE experience is renamed and private.
Role IDs inside the Discord app and trial expiration are still unverified.
Production polling is running (empty successful sync); local polling
is off and checkout is closed. Earlier details below are historical.

Owner confirmed and authorized creation of these Whop tiers on October 2, 2026:

Later that evening, the owner replaced recurring billing with one-time prepaid
Gold/Platinum passes: 30/90/180/365 days, with 0%/5%/10%/15% discounts.
Gold upfront prices: $9.99/$28.47/$53.95/$101.90.
Platinum upfront prices: $29.99/$85.47/$161.95/$305.90.
All eight plans are created, verified hidden/zero-stock, and saved in the paid
allowlist; the original recurring plans remain hidden but are excluded.
The free seven-day trial stays unchanged. See `DEPLOYMENT.md` for current IDs
and the additional prepaid-access migration. Nothing auto-renews or opens
checkout, and sync is still disabled.

- Free Trial: $0, seven days, expires without an automatic charge.
  Product `prod_XR7ObV8EF8hSM`; plan `plan_i9kjEGeSzcuOV`.
- Gold (Level 1): $9.99 per 30-day billing period.
  Product `prod_0Bi4ERPCfSWz1`; plan `plan_rAvIzb0XD2Jdz`.
- Platinum (Level 2): $29.99 per 30-day billing period.
  Product `prod_6Hh9VAzQnzNiE`; plan `plan_pvonMenMO9YDa`.

All three products/plans were created through the API and read back to verify
prices, seller, product associations, expiration, and availability. They are
hidden with zero stock and unlimited stock disabled; checkout remains closed.
Only Gold and Platinum are allowlisted for Member Vault paid access. Membership
sync remains disabled pending database deployment and live eligibility testing.
These confirmed names/prices supersede the older Starter/All Access proposals
below; website pricing and launch materials still require alignment before launch.

`LAUNCH_PLAN.md` currently proposes:

- Free: public verified record, weekly recap, announcements, occasional free analysis.
- Starter: `$9.99/month`, typically 1-2 curated plays on active slates, standard alerts, tracking.
- All Access: `$19.99/month`, every approved capper's official plays, real-time alerts, analysis, archive.
- Possible later Premium: `$34.99/month` with additional analysis, data tools, Q&A, and support.
- Possible trial: `$5` for 7 days, then `$19.99/month`, with clear affirmative consent and provider-supported abuse prevention.

These prices and the trial are planning suggestions, not confirmed final commercial terms. Get owner approval after provider fees and approval are known. Avoid weekly/lifetime plans initially. Do not suggest a specific number of plays every day; describe typical volume with a clear no-play-day caveat.

### Business Name and DBA

- The server display name “Playmaker Picks” alone does not necessarily require a DBA, but requirements depend on state/local law and how the name is used in commerce.
- Consistent customer-facing disclosure proposed: “Playmaker Picks is operated by Playmaker Sports Analytics, LLC.”
- Check state/county DBA rules before charging, contracting, invoicing, or banking under the brand. Do not give a universal legal conclusion.

## Site Direction and Information Architecture

### Public Arena

- Homepage: strong Playmaker Picks identity, current slate status, verified performance, a sample/free play, cappers, membership links, and community call to action.
- Results ledger: complete verified history, filters for sport/capper/date/play type/card, and transparent methodology.
- Sport pages and capper feeds/profiles.
- Public capper summaries; detailed biographies, notes, analysis, and full profile views are member-facing.
- Plans, how it works, methodology/grading, community preview, responsible-play notice, support, and legal pages.

### Private Clubhouse (Future Phases)

- Today: personalized active-play feed.
- Following: sports and cappers.
- Performance: detailed capper/sport analytics.
- Discussion: durable threads tied to plays, cappers, or analysis.
- Cappers: full profiles, records, schedules, notes.
- Archive: searchable analysis.
- Account: plan, expiration, Discord link, billing portal, privacy, support.

Discord remains the live alert/conversation channel at first. Do not duplicate every Discord feature on the website at launch; phase the web-community feature behind a clear scope and moderation plan.

### Visual Language

- Public pages: expressive condensed display type, sports photography or real sports/product imagery, strong scoreboard/editorial rhythm, black/white/field green, amber for pending/live, and red only for negative results.
- Clubhouse: quiet neutral backdrop, dense but readable information, compact consistent controls.
- Do not make this look like a sportsbook/casino: avoid chips, cash, slot imagery, “wager now” language, fake betting slips, or implied bet placement.
- Respect the existing site implementation and design tokens unless intentionally revising them after discussion. Avoid gradient-heavy generic templates.
- Ensure mobile and desktop layouts both work, with no clipped text, overlapping controls, or horizontal page overflow.

## Technical Architecture

### Current Repository

- Existing Discord bot: `src/`, Python, `discord.py`, Supabase.
- Existing play/results schema: `src/database/schema.sql`.
- Existing bot tests: `tests/`.
- Website: `web/`, React 19 + TypeScript + Vite 8, ESLint, Lucide icons.
- Amplify config: root `amplify.yml`, configured with app root `web` and artifacts `dist/`.
- Product roadmap: `LAUNCH_PLAN.md`.
- AI context/handoff: `AI_HANDOFF.md`.
- Keep the bot private on Proxmox. Amplify deploys only the website build artifacts. A private Git repo does not make frontend code secret after browser delivery.

### Current Website State

The first public-site prototype is implemented in `web/src/App.tsx`, `App.css`, and `index.css`. It includes a responsive home page, slate summary, reveal interaction, results preview filters, capper cards, proposed plans, methodology, and community section. Existing graphic copied to `web/public/growth.png`.

**Critical:** current result rows, slate counts, date, cappers, and play examples in `web/src/App.tsx` are hard-coded mock/demo content. They are not verified records and must not be represented as genuine historical performance. Before publishing, either replace with clearly identified approved sample content or connect the page to verified Supabase-backed data and ensure correct dates/timezones. The page currently labels the ledger as preview data; preserve or strengthen the disclosure until real data is integrated.

The plan buttons currently use a support email placeholder, and Discord invite is also a placeholder. Do not claim checkout, login, email delivery, live feed, membership role automation, or actual invite integration exists. Keep payment disabled until provider approval.

### Security Architecture

- Keep all secrets outside Git and outside the frontend bundle. Any `VITE_*` variable is public.
- Do not put Discord bot token, Supabase service role, provider secret, or webhook secret in frontend code or Amplify public variables.
- Use a protected backend endpoint (AWS Lambda, Supabase Edge Function, or approved server-side service) for signed payment webhooks and privileged entitlement changes.
- Supabase should be the system of record for membership entitlements; Discord roles are only a projection of entitlement state.
- Apply row-level security before exposing data to the site. Public result data may be served only if it is intended for public display. Locked live picks must be withheld server-side for unauthorized users.
- Use separate least-privilege credentials and staging/production configuration.

### Membership and Discord Integration (Not Yet Built)

- Do not overload existing `users.role` to represent billing. Add separate tables/migrations for plans, linked accounts, subscriptions, entitlements, provider webhook events, entitlement audit events, and Discord role-sync state.
- Webhooks must verify the raw-body signature, persist unique provider event IDs, and process idempotently.
- Handle purchase, renewal, upgrade/downgrade, cancel-at-period-end, expiration, refund, chargeback, admin grant/revoke, and role reconciliation.
- Cancellation should preserve entitlement through paid-through time if the provider/business terms say so. Refund/chargeback rules must match approved policy/provider behavior.
- Proposed roles: `Visitor`, `Free Member`, `Starter`, `All Access`, future `Premium`, `Founding Member`, `Promotional Access`, `Capper`, `Administrator`. Validate actual guild role IDs and permission setup before wiring code.
- Website authentication remains an open implementation decision. Owner floated a hybrid: free members use email plus Discord; paid billing is managed by the provider, then linked to an application account/Discord. Do not assume a particular auth product or final flow without checking owner/provider requirements.

## Operations and Promotions

- Start with at most three cappers and a documented trial; the plan suggests 30 days and 75-100 graded plays, but owner should approve criteria.
- Timestamp plays with line and odds. Preserve losses. Corrections must leave an audit trail.
- Keep capper status/compensation separate from member subscription entitlements. Use written contractor terms before compensation.
- Complimentary membership prizes should be time-limited, nontransferable, have no cash value, and extend an existing member's access rather than overlap if applicable.
- Random giveaway rules/free-entry mechanisms need proper review; a disclaimer alone does not make a promotion compliant.

## Current Worktree and Verification (At Handoff Creation)

- Current branch: `main`.
- Changes present: modified `.gitignore`; untracked `LAUNCH_PLAN.md`, `amplify.yml`, and `web/`.
- No changes have been committed.
- Website checks passed: `cd web && npm run build`; `cd web && npm run lint`.
- Playwright Chromium inspection passed at 1440x1000 and 390x844: no horizontal overflow, broken images, console errors, or runtime exceptions. Mobile menu, results filtering, and free-play reveal were exercised.
- Local dev URL used: `http://localhost:5173/`. If not running after workspace reopen, start it from the website directory with `npm run dev -- --host 0.0.0.0`.
- Playwright is installed as a development dependency. Browser Linux shared libraries were needed in the container; do not rerun dependency installation without checking environment first.

## Recommended Next Actions

1. Reconfirm active repository root and inspect `git status`; do not discard existing uncommitted work.
2. Keep `AI_HANDOFF.md` and `LAUNCH_PLAN.md` in view while proceeding.
3. Fix the public prototype's mock/demo data treatment before any real deployment. Do not present fabricated play history as verified.
4. Continue public-site work: split the large one-page `App.tsx` into purposeful routes/components only as the pages become real; add public legal/methodology/pricing pages and accessible filtering.
5. Confirm whether the existing Supabase `plays`/`users` records are safe to expose, then build a read-only, aggregated public-results API/query with appropriate RLS. Do not expose service-role credentials.
6. Get written payment-provider approval and current full fees before enabling paid checkout or committing to provider-specific code.
7. Resolve authentication/Discord linking, final plan pricing, trial terms, public result data scope, and web-community moderation scope with the owner.
8. Design membership migration and server-side entitlement policy; review before applying to production.
9. Build hosted checkout/webhooks and bot role sync only after provider approval and security review.
10. Retest production build, lint, browser desktop/mobile, and key user flows after every substantial UI or access-control change.

## Collaboration Style

- Work directly and pragmatically; the owner prefers building once ideas are clear.
- For ambiguous product questions, brainstorm with the owner instead of prematurely committing to an implementation.
- Clearly mark which pricing, legal, provider, and policy items remain proposals.
- Before editing, identify the owning code path and one focused check. After the first edit, run the narrow validation immediately.
- Keep updates concise. Never commit or create branches unless explicitly asked. Never overwrite or revert user changes.
