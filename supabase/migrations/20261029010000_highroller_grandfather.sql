begin;

create table public.highroller_grandfather_captures (
  guild_id text not null,
  role_id text not null,
  account_id text not null,
  member_ids text[] not null,
  captured_at timestamptz not null default now(),
  expires_at timestamptz not null default '2027-02-01T00:00:00-05:00',
  primary key (guild_id, role_id)
);
alter table public.highroller_grandfather_captures enable row level security;
revoke all on public.highroller_grandfather_captures from public, anon, authenticated;
grant select on public.highroller_grandfather_captures to service_role;

create table public.highroller_grandfather_members (
  discord_user_id text primary key,
  account_id text not null,
  starts_at timestamptz not null default now(),
  expires_at timestamptz not null default '2027-02-01T00:00:00-05:00'
);
alter table public.highroller_grandfather_members enable row level security;
revoke all on public.highroller_grandfather_members from public, anon, authenticated;
grant select, delete on public.highroller_grandfather_members to service_role;

create function public.capture_highroller_grandfather(
  p_guild_id text, p_role_id text, p_account_id text, p_member_ids text[]
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
  deadline constant timestamptz := '2027-02-01T00:00:00-05:00';
begin
  if p_guild_id is null or p_guild_id !~ '^[0-9]{1,20}$'
     or p_role_id is null or p_role_id !~ '^[0-9]{1,20}$'
     or p_account_id is null or length(trim(p_account_id)) = 0
     or p_member_ids is null or exists (
       select 1 from unnest(p_member_ids) as member_id
       where member_id is null or member_id !~ '^[0-9]{1,20}$'
     ) then
    raise exception 'Invalid HIGHROLLER grandfather capture';
  end if;
  if now() >= deadline then
    raise exception 'HIGHROLLER grandfather enrollment has expired';
  end if;
  insert into public.highroller_grandfather_captures
    (guild_id, role_id, account_id, member_ids)
  values (p_guild_id, p_role_id, p_account_id, p_member_ids)
  on conflict (guild_id, role_id) do nothing;
  if not found then return false; end if;

  insert into public.highroller_grandfather_members
    (discord_user_id, account_id, expires_at)
  select distinct member_id, p_account_id, deadline
  from unnest(p_member_ids) as member_id
  on conflict (discord_user_id) do nothing;
  return true;
end;
$$;
revoke all on function public.capture_highroller_grandfather(text, text, text, text[])
from public, anon, authenticated;
grant execute on function public.capture_highroller_grandfather(text, text, text, text[])
to service_role;

create function public.has_highroller_grandfather(p_discord_user_id text, p_account_id text)
returns boolean language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1 from public.highroller_grandfather_members as member
    where member.discord_user_id = p_discord_user_id and member.account_id = p_account_id
      and member.starts_at <= now() and member.expires_at > now()
  );
$$;
revoke all on function public.has_highroller_grandfather(text, text) from public, anon, authenticated;
grant execute on function public.has_highroller_grandfather(text, text) to service_role;

alter function public.discord_has_paid_access(text) rename to discord_has_access_before_grandfather;
alter function public.discord_has_paid_plan_access(text, text, text[]) rename to discord_has_plan_access_before_grandfather;
alter function public.paid_discord_member_ids() rename to discord_member_ids_before_grandfather;
alter function public.website_member_access() rename to website_member_access_before_grandfather;
revoke all on function public.discord_has_access_before_grandfather(text) from public, anon, authenticated, service_role;
revoke all on function public.discord_has_plan_access_before_grandfather(text, text, text[]) from public, anon, authenticated, service_role;
revoke all on function public.discord_member_ids_before_grandfather() from public, anon, authenticated, service_role;
revoke all on function public.website_member_access_before_grandfather() from public, anon, authenticated, service_role;

create function public.discord_has_paid_access(p_discord_user_id text)
returns boolean language sql stable security definer set search_path = ''
as $$
  select public.discord_has_access_before_grandfather(p_discord_user_id) or exists (
    select 1 from public.highroller_grandfather_members as member
    where member.discord_user_id = p_discord_user_id
      and member.starts_at <= now() and member.expires_at > now()
  );
$$;
create function public.discord_has_paid_plan_access(
  p_discord_user_id text, p_account_id text, p_plan_ids text[]
)
returns boolean language sql stable security definer set search_path = ''
as $$
  select public.discord_has_plan_access_before_grandfather(p_discord_user_id, p_account_id, p_plan_ids)
    or (cardinality(p_plan_ids) > 0
      and public.has_highroller_grandfather(p_discord_user_id, p_account_id));
$$;
create function public.paid_discord_member_ids()
returns table (discord_user_id text)
language sql stable security definer set search_path = ''
as $$
  select member.discord_user_id from public.discord_member_ids_before_grandfather() as member
  union
  select member.discord_user_id from public.highroller_grandfather_members as member
  where member.starts_at <= now() and member.expires_at > now();
$$;
create function public.website_member_access()
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
declare
  access jsonb := public.website_member_access_before_grandfather();
  deadline timestamptz;
begin
  -- The existing check must first enforce sign-in, age, Discord identity and launch gates.
  if access ->> 'state' <> 'membership-required' then return access; end if;
  select member.expires_at into deadline
  from public.highroller_grandfather_members as member
  join auth.identities as identity on identity.provider_id = member.discord_user_id
    and identity.provider = 'discord' and identity.user_id = auth.uid()
  join public.website_membership_config as config
    on config.singleton and config.account_id = member.account_id
  where member.starts_at <= now() and member.expires_at > now();
  if deadline is null then return access; end if;
  return jsonb_build_object('state', 'active', 'kind', 'grandfather', 'expires_at', deadline);
end;
$$;
revoke all on function public.discord_has_paid_access(text) from public, anon, authenticated;
revoke all on function public.discord_has_paid_plan_access(text, text, text[]) from public, anon, authenticated;
revoke all on function public.paid_discord_member_ids() from public, anon, authenticated;
revoke all on function public.website_member_access() from public, anon, authenticated;
grant execute on function public.discord_has_paid_access(text) to service_role;
grant execute on function public.discord_has_paid_plan_access(text, text, text[]) to service_role;
grant execute on function public.paid_discord_member_ids() to service_role;
grant execute on function public.website_member_access() to anon, authenticated;

commit;
