begin;

-- Role eligibility is separate from website/Vault access and team grants.
create function public.highroller_role_entitlements(
  p_account_id text, p_paid_plan_ids text[], p_trial_plan_ids text[]
)
returns table (discord_user_id text, source text)
language sql stable security definer set search_path = ''
as $$
  select distinct membership.discord_user_id, 'whop'::text
  from public.whop_memberships as membership
  where membership.account_id = p_account_id
    and membership.plan_id = any(p_paid_plan_ids)
    and membership.discord_user_id is not null and membership.paid
    and membership.status in ('active', 'completed')
    and membership.paid_from <= now() and membership.paid_through > now()
    and membership.verified_at > now() - interval '15 minutes'
    and membership.verified_at <= now() + interval '5 minutes'
  union
  select member.discord_user_id, 'whop'::text
  from public.trial_discord_member_ids(p_account_id, p_trial_plan_ids) as member
  union
  select member.discord_user_id, 'grandfather'::text
  from public.highroller_grandfather_members as member
  where member.account_id = p_account_id
    and member.starts_at <= now() and member.expires_at > now();
$$;
revoke all on function public.highroller_role_entitlements(text, text[], text[])
from public, anon, authenticated;
grant execute on function public.highroller_role_entitlements(text, text[], text[])
to service_role;

commit;
