begin;

create or replace view public.display_plays with (security_invoker = true) as
  select p.* from public.plays p
  where p.created_at >= (select starts_at from public.tracking_display_settings where singleton);

revoke all on public.display_plays from public, anon, authenticated;
grant select on public.display_plays to service_role;

commit;
