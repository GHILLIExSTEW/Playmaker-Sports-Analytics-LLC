-- Multi-sport official play tracking schema

create table if not exists public.sports (
  id bigserial primary key,
  api_slug text not null unique,
  name text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

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
  settled_by bigint references public.users(id) on delete set null,
  auto_suggested_at timestamptz
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
  details jsonb,
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

-- Upgrade existing installations with the free-text team label used for untracked plays.
alter table public.plays add column if not exists team_name text;

-- Member tails and capper follows (see supabase/migrations/20261006000000_official_play_features.sql).
create table if not exists public.play_tails (
  play_id bigint not null references public.plays(id) on delete cascade,
  discord_user_id text not null,
  created_at timestamptz not null default now(),
  primary key (play_id, discord_user_id)
);

create table if not exists public.capper_follows (
  capper_user_id bigint not null references public.users(id) on delete cascade,
  follower_discord_id text not null,
  created_at timestamptz not null default now(),
  primary key (capper_user_id, follower_discord_id)
);
