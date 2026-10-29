begin;

create table public.highroller_access_states (
  discord_user_id text primary key check (discord_user_id ~ '^[0-9]{1,20}$'),
  has_role boolean not null,
  exempt boolean not null,
  restoration_blocked boolean not null,
  verified_at timestamptz not null default now()
);
alter table public.highroller_access_states enable row level security;
revoke all on public.highroller_access_states from public, anon, authenticated;
grant select on public.highroller_access_states to service_role;

create function public.record_highroller_access_state(
  p_discord_user_id text, p_has_role boolean, p_exempt boolean
)
returns boolean language plpgsql security definer set search_path = ''
as $$
begin
  if p_has_role is null or p_exempt is null then raise exception 'Role state is required'; end if;
  insert into public.highroller_access_states as previous
    (discord_user_id, has_role, exempt, restoration_blocked)
  values (
    p_discord_user_id, p_has_role, p_exempt,
    not p_has_role and exists (
      select 1 from public.highroller_grandfather_members where discord_user_id = p_discord_user_id
    )
  )
  on conflict (discord_user_id) do update set
    has_role = excluded.has_role, exempt = excluded.exempt, verified_at = now(),
    restoration_blocked = case when excluded.has_role then false
      else previous.restoration_blocked or previous.has_role end;
  return true;
end;
$$;

create function public.sync_highroller_access_states(p_members jsonb)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare entry jsonb;
begin
  if jsonb_typeof(p_members) is distinct from 'array' then
    raise exception 'A complete member roster is required';
  end if;
  for entry in select value from jsonb_array_elements(p_members) loop
    perform public.record_highroller_access_state(
      entry ->> 'discord_user_id', (entry ->> 'has_role')::boolean, (entry ->> 'exempt')::boolean
    );
  end loop;
  update public.highroller_access_states as state
  set has_role = false, exempt = false,
    restoration_blocked = state.restoration_blocked or state.has_role, verified_at = now()
  where not exists (
    select 1 from jsonb_array_elements(p_members) as member
    where member ->> 'discord_user_id' = state.discord_user_id
  );
  return true;
end;
$$;

create function public.highroller_access_allowed(p_discord_user_id text)
returns boolean language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1 from public.highroller_access_states
    where discord_user_id = p_discord_user_id and (has_role or exempt)
      and verified_at > now() - interval '15 minutes' and verified_at <= now()
  );
$$;

alter function public.highroller_role_entitlements(text, text[], text[])
  rename to highroller_role_entitlements_before_revocation;
create function public.highroller_role_entitlements(
  p_account_id text, p_paid_plan_ids text[], p_trial_plan_ids text[]
)
returns table (discord_user_id text, source text)
language sql stable security definer set search_path = ''
as $$
  select entitlement.* from public.highroller_role_entitlements_before_revocation(
    p_account_id, p_paid_plan_ids, p_trial_plan_ids
  ) as entitlement
  where not exists (
    select 1 from public.highroller_access_states as state
    where state.discord_user_id = entitlement.discord_user_id and state.restoration_blocked
  );
$$;

alter function public.discord_has_paid_access(text) rename to discord_has_paid_access_before_role_gate;
alter function public.discord_has_paid_plan_access(text, text, text[]) rename to discord_has_paid_plan_access_before_role_gate;
alter function public.discord_has_trial_access(text, text, text[]) rename to discord_has_trial_access_before_role_gate;
alter function public.website_member_access() rename to website_member_access_before_role_gate;

create function public.discord_has_paid_access(p_discord_user_id text)
returns boolean language sql stable security definer set search_path = ''
as $$
  select public.highroller_access_allowed(p_discord_user_id)
    and public.discord_has_paid_access_before_role_gate(p_discord_user_id);
$$;
create function public.discord_has_paid_plan_access(p_discord_user_id text, p_account_id text, p_plan_ids text[])
returns boolean language sql stable security definer set search_path = ''
as $$
  select public.highroller_access_allowed(p_discord_user_id)
    and public.discord_has_paid_plan_access_before_role_gate(p_discord_user_id, p_account_id, p_plan_ids);
$$;
create function public.discord_has_trial_access(p_discord_user_id text, p_account_id text, p_plan_ids text[])
returns boolean language sql stable security definer set search_path = ''
as $$
  select public.highroller_access_allowed(p_discord_user_id)
    and public.discord_has_trial_access_before_role_gate(p_discord_user_id, p_account_id, p_plan_ids);
$$;
create function public.website_member_access()
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
declare
  access jsonb := public.website_member_access_before_role_gate();
begin
  if access ->> 'state' <> 'active' then return access; end if;
  if exists (
    select 1 from auth.identities as identity
    where identity.user_id = auth.uid() and identity.provider = 'discord'
      and public.highroller_access_allowed(identity.provider_id)
  ) then return access; end if;
  return jsonb_build_object('state', 'membership-required');
end;
$$;

revoke all on function public.record_highroller_access_state(text, boolean, boolean),
  public.sync_highroller_access_states(jsonb),
  public.highroller_access_allowed(text),
  public.highroller_role_entitlements(text, text[], text[]),
  public.discord_has_paid_access(text),
  public.discord_has_paid_plan_access(text, text, text[]),
  public.discord_has_trial_access(text, text, text[]),
  public.website_member_access()
from public, anon, authenticated;
revoke all on function public.highroller_role_entitlements_before_revocation(text, text[], text[]),
  public.discord_has_paid_access_before_role_gate(text),
  public.discord_has_paid_plan_access_before_role_gate(text, text, text[]),
  public.discord_has_trial_access_before_role_gate(text, text, text[]),
  public.website_member_access_before_role_gate()
from public, anon, authenticated, service_role;
grant execute on function public.record_highroller_access_state(text, boolean, boolean),
  public.sync_highroller_access_states(jsonb),
  public.highroller_access_allowed(text),
  public.highroller_role_entitlements(text, text[], text[]),
  public.discord_has_paid_access(text),
  public.discord_has_paid_plan_access(text, text, text[]),
  public.discord_has_trial_access(text, text, text[])
to service_role;
grant execute on function public.website_member_access() to anon, authenticated;

commit;
