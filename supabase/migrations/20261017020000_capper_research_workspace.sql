begin;

create view public.research_games with (security_invoker = true) as
select 'nfl'::text as competition, 'nfl'::text as sport, game_id::text as game_id,
  kickoff_at as start_at, home_team_id::text as home_id, home_team_name as home_name,
  away_team_id::text as away_id, away_team_name as away_name,
  to_jsonb(home_score) as home_score, to_jsonb(away_score) as away_score,
  status_short as status, synced_at
from public.api_sports_nfl_games where league_id = 1
union all
select sport_slug || ':' || league_id, sport_slug, event_id, start_at,
  home_id, home_name, away_id, away_name, home_score, away_score, status_code, synced_at
from public.api_sports_events
where (sport_slug, league_id) in (
  ('basketball','12'), ('basketball','116'), ('baseball','1'), ('hockey','57'), ('ncaa','2')
);
revoke all on public.research_games from public, anon, authenticated;
grant select on public.research_games to service_role;

create table public.research_odds (
  competition text not null,
  game_id text not null,
  bookmakers jsonb not null check (jsonb_typeof(bookmakers) = 'array'),
  fetched_at timestamptz not null,
  primary key (competition, game_id)
);
alter table public.research_odds enable row level security;
revoke all on public.research_odds from public, anon, authenticated;
grant select, insert, update on public.research_odds to service_role;

create table public.research_refresh_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  discord_user_id text not null,
  competition text not null,
  game_id text not null,
  kind text not null check (kind in ('game', 'players', 'odds')),
  state text not null default 'queued' check (state in ('queued','running','completed','failed')),
  requested_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  result text
);
create index research_refresh_queue on public.research_refresh_requests (requested_at) where state = 'queued';
create index research_refresh_user on public.research_refresh_requests (user_id, requested_at desc);
create index research_refresh_event on public.research_refresh_requests (competition, game_id, kind, requested_at desc);
alter table public.research_refresh_requests enable row level security;
revoke all on public.research_refresh_requests from public, anon, authenticated;
grant select, update on public.research_refresh_requests to service_role;

create function public.operator_research_workspace(p_competition text, p_game_id text default null)
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
declare
  selected public.research_games;
  workspace_data jsonb;
begin
  perform public.require_operator_research_access();
  if p_competition not in ('nfl','basketball:12','basketball:116','baseball:1','hockey:57','ncaa:2')
    or p_competition is null then raise exception 'Unsupported research competition'; end if;
  if p_game_id is not null then
    select * into selected from public.research_games
    where competition = p_competition and game_id = p_game_id;
    if not found then raise exception 'Select a cached game in this competition'; end if;
  end if;
  select jsonb_build_object(
    'games', coalesce((select jsonb_agg(to_jsonb(g) order by g.start_at, g.game_id) from (
      select * from public.research_games where competition = p_competition
        and start_at between now() - interval '7 days' and now() + interval '14 days'
      order by start_at, game_id limit 200
    ) g), '[]'::jsonb),
    'history', coalesce((select jsonb_agg(to_jsonb(g) order by g.start_at desc) from (
      select * from public.research_games
      where competition = p_competition and start_at < selected.start_at
        and status in ('FT','AOT','AP','COMPLETED','FINISHED')
        and (home_id in (selected.home_id, selected.away_id) or away_id in (selected.home_id, selected.away_id))
      order by start_at desc, game_id limit 100
    ) g), '[]'::jsonb),
    'projection', (select to_jsonb(p) from public.sport_model_projections p
      where competition = p_competition and game_id = p_game_id),
    'injuries', coalesce((select jsonb_agg(to_jsonb(i)) from public.sport_model_injuries i
      where competition = p_competition and team_id in (selected.home_id, selected.away_id)
        and (reported_on is null or reported_on >= current_date - 21)), '[]'::jsonb),
    'players', coalesce((select jsonb_agg(to_jsonb(s)) from (
      select s.game_id::text, s.payload, s.synced_at, g.start_at, g.home_name, g.away_name, g.home_id, g.away_id
      from public.api_sports_player_game_stats s
      join public.research_games g on g.sport = s.sport_slug and g.game_id = s.game_id::text
      where g.competition = p_competition and g.start_at <= coalesce(selected.start_at, now())
        and (g.home_id in (selected.home_id,selected.away_id) or g.away_id in (selected.home_id,selected.away_id))
      order by g.start_at desc limit 10
    ) s), '[]'::jsonb),
    'odds', (select to_jsonb(o) from public.research_odds o where competition = p_competition and game_id = p_game_id),
    'refreshes', coalesce((select jsonb_agg(to_jsonb(q)) from (
      select id, competition, game_id, kind, state, requested_at, updated_at, result
      from public.research_refresh_requests where user_id = auth.uid()
      order by requested_at desc limit 10
    ) q), '[]'::jsonb),
    'generated_at', now()
  ) into workspace_data;
  return workspace_data;
end;
$$;
revoke all on function public.operator_research_workspace(text,text) from public, anon;
grant execute on function public.operator_research_workspace(text,text) to authenticated;

