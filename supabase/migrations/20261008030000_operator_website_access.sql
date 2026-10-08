begin;

alter function public.website_member_access() rename to website_member_access_owner_preview;
revoke all on function public.website_member_access_owner_preview() from public, anon, authenticated;

create function public.website_member_access()
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  existing_access jsonb;
  operator_ids text[];
begin
  existing_access := public.website_member_access_owner_preview();
  if existing_access ->> 'state' not in ('launch-pending', 'membership-required', 'active')
     or existing_access ->> 'kind' = 'owner' then
    return existing_access;
  end if;

  -- Previous checks require age verification and provider-managed identity.
  if existing_access ->> 'state' = 'active' then
    select roster.discord_user_ids into operator_ids
    from public.website_capper_roster as roster
    where roster.singleton and roster.role_id = '1328120848992960543'
      and roster.verified_at > now() - interval '15 minutes' and roster.verified_at <= now();
  else
    operator_ids := public.website_capper_ids();
  end if;
  if exists (
    select 1 from auth.identities as identity
    where identity.user_id = auth.uid() and identity.provider = 'discord'
      and identity.provider_id = any(operator_ids)
      and exists (
        select 1 from public.website_capper_roster as roster
        where roster.singleton and roster.role_id = '1328120848992960543'
      )
  ) then
    return jsonb_build_object('state', 'active', 'kind', 'operator', 'expires_at', null);
  end if;
  return existing_access;
end;
$$;

revoke all on function public.website_member_access() from public, anon, authenticated;
grant execute on function public.website_member_access() to anon, authenticated;

commit;
