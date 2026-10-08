begin;

create table public.capper_page_settings (
  user_id bigint primary key references public.users(id) on delete cascade,
  accent_color text not null default '#96d38d' check (accent_color ~ '^#[0-9A-Fa-f]{6}$'),
  bio text not null default '' check (char_length(bio) <= 2000),
  social_links jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table public.capper_page_settings enable row level security;
revoke all on public.capper_page_settings from public, anon, authenticated;

create function public.capper_page_profile(p_capper text)
returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  result jsonb;
begin
  select jsonb_build_object(
    'name', coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks'),
    'accent_color', coalesce(s.accent_color, '#96d38d'),
    'bio', coalesce(s.bio, ''),
    'avatar_url', case when u.public_avatar_url like 'https://%' then u.public_avatar_url else null end,
    'social_links', coalesce(s.social_links, '{}'::jsonb)
  ) into strict result
  from public.users u left join public.capper_page_settings s on s.user_id = u.id
  where u.discord_user_id = any(public.website_capper_ids())
    and coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = p_capper;
  return result;
exception when no_data_found then
  return null;
end;
$$;

create function public.owned_capper_page()
returns text
language plpgsql stable security definer set search_path = ''
as $$
declare
  discord_id text;
  capper_name text;
begin
  if auth.uid() is null then return null; end if;
  select provider_id into strict discord_id from auth.identities
  where user_id = auth.uid() and provider = 'discord';
  if not (discord_id = any(public.website_capper_ids())) then return null; end if;
  select coalesce(nullif(btrim(display_name), ''), 'Playmaker Picks') into strict capper_name
  from public.users where discord_user_id = discord_id;
  -- Existing routes use names; ambiguous names must never expose an editor.
  if (select count(*) from public.users where discord_user_id = any(public.website_capper_ids())
      and coalesce(nullif(btrim(display_name), ''), 'Playmaker Picks') = capper_name) <> 1 then
    raise exception 'Capper page name is ambiguous. Contact the administrator.';
  end if;
  return capper_name;
exception when no_data_found then
  return null;
end;
$$;

create function public.save_capper_page(
  p_accent_color text, p_bio text, p_avatar_url text, p_social_links jsonb
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  capper_name text := public.owned_capper_page();
  owner_id bigint;
  entry record;
begin
  if capper_name is null then
    raise exception 'Verified original OPERATOR page owner required.' using errcode = '42501';
  end if;
  if p_accent_color is null or p_accent_color !~ '^#[0-9A-Fa-f]{6}$'
     or p_bio is null or char_length(p_bio) > 2000
     or p_avatar_url is null or char_length(p_avatar_url) > 2048
     or (p_avatar_url <> '' and p_avatar_url !~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$')
     or p_social_links is null or jsonb_typeof(p_social_links) <> 'object' then
    raise exception 'Invalid page settings. Use a hex color, bio up to 2000 characters and HTTPS image/link URLs.';
  end if;
  for entry in select key, value from jsonb_each(p_social_links) loop
    if entry.key not in ('website', 'x', 'instagram', 'discord')
       or jsonb_typeof(entry.value) <> 'string'
       or char_length(entry.value #>> '{}') > 2048
       or (entry.value #>> '{}') !~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$' then
      raise exception 'Social links must use supported labels and valid HTTPS URLs.';
    end if;
  end loop;
  select u.id into strict owner_id from public.users u
  join auth.identities i on i.provider_id = u.discord_user_id and i.provider = 'discord'
  where i.user_id = auth.uid();
  insert into public.capper_page_settings (user_id, accent_color, bio, social_links)
  values (owner_id, lower(p_accent_color), btrim(p_bio), p_social_links)
  on conflict (user_id) do update set accent_color = excluded.accent_color,
    bio = excluded.bio, social_links = excluded.social_links, updated_at = now();
  update public.users set public_avatar_url = nullif(p_avatar_url, '') where id = owner_id;
  return public.capper_page_profile(capper_name);
end;
$$;

revoke all on function public.capper_page_profile(text) from public, anon, authenticated;
revoke all on function public.owned_capper_page() from public, anon, authenticated;
revoke all on function public.save_capper_page(text, text, text, jsonb) from public, anon, authenticated;
grant execute on function public.capper_page_profile(text) to anon, authenticated;
grant execute on function public.owned_capper_page() to anon, authenticated;
grant execute on function public.save_capper_page(text, text, text, jsonb) to authenticated;

commit;
