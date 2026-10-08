begin;

alter table public.users
  add column if not exists public_avatar_url text;

-- Supabase's default table grants must not bypass the premium RPC checks.
revoke all on public.plays, public.play_legs, public.play_versions,
  public.play_draft_legs, public.settlements, public.users from public, anon, authenticated;

-- Keep website premium access closed until the deployment launch gates pass.
create table public.website_membership_config (
  singleton boolean primary key default true check (singleton),
  enabled boolean not null default false,
  account_id text not null,
  paid_plan_ids text[] not null,
  trial_plan_ids text[] not null,
  check (not enabled or cardinality(paid_plan_ids) > 0),
  check (not (paid_plan_ids && trial_plan_ids))
);
insert into public.website_membership_config
  (account_id, paid_plan_ids, trial_plan_ids)
values (
  'biz_rCNwfXRlnl0bFU',
  array['plan_cdPyKCHjSQeG2'],
  array['plan_R7H8Sx7MEKzh0']
);
alter table public.website_membership_config enable row level security;
revoke all on public.website_membership_config from public, anon, authenticated;
grant select, update on public.website_membership_config to service_role;

create function public.website_member_access()
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  member_id uuid := auth.uid();
  discord_id text;
  config public.website_membership_config;
  access_kind text;
  access_expires timestamptz;
begin
  if member_id is null then
    return jsonb_build_object('state', 'signed-out');
  end if;
  if not exists (
    select 1 from public.member_profiles
    where user_id = member_id and age_verified_at is not null
  ) then
    return jsonb_build_object('state', 'age-required');
  end if;

  -- auth.identities is provider-managed; raw_user_meta_data is member-editable.
  select identity.provider_id into discord_id
  from auth.identities as identity
  where identity.user_id = member_id and identity.provider = 'discord'
    and identity.provider_id ~ '^[0-9]{1,20}$';
  if discord_id is null then
    return jsonb_build_object('state', 'discord-required');
  end if;
  select * into config from public.website_membership_config where singleton;
  if config.enabled is distinct from true then
    return jsonb_build_object('state', 'launch-pending');
  end if;

  select entitlement.kind, entitlement.expires_at into access_kind, access_expires
  from (
    select 'paid'::text as kind, membership.paid_through as expires_at, 1 as priority
    from public.whop_memberships as membership
    where membership.discord_user_id = discord_id
      and membership.account_id = config.account_id
      and membership.plan_id = any(config.paid_plan_ids)
      and membership.paid and membership.status in ('active', 'completed')
      and membership.paid_from <= now() and membership.paid_through > now()
      and membership.verified_at > now() - interval '15 minutes'
      and membership.verified_at <= now() + interval '5 minutes'
    union all
    select 'trial', membership.paid_through, 2
    from public.whop_trial_memberships as membership
    join public.membership_trial_claims as claim
      on claim.membership_id = membership.membership_id
      and claim.account_id = membership.account_id
      and claim.whop_user_id = membership.whop_user_id
      and claim.discord_user_id = membership.discord_user_id
      and claim.starts_at = membership.paid_from
      and claim.expires_at = membership.paid_through
    where membership.discord_user_id = discord_id
      and membership.account_id = config.account_id
      and membership.plan_id = any(config.trial_plan_ids)
      and membership.trial_verified and membership.trial_eligible and claim.eligible
      and membership.status in ('active', 'completed')
      and membership.paid_from <= now() and membership.paid_through > now()
      and membership.verified_at > now() - interval '15 minutes'
      and membership.verified_at <= now() + interval '5 minutes'
    union all
    select 'owner', owner_grant.expires_at, 0
    from public.owner_membership_grants as owner_grant
    where owner_grant.discord_user_id = discord_id
      and owner_grant.account_id = config.account_id and owner_grant.tier = 'highroller'
      and owner_grant.revoked_at is null and owner_grant.starts_at <= now()
      and (owner_grant.expires_at is null or owner_grant.expires_at > now())
  ) as entitlement
  order by entitlement.priority, entitlement.expires_at desc nulls first
  limit 1;
  if access_kind is null then
    return jsonb_build_object('state', 'membership-required');
  end if;
  return jsonb_build_object('state', 'active', 'kind', access_kind, 'expires_at', access_expires);
end;
$$;

create function public.member_current_picks(p_sport text default null, p_capper text default null)
returns table (
  id bigint,
  created_at timestamptz,
  sport text,
  capper text,
  avatar_url text,
  selection text,
  analysis text,
  odds integer,
  units numeric
)
language plpgsql stable security definer set search_path = ''
as $$
begin
  if public.website_member_access() ->> 'state' <> 'active' then
    raise exception 'Verified active membership required.' using errcode = '42501';
  end if;
  return query
  with published as (
    select distinct on (play.message_id) play.*
    from public.plays as play
    where play.message_id is not null
    order by play.message_id, play.id
  )
  select
    play.id::bigint,
    play.created_at,
    coalesce(nullif(btrim(sports.name), ''), 'Other'),
    coalesce(nullif(btrim(users.display_name), ''), 'Playmaker Picks'),
    case when users.public_avatar_url like 'https://%' then users.public_avatar_url else null end,
    coalesce(
      (select string_agg(nullif(btrim(leg.selection), ''), ' / ' order by leg.leg_number)
       from public.play_legs as leg where leg.play_id = play.id),
      nullif(btrim(play.team_name), ''), 'Official play'
    ),
    coalesce(nullif(btrim(play.play_text), ''), ''),
    play.odds,
    play.units
  from published as play
  left join public.sports as sports on sports.id = play.sport_id
  left join public.users as users on users.id = play.user_id
  where play.status = 'open' and play.settled_at is null
    and (p_sport is null or coalesce(nullif(btrim(sports.name), ''), 'Other') = p_sport)
    and (p_capper is null or coalesce(nullif(btrim(users.display_name), ''), 'Playmaker Picks') = p_capper)
  order by play.created_at desc, play.id desc;
end;
$$;

-- Names/approved avatars only: never publish open selections or member identifiers.
create function public.public_capper_directory()
returns table (name text, avatar_url text)
language sql stable security definer set search_path = ''
as $$
  select distinct
    coalesce(nullif(btrim(users.display_name), ''), 'Playmaker Picks'),
    case when users.public_avatar_url like 'https://%' then users.public_avatar_url else null end
  from public.plays as play
  left join public.users as users on users.id = play.user_id
  where play.message_id is not null
    and (play.status = 'open' and play.settled_at is null
         or play.status in ('win', 'loss', 'void', 'partial') and play.settled_at is not null)
  order by 1, 2;
$$;

revoke all on function public.website_member_access() from public, anon, authenticated;
revoke all on function public.member_current_picks(text, text) from public, anon, authenticated;
revoke all on function public.public_capper_directory() from public, anon, authenticated;
grant execute on function public.website_member_access() to anon, authenticated;
grant execute on function public.member_current_picks(text, text) to authenticated;
grant execute on function public.public_capper_directory() to anon, authenticated;

commit;
