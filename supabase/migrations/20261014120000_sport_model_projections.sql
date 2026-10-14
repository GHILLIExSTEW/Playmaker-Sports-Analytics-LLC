begin;

-- Inputs and outputs for the ratings-v2 projection engine. Written by the bot's service role only.
create table public.sport_model_odds (
  competition text not null,
  game_id text not null,
  home_probability double precision check (home_probability is null or home_probability between 0 and 1),
  home_spread double precision,
  total double precision,
  books integer not null default 0 check (books >= 0),
  fetched_at timestamptz not null,
  primary key (competition, game_id)
);

create table public.sport_model_players (
  competition text not null,
  team_id text not null,
  player_id text not null,
  player_name text,
  position text,
  position_group text,
  fetched_at timestamptz not null,
  primary key (competition, team_id, player_id)
);

create table public.sport_model_injuries (
  competition text not null,
  team_id text not null,
  player_id text not null,
  player_name text,
  status text,
  description text,
  reported_on date,
  fetched_at timestamptz not null,
  primary key (competition, team_id, player_id)
);

create table public.sport_model_weather (
  competition text not null,
  game_id text not null,
  temperature_f double precision not null,
  wind_mph double precision not null check (wind_mph >= 0),
  precipitation_mm double precision not null check (precipitation_mm >= 0),
  source text not null check (source in ('forecast', 'observed')),
  fetched_at timestamptz not null,
  primary key (competition, game_id)
);

create table public.sport_model_projections (
  competition text not null,
  game_id text not null,
  start_at timestamptz not null,
  status_code text not null,
  home_id text,
  home_name text not null,
  away_id text,
  away_name text not null,
  home_score double precision not null,
  away_score double precision not null,
  home_probability double precision not null check (home_probability between 0 and 1),
  distribution text not null check (distribution in ('normal', 'poisson')),
  margin_sd double precision not null,
  total_sd double precision not null,
  home_sample integer not null,
  away_sample integer not null,
  league_sample integer not null,
  market_home_probability double precision,
  market_spread double precision,
  market_total double precision,
  market_books integer,
  market_fetched_at timestamptz,
  market_edge double precision,
  cover_probability double precision,
  over_probability double precision,
  details jsonb not null default '{}'::jsonb,
  model_version text not null,
  data_updated_at timestamptz not null,
  generated_at timestamptz not null,
  primary key (competition, game_id)
);
create index sport_model_projections_start_idx on public.sport_model_projections (competition, start_at);

create table public.sport_model_backtests (
  competition text primary key,
  model_version text not null,
  params jsonb not null,
  report jsonb not null,
  generated_at timestamptz not null
);

alter table public.sport_model_odds enable row level security;
alter table public.sport_model_players enable row level security;
alter table public.sport_model_injuries enable row level security;
alter table public.sport_model_weather enable row level security;
alter table public.sport_model_projections enable row level security;
alter table public.sport_model_backtests enable row level security;
revoke all on public.sport_model_odds, public.sport_model_players, public.sport_model_injuries,
  public.sport_model_weather, public.sport_model_projections, public.sport_model_backtests from anon, authenticated;

-- Projections stay visible until one hour after the scheduled start, matching the bot.
create function public.operator_sport_model_projections(p_competition text)
returns setof public.sport_model_projections
language plpgsql stable security definer set search_path = ''
as $$
begin
  perform public.require_operator_research_access();
  return query
    select projections.*
    from public.sport_model_projections as projections
    where projections.competition = p_competition
      and projections.start_at > now() - interval '1 hour'
      and projections.start_at <= now() + interval '14 days'
    order by projections.start_at, projections.game_id;
end;
$$;
revoke all on function public.operator_sport_model_projections(text) from public, anon;
grant execute on function public.operator_sport_model_projections(text) to authenticated;

create function public.operator_sport_model_backtest(p_competition text)
returns setof public.sport_model_backtests
language plpgsql stable security definer set search_path = ''
as $$
begin
  perform public.require_operator_research_access();
  return query
    select backtests.* from public.sport_model_backtests as backtests
    where backtests.competition = p_competition;
end;
$$;
revoke all on function public.operator_sport_model_backtest(text) from public, anon;
grant execute on function public.operator_sport_model_backtest(text) to authenticated;

-- API-Basketball NCAA men's league is 116; league 8 is the French Pro B.
create or replace function public.internal_public_sport_model_events(p_sport_slug text, p_league_id text)
returns table (
  sport_slug text, event_id text, league_id text, league_name text, season text, round_name text,
  event_name text, start_at timestamptz, venue jsonb, home_id text, home_name text, home_logo text,
  home_score jsonb, away_id text, away_name text, away_logo text, away_score jsonb,
  status_code text, status text, synced_at timestamptz
)
language sql stable security definer set search_path = ''
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
      (p_sport_slug = 'basketball' and p_league_id in ('116', '12'))
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
revoke all on function public.internal_public_sport_model_events(text, text) from public, anon, authenticated;

commit;
