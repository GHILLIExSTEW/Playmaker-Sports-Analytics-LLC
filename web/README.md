# React + TypeScript + Vite

## NFL Matchup Lab

Visit `/nfl/lab` from the Sports dropdown or NFL scores page. The lab reuses
the existing public API-Sports NFL cache; no new provider calls, paid feed,
prediction model or database migration are added. All RPC pages are fetched
before deriving a team's same-season/same-stage completed-game record,
home/away splits, last-five results, scoring averages and days between kickoffs.
Only FT/AOT games strictly before the selected kickoff with both scores count.
Source sync timestamps, sample sizes, empty data and failures are displayed.
Historical views are not point-in-time backtests.

The calculator accepts manually entered American odds/stake and an optional
user-estimated probability. It shows implied/break-even probability, payout,
and expected profit conditional on that estimate; no no-vig or model forecast
is claimed. CSV downloads include matchup summaries, season game rows and
calculator inputs/outputs; exported string formulas are neutralized for Excel.
Injuries, weather, live prices and player projections are explicitly unavailable.
Confirm API-Sports licensing allows this data's public display/download before
production rollout. No live provider/licensing approval is claimed here.

## Live Results Setup

The public site reads settled play records through the restricted Supabase
`public.public_settled_results()` RPC. It returns only posted, settled plays
and fields needed for the public ledger and capper summaries. Open plays,
Discord IDs, message IDs, and account data are not exposed.

1. In Supabase Dashboard, open **SQL Editor** and run
   `supabase/migrations/20260930230000_public_settled_results.sql` from the
   repository root.
2. Run `supabase/migrations/20260930240000_public_capper_avatars.sql` to add
   public avatar support to capper pages.
3. Copy `web/.env.example` to `web/.env.local`; set the project URL and public
  anon/publishable key from **Project Settings → API**.
4. Restart Vite after changing local environment variables.

The public key is intended for browser use. Never put the bot's `service_role`
key in a `VITE_*` variable, website setting, or frontend source. The SQL
function grants anonymous access only to the restricted settled-results query.

## Brand Logo

The transparent brand mark is stored at
`website-assets/brand/playmaker-mark-transparent-512.webp` in the dedicated
public Supabase Storage bucket. The existing `Media` bucket remains private.
The site uses the public logo URL by default and falls back to the optimized
local WebP in `web/public/playmaker-mark-transparent.webp` if storage is unavailable. Apply
`supabase/migrations/20260930250000_website_assets_bucket.sql` when setting up a
new Supabase project.

The homepage hero uses the transparent arched artwork derived from
`src/Media/logo_transparent.png`, served as
`website-assets/brand/playmaker-arch-transparent-1024.webp` with a bundled
fallback at `web/public/playmaker-arch-transparent.webp`.

## Capper Pages

Apply `20261008120000_capper_full_appearance.sql` after background colors for
separate name/link/body colors, heading-font choices, an optional banner image
and section ordering. Uploads support banners (1600px) and avatars (512px);
both use the existing owner-restricted Supabase folder. Colors default to
automatic contrast unless customized. The four record/content sections remain
present, and rearranging them also rearranges keyboard/DOM order.

Apply `20261008110000_capper_background_color.sql` after Owner-role editing
for persistent per-capper background colors. The page editor includes **Page
background color**; header/navigation and other pages keep the site-wide theme.
Foreground text adjusts for light/dark backgrounds, while pick cards retain
their readable dark surfaces.

Apply `20261008100000_owner_role_page_editing.sql` and deploy/restart the bot
for Discord Owner role `1347741218158678097` to edit every capper's page
settings and open-pick insights. Ownership is checked against the fresh
bot-synced role roster, not editable auth metadata. Ordinary OPERATORs still
edit only their own content. Owner uploads stay in their own avatar folder.

Apply `20261008090000_website_capper_insight_editing.sql` after the insight
and page-settings migrations for **Add your insight / Edit your insight**
controls on the author's open picks. They appear on both the capper page and
expert board, require verified OPERATOR ownership and member access, and update
the same justification used by the Discord modal. Settled picks are not editable.

Apply `20261008070000_capper_page_settings.sql` to enable owner customization.
Verified Discord owners currently in the OPERATOR roster can edit their page's
accent color, bio, HTTPS profile image URL and Website/X/Instagram/Discord links
from **Edit your capper page**. These settings are public; picks, names and
performance records are not editable. Database ownership checks run on every
save, and raw settings access is denied. Apply `20261008080000_capper_avatar_uploads.sql`
for direct PNG/JPEG/WebP uploads into the existing public `website-assets`
bucket. Images up to 10 MB are resized to 512px and converted to WebP;
each owner can manage only their own avatar folder. HTTPS image URLs remain
an alternative. Clearing the upload and image URL restores the default avatar.

