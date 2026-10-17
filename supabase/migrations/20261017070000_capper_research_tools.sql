begin;

create table public.capper_tools_grants (
  discord_user_id text primary key check (discord_user_id ~ '^[0-9]{1,20}$'),
  allowed boolean not null,
  updated_at timestamptz not null default now()
);
create table public.capper_tools_ai_usage (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  requested_at timestamptz not null default now()
);
create index capper_tools_ai_usage_day on public.capper_tools_ai_usage(requested_at);
alter table public.capper_tools_grants enable row level security;
alter table public.capper_tools_ai_usage enable row level security;
revoke all on public.capper_tools_grants, public.capper_tools_ai_usage from public, anon, authenticated;

create function public.capper_tools_access()
returns boolean language sql stable security definer set search_path = ''
as $$
  select auth.uid() is not null
    and public.website_member_access()->>'state' = 'active'
    and exists (
      select 1 from auth.identities i
      join public.member_profiles p on p.user_id = i.user_id and p.age_verified_at is not null
      where i.user_id = auth.uid() and i.provider = 'discord'
        and i.provider_id = any(public.website_capper_ids())
        and coalesce(
          (select g.allowed from public.capper_tools_grants g where g.discord_user_id = i.provider_id),
          exists(select 1 from public.capper_api_keys k where k.discord_user_id = i.provider_id and k.revoked_at is null)
        )
    );
$$;
revoke all on function public.capper_tools_access() from public, anon;
grant execute on function public.capper_tools_access() to authenticated;

create function public.manage_capper_tools(p_discord_user_id text default null, p_allowed boolean default null)
returns jsonb language plpgsql security definer set search_path = ''
as $$
begin
  if auth.uid() is null or public.website_can_edit_all_cappers() is distinct from true
    or not exists(select 1 from public.member_profiles where user_id = auth.uid() and age_verified_at is not null)
    then raise exception 'Age-verified current Owner role required' using errcode = '42501'; end if;
  if p_discord_user_id is not null then
    if p_allowed is null or p_discord_user_id !~ '^[0-9]{1,20}$'
      then raise exception 'Supply a Discord ID and approval decision'; end if;
    if p_allowed and (
      (p_discord_user_id = any(public.website_capper_ids())) is distinct from true or not exists(
        select 1 from auth.identities i join public.member_profiles p on p.user_id = i.user_id
        where i.provider = 'discord' and i.provider_id = p_discord_user_id and p.age_verified_at is not null
      )
    ) then raise exception 'Recipient must be a current capper with Discord sign-in and age verification'; end if;
    insert into public.capper_tools_grants values(p_discord_user_id, p_allowed, now())
      on conflict(discord_user_id) do update set allowed = excluded.allowed, updated_at = excluded.updated_at;
  elsif p_allowed is not null then raise exception 'Supply a Discord ID'; end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object('discord_user_id', ids.id, 'allowed',
      coalesce(g.allowed, exists(select 1 from public.capper_api_keys k where k.discord_user_id = ids.id and k.revoked_at is null)))
      order by ids.id)
    from (select discord_user_id as id from public.capper_tools_grants union select discord_user_id from public.capper_api_keys) ids
    left join public.capper_tools_grants g on g.discord_user_id = ids.id
  ), '[]'::jsonb);
end;
$$;
revoke all on function public.manage_capper_tools(text,boolean) from public, anon;
grant execute on function public.manage_capper_tools(text,boolean) to authenticated;

create function public.capper_tools_game(p_competition text, p_game_id text)
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
    'odds', (select to_jsonb(o) from public.sport_model_odds o where o.competition = p_competition and o.game_id = p_game_id)
  );
end;
$$;
revoke all on function public.capper_tools_game(text,text) from public, anon;
grant execute on function public.capper_tools_game(text,text) to authenticated;

create function public.reserve_capper_tools_ai()
returns uuid language plpgsql security definer set search_path = ''
as $$
declare
  today timestamptz := ((now() at time zone 'UTC')::date::timestamp at time zone 'UTC');
  request_id uuid;
begin
  if public.capper_tools_access() is distinct from true
    then raise exception 'Approved capper access required' using errcode = '42501'; end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('capper-tools-ai',0));
  if (select count(*) from public.capper_tools_ai_usage where requested_at >= today) >= 250
    or (select count(*) from public.capper_tools_ai_usage where requested_at >= today and user_id = auth.uid()) >= 25
    then raise exception 'Daily AI allowance reached (25 per capper, 250 shared, UTC)' using errcode = 'P0001'; end if;
  if exists(select 1 from public.capper_tools_ai_usage where user_id = auth.uid() and requested_at > now() - interval '15 seconds')
    then raise exception 'Wait 15 seconds before another AI request' using errcode = 'P0001'; end if;
  insert into public.capper_tools_ai_usage(user_id) values(auth.uid()) returning id into request_id;
  return request_id;
end;
$$;
revoke all on function public.reserve_capper_tools_ai() from public, anon;
grant execute on function public.reserve_capper_tools_ai() to authenticated;

commit;
