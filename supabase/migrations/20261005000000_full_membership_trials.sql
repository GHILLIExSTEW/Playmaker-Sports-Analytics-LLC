begin;

-- Trials are entitlements, never fabricated payment snapshots.
create table public.whop_trial_memberships (
  membership_id text primary key,
  whop_user_id text,
  discord_user_id text check (discord_user_id is null or discord_user_id ~ '^[0-9]{1,20}$'),
  account_id text not null,
  plan_id text not null,
  status text not null,
  paid_from timestamptz,
  paid_through timestamptz,
  trial_verified boolean not null,
  trial_eligible boolean not null default false,
  source_updated_at timestamptz not null,
  verified_at timestamptz not null,
  verification_reason text not null,
  check (not trial_verified or (
    whop_user_id is not null and discord_user_id is not null
    and paid_from is not null and paid_through is not null
    and paid_through = paid_from + interval '168 hours'
  ))
);

-- Retain consumed identities after expiry, cancellation, and snapshot removal.
create table public.membership_trial_claims (
  account_id text not null,
  whop_user_id text not null,
  discord_user_id text not null,
  membership_id text not null unique,
  starts_at timestamptz not null,
  expires_at timestamptz not null,
  eligible boolean not null,
  claimed_at timestamptz not null default now(),
  primary key (account_id, whop_user_id),
  unique (account_id, discord_user_id),
  check (expires_at = starts_at + interval '168 hours')
);

create table public.whop_trial_membership_audit (
  id bigint generated always as identity primary key,
  membership_id text not null,
  snapshot jsonb not null,
  created_at timestamptz not null default now()
);

alter table public.whop_trial_memberships enable row level security;
alter table public.membership_trial_claims enable row level security;
alter table public.whop_trial_membership_audit enable row level security;
revoke all on public.whop_trial_memberships, public.membership_trial_claims,
  public.whop_trial_membership_audit from public, anon, authenticated;
grant select, insert, update on public.whop_trial_memberships to service_role;
grant select, insert on public.membership_trial_claims to service_role;
grant select, insert on public.whop_trial_membership_audit to service_role;
grant usage, select on sequence public.whop_trial_membership_audit_id_seq to service_role;

create function public.record_whop_trial_membership(p_snapshot jsonb)
returns boolean language plpgsql set search_path = ''
as $$
declare
  incoming public.whop_trial_memberships;
  previous jsonb;
  first_time boolean;
