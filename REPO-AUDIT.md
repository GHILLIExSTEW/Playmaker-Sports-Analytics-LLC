# Playmaker Sports Analytics — Repository Audit & Security Review

Audit date: October 31, 2026

**What this is:** A sports-handicapping ("capper") community platform with three cooperating
systems sharing one Supabase (Postgres) backend:

1. **A Python Discord bot** (`src/`) — the operational core
2. **A Supabase database + edge functions layer** (`supabase/`) — 81 migrations, 2 Deno edge functions
3. **A React/Vite public website** (`web/`) — deployed via AWS Amplify

---

## 1. The Discord Bot (Python, `src/`)

**Entry point:** `src/bot.py` builds the discord.py client, loads all cogs, validates
guild/channels on ready, and starts background loops. Deployed on a Proxmox VM via
`scripts/discord-bot.service` (systemd, `/opt/discord-bot`). Config is entirely env-driven
through `src/config.py` (see `.env.example`).

**Core play lifecycle:**

- `play_submission.py` — cappers submit official picks via slash commands/modals; validated for
  sport/game/odds/units/duplicates, persisted to Supabase, published to Discord
- `play_settlement.py` — grades plays (win/loss/void/partial) once scores arrive; updates
  units/status
- `reporting.py`, `results.py`, `member_stats.py` — records, net units, tracker reports, weekly
  recaps
- `bang_notifications.py` — "BANG" (winning-play) announcements with an at-least-once
  delivery/recovery state machine backed by Supabase RPCs

**Capper moderation pipeline:** `capper_ballots.py` discovers slip images in a private forum
(with a handshake-emoji acknowledgement gate), extracts picks via OpenAI vision
(`services/image_play_service.py`), then `capper_chat_votes.py` + polls collect peer votes,
`capper_slip_reviews.py` (the largest module) runs approval/rejection/evidence workflows, and
`capper_review_records.py` writes durable audit records to a forum.

**Membership enforcement:** `membership_access.py` syncs Whop paid entitlements → Discord
HIGHROLLER role, with fail-closed roster reconciliation every 5 minutes, a one-time grandfather
capture (expires Feb 1, 2027), persistent revocation blocks, and strict exclusions (bots, owner,
admins, cappers never get HIGHROLLER).

**Member features:** `member_bet_vault.py` (private bet tracking + monthly challenge),
`member_activity.py`, `member_onboarding.py`, `research_workspace.py`, `data_models.py`
(model-card commands). `research_workspace.py` also exposes `/research matchup`, `players`,
`odds`, and budgeted `refresh` commands to eligible members; tracker-role cappers
automatically queue a refresh when game, player, or odds data is over 60 minutes old.
Other users receive cached reports only. `/research model` and `performance` remain
restricted to OPERATOR/Owner or approved research access.

## 2. Service Layer (`src/services/`, 34 modules)

- **Data plumbing:** `supabase_service.py` wraps the client with retries/pagination; connection
  drops are replayed up to 3x with idempotent worker claim tokens
- **Sports data pipeline:** `api_sports_service.py` (NFL) + `api_sports_multi_service.py`
  (NBA/MLB/NHL/soccer/MMA/F1 etc.) fetch from API-Sports under a strict shared budget
  (`api_budget_service.py`, 7,500 requests/UTC day, atomic reservations) → normalized into
  Supabase cache tables → `nflverse_service.py` adds EPA/efficiency data
- **Model stack:** `model_inputs_service.py` → `model_engine.py` ("ratings-v2") →
  `model_projection_service.py` → rendered as Pillow images by `model_card_image_service.py`.
  The bot also runs a 2-second-poll worker that claims website-queued model-refresh jobs
- **Settlement/grading:** `settlement_service.py`, `play_features_service.py` (gradability),
  `official_play_service.py` (void/regrade/audit)
- **Imaging:** play cards, tracker images, photo sanitization, OpenAI vision slip extraction
- **Ops scripts** (`scripts/`): void plays, settle outstanding, diagnose open plays, audit
  units, and generate the user-guide PDF from HTML

## 3. Supabase Layer (`supabase/`)

- **82 migrations** (Sept 30 – Oct 31, 2026) build: core play/settlement schema, member
  profiles/favorites, Whop membership + trials, bet vault, API budget accounting, sports caches,
  model history/projections, research workspace, capper credentials/ballots/insights/forums,
  BANG delivery recovery, HIGHROLLER grandfather/revocation, plus 7 large guide-content
  migrations. `src/database/schema.sql` holds the base relational model
- **Access model is deny-by-default:** anon users get only public RPCs (settled results, events,
  profiles); members get self-scoped data; roles escalate capper → highroller → operator →
  owner; service-role owns caches, queues, and audits. Privileged tables revoke direct access
  and expose narrow `SECURITY DEFINER` RPCs
- **Edge function `capper-api`:** authenticated HTTP proxy to API-Sports for approved cappers'
  backend systems. SHA-256-hashed bearer keys, atomic budget reservation, 20s timeout, 2MiB cap,
  full audit, fail-closed
- **Edge function `capper-research`:** `analyze` / `extract` / `pull` actions for the website's
  phone-friendly research tools. Verifies the user's Supabase session itself (gateway JWT off),
  uses the user's JWT for reads so RLS stays authoritative, service role only for validated
  provider writes. AI analysis excludes historical scores, filters unsupported qualitative
  claims, and runs OpenAI with `store: false`; quotas are 50/user + 500 global per day

## 4. Website (`web/`)

