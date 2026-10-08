begin;

-- Preserve the bucket's existing public branding assets and limits.
do $$
begin
  if not exists (select 1 from storage.buckets where id = 'website-assets' and public) then
    raise exception 'The public website-assets bucket must exist before enabling capper uploads.';
  end if;
end;
$$;

create function public.can_manage_capper_avatar(p_bucket text, p_name text)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select auth.uid() is not null
    and p_bucket = 'website-assets'
    and p_name ~ ('^capper-avatars/' || auth.uid()::text || '/[0-9a-f-]{36}[.]webp$')
    and public.owned_capper_page() is not null;
$$;
revoke all on function public.can_manage_capper_avatar(text, text) from public, anon, authenticated;
grant execute on function public.can_manage_capper_avatar(text, text) to authenticated;

create policy capper_avatar_insert on storage.objects for insert to authenticated
with check (public.can_manage_capper_avatar(bucket_id, name));
create policy capper_avatar_select on storage.objects for select to authenticated
using (public.can_manage_capper_avatar(bucket_id, name));
create policy capper_avatar_delete on storage.objects for delete to authenticated
using (public.can_manage_capper_avatar(bucket_id, name));

-- Keep any unrelated permissive bucket policies from bypassing avatar ownership.
create policy capper_avatar_owner_guard on storage.objects as restrictive for all to authenticated
using (
  bucket_id <> 'website-assets' or name not like 'capper-avatars/%'
  or public.can_manage_capper_avatar(bucket_id, name)
)
with check (
  bucket_id <> 'website-assets' or name not like 'capper-avatars/%'
  or public.can_manage_capper_avatar(bucket_id, name)
);

commit;
