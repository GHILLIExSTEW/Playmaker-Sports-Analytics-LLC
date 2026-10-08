import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { PGlite } from '@electric-sql/pglite'

test('team refresh caches seed identities and deny browser writes/reads', async (t) => {
  const db = new PGlite()
  t.after(() => db.close())
  await db.exec(`
    create role anon;
    create role authenticated;
    create role service_role bypassrls;
    create table public.api_sports_nfl_teams(team_id bigint, name text, synced_at timestamptz);
    create table public.api_sports_events(sport_slug text, league_id text, home_id text, home_name text,
      away_id text, away_name text, synced_at timestamptz);
    insert into public.api_sports_nfl_teams values(10, 'NFL team', now());
    insert into public.api_sports_events values
      ('basketball','12','20','Home','21','Away',now()),
      ('basketball','12','20','Home','21','Away',now()),
      ('mma','3',null,null,null,null,now());
  `)
  await db.exec(await readFile(new URL('../../supabase/migrations/20261008130000_owner_team_api_cache.sql', import.meta.url), 'utf8'))
  assert.equal((await db.query('select count(*)::int as count from public.api_sports_team_directory')).rows[0].count, 3)
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`set role ${role}`)
    await assert.rejects(db.query('select * from public.api_sports_team_stats_cache'), /permission denied/)
    await assert.rejects(db.query('select * from public.api_sports_team_directory'), /permission denied/)
    await db.exec('reset role')
  }
  await db.exec(`set role service_role;
    insert into public.api_sports_team_stats_cache values('nfl','1',10,'2026','season_team','{"yards":500,"missing":null}',now());
    insert into public.api_sports_team_stats_cache values('nfl','1',10,'2026','game_team:1','[]',now());`)
  const rows = (await db.query('select payload from public.api_sports_team_stats_cache order by stat_kind')).rows
  assert.deepEqual(rows, [{ payload: [] }, { payload: { yards: 500, missing: null } }])
  await assert.rejects(db.exec(`insert into public.api_sports_team_stats_cache values('nfl','1',10,'2026','invalid','42',now())`), /check constraint/)
})
