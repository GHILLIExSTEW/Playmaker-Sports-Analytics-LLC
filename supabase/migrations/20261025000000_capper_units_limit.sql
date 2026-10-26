begin;

alter table public.plays
  add constraint plays_capper_units_limit
  check (tracking_scope <> 'capper' or units <= 3)
  not valid;

create or replace function public.public_settled_results()
returns table (
  created_at timestamptz, settled_at timestamptz, sport text, capper text,
  avatar_url text, selection text, odds integer, units numeric,
  status text, net_units numeric, tracking_scope text
)
language sql stable security definer set search_path = ''
as $$
  select source.* from public.archived_settled_results() source
  where source.created_at >= (select starts_at from public.tracking_display_settings where singleton)
    and (source.tracking_scope <> 'capper' or source.units <= 3)
  order by source.settled_at desc, source.created_at desc;
$$;
revoke all on function public.public_settled_results() from public, anon, authenticated;
grant execute on function public.public_settled_results() to anon, authenticated;

commit;
