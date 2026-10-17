begin;

create table public.capper_team_pulls (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  competition text not null,
  team_id text not null,
  season text not null,
  requested_at timestamptz not null default now(),
  completed_at timestamptz,
  state text not null default 'reserved' check (state in ('reserved','completed','failed'))
);
create index capper_team_pulls_usage on public.capper_team_pulls(requested_at,user_id);
alter table public.capper_team_pulls enable row level security;
revoke all on public.capper_team_pulls from public, anon, authenticated;
grant select on public.capper_team_pulls to service_role;

create function public.reserve_capper_team_pull(p_competition text,p_game_id text,p_team_id text)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  selected public.research_games;
  season_value text;
  product text;
  budget text;
  request_id uuid;
  today timestamptz := ((now() at time zone 'UTC')::date::timestamp at time zone 'UTC');
begin
  if public.capper_tools_access() is distinct from true
    then raise exception 'Approved capper access required' using errcode='42501'; end if;
  select * into selected from public.research_games where competition=p_competition and game_id=p_game_id;
  if not found or p_team_id is null or p_team_id !~ '^[0-9]{1,15}$'
    or p_team_id not in (coalesce(selected.home_id,''),coalesce(selected.away_id,''))
    then raise exception 'Select a team in this matchup' using errcode='22023'; end if;
  if p_competition='nfl' then
    select season::text into season_value from public.api_sports_nfl_games where game_id::text=p_game_id;
  else
    select season into season_value from public.api_sports_events
      where sport_slug=selected.sport and event_id=p_game_id;
  end if;
  if season_value is null or season_value !~ '^[0-9]{4}(-[0-9]{4})?$'
    then raise exception 'Cached season unavailable; cannot safely pull this team' using errcode='22023'; end if;
  product := case when selected.sport in ('nfl','ncaa') then 'american-football' else selected.sport end;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('capper-team-pulls',0));
  if (select count(*) from public.capper_team_pulls where requested_at>=today)>=500
    or (select count(*) from public.capper_team_pulls where requested_at>=today and user_id=auth.uid())>=50
    then raise exception 'Daily data-pull allowance reached (50 per capper, 500 shared, UTC)' using errcode='P0001'; end if;
  if exists(select 1 from public.capper_team_pulls where user_id=auth.uid() and requested_at>now()-interval '15 seconds')
    then raise exception 'Wait 15 seconds before another data pull' using errcode='P0001'; end if;
  budget := public.reserve_api_requests(product,null,case when p_competition='nfl' then 2 else 1 end);
  if budget is distinct from 'reserved' then raise exception '%',budget using errcode='P0001'; end if;
  insert into public.capper_team_pulls(user_id,competition,team_id,season)
    values(auth.uid(),p_competition,p_team_id,season_value) returning id into request_id;
  return jsonb_build_object('request_id',request_id,'product',product,'sport',selected.sport,
    'league',case when p_competition='nfl' then '1' else split_part(p_competition,':',2) end,
    'season',season_value,'team_id',p_team_id,'competition',p_competition);
end;
$$;
revoke all on function public.reserve_capper_team_pull(text,text,text) from public,anon;
grant execute on function public.reserve_capper_team_pull(text,text,text) to authenticated;

create function public.finish_capper_team_pull(p_request_id uuid,p_games jsonb,p_injuries jsonb,p_failed boolean)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
  reservation public.capper_team_pulls;
begin
  select * into reservation from public.capper_team_pulls where id=p_request_id for update;
  if not found or reservation.state<>'reserved' then return false; end if;
  if p_failed then
    update public.capper_team_pulls set state='failed',completed_at=now() where id=p_request_id;
    return true;
  end if;
  if p_games is null or jsonb_typeof(p_games)<>'array' or jsonb_array_length(p_games)>1000
    then raise exception 'Invalid team games'; end if;
  if reservation.competition='nfl' then
    if exists(select 1 from jsonb_populate_recordset(null::public.api_sports_nfl_games,p_games) g
      where g.league_id is distinct from 1 or g.season::text is distinct from reservation.season
        or reservation.team_id not in (coalesce(g.home_team_id::text,''),coalesce(g.away_team_id::text,'')))
      then raise exception 'Unexpected team or season'; end if;
    insert into public.api_sports_nfl_games
      select * from jsonb_populate_recordset(null::public.api_sports_nfl_games,p_games)
      on conflict(game_id) do update set
        kickoff_at=excluded.kickoff_at,stage=excluded.stage,week=excluded.week,
        venue_name=excluded.venue_name,venue_city=excluded.venue_city,
        status_short=excluded.status_short,status_long=excluded.status_long,
        home_team_id=excluded.home_team_id,home_team_name=excluded.home_team_name,home_team_logo=excluded.home_team_logo,
        away_team_id=excluded.away_team_id,away_team_name=excluded.away_team_name,away_team_logo=excluded.away_team_logo,
        home_score=excluded.home_score,away_score=excluded.away_score,scores=excluded.scores,synced_at=excluded.synced_at;
    if p_injuries is null or jsonb_typeof(p_injuries)<>'array' then raise exception 'Missing injury response'; end if;
    if exists(select 1 from jsonb_populate_recordset(null::public.sport_model_injuries,p_injuries) i
      where i.competition is distinct from 'nfl' or i.team_id is distinct from reservation.team_id)
      then raise exception 'Unexpected injury team'; end if;
    delete from public.sport_model_injuries where competition='nfl' and team_id=reservation.team_id;
    insert into public.sport_model_injuries select * from jsonb_populate_recordset(null::public.sport_model_injuries,p_injuries);
  else
    if exists(select 1 from jsonb_populate_recordset(null::public.api_sports_events,p_games) g
      where g.sport_slug||':'||g.league_id is distinct from reservation.competition
        or g.season is distinct from reservation.season
        or reservation.team_id not in (coalesce(g.home_id,''),coalesce(g.away_id,'')))
      then raise exception 'Unexpected team or season'; end if;
    insert into public.api_sports_events select * from jsonb_populate_recordset(null::public.api_sports_events,p_games)
      on conflict(sport_slug,event_id) do update set
        start_at=excluded.start_at,round_name=excluded.round_name,event_name=excluded.event_name,venue=excluded.venue,
        home_id=excluded.home_id,home_name=excluded.home_name,home_logo=excluded.home_logo,
        away_id=excluded.away_id,away_name=excluded.away_name,away_logo=excluded.away_logo,
        home_score=excluded.home_score,away_score=excluded.away_score,status_code=excluded.status_code,
        status=excluded.status,raw_event=excluded.raw_event,synced_at=excluded.synced_at;
  end if;
  update public.capper_team_pulls set state='completed',completed_at=now() where id=p_request_id;
  return true;
end;
$$;
revoke all on function public.finish_capper_team_pull(uuid,jsonb,jsonb,boolean) from public,anon,authenticated;
grant execute on function public.finish_capper_team_pull(uuid,jsonb,jsonb,boolean) to service_role;

commit;