React 19 + TypeScript + Vite + React Router 7 + Recharts, built by AWS Amplify (`amplify.yml`).
`web/src/App.tsx` does pathname-based routing with lazy-loaded pages:

- **Public:** sports/NFL scores, settled results, capper profiles, membership marketing (Whop
  checkout), policies
- **Members** (gated by `website_member_access` via `MemberAccess.tsx`: sign-in + age
  verification + Discord link + active paid/trial/grandfather entitlement): current picks feed,
  home feed, stats workspace, account page
- **Cappers:** `CapperToolsPage.tsx` (research + team pulls + AI analysis), pick insight
  editing, profile customization
- **Operator/Owner:** NFL Matchup Lab, Data Models, Research Workspace, API-key management,
  user guide

All authorization is server-side via RPCs/RLS — client gating is UX only. 20 Node unit-test
files + 9 Playwright specs cover access control, budgets, caches, and workflows.

## 5. Test Suite (`tests/`, 54 files)

Heavily mocked pytest suite emphasizing **fail-closed authorization, idempotency, and
partial-failure recovery**. Deepest coverage: slip reviews, bet vault, membership access, model
engine. Plus 2 transactional SQL tests (BANG recovery, membership trials) that roll back.
Gaps: thin wrappers (supabase/photo/diagnostic services) get smoke-level coverage only; no true
end-to-end or migration-correctness tests.

## Cross-cutting design themes

1. **Fail-closed everywhere** — missing role evidence, unavailable budget checks, or unverified
   rosters deny access rather than defaulting open
2. **Strict cost governance** — every API-Sports and OpenAI call passes through atomic
   reservation RPCs with per-user, global, and shared daily ceilings; failures consume quota and
   are never auto-retried
3. **Idempotent distributed work** — claim tokens, replay-safe RPCs, and uncertain-outcome
   handling for the bot↔Supabase worker queues
4. **Server-authoritative security** — the browser holds only the anon key; the bot holds
   service-role; edge functions re-verify sessions or hashed keys themselves

---

# Security Review (October 31, 2026)

## Findings

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| 1 | MEDIUM | src/bot.py (also play_submission.py, administration.py, config.py) | 431-439 | Official-role authorization failed open: when `OFFICIAL_ROLE_IDS` was empty (as shipped in `.env.example`), every guild member passed `is_official()` and could `/settle`, `/regrade`, `/unsettle`, `/edit_play`, post recaps, update the tracker, and trigger paid OpenAI vision calls via `/test` — corrupting the official track record | 8/10 |

### Finding 1 — RESOLVED

Fixed on October 31, 2026:

- `src/bot.py` — `is_official()` now returns `False` when `OFFICIAL_ROLE_IDS` is empty
  (fail closed); `main()` logs a prominent warning at startup when unconfigured
- `src/play_submission.py` — `/play` and image-import official checks now deny when
  `OFFICIAL_ROLE_IDS` is empty
- `src/administration.py` — `/test` diagnostics now deny when unconfigured
- `.env.example` — documented that blank role IDs fail closed
- `tests/test_administration.py` — tests updated to use a configured role + authorized user
- `tests/test_play_picker.py` — new regression test asserting fail-closed behavior

**Deployment note:** production `.env` must set `OFFICIAL_ROLE_IDS` (and `OPERATOR_ROLE_IDS`) or
officials will be locked out of settlement commands. Operators and users with Manage Server
retain `can_manage_plays` access regardless.

## Verified clean (no findings)

- **Edge functions (capper-api, capper-research):** SSRF prevented by fixed host allowlists and
  strict path regex (no `..`/`//`), `redirect: 'error'`, scalar-only params; bearer keys SHA-256
  hashed with `FOR UPDATE` serialization; CORS restricted to an exact origin allowlist; image
  uploads validated by MIME regex + magic bytes; untrusted notes/images explicitly wrapped as
  `user_supplied_unverified` with hardened system instructions (prompt injection mitigated)
- **Migrations (81 files):** every `SECURITY DEFINER` function sets `search_path = ''`; RLS
  enabled on all created tables with blanket revokes from public/anon/authenticated; `anon`
  grants limited to genuinely public read RPCs; social-link/avatar URLs validated server-side
  and client-side; storage buckets use restrictive owner-guard policies with per-user path
  regexes; AI/budget reservations use advisory locks and write-once completion (no refund race)
- **React SPA:** the only `dangerouslySetInnerHTML` renders repository-owned HTML from an
  operator-gated `SECURITY DEFINER` RPC; all user-controlled `href`/image URLs are validated to
  `https:` with credential checks; the hard-coded Supabase URL + publishable (anon) key in
  `web/src/supabaseClient.ts` is public by design
- **Python bot:** no raw SQL (PostgREST builder only), no `subprocess`/`eval`/`pickle`; artwork
  downloads restricted to an HTTPS host allowlist with redirects disabled and size/pixel caps;
  Whop sync strictly validates seller/plan/payment windows
- **Secrets:** no credentials committed; `.env.example` values are blank or non-secret IDs

## Observations (non-vulnerabilities worth knowing)

- `web/src/supabaseClient.ts` hard-codes a fallback project URL + publishable anon key
  (acceptable for a public anon key)
- Routing in `web/src/App.tsx` is manual pathname matching rather than declarative routes
- `.env.example` embeds real production channel/role/plan IDs (non-secret, but identifying)
- Pre-existing test-environment caveat: 4 tests in `tests/test_capper_insights.py` and
  `tests/test_reaction_reconciliation.py` fail without `SUPABASE_URL`/`SUPABASE_KEY` configured;
  unrelated to the security fix
