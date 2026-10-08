begin;

alter table public.capper_page_settings add column appearance jsonb not null default
  '{"name_color":null,"link_color":null,"text_color":null,"heading_font":"barlow","banner_url":null,"section_order":["stats","picks","charts","results"]}'::jsonb;

alter function public.capper_page_profile(text) rename to capper_page_profile_before_appearance;
revoke all on function public.capper_page_profile_before_appearance(text) from public, anon, authenticated;
create function public.capper_page_profile(p_capper text)
returns jsonb
language sql stable security definer set search_path = ''
as $$
  select public.capper_page_profile_before_appearance(p_capper) || jsonb_build_object(
    'appearance', coalesce((
      select s.appearance from public.capper_page_settings s join public.users u on u.id = s.user_id
      where coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = p_capper
        and u.discord_user_id = any(public.website_capper_ids())
    ), '{"name_color":null,"link_color":null,"text_color":null,"heading_font":"barlow","banner_url":null,"section_order":["stats","picks","charts","results"]}'::jsonb)
  );
$$;

alter function public.save_capper_page(text, text, text, jsonb, text, text) rename to save_capper_page_before_appearance;
revoke all on function public.save_capper_page_before_appearance(text, text, text, jsonb, text, text) from public, anon, authenticated;
create function public.save_capper_page(
  p_accent_color text, p_bio text, p_avatar_url text, p_social_links jsonb,
  p_capper text default null, p_background_color text default null, p_appearance jsonb default null
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  saved jsonb;
  color_key text;
begin
  if p_appearance is not null then
    if jsonb_typeof(p_appearance) <> 'object'
       or not (p_appearance ?& array['name_color','link_color','text_color','heading_font','banner_url','section_order'])
       or exists (select 1 from jsonb_object_keys(p_appearance) k
         where k not in ('name_color','link_color','text_color','heading_font','banner_url','section_order')) then
      raise exception 'Invalid page appearance fields.';
    end if;
    foreach color_key in array array['name_color','link_color','text_color'] loop
      if p_appearance -> color_key <> 'null'::jsonb and (
        jsonb_typeof(p_appearance -> color_key) <> 'string'
        or p_appearance ->> color_key !~ '^#[0-9A-Fa-f]{6}$'
      ) then raise exception 'Appearance colors must be six-digit hex colors or automatic.'; end if;
    end loop;
    if coalesce(p_appearance ->> 'heading_font', '') not in ('barlow','ibm','georgia') then
      raise exception 'Unsupported heading font.';
    end if;
    if p_appearance -> 'banner_url' <> 'null'::jsonb and (
      jsonb_typeof(p_appearance -> 'banner_url') <> 'string'
      or char_length(p_appearance ->> 'banner_url') > 2048
      or p_appearance ->> 'banner_url' !~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$'
    ) then raise exception 'Banner must use an HTTPS image URL.'; end if;
    if jsonb_typeof(p_appearance -> 'section_order') <> 'array' then
      raise exception 'Invalid section order.';
    end if;
    if jsonb_array_length(p_appearance -> 'section_order') <> 4
       or not (p_appearance -> 'section_order' @> '["stats","picks","charts","results"]'::jsonb) then
      raise exception 'Section order must include each section exactly once.';
    end if;
  end if;
  saved := public.save_capper_page_before_appearance(
    p_accent_color, p_bio, p_avatar_url, p_social_links, p_capper, p_background_color);
  if p_appearance is not null then
    update public.capper_page_settings s set appearance = p_appearance
    from public.users u where u.id = s.user_id
      and coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = saved ->> 'name'
      and u.discord_user_id = any(public.website_capper_ids());
  end if;
  return public.capper_page_profile(saved ->> 'name');
end;
$$;
revoke all on function public.capper_page_profile(text) from public, anon, authenticated;
revoke all on function public.save_capper_page(text, text, text, jsonb, text, text, jsonb) from public, anon, authenticated;
grant execute on function public.capper_page_profile(text) to anon, authenticated;
grant execute on function public.save_capper_page(text, text, text, jsonb, text, text, jsonb) to authenticated;

commit;
