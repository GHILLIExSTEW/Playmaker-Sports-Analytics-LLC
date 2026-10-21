begin;

drop function public.member_current_picks(text, text);
create function public.member_current_picks(p_sport text default null, p_capper text default null)
returns table (
  id bigint, created_at timestamptz, sport text, capper text, avatar_url text,
  selection text, analysis text, odds integer, units numeric,
  approval_yes integer, eligible_cappers integer
)
language sql stable security definer set search_path = ''
as $$
  select source.*, (p.team_approval ->> 'yes')::integer, (p.team_approval ->> 'roster_size')::integer
  from public.archived_current_picks(p_sport, p_capper) source
  join public.plays p on p.id = source.id
  where source.created_at >= (select starts_at from public.tracking_display_settings where singleton)
  order by source.created_at desc, source.id desc;
$$;
revoke all on function public.member_current_picks(text, text) from public, anon, authenticated;
grant execute on function public.member_current_picks(text, text) to authenticated;

commit;
