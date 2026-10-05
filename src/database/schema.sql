-- Multi-sport official play tracking schema

create table if not exists public.sports (
  id bigserial primary key,
  api_slug text not null unique,
  name text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

insert into public.sports (api_slug, name, is_active)
values
  ('baseball', 'Baseball', true),
  ('american-football', 'American Football', true),
  ('ncaa-football', 'NCAA Football', true),
  ('basketball', 'Basketball', true),
  ('football', 'Soccer', true),
  ('formula-1', 'Formula 1', true)
on conflict (api_slug) do update
set name = excluded.name, is_active = true;

create table if not exists public.leagues (
  id bigserial primary key,
  sport_id bigint not null references public.sports(id) on delete cascade,
  api_league_id text not null,
  name text not null,
  country text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (sport_id, api_league_id)
);

create table if not exists public.teams (
  id bigserial primary key,
  sport_id bigint not null references public.sports(id) on delete cascade,
  league_id bigint references public.leagues(id) on delete set null,
  api_team_id text not null,
  name text not null,
  short_name text,
  country text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (sport_id, api_team_id)
);

create table if not exists public.players (
  id bigserial primary key,
  sport_id bigint not null references public.sports(id) on delete cascade,
  api_player_id text not null,
  full_name text not null,
  first_name text,
  last_name text,
  position text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (sport_id, api_player_id)
);

create table if not exists public.roster_snapshots (
  id bigserial primary key,
  sport_id bigint not null references public.sports(id) on delete cascade,
  league_id bigint not null references public.leagues(id) on delete cascade,
  team_id bigint not null references public.teams(id) on delete cascade,
  player_id bigint not null references public.players(id) on delete cascade,
  season text,
  snapshot_at timestamptz not null default now(),
  source text not null default 'api-sports',
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.users (
  id bigserial primary key,
  discord_user_id text not null unique,
  username text,
  display_name text,
  team_id bigint references public.teams(id) on delete set null,
  role text not null default 'member' check (role in ('member', 'official', 'operator', 'admin')),
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.plays (
  id bigserial primary key,
  user_id bigint not null references public.users(id) on delete restrict,
  team_id bigint references public.teams(id) on delete set null,
  sport_id bigint not null references public.sports(id) on delete restrict,
  league_id bigint references public.leagues(id) on delete set null,
  team_name text,
  units numeric(10,2) not null check (units > 0),
  legs integer not null check (legs >= 1),
  odds integer not null check (odds <> 0),
  status text not null default 'open' check (status in ('open', 'win', 'loss', 'void', 'partial', 'regraded')),
  play_text text,
  message_id text,
  created_at timestamptz not null default now(),
  settled_at timestamptz,
  settled_by bigint references public.users(id) on delete set null
);

create table if not exists public.play_draft_legs (
  id bigserial primary key,
  draft_id text not null,
  discord_user_id text not null,
  units numeric(10,2) not null check (units > 0),
  expected_legs integer not null check (expected_legs >= 1),
  leg_number integer not null check (leg_number >= 1),
  selection text not null,
  odds integer not null check (odds <> 0),
  team_name text,
  created_at timestamptz not null default now(),
  unique (draft_id, leg_number)
);

create table if not exists public.play_legs (
  id bigserial primary key,
  play_id bigint not null references public.plays(id) on delete cascade,
  leg_number integer not null check (leg_number >= 1),
  selection text not null,
  odds integer not null check (odds <> 0),
  created_at timestamptz not null default now(),
  unique (play_id, leg_number)
);

create table if not exists public.play_versions (
  id bigserial primary key,
  play_id bigint not null references public.plays(id) on delete cascade,
  version_number integer not null,
  changed_by bigint references public.users(id) on delete set null,
  changed_at timestamptz not null default now(),
  old_values jsonb,
  new_values jsonb,
  unique (play_id, version_number)
);

create table if not exists public.settlements (
  id bigserial primary key,
  play_id bigint not null references public.plays(id) on delete cascade,
  result text not null check (result in ('win', 'loss', 'void', 'partial', 'regraded')),
  settled_units numeric(10,2),
  settled_by bigint references public.users(id) on delete set null,
  note text,
  created_at timestamptz not null default now()
);

create table if not exists public.sync_jobs (
  id bigserial primary key,
  job_name text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  success boolean not null default false,
  record_count integer not null default 0,
  error_message text
);

create table if not exists public.team_daily_summary (
  id bigserial primary key,
  sport_id bigint not null references public.sports(id) on delete cascade,
  league_id bigint references public.leagues(id) on delete set null,
  team_id bigint not null references public.teams(id) on delete cascade,
  report_date date not null,
  wins integer not null default 0,
  losses integer not null default 0,
  voids integer not null default 0,
  partials integer not null default 0,
  net_units numeric(10,2) not null default 0,
  updated_at timestamptz not null default now(),
  unique (sport_id, team_id, report_date)
);

create table if not exists public.unit_entries (
  id bigint generated always as identity primary key,
  user_id text not null,
  total_units numeric not null,
  message_id text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.unit_results (
  id bigint generated always as identity primary key,
  user_id text not null,
  total_units numeric not null,
  message_id text not null,
  result text not null check (result in ('win', 'loss')),
  created_at timestamptz not null default now()
);

create table if not exists public.unit_entries_archive (
  id bigint generated always as identity primary key,
  user_id text not null,
  total_units numeric not null,
  message_id text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.unit_results_archive (
  id bigint generated always as identity primary key,
  user_id text not null,
  total_units numeric not null,
  message_id text not null,
  result text not null check (result in ('win', 'loss')),
  created_at timestamptz not null default now()
);

create table if not exists public.playmakers (
  id bigint generated always as identity primary key,
  user_id text not null unique,
  display_name text,
  image_path text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.api_sports_nfl_games (
  game_id bigint primary key,
  league_id bigint not null,
  season integer not null,
  stage text,
  week text,
  kickoff_at timestamptz not null,
  venue_name text,
  venue_city text,
  status_short text not null,
  status_long text not null,
  home_team_id bigint,
  home_team_name text not null,
  home_team_logo text,
  away_team_id bigint,
  away_team_name text,
  away_team_logo text,
  home_score integer,
  away_score integer,
  scores jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now()
);

create table if not exists public.api_sports_nfl_standings (
  league_id bigint not null,
  season integer not null,
  team_id bigint not null,
  team_name text not null,
  team_logo text,
  conference text,
  division text,
  position integer not null default 0,
  wins integer not null default 0,
  losses integer not null default 0,
  ties integer not null default 0,
  points_for integer not null default 0,
  points_against integer not null default 0,
  point_difference integer not null default 0,
  streak text,
  records jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now(),
  primary key (league_id, season, team_id)
);

create table if not exists public.api_sports_nfl_teams (
  team_id bigint primary key,
  name text not null,
  code text,
  city text,
  coach text,
  stadium jsonb not null default '{}'::jsonb,
  established integer,
  logo text,
  country jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now()
);

create table if not exists public.api_sports_sync_state (
  sync_key text primary key,
  last_attempt_at timestamptz not null,
  last_success_at timestamptz,
  request_count integer not null default 0,
  success boolean not null default false,
  error_message text
);

create table if not exists public.member_profiles (
  user_id uuid primary key references auth.users(id),
  age_verified_at timestamptz,
  created_at timestamptz not null default now(),
  display_name text not null default 'Playmaker Member'
    check (length(btrim(display_name)) between 1 and 60),
  public_handle text not null default (
    'member-' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 12)
  ) check (
    public_handle ~ '^member-[a-f0-9]{12}$'
    or public_handle ~ '^[a-z0-9][a-z0-9-]{2,29}$'
  ),
  avatar_url text check (avatar_url is null or avatar_url like 'https://%'),
  public_profile_enabled boolean not null default false,
  timezone text not null default 'America/New_York'
    check (length(btrim(timezone)) between 1 and 80),
  discord_alerts_enabled boolean not null default false,
  email_alerts_enabled boolean not null default false
);

create table if not exists public.member_favorite_sports (
  user_id uuid not null references auth.users(id),
  sport_id bigint not null references public.sports(id),
  created_at timestamptz not null default now(),
  primary key (user_id, sport_id)
);

create table if not exists public.member_favorite_cappers (
  user_id uuid not null references auth.users(id),
  sport_id bigint not null references public.sports(id),
  capper_name text not null check (length(btrim(capper_name)) between 1 and 120),
  created_at timestamptz not null default now(),
  primary key (user_id, sport_id, capper_name)
);

create table if not exists public.api_sports_events (
  sport_slug text not null,
  event_id text not null,
  league_id text,
  league_name text,
  season text,
  round_name text,
  event_name text not null,
  start_at timestamptz not null,
  venue jsonb not null default '{}'::jsonb,
  home_id text,
  home_name text,
  home_logo text,
  away_id text,
  away_name text,
  away_logo text,
  home_score jsonb,
  away_score jsonb,
  status_code text not null default 'UNK',
  status text not null default 'Unknown',
  raw_event jsonb not null default '{}'::jsonb,
  synced_at timestamptz not null default now(),
  primary key (sport_slug, event_id)
);

create table if not exists public.member_bets (
  id bigint generated always as identity primary key,
  source_message_id text not null unique,
  channel_id text not null,
  guild_id text not null,
  owner_id text not null,
  owner_name text not null,
  image_path text not null unique,
  card_message_id text unique,
  original_removed boolean not null default false,
  status text not null default 'processing'
    check (status in ('processing', 'draft', 'open', 'review', 'win', 'loss', 'void')),
  details jsonb not null default '{}'::jsonb,
  event jsonb,
  units numeric check (units > 0),
  odds integer check (abs(odds) >= 100),
  verification text,
  review_reason text,
  attempts integer not null default 0,
  next_check_at timestamptz not null default now(),
  card_dirty boolean not null default true,
  created_at timestamptz not null default now(),
  confirmed_at timestamptz,
  settled_at timestamptz,
  updated_at timestamptz not null default now()
);

create table if not exists public.member_bet_audit (
  id bigint generated always as identity primary key,
  bet_id bigint not null references public.member_bets(id),
  actor_id text not null,
  result text not null check (result in ('win', 'loss', 'void')),
  verification text not null,
  reason text not null check (length(btrim(reason)) > 0),
  created_at timestamptz not null default now()
);

create table if not exists public.whop_memberships (
  membership_id text primary key,
  whop_user_id text,
  discord_user_id text check (discord_user_id is null or discord_user_id ~ '^[0-9]{1,20}$'),
  account_id text not null,
  plan_id text not null,
  status text not null,
  paid_from timestamptz,
  paid_through timestamptz,
  payment_id text,
  paid boolean not null,
  cancel_at_period_end boolean not null,
  source_updated_at timestamptz not null,
  verified_at timestamptz not null,
  verification_reason text not null
);

create table if not exists public.whop_membership_audit (
  id bigint generated always as identity primary key,
  membership_id text not null,
  snapshot jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists public.owner_membership_grants (
  discord_user_id text primary key check (discord_user_id ~ '^[0-9]{1,20}$'),
  account_id text not null,
  tier text not null check (tier = 'highroller'),
  vault_access boolean not null default false,
  reason text not null check (length(btrim(reason)) > 0),
  starts_at timestamptz not null default now(),
  expires_at timestamptz,
  revoked_at timestamptz
);

create table if not exists public.owner_membership_grant_audit (
  id bigint generated always as identity primary key,
  discord_user_id text not null,
  action text not null,
  previous_snapshot jsonb,
  snapshot jsonb,
  changed_at timestamptz not null default now()
);

create table if not exists public.api_request_budget (
  product text not null,
  day date not null,
  system_requests integer not null default 0,
  member_requests integer not null default 0,
  last_member_request timestamptz,
  primary key (product, day)
);

create table if not exists public.api_member_request_usage (
  discord_user_id text not null,
  day date not null,
  requests integer not null default 0,
  primary key (discord_user_id, day)
);

create table if not exists public.api_sports_player_game_stats (
  sport_slug text not null
    check (sport_slug in ('nfl', 'ncaa', 'basketball', 'football', 'formula-1')),
  game_id bigint not null check (game_id > 0),
  payload jsonb not null check (jsonb_typeof(payload) = 'array'),
  synced_at timestamptz not null,
  primary key (sport_slug, game_id)
);

create table if not exists public.api_sports_player_directory (
  sport_slug text not null,
  league_id text not null,
  player_id bigint not null check (player_id > 0),
  name text not null,
  team_name text,
  synced_at timestamptz not null,
  primary key (sport_slug, league_id, player_id)
);

create table if not exists public.api_sports_player_seasons (
  sport_slug text not null,
  league_id text not null,
  player_id bigint not null check (player_id > 0),
  season text not null,
  groups jsonb not null check (jsonb_typeof(groups) = 'array'),
  coverage text not null,
  synced_at timestamptz not null,
  primary key (sport_slug, league_id, player_id, season)
);

create table if not exists public.api_sports_player_leagues (
  sport_slug text not null,
  league_id text not null,
  name text not null,
  current_season text not null,
  primary key (sport_slug, league_id)
);

create table if not exists public.api_sports_player_season_metadata (
  sport_slug text not null,
  league_id text not null,
  season text not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'array'),
  synced_at timestamptz not null,
  primary key (sport_slug, league_id, season)
);

create table if not exists public.whop_trial_memberships (
  membership_id text primary key,
  whop_user_id text,
  discord_user_id text check (discord_user_id is null or discord_user_id ~ '^[0-9]{1,20}$'),
  account_id text not null,
  plan_id text not null,
  status text not null,
  paid_from timestamptz,
  paid_through timestamptz,
  trial_verified boolean not null,
  trial_eligible boolean not null default false,
  source_updated_at timestamptz not null,
  verified_at timestamptz not null,
  verification_reason text not null
);

create table if not exists public.membership_trial_claims (
  account_id text not null,
  whop_user_id text not null,
  discord_user_id text not null,
  membership_id text not null unique,
  starts_at timestamptz not null,
  expires_at timestamptz not null,
  eligible boolean not null,
  claimed_at timestamptz not null default now(),
  primary key (account_id, whop_user_id)
);

create table if not exists public.whop_trial_membership_audit (
  id bigint generated always as identity primary key,
  membership_id text not null,
  snapshot jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_leagues_sport on public.leagues(sport_id);
create index if not exists idx_teams_sport_league on public.teams(sport_id, league_id);
create index if not exists idx_players_sport on public.players(sport_id);
create index if not exists idx_roster_team_snapshot on public.roster_snapshots(team_id, snapshot_at);
create index if not exists idx_plays_user_created on public.plays(user_id, created_at);
create index if not exists idx_plays_status on public.plays(status);
create index if not exists idx_plays_sport_team on public.plays(sport_id, team_id, created_at);
create index if not exists idx_plays_team_name on public.plays(team_name);
create index if not exists idx_play_draft_legs_draft on public.play_draft_legs(draft_id, leg_number);
create index if not exists idx_play_legs_play on public.play_legs(play_id, leg_number);
create index if not exists idx_play_versions_play on public.play_versions(play_id, version_number);
create index if not exists idx_settlements_play on public.settlements(play_id, created_at);
create index if not exists idx_team_daily_summary_date on public.team_daily_summary(report_date);
create unique index if not exists idx_api_sports_player_directory_cache_key
  on public.api_sports_player_directory(sport_slug, league_id, player_id);
create unique index if not exists idx_api_sports_player_seasons_cache_key
  on public.api_sports_player_seasons(sport_slug, league_id, player_id, season);

-- Upgrade existing installations with the free-text team label used for untracked plays.
alter table public.plays add column if not exists team_name text;
