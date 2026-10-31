begin;

create or replace view public.research_games with (security_invoker = true) as
select 'nfl'::text as competition, 'nfl'::text as sport, game_id::text as game_id,
  kickoff_at as start_at, home_team_id::text as home_id, home_team_name as home_name,
  away_team_id::text as away_id, away_team_name as away_name,
  to_jsonb(home_score) as home_score, to_jsonb(away_score) as away_score,
  status_short as status, synced_at, season::text as season
from public.api_sports_nfl_games where league_id = 1
union all
select sport_slug || ':' || league_id, sport_slug, event_id, start_at,
  home_id, home_name, away_id, away_name, home_score, away_score, status_code, synced_at, season
from public.api_sports_events
where (sport_slug, league_id) in (
  ('basketball','12'), ('basketball','116'), ('baseball','1'), ('hockey','57'), ('ncaa','2')
);

revoke all on public.research_games from public, anon, authenticated;
grant select on public.research_games to service_role;

commit;
