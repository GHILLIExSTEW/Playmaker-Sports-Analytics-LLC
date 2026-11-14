-- Run as database owner, only after deploying the current repository everywhere.
-- Preview first. Back up these tables and verify the backup before applying.
-- Dynamic SQL and external clients cannot be proven absent by catalog searches.
-- capper_role_roster is intentionally retained: live views depend on it.
do $$
declare
  apply_delete boolean := false;
  backup_verified boolean := false;
  targets text[] := array[
    'unit_entries', 'unit_results', 'unit_entries_archive', 'unit_results_archive',
    'playmakers', 'roster_snapshots', 'sync_jobs', 'team_daily_summary',
    'capper_daily_review_limits'
  ];
  references_to_check text[] := targets || array[
    'archive_unit_entry', 'archive_unit_result', 'set_capper_daily_review_limit'
  ];
  expected_functions oid[] := array_remove(array[
    to_regprocedure('public.archive_unit_entry()')::oid,
    to_regprocedure('public.archive_unit_result()')::oid,
    to_regprocedure('public.set_capper_daily_review_limit(text,integer)')::oid
  ], null);
  target text;
  relation_oid oid;
  row_count bigint;
  unexpected text;
begin
  foreach target in array targets loop
    select c.oid into relation_oid
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relname = target and c.relkind in ('r', 'p');
    if relation_oid is null then
      raise exception 'Expected table public.% is missing. Nothing deleted.', target;
    end if;
    execute format('lock table public.%I in access exclusive mode', target);
    execute format('select count(*) from public.%I', target) into row_count;
    raise notice 'public.%: % rows', target, row_count;
  end loop;

  select string_agg(format('%I.%I', n.nspname, p.proname), ', ')
  into unexpected
  from pg_catalog.pg_proc p
  join pg_catalog.pg_namespace n on n.oid = p.pronamespace
  where n.nspname not like 'pg\_%' escape '\'
    and n.nspname <> 'information_schema'
    and not (p.oid = any(expected_functions))
    and exists (
      select 1 from unnest(references_to_check) ref
      where position(ref in lower(p.prosrc)) > 0
         or position(ref in lower(pg_catalog.pg_get_function_identity_arguments(p.oid))) > 0
    );
  if unexpected is not null then
    raise exception 'Unexpected routine references: %. Nothing deleted.', unexpected;
  end if;

  select string_agg(format('%I.%I', n.nspname, c.relname), ', ')
  into unexpected
  from pg_catalog.pg_class c
  join pg_catalog.pg_namespace n on n.oid = c.relnamespace
  where c.relkind in ('v', 'm')
    and n.nspname not like 'pg\_%' escape '\'
    and n.nspname <> 'information_schema'
    and exists (
      select 1 from unnest(references_to_check) ref
      where position(ref in lower(pg_catalog.pg_get_viewdef(c.oid, true))) > 0
    );
  if unexpected is not null then
    raise exception 'Unexpected view references: %. Nothing deleted.', unexpected;
  end if;

  select string_agg(format('%I.%I: %I', p.schemaname, p.tablename, p.policyname), ', ')
  into unexpected
  from pg_catalog.pg_policies p
  where (p.schemaname = 'public' and p.tablename = any(targets))
     or exists (
       select 1 from unnest(references_to_check) ref
       where position(ref in lower(coalesce(p.qual, '') || ' ' || coalesce(p.with_check, ''))) > 0
     );
  if unexpected is not null then
    raise exception 'Unexpected policy references: %. Nothing deleted.', unexpected;
  end if;

  select string_agg(format('%I: %I.%I', p.pubname, p.schemaname, p.tablename), ', ')
  into unexpected
  from pg_catalog.pg_publication_tables p
  where p.schemaname = 'public' and p.tablename = any(targets);
  if unexpected is not null then
    raise exception 'Tables in publications: %. Nothing deleted.', unexpected;
  end if;

  select string_agg(format('%s on %s', t.tgname, t.tgrelid::regclass), ', ')
  into unexpected
  from pg_catalog.pg_trigger t
  where not t.tgisinternal
    and t.tgrelid in (
      select c.oid from pg_catalog.pg_class c
      join pg_catalog.pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relname = any(targets)
    )
    and not coalesce((
      (t.tgrelid = to_regclass('public.unit_entries')
        and t.tgfoid = to_regprocedure('public.archive_unit_entry()'))
      or (t.tgrelid = to_regclass('public.unit_results')
        and t.tgfoid = to_regprocedure('public.archive_unit_result()'))
    ), false);
  if unexpected is not null then
    raise exception 'Unexpected triggers: %. Nothing deleted.', unexpected;
  end if;

  if to_regclass('cron.job') is not null then
    execute 'select string_agg(coalesce(jobname, jobid::text), '', '') from cron.job
             where active and exists (
               select 1 from unnest($1::text[]) ref
               where position(ref in lower(command)) > 0
             )'
      into unexpected using references_to_check;
    if unexpected is not null then
      raise exception 'Scheduled job references: %. Nothing deleted.', unexpected;
    end if;
  end if;

  if not apply_delete then
    raise notice 'PREVIEW ONLY: nothing deleted. Verify a backup before enabling deletion.';
    return;
  end if;
  if not backup_verified then
    raise exception 'Set backup_verified to true only after verifying a backup. Nothing deleted.';
  end if;

  -- Dropping the entry tables removes their own archive triggers.
  drop table public.unit_entries, public.unit_results;
  drop function if exists public.archive_unit_entry();
  drop function if exists public.archive_unit_result();
  drop function if exists public.set_capper_daily_review_limit(text, integer);
  drop table public.unit_entries_archive, public.unit_results_archive,
    public.playmakers, public.roster_snapshots, public.sync_jobs,
    public.team_daily_summary, public.capper_daily_review_limits;
  raise notice 'Legacy tables and their obsolete functions removed.';
end;
$$;
