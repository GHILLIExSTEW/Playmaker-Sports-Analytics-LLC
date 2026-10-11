begin;

create table if not exists public.api_sports_model_backfill_state (
  sport_slug text not null,
  league_id text not null,
  season text not null,
  next_page integer not null default 1,
  total_pages integer not null default 1,
  complete boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (sport_slug, league_id, season),
  check (next_page > 0),
  check (total_pages >= 0)
);

alter table public.api_sports_model_backfill_state enable row level security;
revoke all on public.api_sports_model_backfill_state from public, anon, authenticated;
grant all on public.api_sports_model_backfill_state to service_role;

create index if not exists idx_api_sports_events_model_history
  on public.api_sports_events(sport_slug, league_id, season, start_at);

create or replace function public.public_nfl_model_games()
returns table (
  game_id bigint,
  season integer,
  stage text,
  week text,
  kickoff_at timestamptz,
  venue_name text,
  venue_city text,
  status_short text,
  status_long text,
  home_team_id bigint,
  home_team_name text,
  home_team_logo text,
  home_score integer,
  away_team_id bigint,
  away_team_name text,
  away_team_logo text,
  away_score integer,
  scores jsonb,
  synced_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select games.game_id, games.season, games.stage, games.week, games.kickoff_at,
         games.venue_name, games.venue_city, games.status_short, games.status_long,
         games.home_team_id, games.home_team_name, games.home_team_logo, games.home_score,
         games.away_team_id, games.away_team_name, games.away_team_logo, games.away_score,
         games.scores, games.synced_at
  from public.api_sports_nfl_games as games
  where games.season >= (
    select coalesce(max(current_games.season), 0) - 1
    from public.api_sports_nfl_games as current_games
  )
  order by games.kickoff_at, games.game_id;
$$;

revoke all on function public.public_nfl_model_games() from public;
grant execute on function public.public_nfl_model_games() to anon, authenticated;

create or replace function public.public_sport_model_events(p_sport_slug text, p_league_id text)
returns table (
  sport_slug text,
  event_id text,
  league_id text,
  league_name text,
  season text,
  round_name text,
  event_name text,
  start_at timestamptz,
  venue jsonb,
  home_id text,
  home_name text,
  home_logo text,
  home_score jsonb,
  away_id text,
  away_name text,
  away_logo text,
  away_score jsonb,
  status_code text,
  status text,
  synced_at timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select events.sport_slug, events.event_id, events.league_id, events.league_name,
         events.season, events.round_name, events.event_name, events.start_at, events.venue,
         events.home_id, events.home_name, events.home_logo, events.home_score,
         events.away_id, events.away_name, events.away_logo, events.away_score,
         events.status_code, events.status, events.synced_at
  from public.api_sports_events as events
  where events.sport_slug = p_sport_slug
    and events.league_id = p_league_id
    and (
      (p_sport_slug = 'basketball' and p_league_id in ('8', '12'))
      or (p_sport_slug = 'baseball' and p_league_id = '1')
      or (p_sport_slug = 'hockey' and p_league_id = '57')
      or (p_sport_slug = 'ncaa' and p_league_id = '2')
    )
    and events.season in (
      select history.season
      from public.api_sports_events as history
      where history.sport_slug = p_sport_slug
        and history.league_id = p_league_id
        and history.season is not null
      group by history.season
      order by history.season desc
      limit 2
    )
  order by events.start_at, events.event_id;
$$;

revoke all on function public.public_sport_model_events(text, text) from public;
grant execute on function public.public_sport_model_events(text, text) to anon, authenticated;

commit;
