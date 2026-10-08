begin;

alter function public.website_member_access() rename to website_member_access_standard;
revoke all on function public.website_member_access_standard() from public, anon, authenticated;

create function public.website_member_access()
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  standard_access jsonb;
  owner_grant public.owner_membership_grants;
begin
  standard_access := public.website_member_access_standard();
  if standard_access ->> 'state' <> 'launch-pending' then
    return standard_access;
  end if;

  -- Owner-only preview never enables general enrollment. The standard check
  -- has already required age verification and provider-managed Discord identity.
  select grant_record.* into owner_grant
  from public.owner_membership_grants as grant_record
  join public.website_membership_config as config
    on config.singleton and config.account_id = grant_record.account_id
  where grant_record.discord_user_id = '761388542965448767'
    and grant_record.tier = 'highroller'
    and grant_record.revoked_at is null
    and grant_record.starts_at <= now()
    and (grant_record.expires_at is null or grant_record.expires_at > now())
    and exists (
      select 1 from auth.identities as identity
      where identity.user_id = auth.uid() and identity.provider = 'discord'
        and identity.provider_id = grant_record.discord_user_id
    );
  if owner_grant.discord_user_id is null then
    return standard_access;
  end if;
  return jsonb_build_object(
    'state', 'active', 'kind', 'owner', 'expires_at', owner_grant.expires_at
  );
end;
$$;

revoke all on function public.website_member_access() from public, anon, authenticated;
grant execute on function public.website_member_access() to anon, authenticated;

commit;
