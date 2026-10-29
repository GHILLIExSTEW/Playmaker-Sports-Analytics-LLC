begin;

alter table public.capper_page_settings
  add column if not exists play_card_frame_path text;

alter table public.plays
  add column if not exists capper_frame_path text;

alter function public.capper_page_profile(text) rename to capper_page_profile_before_play_card_frame;
revoke all on function public.capper_page_profile_before_play_card_frame(text) from public, anon, authenticated;
create function public.capper_page_profile(p_capper text)
returns jsonb
language sql stable security definer set search_path = ''
as $$
  select public.capper_page_profile_before_play_card_frame(p_capper) || jsonb_build_object(
    'play_card_frame_path', (
      select s.play_card_frame_path from public.capper_page_settings s
      join public.users u on u.id = s.user_id
      where coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = p_capper
        and u.discord_user_id = any(public.website_capper_ids())
    )
  );
$$;

alter function public.save_capper_page(text, text, text, jsonb, text, text, jsonb)
  rename to save_capper_page_before_play_card_frame;
revoke all on function public.save_capper_page_before_play_card_frame(text, text, text, jsonb, text, text, jsonb)
  from public, anon, authenticated;
create function public.save_capper_page(
  p_accent_color text, p_bio text, p_avatar_url text, p_social_links jsonb,
  p_capper text default null, p_background_color text default null, p_appearance jsonb default null
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  saved jsonb;
begin
  saved := public.save_capper_page_before_play_card_frame(
    p_accent_color, p_bio, p_avatar_url, p_social_links,
    p_capper, p_background_color, p_appearance
  );
  return public.capper_page_profile(saved ->> 'name');
end;
$$;

create function public.save_capper_play_card_frame(p_capper text, p_frame_path text)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  target_user_id bigint;
begin
  if auth.uid() is null or not (
    public.website_can_edit_all_cappers() or public.owned_capper_page() = p_capper
  ) then
    raise exception 'Verified capper page owner or current Owner role required.' using errcode = '42501';
  end if;
  if p_frame_path is not null and p_frame_path !~ (
    '^capper-frames/' || auth.uid()::text || '/[0-9a-f-]{36}[.]webp$'
  ) then
    raise exception 'Invalid capper play-card frame path.';
  end if;
  if p_frame_path is not null and not exists (
    select 1 from storage.objects
    where bucket_id = 'website-assets' and name = p_frame_path
  ) then
    raise exception 'Upload the capper play-card frame before saving it.';
  end if;
  if (select count(*) from public.users u
      where u.discord_user_id = any(public.website_capper_ids())
        and coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = p_capper) <> 1 then
    raise exception 'Capper page name is ambiguous. Contact the administrator.';
  end if;
  select u.id into strict target_user_id
  from public.users u
  where u.discord_user_id = any(public.website_capper_ids())
    and coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = p_capper;
  insert into public.capper_page_settings (user_id, play_card_frame_path)
  values (target_user_id, p_frame_path)
  on conflict (user_id) do update set play_card_frame_path = excluded.play_card_frame_path,
    updated_at = now();
  return public.capper_page_profile(p_capper);
end;
$$;

create function public.can_manage_capper_play_card_frame(p_bucket text, p_name text)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select auth.uid() is not null
    and p_bucket = 'website-assets'
    and p_name ~ ('^capper-frames/' || auth.uid()::text || '/[0-9a-f-]{36}[.]webp$')
    and (public.website_can_edit_all_cappers() or public.owned_capper_page() is not null);
$$;

create function public.can_delete_capper_play_card_frame(p_bucket text, p_name text)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select public.can_manage_capper_play_card_frame(p_bucket, p_name)
    and not exists (
      select 1 from public.capper_page_settings where play_card_frame_path = p_name
    )
    and not exists (
      select 1 from public.plays where capper_frame_path = p_name
    );
$$;

revoke all on function public.can_manage_capper_play_card_frame(text, text) from public, anon, authenticated;
revoke all on function public.can_delete_capper_play_card_frame(text, text) from public, anon, authenticated;
grant execute on function public.can_manage_capper_play_card_frame(text, text) to authenticated;
grant execute on function public.can_delete_capper_play_card_frame(text, text) to authenticated;

create policy capper_play_card_frame_insert on storage.objects for insert to authenticated
with check (public.can_manage_capper_play_card_frame(bucket_id, name));
create policy capper_play_card_frame_select on storage.objects for select to authenticated
using (public.can_manage_capper_play_card_frame(bucket_id, name));
create policy capper_play_card_frame_delete on storage.objects for delete to authenticated
using (public.can_delete_capper_play_card_frame(bucket_id, name));
create policy capper_play_card_frame_owner_guard on storage.objects as restrictive for all to authenticated
using (
  bucket_id <> 'website-assets' or name not like 'capper-frames/%'
  or public.can_manage_capper_play_card_frame(bucket_id, name)
)
with check (
  bucket_id <> 'website-assets' or name not like 'capper-frames/%'
  or public.can_manage_capper_play_card_frame(bucket_id, name)
);
create policy capper_play_card_frame_delete_guard on storage.objects as restrictive for delete to authenticated
using (public.can_delete_capper_play_card_frame(bucket_id, name));
create policy capper_play_card_frame_update_guard on storage.objects as restrictive for update to authenticated
using (false)
with check (false);

revoke all on function public.capper_page_profile(text) from public, anon, authenticated;
revoke all on function public.save_capper_page(text, text, text, jsonb, text, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.save_capper_play_card_frame(text, text) from public, anon, authenticated;
grant execute on function public.capper_page_profile(text) to anon, authenticated;
grant execute on function public.save_capper_page(text, text, text, jsonb, text, text, jsonb) to authenticated;
grant execute on function public.save_capper_play_card_frame(text, text) to authenticated;

commit;
