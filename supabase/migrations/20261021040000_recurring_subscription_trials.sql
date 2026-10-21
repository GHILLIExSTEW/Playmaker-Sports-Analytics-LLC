begin;

-- A renewing plan can provide both an unpaid trial and paid billing periods.
alter table public.website_membership_config
  drop constraint website_membership_config_check1;

create function public.record_whop_subscription_membership(p_snapshot jsonb)
returns boolean language plpgsql set search_path = ''
as $$
declare
  trial_snapshot jsonb;
begin
  if p_snapshot ->> 'membership_id' is null then
    raise exception 'Subscription membership ID is required.';
  end if;
  if (p_snapshot ->> 'paid')::boolean and (p_snapshot ->> 'trial_verified')::boolean then
    raise exception 'A membership period cannot be both paid and an unpaid trial.';
  end if;
  if (p_snapshot ->> 'trial_verified')::boolean
     and p_snapshot ->> 'status' <> 'trialing' then
    raise exception 'A subscription trial must have trialing status.';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_snapshot ->> 'membership_id', 0));
  if exists (
    select 1 from public.whop_memberships m
    where m.membership_id = p_snapshot ->> 'membership_id'
      and (m.source_updated_at > (p_snapshot ->> 'source_updated_at')::timestamptz
        or m.verified_at >= (p_snapshot ->> 'verified_at')::timestamptz)
    union all
    select 1 from public.whop_trial_memberships m
    where m.membership_id = p_snapshot ->> 'membership_id'
      and (m.source_updated_at > (p_snapshot ->> 'source_updated_at')::timestamptz
        or m.verified_at >= (p_snapshot ->> 'verified_at')::timestamptz)
  ) then return false; end if;
  trial_snapshot := p_snapshot || jsonb_build_object('paid', false, 'payment_id', null);
  if not public.record_whop_membership(p_snapshot) then
    raise exception 'Subscription ledgers were not updated together.';
  end if;
  if not public.record_whop_trial_membership(trial_snapshot) then
    raise exception 'Subscription ledgers were not updated together.';
  end if;
  return true;
end;
$$;
revoke all on function public.record_whop_subscription_membership(jsonb) from public, anon, authenticated;
grant execute on function public.record_whop_subscription_membership(jsonb) to service_role;

create or replace function public.trial_discord_member_ids(p_account_id text, p_plan_ids text[])
returns table (discord_user_id text)
language sql stable set search_path = ''
as $$
  select distinct m.discord_user_id
  from public.whop_trial_memberships m
  join public.membership_trial_claims c
    on c.membership_id = m.membership_id and c.account_id = m.account_id
    and c.whop_user_id = m.whop_user_id and c.discord_user_id = m.discord_user_id
    and c.starts_at = m.paid_from and c.expires_at = m.paid_through
  where m.account_id = p_account_id and m.plan_id = any(p_plan_ids)
    and m.trial_verified and m.trial_eligible and c.eligible
    and m.status in ('trialing', 'active', 'completed')
    and m.paid_from <= now() and m.paid_through > now()
    and m.verified_at > now() - interval '15 minutes'
    and m.verified_at <= now() + interval '5 minutes';
$$;

-- Preserve the Owner, OPERATOR and research wrappers around this base check.
create or replace function public.website_member_access_standard()
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
declare
  member_id uuid := auth.uid();
  discord_id text;
  config public.website_membership_config;
  access_kind text;
  access_expires timestamptz;
begin
  if member_id is null then return jsonb_build_object('state', 'signed-out'); end if;
  if not exists (
    select 1 from public.member_profiles where user_id = member_id and age_verified_at is not null
  ) then return jsonb_build_object('state', 'age-required'); end if;
  select i.provider_id into discord_id from auth.identities i
  where i.user_id = member_id and i.provider = 'discord' and i.provider_id ~ '^[0-9]{1,20}$';
  if discord_id is null then return jsonb_build_object('state', 'discord-required'); end if;
  select * into config from public.website_membership_config where singleton;
  if config.enabled is distinct from true then return jsonb_build_object('state', 'launch-pending'); end if;
  select e.kind, e.expires_at into access_kind, access_expires from (
    select 'paid'::text kind, m.paid_through expires_at, 1 priority
    from public.whop_memberships m
    where m.discord_user_id = discord_id and m.account_id = config.account_id
      and m.plan_id = any(config.paid_plan_ids) and m.paid and m.status in ('active', 'completed')
      and m.paid_from <= now() and m.paid_through > now()
      and m.verified_at > now() - interval '15 minutes'
      and m.verified_at <= now() + interval '5 minutes'
    union all
    select 'trial', m.paid_through, 2
    from public.whop_trial_memberships m
    where m.discord_user_id = discord_id and m.account_id = config.account_id
      and m.plan_id = any(config.trial_plan_ids)
      and m.trial_verified and m.trial_eligible and m.status in ('trialing', 'active', 'completed')
      and m.paid_from <= now() and m.paid_through > now()
      and m.verified_at > now() - interval '15 minutes' and m.verified_at <= now() + interval '5 minutes'
      and exists (
        select 1 from public.membership_trial_claims c
        where c.membership_id = m.membership_id and c.account_id = m.account_id
          and c.whop_user_id = m.whop_user_id and c.discord_user_id = m.discord_user_id
          and c.starts_at = m.paid_from and c.expires_at = m.paid_through and c.eligible
      )
    union all
    select 'owner', g.expires_at, 0 from public.owner_membership_grants g
    where g.discord_user_id = discord_id and g.account_id = config.account_id and g.tier = 'highroller'
      and g.revoked_at is null and g.starts_at <= now()
      and (g.expires_at is null or g.expires_at > now())
  ) e order by e.priority, e.expires_at desc nulls first limit 1;
  if access_kind is null then return jsonb_build_object('state', 'membership-required'); end if;
  return jsonb_build_object('state', 'active', 'kind', access_kind, 'expires_at', access_expires);
end;
$$;
revoke all on function public.website_member_access_standard() from public, anon, authenticated;

update public.website_membership_config
set paid_plan_ids = array['plan_0MxgyyucPbdQZ','plan_sN89nOD7airIA','plan_FAZPnnd4WxDOU'],
    trial_plan_ids = array['plan_0MxgyyucPbdQZ','plan_sN89nOD7airIA','plan_FAZPnnd4WxDOU'],
    enabled = true
where singleton and account_id = 'biz_rCNwfXRlnl0bFU';
do $$
begin
  if not exists (
    select 1 from public.website_membership_config
    where singleton and account_id = 'biz_rCNwfXRlnl0bFU' and enabled
  ) then raise exception 'Expected Playmaker seller configuration is missing.'; end if;
end;
$$;

commit;
