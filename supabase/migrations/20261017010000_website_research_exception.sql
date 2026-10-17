begin;

alter function public.website_member_access() rename to website_member_access_before_research_exception;
revoke all on function public.website_member_access_before_research_exception() from public, anon, authenticated;

create function public.website_member_access()
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  existing_access jsonb := public.website_member_access_before_research_exception();
begin
  if existing_access ->> 'state' in ('active', 'launch-pending', 'membership-required')
     and coalesce(existing_access ->> 'kind', '') not in ('owner', 'operator')
     and exists (
       select 1 from public.member_profiles
       where user_id = auth.uid() and age_verified_at is not null
     )
     and exists (
       select 1 from auth.identities
       where user_id = auth.uid() and provider = 'discord'
         and provider_id = '1179625165332676632'
     ) then
    return jsonb_build_object('state', 'active', 'kind', 'research', 'expires_at', null);
  end if;
  return existing_access;
end;
$$;

revoke all on function public.website_member_access() from public, anon, authenticated;
grant execute on function public.website_member_access() to anon, authenticated;

create or replace function public.require_operator_research_access()
returns void language plpgsql stable security definer set search_path = ''
as $$
declare
  access jsonb := public.website_member_access();
begin
  if auth.uid() is null
    or access ->> 'state' is distinct from 'active'
    or coalesce(access ->> 'kind', '') not in ('operator', 'owner', 'research') then
    raise exception 'Verified OPERATOR or Owner research access or an approved research exception required'
      using errcode = '42501';
  end if;
end;
$$;
revoke all on function public.require_operator_research_access() from public, anon, authenticated;

commit;
