begin;

create table if not exists public.nflverse_team_game_efficiency (
  season integer not null,
  game_id text not null,
  game_date date not null,
  team_code text not null,
  offense_epa_sum double precision not null,
  offense_plays integer not null check (offense_plays >= 0),
  offense_successes integer not null check (offense_successes >= 0),
  offense_success_plays integer not null check (offense_success_plays >= 0),
  defense_epa_sum double precision not null,
  defense_plays integer not null check (defense_plays >= 0),
  defense_successes_allowed integer not null check (defense_successes_allowed >= 0),
  defense_success_plays integer not null check (defense_success_plays >= 0),
  synced_at timestamptz not null default now(),
  primary key (season, game_id, team_code)
);

alter table public.nflverse_team_game_efficiency enable row level security;
revoke all on public.nflverse_team_game_efficiency from public, anon, authenticated;
grant all on public.nflverse_team_game_efficiency to service_role;

create index if not exists idx_nflverse_team_game_efficiency_lookup
  on public.nflverse_team_game_efficiency(team_code, game_date desc);

create or replace function public.public_nfl_team_efficiency()
returns table (
  team_id bigint,
  season integer,
  game_id text,
  game_date date,
  offense_epa_sum double precision,
  offense_plays integer,
  offense_successes integer,
  offense_success_plays integer,
  defense_epa_sum double precision,
  defense_plays integer,
  defense_successes_allowed integer,
  defense_success_plays integer,
  synced_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select teams.team_id, stats.season, stats.game_id, stats.game_date,
         stats.offense_epa_sum, stats.offense_plays, stats.offense_successes,
         stats.offense_success_plays, stats.defense_epa_sum, stats.defense_plays,
         stats.defense_successes_allowed, stats.defense_success_plays, stats.synced_at
  from public.nflverse_team_game_efficiency as stats
  join public.api_sports_nfl_teams as teams
    on upper(trim(teams.code)) = stats.team_code
  where stats.season >= (
    select coalesce(max(current_stats.season), 0) - 1
    from public.nflverse_team_game_efficiency as current_stats
  )
  order by stats.season, stats.game_date, stats.game_id, teams.team_id;
$$;

revoke all on function public.public_nfl_team_efficiency() from public;
grant execute on function public.public_nfl_team_efficiency() to anon, authenticated;

commit;
