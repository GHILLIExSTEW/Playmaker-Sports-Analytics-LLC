begin;

create or replace function public.capper_tools_game(p_competition text, p_game_id text)
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
declare
  result jsonb;
begin
  if public.capper_tools_access() is distinct from true
    then raise exception 'Approved capper access required' using errcode = '42501'; end if;
  result := public.member_stats_game(p_competition,p_game_id);
  return result || jsonb_build_object(
    'injuries', coalesce((select jsonb_agg(to_jsonb(i) order by i.team_id,i.player_name) from public.sport_model_injuries i
      where i.competition = p_competition
        and i.team_id in (result->'game'->>'home_id',result->'game'->>'away_id')
        and i.reported_on >= (now() at time zone 'UTC')::date - 21), '[]'::jsonb),
    'weather', (select to_jsonb(w) from public.sport_model_weather w where w.competition = p_competition and w.game_id = p_game_id),
    'odds', (select to_jsonb(o) from public.sport_model_odds o where o.competition = p_competition and o.game_id = p_game_id),
    'projection', (select jsonb_build_object(
      'home_score', p.home_score, 'away_score', p.away_score, 'home_probability', p.home_probability,
      'home_sample', p.home_sample, 'away_sample', p.away_sample, 'league_sample', p.league_sample,
      'model_version', p.model_version, 'data_updated_at', p.data_updated_at, 'generated_at', p.generated_at
    ) from public.sport_model_projections p
      where p.competition = p_competition and p.game_id = p_game_id
        and p.home_id = result->'game'->>'home_id' and p.away_id = result->'game'->>'away_id'
        and p.start_at = (result->'game'->>'start_at')::timestamptz
        and p.start_at > now() and p.generated_at < p.start_at and p.data_updated_at < p.start_at
        and result->'game'->>'status' in ('NS','TBD','SCHEDULED','NOT STARTED')
    )
  );
end;
$$;
revoke all on function public.capper_tools_game(text,text) from public, anon;
grant execute on function public.capper_tools_game(text,text) to authenticated;

commit;