Each capper has a URL at `/cappers/<slug>` with their settled record, cumulative
net-unit graph, sport-by-sport net graph, and full results ledger. To show a
capper's own image instead of the generated initials avatar, set their approved
HTTPS image URL in `public.users.public_avatar_url`. Only this dedicated avatar
field is returned by the public results function.

For AWS Amplify, add `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` as
environment variables. These values are public in the browser build; data
access remains restricted by the RPC and its grants. In **Hosting → Rewrites
and redirects**, add a rewrite from `/<*>` to `/index.html` with status `200`
so direct visits and bookmarks to capper pages load the SPA.

## Member Accounts

1. Run `supabase/migrations/20260930270000_member_accounts_favorites.sql`, then
  `supabase/migrations/20260930280000_member_profile_controls.sql` in Supabase
  SQL Editor.
2. In Supabase **Authentication → Providers**, enable Discord and enter the
  Discord application's client ID and newly rotated client secret.
3. Copy the Supabase Discord callback URL shown for the provider (typically
  `https://<project-ref>.supabase.co/auth/v1/callback`) into Discord Developer
  Portal → OAuth2 → Redirects. Do not use the website's `/OauthURI/redirect`
  path as the provider callback.
4. In Supabase **Authentication → URL Configuration**, set the production Site
  URL to `https://playmakersportsanalytics.com` and allow the production and
  local `/account` redirect URLs.

Members can edit their display name, profile handle, avatar URL, and time zone;
opt into a public `/members/<handle>` profile; and choose favorite sports and
cappers. Favorites remain private unless the member explicitly enables public
profile sharing. Account export downloads those member settings as JSON, and
account deletion removes the auth identity and its saved preferences. Alert
toggles are saved as preferences, but delivery channels are not implemented yet.

The account page signs members in with Discord. It sends date of birth to the
`verify_member_age` database function, which checks the 21+ threshold and saves
only a verification timestamp; it does not store the birth date. Favorites are
available only after verification and are restricted to the signed-in user by
row-level security. This is self-attested age verification, not an identity or
document verification service.

## Current Picks and Verified Website Access

Apply `20261008050000_capper_insights.sql` after the access/team migrations
and deploy the matching bot. Current pick cards are compact, with a collapsed
**Capper insight** disclosure only when the author supplied justification.
The RPC's `analysis` field now contains only that separate authored text;
extracted slip selections are never presented as reasoning. This applies to
the expert board and embedded capper-page feed. Insight is restricted to the
existing member entitlement checks and is not added to public settled results.
The bot requests it in the confirmation channel through an author-only button
and private modal. A play manager can run `/request_insights` for existing open
picks. See the deployment guide for migration order and rollout checks.

Apply `20261008030000_operator_website_access.sql` after the owner-preview
migration and deploy the matching frontend to allow age-verified OPERATOR
members to view picks while enrollment is closed. Access is resolved using
provider-managed Discord identity and the fresh bot roster, not editable role
metadata. Apply `20261008040000_operator_highroller_benefits.sql` to include
stats/vault access and membership reconciliation. The UI displays complimentary
HIGHROLLER team access, not paid access. Benefits have no scheduled expiry
while the OPERATOR role remains verified; removing the role removes team access.
Existing independent paid/trial/owner entitlements are preserved. No Whop
payment or trial claim is fabricated.

For owner-only prelaunch testing, apply
`20261008020000_owner_website_preview.sql`. It permits only the verified owner
Discord identity with completed age verification and a current seller-matched
owner grant through the closed launch gate. Paid/trial enrollment remains
closed; leave the membership config disabled. Current-picks access still
requires a fresh OPERATOR roster. This does not substitute for live launch tests.

Capper eligibility now follows the Discord OPERATOR/tracker role, not merely
having published plays. Apply `20261008010000_operator_capper_roster.sql` after
the member-picks migration and deploy the bot's five-minute roster sync.
Role `1328120848992960543` is the configured default. A missing/stale roster
produces an explicit error; it never lists all historical authors as cappers.
Historical settled results are preserved independently.
An unavailable roster blocks capper choices only, not member profile settings,
age verification, or membership status. The account page shows the roster
error with a retry action; other account-load errors include their database
message so missing schema/grants can be diagnosed.

Apply `supabase/migrations/20261008000000_website_member_picks.sql` after the
existing account, Whop paid, owner-grant, and full-trial migrations. It adds the
optional `users.public_avatar_url` column if missing; the older public-avatar
migration is still needed to include avatars in the public settled-results RPC.
The new
`/picks` board filters published open official plays by sport and capper.
Capper pages include their current board, even before their first settled
result. The public directory exposes only author names and approved avatars;
performance metrics and the public ledger still use settled results only.
The homepage and primary navigation link to the board.

