begin;

create table public.tracking_display_settings (
  singleton boolean primary key default true check (singleton),
  starts_at timestamptz not null
);
insert into public.tracking_display_settings (starts_at)
values ('2026-10-14T00:00:00-04:00');
alter table public.tracking_display_settings enable row level security;
revoke all on public.tracking_display_settings from public, anon, authenticated;
grant select, update on public.tracking_display_settings to service_role;

create view public.display_plays with (security_invoker = true) as
  select p.* from public.plays p
  where p.created_at >= (select starts_at from public.tracking_display_settings where singleton);
revoke all on public.display_plays from public, anon, authenticated;
grant select on public.display_plays to service_role;

alter function public.public_settled_results() rename to archived_settled_results;
revoke all on function public.archived_settled_results() from public, anon, authenticated;
create function public.public_settled_results()
returns table (
  created_at timestamptz, settled_at timestamptz, sport text, capper text,
  avatar_url text, selection text, odds integer, units numeric,
  status text, net_units numeric, tracking_scope text
)
language sql stable security definer set search_path = ''
as $$
  select source.* from public.archived_settled_results() source
  where source.created_at >= (select starts_at from public.tracking_display_settings where singleton)
  order by source.settled_at desc, source.created_at desc;
$$;
revoke all on function public.public_settled_results() from public, anon, authenticated;
grant execute on function public.public_settled_results() to anon, authenticated;

alter function public.member_current_picks(text, text) rename to archived_current_picks;
revoke all on function public.archived_current_picks(text, text) from public, anon, authenticated;
create function public.member_current_picks(p_sport text default null, p_capper text default null)
returns table (
  id bigint, created_at timestamptz, sport text, capper text, avatar_url text,
  selection text, analysis text, odds integer, units numeric
)
language sql stable security definer set search_path = ''
as $$
  select source.* from public.archived_current_picks(p_sport, p_capper) source
  where source.created_at >= (select starts_at from public.tracking_display_settings where singleton)
  order by source.created_at desc, source.id desc;
$$;
revoke all on function public.member_current_picks(text, text) from public, anon, authenticated;
grant execute on function public.member_current_picks(text, text) to authenticated;

commit;