begin
  if (p_snapshot ->> 'paid')::boolean is distinct from false
     or p_snapshot ->> 'payment_id' is not null then
    raise exception 'Trial snapshots must not claim payment';
  end if;
  incoming := pg_catalog.jsonb_populate_record(null::public.whop_trial_memberships, p_snapshot);
  if incoming.verified_at > now() + interval '5 minutes'
     or incoming.source_updated_at > now() + interval '5 minutes' then
    raise exception 'Trial verification timestamp is in the future';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(incoming.membership_id, 0));
  select pg_catalog.to_jsonb(membership) - 'verified_at' into previous
  from public.whop_trial_memberships as membership
  where membership.membership_id = incoming.membership_id;
  if exists (
    select 1 from public.whop_trial_memberships as membership
    where membership.membership_id = incoming.membership_id
      and (membership.source_updated_at > incoming.source_updated_at
           or membership.verified_at >= incoming.verified_at)
  ) then
    return false;
  end if;

  incoming.trial_eligible := false;
  if incoming.trial_verified then
    first_time := not exists (
      select 1 from public.whop_memberships as membership
      where membership.account_id = incoming.account_id and membership.paid
        and (membership.whop_user_id = incoming.whop_user_id
             or membership.discord_user_id = incoming.discord_user_id)
        and membership.paid_from <= incoming.paid_from
    ) and not exists (
      select 1 from public.whop_membership_audit as audit
      where audit.snapshot ->> 'account_id' = incoming.account_id
        and audit.snapshot ->> 'paid' = 'true'
        and (audit.snapshot ->> 'whop_user_id' = incoming.whop_user_id
             or audit.snapshot ->> 'discord_user_id' = incoming.discord_user_id)
        and (audit.snapshot ->> 'paid_from')::timestamptz <= incoming.paid_from
    );
    insert into public.membership_trial_claims
      (account_id, whop_user_id, discord_user_id, membership_id, starts_at, expires_at, eligible)
    values (
      incoming.account_id, incoming.whop_user_id, incoming.discord_user_id,
      incoming.membership_id, incoming.paid_from, incoming.paid_through, first_time
    ) on conflict do nothing;
    incoming.trial_eligible := first_time and exists (
      select 1 from public.membership_trial_claims as claim
      where claim.account_id = incoming.account_id
        and claim.whop_user_id = incoming.whop_user_id
        and claim.discord_user_id = incoming.discord_user_id
        and claim.membership_id = incoming.membership_id
        and claim.starts_at = incoming.paid_from
        and claim.expires_at = incoming.paid_through and claim.eligible
    );
    incoming.verification_reason := case when incoming.trial_eligible
      then 'Verified first-time seven-day trial; status and dates determine current access'
      else 'Trial already claimed, identity/window changed, or prior paid membership'
    end;
  end if;
  insert into public.whop_trial_memberships select incoming.*
  on conflict (membership_id) do update set
    whop_user_id = excluded.whop_user_id, discord_user_id = excluded.discord_user_id,
    account_id = excluded.account_id, plan_id = excluded.plan_id,
    status = excluded.status, paid_from = excluded.paid_from,
    paid_through = excluded.paid_through, trial_verified = excluded.trial_verified,
    trial_eligible = excluded.trial_eligible, source_updated_at = excluded.source_updated_at,
    verified_at = excluded.verified_at, verification_reason = excluded.verification_reason;
  if previous is distinct from (pg_catalog.to_jsonb(incoming) - 'verified_at') then
    insert into public.whop_trial_membership_audit (membership_id, snapshot)
    values (incoming.membership_id, pg_catalog.to_jsonb(incoming) - 'verified_at');
  end if;
  return true;
end;
$$;

create function public.trial_discord_member_ids(p_account_id text, p_plan_ids text[])
returns table (discord_user_id text)
language sql stable set search_path = ''
as $$
  select distinct membership.discord_user_id
  from public.whop_trial_memberships as membership
  join public.membership_trial_claims as claim
    on claim.membership_id = membership.membership_id
    and claim.account_id = membership.account_id
    and claim.whop_user_id = membership.whop_user_id
    and claim.discord_user_id = membership.discord_user_id
    and claim.starts_at = membership.paid_from
    and claim.expires_at = membership.paid_through
  where membership.account_id = p_account_id
    and membership.plan_id = any(p_plan_ids)
    and membership.trial_verified and membership.trial_eligible and claim.eligible
    and membership.status in ('active', 'completed')
    and membership.paid_from <= now() and membership.paid_through > now()
    and membership.verified_at > now() - interval '15 minutes'
    and membership.verified_at <= now() + interval '5 minutes';
$$;

create function public.discord_has_trial_access(
  p_discord_user_id text, p_account_id text, p_plan_ids text[]
)
returns boolean language sql stable set search_path = ''
as $$
  select exists (
    select 1 from public.trial_discord_member_ids(p_account_id, p_plan_ids) as member
    where member.discord_user_id = p_discord_user_id
  );
$$;

revoke all on function public.record_whop_trial_membership(jsonb) from public, anon, authenticated;
revoke all on function public.trial_discord_member_ids(text, text[]) from public, anon, authenticated;
revoke all on function public.discord_has_trial_access(text, text, text[]) from public, anon, authenticated;
grant execute on function public.record_whop_trial_membership(jsonb) to service_role;
grant execute on function public.trial_discord_member_ids(text, text[]) to service_role;
grant execute on function public.discord_has_trial_access(text, text, text[]) to service_role;

commit;