Website premium access is **disabled by default**. The database-owner/service
configuration is `public.website_membership_config`; clients cannot read or
change it. Copy the seller and the **complete** bot `WHOP_PAID_PLAN_IDS` and
`WHOP_TRIAL_PLAN_IDS` allowlists into this configuration before enabling it.
The seeded paid list includes only the new 30-day plan; add all approved
historical paid IDs so existing purchases keep access. Never put a trial ID
in the paid list. Follow the staging/production checklist in `DEPLOYMENT.md`;
do not enable production access or checkout before the launch gates pass.

`website_member_access()` resolves the signed-in user's provider-managed
`auth.identities.provider_id` for Discord, not editable user metadata or
Discord roles. It requires age verification and a seller/plan-scoped verified
paid snapshot, an eligible matching seven-day trial claim, or a current
explicit owner HIGHROLLER grant. Paid/trial snapshots older than 15 minutes
fail closed, as do expired or revoked access and future verification timestamps
more than five minutes ahead. The account page shows this verified access
instead of a static "No paid plan" placeholder.

`member_current_picks()` rechecks access on every request and rejects ineligible
users. It excludes unpublished drafts, settled/inconsistent records, and
duplicate message records. Only display fields are returned, not Discord or
message identifiers. Source play/user/history tables have their browser grants
revoked, preserving service-role access and the restricted public RPCs.
No slip-image reveal, reaction requirement, or change to Discord Tail tracking
is included. Event times and live status are not inferred from publication time.

The browser rechecks access and refreshes the board every 30 seconds, on focus,
and on explicit refresh. It hides previously loaded picks while checking access,
when signed out, when expired, or on access-check failure. Exact pass expiry
also triggers a recheck; changes already viewed cannot be retroactively erased
from a user's memory or saved copies.

Validation from `web/`:

```powershell
npm.cmd run test:access
npx.cmd playwright install chromium --only-shell
npm.cmd run test:picks
npm.cmd run build
npm.cmd run lint
```

The access suite executes the real migrations in isolated embedded PostgreSQL
(PGlite), with synthetic OAuth identities and entitlements. Browser tests use
mock API responses and a dedicated localhost port. Neither suite writes to
production or proves live Discord OAuth, Whop purchases, role mappings, or
provider approval.

## NFL API-Sports Feed

1. Run `supabase/migrations/20260930260000_api_sports_nfl.sql` in Supabase SQL
  Editor. It creates private cache tables and public read-only RPCs.
2. Add `API_SPORTS_KEY` to the Proxmox bot's `/opt/discord-bot/.env`. Keep this
  key server-side; never add it to Amplify or a `VITE_*` variable.
3. Deploy/restart the bot. It performs an initial sync, then a daily sync at
  6:10 AM Eastern for the full-season schedule, standings, and teams: three
  API requests per successful daily sync.
4. During cached game windows only, the bot checks scores every 15 minutes with
  one date-scoped request. Outside those windows it makes no live-score calls.

The website reads `public_nfl_games`, `public_nfl_standings`, and
`public_nfl_data_status` from Supabase. It never calls API-Sports directly.

## Multi-Sport API-Sports Events

1. Run `supabase/migrations/20260930300000_api_sports_multi_event_cache.sql` in
  Supabase SQL Editor. It creates a private event cache and a public read-only
  RPC.
2. Run `supabase/migrations/20260930310000_add_ncaa_public_event_feed.sql` to
  expose the dedicated NCAA feed through that RPC.
3. The Proxmox bot uses `API_SPORTS_KEY` from its server-side `.env`; never put
  this key in Amplify or a `VITE_*` setting.
4. The daily sync requests a rolling seven-day schedule for each reachable
  date-based API, plus the current Formula 1 season. Live scores are refreshed
  every 15 minutes only for sports with a cached game near/in progress.

Connected product feeds currently include Football, Basketball, Baseball,
Hockey, Rugby, Handball, Volleyball, Formula 1, MMA, and NCAA football.
American Football uses the dedicated NFL feed above, while NCAA has its own
`/sports/ncaa` page backed by the American Football NCAA league filter. Sports
with multiple leagues, including Hockey, show league tabs before their
round/week schedule tabs. Cricket and Cycling remain listed but their
configured API-Sports hosts did not resolve during setup, so those pages show
that their feed is unavailable instead of fabricating data. API-Sports access
depends on the account's subscription.

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])

```

You can also install [eslint-plugin-react-x](https://npmx.dev/package/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://npmx.dev/package/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])

```
