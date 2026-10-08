begin;
create table public.api_sports_team_directory (
  sport_slug text not null,
  league_id text not null,
  team_id bigint not null check (team_id > 0),
  name text not null,
  synced_at timestamptz not null,
  primary key (sport_slug, league_id, team_id)
);
create table public.api_sports_team_stats_cache (
  sport_slug text not null,
  league_id text not null,
  team_id bigint not null check (team_id > 0),
  season text not null,
  stat_kind text not null,
  payload jsonb not null check (jsonb_typeof(payload) in ('array', 'object')),
  synced_at timestamptz not null,
  primary key (sport_slug, league_id, team_id, season, stat_kind)
);
alter table public.api_sports_team_directory enable row level security;
alter table public.api_sports_team_stats_cache enable row level security;
revoke all on public.api_sports_team_directory, public.api_sports_team_stats_cache from public, anon, authenticated;
grant select, insert, update on public.api_sports_team_directory, public.api_sports_team_stats_cache to service_role;

insert into public.api_sports_team_directory
select 'nfl', '1', team_id, name, synced_at from public.api_sports_nfl_teams;
insert into public.api_sports_team_directory
select distinct on (sport_slug, league_id, team_id)
  sport_slug, league_id, team_id::bigint, name, synced_at
from (
  select sport_slug, league_id, home_id as team_id, home_name as name, synced_at from public.api_sports_events
  union all
  select sport_slug, league_id, away_id, away_name, synced_at from public.api_sports_events
) teams
where sport_slug in ('ncaa', 'football', 'basketball', 'baseball', 'hockey', 'rugby', 'handball', 'volleyball')
  and league_id is not null and team_id ~ '^[1-9][0-9]{0,9}$' and name is not null
order by sport_slug, league_id, team_id, synced_at desc;
commit;