create function public.request_research_refresh(p_competition text, p_game_id text, p_kind text)
returns uuid language plpgsql security definer set search_path = ''
as $$
declare
  discord_id text;
  request_id uuid;
begin
  perform public.require_operator_research_access();
  if p_kind is null or p_kind not in ('game','players','odds') then raise exception 'Unsupported refresh type'; end if;
  if not exists (select 1 from public.research_games where competition = p_competition
      and game_id = p_game_id and start_at between now() - interval '7 days' and now() + interval '14 days') then
    raise exception 'Refresh requires a recent or upcoming cached game';
  end if;
  if p_kind = 'players' and p_competition in ('baseball:1','hockey:57') then
    raise exception 'This provider has no supported individual-player adapter for this league';
  end if;
  select provider_id into strict discord_id from auth.identities
  where user_id = auth.uid() and provider = 'discord';
  perform pg_advisory_xact_lock(hashtextextended('website-research-refresh',0));
  if exists (select 1 from public.research_refresh_requests where user_id = auth.uid()
      and requested_at > now() - interval '1 minute') then
    raise exception 'One refresh request per user per minute; use cached data meanwhile';
  end if;
  if exists (select 1 from public.research_refresh_requests where competition = p_competition
      and game_id = p_game_id and kind = p_kind and
        (requested_at > now() - interval '5 minutes' or state in ('queued','running'))) then
    raise exception 'This event/data type is refreshing or in its five-minute cooldown';
  end if;
  if (select count(*) from public.research_refresh_requests where state in ('queued','running')) >= 50 then
    raise exception 'Research refresh queue is full; retry later';
  end if;
  insert into public.research_refresh_requests (user_id,discord_user_id,competition,game_id,kind)
  values (auth.uid(),discord_id,p_competition,p_game_id,p_kind) returning id into request_id;
  return request_id;
end;
$$;
revoke all on function public.request_research_refresh(text,text,text) from public, anon;
grant execute on function public.request_research_refresh(text,text,text) to authenticated;

create function public.claim_research_refresh()
returns setof public.research_refresh_requests language plpgsql security definer set search_path = ''
as $$
begin
  -- Never retry an interrupted provider request whose outcome is unknown.
  update public.research_refresh_requests set state='failed', updated_at=now(),
    result='Worker interrupted or timed out. Inspect cached data before requesting again.'
  where state='running' and updated_at < now() - interval '10 minutes';
  update public.research_refresh_requests set state='failed', updated_at=now(),
    result='Request expired before a worker claimed it. No provider call was made.'
  where state='queued' and requested_at < now() - interval '15 minutes';
  return query
  update public.research_refresh_requests r set state='running', updated_at=now()
  where id = (select id from public.research_refresh_requests where state='queued'
    order by requested_at for update skip locked limit 1)
  returning r.*;
end;
$$;
revoke all on function public.claim_research_refresh() from public, anon, authenticated;
grant execute on function public.claim_research_refresh() to service_role;

create function public.research_refresh_authorized(p_user_id uuid, p_discord_user_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare access jsonb;
begin
  perform set_config('request.jwt.claim.sub',p_user_id::text,true);
  access := public.website_member_access();
  return access ->> 'state' = 'active' and access ->> 'kind' in ('owner','operator','research')
    and exists (select 1 from auth.identities where user_id=p_user_id
      and provider='discord' and provider_id=p_discord_user_id);
end;
$$;
revoke all on function public.research_refresh_authorized(uuid,text) from public, anon, authenticated;
grant execute on function public.research_refresh_authorized(uuid,text) to service_role;

create function public.operator_capper_performance()
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
begin
  perform public.require_operator_research_access();
  return coalesce((
    select jsonb_agg(to_jsonb(r)) from (
      with published as (
        select distinct on (p.message_id) p.* from public.display_plays p
        where p.message_id is not null and p.settled_at is not null
          and p.tracking_scope='capper' and p.status in ('win','loss','partial','void')
        order by p.message_id, p.id
      )
      select p.created_at, p.settled_at, coalesce(nullif(btrim(s.name),''),'Other') as sport,
        coalesce(nullif(btrim(u.display_name),''),'Playmaker Picks') as capper,
        p.odds, p.units, p.status,
        case p.status
          when 'win' then round(p.units * case when p.odds > 0 then p.odds::numeric / 100 else 100::numeric / abs(p.odds) end, 2)
          when 'loss' then -p.units
          when 'partial' then round(p.units * case when p.odds > 0 then p.odds::numeric / 100 else 100::numeric / abs(p.odds) end * 0.5, 2)
          else 0::numeric end as net_units,
        case when p.legs > 1 then 'Parlay'
          else coalesce((select nullif(btrim(l.details->>'market'),'') from public.play_legs l
            where l.play_id=p.id order by l.leg_number limit 1),'Unclassified') end as market
      from published p left join public.users u on u.id=p.user_id
      left join public.sports s on s.id=p.sport_id
      order by p.settled_at desc, p.id desc limit 10000
    ) r
  ), '[]'::jsonb);
end;
$$;
revoke all on function public.operator_capper_performance() from public, anon;
grant execute on function public.operator_capper_performance() to authenticated;

commit;
