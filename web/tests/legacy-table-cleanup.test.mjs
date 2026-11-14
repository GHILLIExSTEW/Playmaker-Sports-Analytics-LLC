import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('legacy cleanup requires a backup and rejects unexpected dependencies', async (t) => {
  const db = new PGlite()
  t.after(() => db.close())
  const script = await readFile(new URL('../../supabase/maintenance/remove_unused_legacy_tables.sql', import.meta.url), 'utf8')
  await db.exec(`
    create table public.unit_entries(id integer);
    create table public.unit_results(id integer);
    create table public.unit_entries_archive(id integer);
    create table public.unit_results_archive(id integer);
    create table public.playmakers(id integer primary key);
    create table public.roster_snapshots(id integer);
    create table public.sync_jobs(id integer);
    create table public.team_daily_summary(id integer);
    create table public.capper_daily_review_limits(id integer);
    create table public.capper_role_roster(id integer);
    insert into public.unit_entries values (1);
    create function public.archive_unit_entry() returns trigger language plpgsql as $$
      begin insert into public.unit_entries_archive values (new.id); return new; end $$;
    create function public.archive_unit_result() returns trigger language plpgsql as $$
      begin insert into public.unit_results_archive values (new.id); return new; end $$;
    create trigger unit_entry_archive after insert on public.unit_entries
      for each row execute function public.archive_unit_entry();
    create trigger unit_result_archive after insert on public.unit_results
      for each row execute function public.archive_unit_result();
    create function public.set_capper_daily_review_limit(text,integer) returns void language sql
      as $$ delete from public.capper_daily_review_limits $$;
  `)
  const exists = async (name) =>
    (await db.query('select to_regclass($1) is not null as present', [`public.${name}`])).rows[0].present
  const enabled = script.replace('apply_delete boolean := false', 'apply_delete boolean := true')

  await db.exec(script)
  assert.equal(await exists('unit_entries'), true)
  await assert.rejects(db.exec(enabled), /backup_verified/)
  assert.equal(await exists('unit_entries'), true)
  await db.exec('create view public.dependent_view as select * from public.unit_entries')
  await assert.rejects(db.exec(enabled), /Unexpected view references/)
  await db.exec('drop view public.dependent_view')
  await db.exec('create function public.unexpected_use() returns integer language sql as $$ select count(*)::integer from public.sync_jobs $$')
  await assert.rejects(db.exec(enabled), /Unexpected routine references/)
  await db.exec('drop function public.unexpected_use()')
  await db.exec('create trigger unexpected_trigger after insert on public.sync_jobs for each row execute function public.archive_unit_entry()')
  await assert.rejects(db.exec(enabled), /Unexpected triggers/)
  await db.exec('drop trigger unexpected_trigger on public.sync_jobs')

  await db.exec('create schema cron; create table cron.job(jobid bigint, jobname text, active boolean, command text)')
  await db.exec("insert into cron.job values(1, null, true, 'select * from public.sync_jobs')")
  await assert.rejects(db.exec(enabled), /Scheduled job references/)
  await db.exec('delete from cron.job')

  const confirmed = enabled.replace('backup_verified boolean := false', 'backup_verified boolean := true')
  await db.exec('create table public.protected(id integer references public.playmakers(id))')
  await assert.rejects(db.exec(confirmed), /depend/)
  assert.equal(await exists('unit_entries'), true)
  await db.exec('drop table public.protected')
  await db.exec(confirmed)
  for (const name of [
    'unit_entries', 'unit_results', 'unit_entries_archive', 'unit_results_archive', 'playmakers',
    'roster_snapshots', 'sync_jobs', 'team_daily_summary', 'capper_daily_review_limits',
  ]) {
    assert.equal(await exists(name), false, name)
  }
  assert.equal(await exists('capper_role_roster'), true)
  assert.equal((await db.query("select to_regprocedure('public.archive_unit_entry()') is null as removed")).rows[0].removed, true)
})
