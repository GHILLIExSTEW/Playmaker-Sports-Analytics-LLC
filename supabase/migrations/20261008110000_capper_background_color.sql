begin;

alter table public.capper_page_settings add column background_color text not null
  default '#040916' check (background_color ~ '^#[0-9A-Fa-f]{6}$');

alter function public.capper_page_profile(text) rename to capper_page_profile_before_background;
revoke all on function public.capper_page_profile_before_background(text) from public, anon, authenticated;
create function public.capper_page_profile(p_capper text)
returns jsonb
language sql stable security definer set search_path = ''
as $$
  select public.capper_page_profile_before_background(p_capper) || jsonb_build_object(
    'background_color', coalesce((
      select s.background_color from public.capper_page_settings s join public.users u on u.id = s.user_id
      where coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = p_capper
        and u.discord_user_id = any(public.website_capper_ids())
    ), '#040916')
  );
$$;

alter function public.save_capper_page(text, text, text, jsonb, text) rename to save_capper_page_before_background;
revoke all on function public.save_capper_page_before_background(text, text, text, jsonb, text) from public, anon, authenticated;
create function public.save_capper_page(
  p_accent_color text, p_bio text, p_avatar_url text, p_social_links jsonb,
  p_capper text default null, p_background_color text default null
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare saved jsonb;
begin
  if p_background_color is not null and p_background_color !~ '^#[0-9A-Fa-f]{6}$' then
    raise exception 'Background color must be a six-digit hex color.';
  end if;
  saved := public.save_capper_page_before_background(p_accent_color, p_bio, p_avatar_url, p_social_links, p_capper);
  if p_background_color is not null then
    update public.capper_page_settings s set background_color = lower(p_background_color)
    from public.users u where u.id = s.user_id
      and coalesce(nullif(btrim(u.display_name), ''), 'Playmaker Picks') = saved ->> 'name'
      and u.discord_user_id = any(public.website_capper_ids());
  end if;
  return public.capper_page_profile(saved ->> 'name');
end;
$$;
revoke all on function public.capper_page_profile(text) from public, anon, authenticated;
revoke all on function public.save_capper_page(text, text, text, jsonb, text, text) from public, anon, authenticated;
grant execute on function public.capper_page_profile(text) to anon, authenticated;
grant execute on function public.save_capper_page(text, text, text, jsonb, text, text) to authenticated;

commit;
