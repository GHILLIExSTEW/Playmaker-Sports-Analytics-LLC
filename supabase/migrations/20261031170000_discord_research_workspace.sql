begin;

create or replace function public.research_refresh_authorized(p_user_id uuid, p_discord_user_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare access jsonb;
begin
  perform set_config('request.jwt.claim.sub', p_user_id::text, true);
  access := public.website_member_access();
  return access ->> 'state' = 'active'
    and exists (
      select 1 from auth.identities
      where user_id = p_user_id and provider = 'discord' and provider_id = p_discord_user_id
    );
end;
$$;

create function public.discord_operator_research_access(p_discord_user_id text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
  member_id uuid;
  access jsonb;
begin
  if p_discord_user_id is null or p_discord_user_id !~ '^[0-9]{1,20}$' then return false; end if;
  select user_id into member_id from auth.identities
  where provider = 'discord' and provider_id = p_discord_user_id;
  if member_id is null then return false; end if;
  perform set_config('request.jwt.claim.sub', member_id::text, true);
  access := public.website_member_access();
  return access ->> 'state' = 'active'
    and access ->> 'kind' in ('operator', 'owner', 'research');
end;
$$;
revoke all on function public.discord_operator_research_access(text) from public, anon, authenticated;
grant execute on function public.discord_operator_research_access(text) to service_role;

create function public.discord_operator_capper_performance(p_discord_user_id text)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
  member_id uuid;
begin
  if p_discord_user_id is null or p_discord_user_id !~ '^[0-9]{1,20}$' then
    raise exception 'Verified Discord identity required';
  end if;
  select user_id into member_id from auth.identities
  where provider = 'discord' and provider_id = p_discord_user_id;
  if member_id is null then raise exception 'Verified Discord identity required'; end if;
  perform set_config('request.jwt.claim.sub', member_id::text, true);
  perform public.require_operator_research_access();
  return public.operator_capper_performance();
end;
$$;
revoke all on function public.discord_operator_capper_performance(text) from public, anon, authenticated;
grant execute on function public.discord_operator_capper_performance(text) to service_role;

create function public.request_discord_research_refresh(
  p_discord_user_id text, p_competition text, p_game_id text, p_kind text
)
returns uuid language plpgsql security definer set search_path = ''
as $$
declare
  member_id uuid;
  request_id uuid;
begin
  if p_discord_user_id is null or p_discord_user_id !~ '^[0-9]{1,20}$'
    or p_competition is null or p_competition not in
      ('nfl','basketball:12','basketball:116','baseball:1','hockey:57','ncaa:2')
    or p_game_id is null or p_game_id !~ '^[0-9]{1,20}$'
    or p_kind is null or p_kind not in ('game','players','odds') then
    raise exception 'Invalid research refresh request';
  end if;
  select user_id into member_id from auth.identities
  where provider = 'discord' and provider_id = p_discord_user_id;
  if member_id is null then raise exception 'Verified Discord identity required'; end if;
  perform set_config('request.jwt.claim.sub', member_id::text, true);
  perform public.require_member_stats_access(p_competition);
  if not exists (
    select 1 from public.research_games
    where competition = p_competition and game_id = p_game_id
      and start_at between now() - interval '7 days' and now() + interval '14 days'
  ) then raise exception 'Refresh requires a recent or upcoming cached game'; end if;
  if p_kind = 'players' and p_competition in ('baseball:1','hockey:57') then
    raise exception 'This provider has no supported individual-player adapter for this league';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('website-research-refresh', 0));
  if exists (
    select 1 from public.research_refresh_requests where user_id = member_id
      and requested_at > now() - interval '1 minute'
  ) then raise exception 'One refresh request per user per minute; use cached data meanwhile'; end if;
  if exists (
    select 1 from public.research_refresh_requests where competition = p_competition
      and game_id = p_game_id and kind = p_kind
      and (requested_at > now() - interval '5 minutes' or state in ('queued','running'))
  ) then raise exception 'This event/data type is refreshing or in its five-minute cooldown'; end if;
  if (select count(*) from public.research_refresh_requests where state in ('queued','running')) >= 50
    then raise exception 'Research refresh queue is full; retry later'; end if;
  insert into public.research_refresh_requests
    (user_id, discord_user_id, competition, game_id, kind)
  values (member_id, p_discord_user_id, p_competition, p_game_id, p_kind)
  returning id into request_id;
  return request_id;
end;
$$;
revoke all on function public.request_discord_research_refresh(text,text,text,text) from public, anon, authenticated;
grant execute on function public.request_discord_research_refresh(text,text,text,text) to service_role;

commit;
