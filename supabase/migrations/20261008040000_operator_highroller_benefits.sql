begin;

-- Team access is complimentary, not a fabricated payment or consumed trial.
create function public.team_highroller_ids(p_account_id text)
returns table (discord_user_id text)
language sql stable security definer set search_path = ''
as $$
  select distinct member_id
  from public.website_capper_roster as roster
  join public.website_membership_config as config
    on config.singleton and config.account_id = p_account_id
  cross join lateral unnest(roster.discord_user_ids) as member_id
  where roster.singleton and roster.role_id = '1328120848992960543'
    and roster.verified_at > now() - interval '15 minutes'
    and roster.verified_at <= now();
$$;

alter function public.discord_has_paid_access(text) rename to discord_has_purchase_or_owner_access;
alter function public.discord_has_paid_plan_access(text, text, text[]) rename to discord_has_purchase_or_owner_plan_access;
alter function public.paid_discord_member_ids() rename to purchase_or_owner_discord_member_ids;
revoke all on function public.discord_has_purchase_or_owner_access(text) from public, anon, authenticated, service_role;
revoke all on function public.discord_has_purchase_or_owner_plan_access(text, text, text[]) from public, anon, authenticated, service_role;
revoke all on function public.purchase_or_owner_discord_member_ids() from public, anon, authenticated, service_role;

create function public.discord_has_paid_access(p_discord_user_id text)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select public.discord_has_purchase_or_owner_access(p_discord_user_id) or exists (
    select 1 from public.website_membership_config as config
    cross join lateral public.team_highroller_ids(config.account_id) as member
    where config.singleton and member.discord_user_id = p_discord_user_id
  );
$$;

create function public.discord_has_paid_plan_access(
  p_discord_user_id text, p_account_id text, p_plan_ids text[]
)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select public.discord_has_purchase_or_owner_plan_access(
    p_discord_user_id, p_account_id, p_plan_ids
  ) or exists (
    select 1 from public.team_highroller_ids(p_account_id) as member
    where member.discord_user_id = p_discord_user_id
      and cardinality(p_plan_ids) > 0
  );
$$;

create function public.paid_discord_member_ids()
returns table (discord_user_id text)
language sql stable security definer set search_path = ''
as $$
  select member.discord_user_id from public.purchase_or_owner_discord_member_ids() as member
  union
  select member.discord_user_id
  from public.website_membership_config as config
  cross join lateral public.team_highroller_ids(config.account_id) as member
  where config.singleton;
$$;

revoke all on function public.team_highroller_ids(text) from public, anon, authenticated;
revoke all on function public.discord_has_paid_access(text) from public, anon, authenticated;
revoke all on function public.discord_has_paid_plan_access(text, text, text[]) from public, anon, authenticated;
revoke all on function public.paid_discord_member_ids() from public, anon, authenticated;
grant execute on function public.discord_has_paid_access(text) to service_role;
grant execute on function public.discord_has_paid_plan_access(text, text, text[]) to service_role;
grant execute on function public.paid_discord_member_ids() to service_role;

commit;
