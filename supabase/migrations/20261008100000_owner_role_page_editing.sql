begin;

create table public.website_owner_roster (
  singleton boolean primary key default true check (singleton),
  guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
  role_id text not null check (role_id = '1347741218158678097'),
  discord_user_ids text[] not null,
  verified_at timestamptz not null
);
alter table public.website_owner_roster enable row level security;
revoke all on public.website_owner_roster from public, anon, authenticated;

create function public.sync_website_owner_roster(p_guild_id text, p_role_id text, p_discord_user_ids text[])
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  if p_discord_user_ids is null or exists (
    select 1 from unnest(p_discord_user_ids) member_id
    where member_id is null or member_id !~ '^[0-9]{1,20}$'
  ) then raise exception 'Invalid Owner roster identities.'; end if;
  insert into public.website_owner_roster (guild_id, role_id, discord_user_ids, verified_at)
  values (p_guild_id, p_role_id, p_discord_user_ids, now())
  on conflict (singleton) do update set guild_id = excluded.guild_id,
    role_id = excluded.role_id, discord_user_ids = excluded.discord_user_ids, verified_at = excluded.verified_at;
  return true;
end;
$$;

create function public.website_can_edit_all_cappers()
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1 from public.website_owner_roster r
    join public.website_capper_roster c on c.singleton and c.guild_id = r.guild_id
    join auth.identities i on i.user_id = auth.uid() and i.provider = 'discord'
      and i.provider_id = any(r.discord_user_ids)
    where r.singleton and r.role_id = '1347741218158678097'
      and r.verified_at > now() - interval '15 minutes' and r.verified_at <= now()
      and c.verified_at > now() - interval '15 minutes' and c.verified_at <= now()
  );
$$;

-- Preserve existing validation and write logic in a private target-specific helper.
create function public.write_capper_page_settings(
  p_user_id bigint, p_accent_color text, p_bio text, p_avatar_url text, p_social_links jsonb
)
returns void
language plpgsql security definer set search_path = ''
as $$
declare entry record;
begin
  if p_accent_color is null or p_accent_color !~ '^#[0-9A-Fa-f]{6}$'
     or p_bio is null or char_length(p_bio) > 2000
     or p_avatar_url is null or char_length(p_avatar_url) > 2048
     or (p_avatar_url <> '' and p_avatar_url !~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$')
     or p_social_links is null or jsonb_typeof(p_social_links) <> 'object' then
    raise exception 'Invalid page settings. Use a hex color, bio up to 2000 characters and HTTPS image/link URLs.';
  end if;
  for entry in select key, value from jsonb_each(p_social_links) loop
    if entry.key not in ('website', 'x', 'instagram', 'discord')
       or jsonb_typeof(entry.value) <> 'string' or char_length(entry.value #>> '{}') > 2048
       or (entry.value #>> '{}') !~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$' then
      raise exception 'Social links must use supported labels and valid HTTPS URLs.';
    end if;
  end loop;
  insert into public.capper_page_settings (user_id, accent_color, bio, social_links)
  values (p_user_id, lower(p_accent_color), btrim(p_bio), p_social_links)
  on conflict (user_id) do update set accent_color = excluded.accent_color,
    bio = excluded.bio, social_links = excluded.social_links, updated_at = now();
  update public.users set public_avatar_url = nullif(p_avatar_url, '') where id = p_user_id;
end;
$$;

alter function public.website_member_access() rename to website_member_access_before_owner_role;
revoke all on function public.website_member_access_before_owner_role() from public, anon, authenticated;
create function public.website_member_access()
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
begin
  if public.website_can_edit_all_cappers() and exists (
    select 1 from public.member_profiles where user_id = auth.uid() and age_verified_at is not null
  ) then
    return jsonb_build_object('state', 'active', 'kind', 'owner', 'expires_at', null);
  end if;
  return public.website_member_access_before_owner_role();
end;
$$;
revoke all on function public.website_member_access() from public, anon, authenticated;
grant execute on function public.website_member_access() to anon, authenticated;

drop function public.save_capper_page(text, text, text, jsonb);
create function public.save_capper_page(
  p_accent_color text, p_bio text, p_avatar_url text, p_social_links jsonb, p_capper text default null
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  capper_name text;
  target_id bigint;
begin
  if public.website_can_edit_all_cappers() then
    capper_name := coalesce(p_capper, public.owned_capper_page());
  else
    capper_name := public.owned_capper_page();
    if capper_name is null or (p_capper is not null and p_capper <> capper_name) then
      raise exception 'Verified original OPERATOR page owner or current Owner role required.' using errcode = '42501';
    end if;
  end if;
  select u.id into strict target_id from public.users u
  where u.discord_user_id = any(public.website_capper_ids())
    and coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = capper_name;
  perform public.write_capper_page_settings(target_id, p_accent_color, p_bio, p_avatar_url, p_social_links);
  return public.capper_page_profile(capper_name);
end;
$$;

create or replace function public.save_my_pick_insight(p_play_id bigint, p_justification text)
returns boolean
language plpgsql security definer set search_path = ''
as $$
declare
  discord_id text;
begin
  if public.website_can_edit_all_cappers() then
    -- Reuse the same open/published/current-capper validation and insight storage.
    select u.discord_user_id into strict discord_id from public.plays p
    join public.users u on u.id = p.user_id where p.id = p_play_id;
  else
    if public.website_member_access() ->> 'state' <> 'active' or public.owned_capper_page() is null then
      raise exception 'Verified active OPERATOR page owner or current Owner role required.' using errcode = '42501';
    end if;
    select provider_id into strict discord_id from auth.identities
    where user_id = auth.uid() and provider = 'discord';
  end if;
  return public.save_capper_insight(p_play_id, discord_id, p_justification);
end;
$$;

create or replace function public.can_manage_capper_avatar(p_bucket text, p_name text)
returns boolean
language sql stable security definer set search_path = ''
as $$
  select auth.uid() is not null and p_bucket = 'website-assets'
    and p_name ~ ('^capper-avatars/' || auth.uid()::text || '/[0-9a-f-]{36}[.]webp$')
    and (public.website_can_edit_all_cappers() or public.owned_capper_page() is not null);
$$;

revoke all on function public.sync_website_owner_roster(text, text, text[]) from public, anon, authenticated;
revoke all on function public.website_can_edit_all_cappers() from public, anon, authenticated;
revoke all on function public.write_capper_page_settings(bigint, text, text, text, jsonb) from public, anon, authenticated;
revoke all on function public.save_capper_page(text, text, text, jsonb, text) from public, anon, authenticated;
grant execute on function public.sync_website_owner_roster(text, text, text[]) to service_role;
grant execute on function public.website_can_edit_all_cappers() to anon, authenticated;
grant execute on function public.save_capper_page(text, text, text, jsonb, text) to authenticated;

commit;
