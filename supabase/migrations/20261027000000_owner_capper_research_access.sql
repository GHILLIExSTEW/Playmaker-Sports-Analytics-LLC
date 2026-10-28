begin;

create or replace function public.capper_tools_access()
returns boolean language sql stable security definer set search_path = ''
as $$
  select auth.uid() is not null
    and public.website_member_access()->>'state' = 'active'
    and (
      public.website_member_access()->>'kind' = 'owner'
      or exists (
        select 1 from auth.identities i
        join public.member_profiles p on p.user_id = i.user_id and p.age_verified_at is not null
        where i.user_id = auth.uid() and i.provider = 'discord'
          and i.provider_id = any(public.website_capper_ids())
          and coalesce(
            (select g.allowed from public.capper_tools_grants g where g.discord_user_id = i.provider_id),
            exists(select 1 from public.capper_api_keys k where k.discord_user_id = i.provider_id and k.revoked_at is null)
          )
      )
    );
$$;
revoke all on function public.capper_tools_access() from public, anon;
grant execute on function public.capper_tools_access() to authenticated;

commit;
