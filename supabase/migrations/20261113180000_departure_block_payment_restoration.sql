begin;

-- Distinguish why automatic HIGHROLLER restoration was blocked. Manual role
-- removal ('role_removed') stays persistent until staff re-add the role; a
-- departure ('departed') lifts once a new verified paid period starts after it.
alter table public.highroller_access_states
  add column blocked_at timestamptz,
  add column block_reason text;

-- Existing blocks predate reason tracking; keep them persistent.
update public.highroller_access_states
set blocked_at = verified_at, block_reason = 'role_removed'
where restoration_blocked;

alter table public.highroller_access_states
  add constraint highroller_access_states_block_metadata check (
    (restoration_blocked and blocked_at is not null
      and block_reason in ('role_removed', 'departed'))
    or (not restoration_blocked and blocked_at is null and block_reason is null)
  );

drop function public.record_highroller_access_state(text, boolean, boolean);
create function public.record_highroller_access_state(
  p_discord_user_id text, p_has_role boolean, p_exempt boolean, p_departed boolean default false
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
  reason text := case when p_departed then 'departed' else 'role_removed' end;
begin
  if p_has_role is null or p_exempt is null or p_departed is null then
    raise exception 'Role state is required';
  end if;
  insert into public.highroller_access_states as previous
    (discord_user_id, has_role, exempt, restoration_blocked, blocked_at, block_reason)
  select p_discord_user_id, p_has_role, p_exempt, initial.blocked,
    case when initial.blocked then now() end, case when initial.blocked then reason end
  from (
    select not p_has_role and exists (
      select 1 from public.highroller_grandfather_members where discord_user_id = p_discord_user_id
    ) as blocked
  ) as initial
  on conflict (discord_user_id) do update set
    has_role = excluded.has_role, exempt = excluded.exempt, verified_at = now(),
    restoration_blocked = case when excluded.has_role then false
      else previous.restoration_blocked or previous.has_role end,
    blocked_at = case when excluded.has_role then null
      when previous.restoration_blocked then previous.blocked_at
      when previous.has_role then now() end,
    block_reason = case when excluded.has_role then null
      when previous.restoration_blocked then previous.block_reason
      when previous.has_role then reason end;
  return true;
end;
$$;

create or replace function public.sync_highroller_access_states(p_members jsonb)
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
  -- Members absent from the complete roster have left the server.
  update public.highroller_access_states as state
  set has_role = false, exempt = false,
    restoration_blocked = state.restoration_blocked or state.has_role,
    blocked_at = case when state.restoration_blocked then state.blocked_at
      when state.has_role then now() end,
    block_reason = case when state.restoration_blocked then state.block_reason
      when state.has_role then 'departed' end,
    verified_at = now()
  where not exists (
    select 1 from jsonb_array_elements(p_members) as member
    where member ->> 'discord_user_id' = state.discord_user_id
  );
  return true;
end;
$$;

create or replace function public.highroller_role_entitlements(
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
      -- A verified paid period that started after a departure lifts that block.
      and not (state.block_reason = 'departed' and exists (
        select 1 from public.whop_memberships as membership
        where membership.discord_user_id = state.discord_user_id
          and membership.account_id = p_account_id
          and membership.plan_id = any(p_paid_plan_ids)
          and membership.paid and membership.status in ('active', 'completed')
          and membership.paid_from > state.blocked_at
          and membership.paid_from <= now() and membership.paid_through > now()
          and membership.verified_at > now() - interval '15 minutes'
          and membership.verified_at <= now() + interval '5 minutes'
      ))
  );
$$;

revoke all on function public.record_highroller_access_state(text, boolean, boolean, boolean)
from public, anon, authenticated;
grant execute on function public.record_highroller_access_state(text, boolean, boolean, boolean)
to service_role;

commit;
