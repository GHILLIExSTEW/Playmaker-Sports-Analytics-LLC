begin;

create function public.require_operator_research_access()
returns void language plpgsql stable security definer set search_path = ''
as $$
declare
  access jsonb := public.website_member_access();
begin
  if auth.uid() is null
    or access ->> 'state' is distinct from 'active'
    or coalesce(access ->> 'kind', '') not in ('operator', 'owner') then
    raise exception 'Verified OPERATOR or Owner research access required'
      using errcode = '42501';
  end if;
end;
$$;
revoke all on function public.require_operator_research_access() from public, anon, authenticated;

-- Preserve the existing RPC result schemas while restricting their implementations.
do $migration$
declare
  item record;
  source_oid oid;
  result_schema text;
begin
  for item in select * from (values
    ('public_nfl_model_games', '', ''),
    ('public_sport_model_events', 'p_sport_slug text, p_league_id text', 'p_sport_slug, p_league_id'),
    ('public_nfl_team_efficiency', '', '')
  ) as functions(name, arguments, call_arguments)
  loop
    source_oid := case when item.name = 'public_sport_model_events'
      then 'public.public_sport_model_events(text,text)'::regprocedure::oid
      else to_regprocedure('public.' || item.name || '()')::oid end;
    result_schema := pg_get_function_result(source_oid);
    execute format('alter function public.%I(%s) rename to %I',
      item.name, item.arguments, 'internal_' || item.name);
    execute format('revoke all on function public.%I(%s) from public, anon, authenticated',
      'internal_' || item.name, item.arguments);
    execute format(
      'create function public.%I(%s) returns %s language plpgsql stable security definer set search_path = '''' as %L',
      item.name, item.arguments, result_schema,
      'begin perform public.require_operator_research_access(); return query select * from public.'
        || quote_ident('internal_' || item.name) || '(' || item.call_arguments || '); end;'
    );
    execute format('revoke all on function public.%I(%s) from public, anon',
      item.name, item.arguments);
    execute format('grant execute on function public.%I(%s) to authenticated',
      item.name, item.arguments);
  end loop;
end;
$migration$;

commit;
