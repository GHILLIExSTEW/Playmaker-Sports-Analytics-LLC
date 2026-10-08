begin;

create table public.website_capper_roster (
  singleton boolean primary key default true check (singleton),
  guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
  role_id text not null check (role_id ~ '^[0-9]{1,20}$'),
  discord_user_ids text[] not null,
  verified_at timestamptz not null
);
alter table public.website_capper_roster enable row level security;
revoke all on public.website_capper_roster from public, anon, authenticated;

create function public.sync_website_capper_roster(
  p_guild_id text, p_role_id text, p_discord_user_ids text[]
)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  if p_discord_user_ids is null or exists (
    select 1 from unnest(p_discord_user_ids) as member_id
    where member_id is null or member_id !~ '^[0-9]{1,20}$'
  ) then
    raise exception 'Invalid capper roster identities.';
  end if;
  insert into public.website_capper_roster
    (guild_id, role_id, discord_user_ids, verified_at)
  values (p_guild_id, p_role_id, p_discord_user_ids, now())
  on conflict (singleton) do update set
    guild_id = excluded.guild_id, role_id = excluded.role_id,
    discord_user_ids = excluded.discord_user_ids, verified_at = excluded.verified_at;
  return true;
end;
$$;

create function public.website_capper_ids()
returns text[]
language plpgsql stable security definer set search_path = ''
as $$
declare
  roster public.website_capper_roster;
begin
  select * into roster from public.website_capper_roster where singleton;
  if roster.verified_at is null
     or roster.verified_at <= now() - interval '15 minutes'
     or roster.verified_at > now() then
    raise exception 'Website capper roster is unavailable or stale. Check the bot OPERATOR role sync.';
  end if;
  return roster.discord_user_ids;
end;
$$;

create or replace function public.public_capper_directory()
returns table (name text, avatar_url text)
language sql stable security definer set search_path = ''
as $$
  select distinct
    coalesce(nullif(btrim(users.display_name), ''), 'Playmaker Picks'),
    case when users.public_avatar_url like 'https://%' then users.public_avatar_url else null end
  from public.users as users
  where users.discord_user_id = any(public.website_capper_ids())
  order by 1, 2;
$$;

create or replace function public.public_favorite_cappers()
returns table (sport_id bigint, sport text, capper_name text)
language sql stable security definer set search_path = ''
as $$
  select distinct
    plays.sport_id, sports.name,
    coalesce(nullif(btrim(users.display_name), ''), 'Playmaker Picks')
  from public.plays as plays
  join public.sports as sports on sports.id = plays.sport_id
  join public.users as users on users.id = plays.user_id
  where users.discord_user_id = any(public.website_capper_ids())
    and plays.message_id is not null
    and (plays.status = 'open' and plays.settled_at is null
         or plays.status in ('win', 'loss', 'void', 'partial') and plays.settled_at is not null)
    and sports.api_slug <> 'official'
  order by sports.name, 3;
$$;

-- Keep the original entitlement-protected query private; filter by author ID
-- before returning any current picks to the browser.
alter function public.member_current_picks(text, text) rename to website_current_picks_source;
revoke all on function public.website_current_picks_source(text, text) from public, anon, authenticated;

create function public.member_current_picks(p_sport text default null, p_capper text default null)
returns table (
  id bigint, created_at timestamptz, sport text, capper text, avatar_url text,
  selection text, analysis text, odds integer, units numeric
)
language plpgsql stable security definer set search_path = ''
as $$
declare
  capper_ids text[];
begin
  if public.website_member_access() ->> 'state' <> 'active' then
    raise exception 'Verified active membership required.' using errcode = '42501';
  end if;
  capper_ids := public.website_capper_ids();
  return query
  select source.*
  from public.website_current_picks_source(p_sport, p_capper) as source
  join public.plays as plays on plays.id = source.id
  join public.users as users on users.id = plays.user_id
  where users.discord_user_id = any(capper_ids)
  order by source.created_at desc, source.id desc;
end;
$$;

revoke all on function public.sync_website_capper_roster(text, text, text[]) from public, anon, authenticated;
revoke all on function public.website_capper_ids() from public, anon, authenticated;
revoke all on function public.member_current_picks(text, text) from public, anon, authenticated;
grant execute on function public.sync_website_capper_roster(text, text, text[]) to service_role;
grant execute on function public.member_current_picks(text, text) to authenticated;

commit;
