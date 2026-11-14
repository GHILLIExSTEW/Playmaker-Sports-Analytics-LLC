-- Read-only inspection. Run in the Supabase SQL editor and share the results.
-- Text matches can produce false positives and cannot find dynamically built SQL
-- or external clients. They do not prove that a table is safe to remove.
with candidates(name) as (
  values
    ('unit_entries'),
    ('unit_results'),
    ('unit_entries_archive'),
    ('unit_results_archive'),
    ('playmakers'),
    ('capper_role_roster'),
    ('roster_snapshots'),
    ('sync_jobs'),
    ('team_daily_summary'),
    ('capper_daily_review_limits')
),
relations as (
  select candidates.name, c.oid, c.relkind
  from candidates
  left join pg_catalog.pg_namespace n on n.nspname = 'public'
  left join pg_catalog.pg_class c on c.relnamespace = n.oid
    and c.relname = candidates.name
    and c.relkind in ('r', 'p')
)
select
  r.name as table_name,
  r.oid is not null as table_exists,
  coalesce((
    select jsonb_agg(format('%I.%I(%s)', n.nspname, p.proname,
                            pg_catalog.pg_get_function_identity_arguments(p.oid))
                     order by n.nspname, p.proname)
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname not like 'pg\_%' escape '\'
      and n.nspname <> 'information_schema'
      and (position(lower(r.name) in lower(p.prosrc)) > 0
        or position(lower(r.name) in lower(pg_catalog.pg_get_function_identity_arguments(p.oid))) > 0)
  ), '[]'::jsonb) as matching_routines,
  coalesce((
    select jsonb_agg(format('%I.%I', n.nspname, c.relname) order by n.nspname, c.relname)
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('v', 'm')
      and n.nspname not like 'pg\_%' escape '\'
      and n.nspname <> 'information_schema'
      and position(lower(r.name) in lower(pg_catalog.pg_get_viewdef(c.oid, true))) > 0
  ), '[]'::jsonb) as matching_views,
  coalesce((
    select jsonb_agg(format('%s on %s references %s', con.conname,
                            con.conrelid::regclass, con.confrelid::regclass)
                     order by con.conname)
    from pg_catalog.pg_constraint con
    where con.contype = 'f' and (con.conrelid = r.oid or con.confrelid = r.oid)
  ), '[]'::jsonb) as foreign_keys,
  coalesce((
    select jsonb_agg(format('%s: %s', t.tgname, pg_catalog.pg_get_triggerdef(t.oid))
                     order by t.tgname)
    from pg_catalog.pg_trigger t
    where t.tgrelid = r.oid and not t.tgisinternal
  ), '[]'::jsonb) as triggers,
  coalesce((
    select jsonb_agg(p.policyname order by p.policyname)
    from pg_catalog.pg_policies p
    where p.schemaname = 'public' and p.tablename = r.name
  ), '[]'::jsonb) as policies,
  coalesce((
    select jsonb_agg(p.pubname order by p.pubname)
    from pg_catalog.pg_publication_tables p
    where p.schemaname = 'public' and p.tablename = r.name
  ), '[]'::jsonb) as publications
from relations r
order by r.name;
