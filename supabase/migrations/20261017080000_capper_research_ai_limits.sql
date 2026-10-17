begin;

create or replace function public.reserve_capper_tools_ai()
returns uuid language plpgsql security definer set search_path = ''
as $$
declare
  today timestamptz := ((now() at time zone 'UTC')::date::timestamp at time zone 'UTC');
  request_id uuid;
begin
  if public.capper_tools_access() is distinct from true
    then raise exception 'Approved capper access required' using errcode = '42501'; end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('capper-tools-ai',0));
  if (select count(*) from public.capper_tools_ai_usage where requested_at >= today) >= 500
    or (select count(*) from public.capper_tools_ai_usage where requested_at >= today and user_id = auth.uid()) >= 50
    then raise exception 'Daily AI allowance reached (50 per capper, 500 shared, UTC)' using errcode = 'P0001'; end if;
  if exists(select 1 from public.capper_tools_ai_usage where user_id = auth.uid() and requested_at > now() - interval '15 seconds')
    then raise exception 'Wait 15 seconds before another AI request' using errcode = 'P0001'; end if;
  insert into public.capper_tools_ai_usage(user_id) values(auth.uid()) returning id into request_id;
  return request_id;
end;
$$;
revoke all on function public.reserve_capper_tools_ai() from public, anon;
grant execute on function public.reserve_capper_tools_ai() to authenticated;

commit;
