begin;

alter table public.research_refresh_requests
  add column guild_id text check (guild_id ~ '^[0-9]{1,20}$'),
  add column role_id text check (role_id ~ '^[0-9]{1,20}$');

drop function public.research_refresh_authorized(uuid,text);
create function public.research_refresh_authorized(
  p_user_id uuid, p_discord_user_id text, p_guild_id text, p_role_id text
)
returns boolean language sql stable security definer set search_path = ''
as $$
  select p_user_id is not null
    and p_discord_user_id is not null
    and p_guild_id is not null
    and p_role_id is not null
    and exists (
      select 1
      from auth.identities as identity
      join public.website_capper_roster as roster on roster.singleton
      where identity.user_id = p_user_id
        and identity.provider = 'discord'
        and identity.provider_id = p_discord_user_id
        and roster.guild_id = p_guild_id
        and roster.role_id = p_role_id
        and roster.verified_at > now() - interval '15 minutes'
        and roster.verified_at <= now()
        and p_discord_user_id = any(roster.discord_user_ids)
    );
$$;
revoke all on function public.research_refresh_authorized(uuid,text,text,text) from public, anon, authenticated;
grant execute on function public.research_refresh_authorized(uuid,text,text,text) to service_role;

drop function public.request_discord_research_refresh(text,text,text,text);
create function public.request_discord_research_refresh(
  p_discord_user_id text, p_competition text, p_game_id text, p_kind text,
  p_guild_id text, p_role_id text
)
returns uuid language plpgsql security definer set search_path = ''
as $$
declare
  member_id uuid;
  request_id uuid;
begin
  if p_discord_user_id is null or p_discord_user_id !~ '^[0-9]{1,20}$'
    or p_guild_id is null or p_guild_id !~ '^[0-9]{1,20}$'
    or p_role_id is null or p_role_id !~ '^[0-9]{1,20}$'
    or p_competition is null or p_competition not in
      ('nfl','basketball:12','basketball:116','baseball:1','hockey:57','ncaa:2')
    or p_game_id is null or p_game_id !~ '^[0-9]{1,20}$'
    or p_kind is null or p_kind not in ('game','players','odds') then
    raise exception 'Invalid research refresh request';
  end if;
  select user_id into member_id from auth.identities
  where provider = 'discord' and provider_id = p_discord_user_id;
  if member_id is null then raise exception 'Verified Discord identity required'; end if;
  if not exists (
    select 1 from public.website_capper_roster
    where singleton and guild_id = p_guild_id and role_id = p_role_id
      and verified_at > now() - interval '15 minutes'
      and verified_at <= now()
      and p_discord_user_id = any(discord_user_ids)
  ) then
    raise exception 'A fresh tracker-role capper roster is required' using errcode = '42501';
  end if;
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
    (user_id, discord_user_id, competition, game_id, kind, guild_id, role_id)
  values (member_id, p_discord_user_id, p_competition, p_game_id, p_kind, p_guild_id, p_role_id)
  returning id into request_id;
  return request_id;
end;
$$;
revoke all on function public.request_discord_research_refresh(text,text,text,text,text,text) from public, anon, authenticated;
grant execute on function public.request_discord_research_refresh(text,text,text,text,text,text) to service_role;

commit;
